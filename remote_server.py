from __future__ import annotations

import hmac
import ipaddress
import json
import socket
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from pairing import PairingIdentity
from version import VERSION

PAIRING_COOKIE = "obs_monitor_pairing"
PAIRING_MAX_AGE = 365 * 24 * 60 * 60


def lan_addresses():
    addresses = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        addresses.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    addresses = list(dict.fromkeys(ip for ip in addresses if not ip.startswith(("127.", "169.254."))))
    # VPN benchmark addresses (198.18/15) are not reachable from a phone's Wi-Fi.
    benchmark = ipaddress.ip_network("198.18.0.0/15")
    addresses = [ip for ip in addresses if ipaddress.ip_address(ip) not in benchmark]
    return sorted(addresses, key=lambda ip: 0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2)


class RemoteServer:
    def __init__(self, controller, host="0.0.0.0", port=8765, identity=None):
        self.controller, self.host, self.port = controller, host, port
        self.identity = identity or PairingIdentity()
        self.server = None
        self.thread = None
        self._accepting = threading.Event()

    @property
    def token(self):
        return self.identity.token

    def reset_pairing(self):
        return self.identity.rotate()

    def url(self, address=None):
        address = address or next(iter(lan_addresses()), "127.0.0.1")
        return f"http://{address}:{self.port}/#token={self.token}"

    def start(self):
        if self.server:
            return
        owner = self
        page = (Path(__file__).parent / "mobile.html").read_bytes().replace(b"__APP_VERSION__", VERSION.encode("ascii"))

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, *args):
                pass  # Never log tokens, connection settings, or request payloads.

            def send_payload(self, status, payload, content_type="application/json; charset=utf-8", forget=False):
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
                    self.send_header("Connection", "close")
                    if forget:
                        self.send_header("Set-Cookie", f"{PAIRING_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
                    elif getattr(self, "_renew_pairing", False) and self._pairing_token == owner.token:
                        self.send_header("Set-Cookie", f"{PAIRING_COOKIE}={self._pairing_token}; Path=/; Max-Age={PAIRING_MAX_AGE}; HttpOnly; SameSite=Strict")
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # A phone may cancel a superseded poll or leave the page.
                finally:
                    self.close_connection = True

            def authenticated(self):
                if not owner._accepting.is_set():
                    self.send_payload(503, {"ok": False, "message": "手机遥控已关闭"})
                    return False
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers.get("Host", ""):
                    self.send_payload(403, {"ok": False, "message": "请求来源无效"})
                    return False
                provided = self.headers.get("X-OBS-Token", "")
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    saved = cookie[PAIRING_COOKIE].value if PAIRING_COOKIE in cookie else ""
                except CookieError:
                    saved = ""
                token_at_auth = owner.token
                expected = token_at_auth.encode("ascii")
                if not any(hmac.compare_digest(value.encode("utf-8"), expected) for value in (provided, saved)):
                    self.send_payload(401, {"ok": False, "message": "此浏览器尚未配对，或电脑已重置配对。请粘贴电脑中的完整控制链接，或扫码连接。"})
                    return False
                self._pairing_token = token_at_auth
                self._renew_pairing = not hmac.compare_digest(saved.encode("utf-8"), expected)
                return True

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/":
                    self.send_payload(200, page, "text/html; charset=utf-8")
                elif path == "/api/status":
                    if self.authenticated():
                        self.send_payload(200, owner.controller.snapshot())
                elif path == "/favicon.ico":
                    self.send_payload(204, b"", "image/x-icon")
                else:
                    self.send_payload(404, {"ok": False, "message": "页面不存在"})

            def do_POST(self):
                if not self.authenticated():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("请求长度无效")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError("请求格式无效")
                except (ValueError, TypeError):
                    self.send_payload(400, {"ok": False, "message": "请求格式无效"})
                    return
                # Reading a slow request body can overlap a desktop pairing reset.
                # Do not let an already revoked credential change recording/settings.
                if not owner._accepting.is_set():
                    self.send_payload(503, {"ok": False, "message": "手机遥控已关闭，操作未执行"})
                    return
                if not hmac.compare_digest(self._pairing_token, owner.token):
                    self.send_payload(401, {"ok": False, "message": "配对已重置，请使用电脑中的新链接重新连接"})
                    return
                path = urlsplit(self.path).path
                if path == "/api/control":
                    if data.get("action") not in ("start", "stop", "pause", "resume"):
                        self.send_payload(400, {"ok": False, "message": "操作无效"})
                        return
                    future = owner.controller.submit(data["action"])
                    try:
                        self.send_payload(200, future.result(timeout=owner.controller.control_timeout))
                    except FutureTimeout:
                        cancelled = future.cancel()
                        message = "操作已取消，请稍后重试" if cancelled else "操作结果尚未确认，请查看 OBS 当前状态后再操作"
                        self.send_payload(504, {"ok": False, "message": message})
                    except Exception as exc:
                        self.send_payload(409, {"ok": False, "message": str(exc)})
                elif path == "/api/sound":
                    if not isinstance(data.get("enabled"), bool):
                        self.send_payload(400, {"ok": False, "message": "提示音开关格式无效"})
                        return
                    try:
                        owner.controller.set_sound_enabled(data["enabled"])
                        self.send_payload(200, {"ok": True, "state": owner.controller.snapshot()})
                    except OSError:
                        self.send_payload(500, {"ok": False, "message": "提示音设置保存失败，请在电脑检查"})
                elif path == "/api/unpair":
                    self.send_payload(200, {"ok": True}, forget=True)
                else:
                    self.send_payload(404, {"ok": False, "message": "接口不存在"})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="phone-remote")
        self._accepting.set()
        self.thread.start()

    def stop(self):
        self._accepting.clear()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
