from __future__ import annotations

import base64
import hashlib
import json
import queue
import struct
import threading
import time
import uuid
from concurrent.futures import Future

import websocket

REQUESTS = {"start": "StartRecord", "stop": "StopRecord", "pause": "PauseRecord", "resume": "ResumeRecord"}
LABELS = {"start": "开始录制", "stop": "结束录制", "pause": "暂停录制", "resume": "继续录制"}


class OBSRequestError(RuntimeError):
    pass


class OBSWebSocketClient:
    """OBS WebSocket v5. One controller thread owns all requests."""

    def __init__(self, host, port, password, socket_factory=None, timeout=2.5):
        self.host, self.port, self.password = host, port, password
        self.timeout = timeout
        self.socket_factory = socket_factory or websocket.create_connection
        self.ws = None

    @staticmethod
    def build_authentication(password, salt, challenge):
        secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
        return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()

    def _recv_json(self):
        if isinstance(self.ws, websocket.WebSocket):
            with self.ws.readlock:
                opcode, raw_data = self.ws.recv_data()
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                if isinstance(raw_data, (bytes, bytearray)) and len(raw_data) >= 2:
                    code = struct.unpack("!H", raw_data[:2])[0]
                    if code == 4009:
                        raise OBSRequestError("OBS 认证失败，请检查密码")
                    if code in (4006, 4007, 4010, 4011):
                        raise OBSRequestError(f"OBS WebSocket 握手被拒绝（错误码 {code}）")
                raise ConnectionError("OBS 连接已断开")
            if opcode == websocket.ABNF.OPCODE_TEXT:
                raw = raw_data.decode("utf-8") if isinstance(raw_data, (bytes, bytearray)) else raw_data
            elif opcode == websocket.ABNF.OPCODE_BINARY:
                raw = raw_data
            else:
                raw = ""
        else:
            raw = self.ws.recv()
        if raw is None or raw == "" or raw == b"":
            raise ConnectionError("OBS 连接已断开")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectionError("OBS 返回了无法解析的消息") from exc
        if not isinstance(message, dict):
            raise ConnectionError("OBS 返回了无效的消息格式")
        return message

    def connect(self):
        self.close()
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        self.ws = self.socket_factory(f"ws://{host}:{self.port}", timeout=self.timeout, suppress_origin=True)
        try:
            hello = self._recv_json()
            if hello.get("op") != 0:
                raise RuntimeError("OBS 握手失败")
            data = {"rpcVersion": 1, "eventSubscriptions": 0}
            hello_data = hello.get("d") if isinstance(hello.get("d"), dict) else {}
            auth = hello_data.get("authentication")
            if auth:
                if not isinstance(auth, dict) or "salt" not in auth or "challenge" not in auth:
                    raise RuntimeError("OBS 认证握手数据无效")
                if not self.password:
                    raise OBSRequestError("请在连接设置中填写 OBS WebSocket 密码")
                data["authentication"] = self.build_authentication(self.password, auth["salt"], auth["challenge"])
            self.ws.send(json.dumps({"op": 1, "d": data}))
            identified = self._recv_json()
            if identified.get("op") != 2:
                raise OBSRequestError("OBS 认证失败，请检查密码")
            return hello_data
        except Exception:
            self.close()
            raise

    def request(self, request_type, request_data=None):
        if self.ws is None:
            raise ConnectionError("OBS 未连接")
        request_id = uuid.uuid4().hex
        data = {"requestType": request_type, "requestId": request_id}
        if request_data is not None:
            data["requestData"] = request_data
        self.ws.send(json.dumps({"op": 6, "d": data}))
        deadline = time.monotonic() + self.timeout
        has_settimeout = hasattr(self.ws, "settimeout")
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if has_settimeout:
                    self.ws.settimeout(max(0.05, remaining))
                message = self._recv_json()
                if message.get("op") == 5:
                    continue
                payload = message.get("d")
                if message.get("op") != 7 or not isinstance(payload, dict) or payload.get("requestId") != request_id:
                    continue
                # The official protocol uses requestStatus, never responseStatus.
                status = payload.get("requestStatus")
                status = status if isinstance(status, dict) else {}
                if not status.get("result"):
                    comment = status.get("comment") or f"请求失败（{status.get('code', '未知错误')}）"
                    raise OBSRequestError(comment)
                response = payload.get("responseData")
                return {} if response is None else response
            raise TimeoutError("OBS 响应超时")
        finally:
            if has_settimeout and self.ws is not None:
                try:
                    self.ws.settimeout(self.timeout)
                except Exception:
                    pass

    def get_record_status(self):
        data = self.request("GetRecordStatus")
        if not isinstance(data, dict):
            raise ValueError("OBS 返回的录制状态格式无效")
        for key in ("outputBytes", "outputDuration"):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"OBS 未返回有效的 {key}，无法确认实时录制数据")
        for key in ("outputActive", "outputPaused"):
            if not isinstance(data.get(key), bool):
                raise ValueError(f"OBS 未返回有效的 {key}，无法确认录制状态")
        return data

    def get_frame_interval(self):
        data = self.request("GetVideoSettings")
        if not isinstance(data, dict):
            raise ValueError("OBS 未返回有效的帧率，无法确认录制切换的稳定状态")
        numerator, denominator = data.get("fpsNumerator"), data.get("fpsDenominator")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in (numerator, denominator)):
            raise ValueError("OBS 未返回有效的帧率，无法确认录制切换的稳定状态")
        return denominator / numerator

    def close(self):
        connection, self.ws = self.ws, None
        if connection:
            try:
                connection.close()
            except Exception:
                pass


def format_size(size):
    number = max(0, float(size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if unit == "B":
            if number < 1024:
                return f"{int(number)} B"
        elif round(number, 2) < 1024 or unit == "TB":
            return f"{number:.2f} {unit}"
        number /= 1024


def format_duration(milliseconds):
    seconds = max(0, int(milliseconds)) // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class OBSController:
    def __init__(self, config, on_event=None, client_factory=OBSWebSocketClient, interval=0.5, confirmation_timeout=4.5):
        self.config = config
        self.on_event = on_event or (lambda kind, payload: None)
        self.client_factory = client_factory
        self.interval = interval
        self.confirmation_timeout = confirmation_timeout
        self._lock = threading.RLock()
        self._config_lock = threading.RLock()
        self._config_override = {}
        self._state = {"connected": False, "active": False, "paused": False, "bytes": 0,
                       "duration_ms": 0, "message": "正在连接 OBS…", "last_file": "", "busy": False}
        self._commands = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._client = None
        self._last_observed = None
        self._alerted = False
        self._last_error = ""
        self._sampled_at = None
        self._snapshot_revision = 0
        self._instance_id = uuid.uuid4().hex
        self._settle_seconds = 0.2
        self._drain_seconds = 0.0
        self._reconfiguring = False
        self._text_cache = (False, 0, 0, "—", "尚未收到 OBS 数据", "00:00:00")

    def _effective_config_values(self):
        cfg = self.config
        override = self._config_override
        return {
            "sound_enabled": override.get("sound_enabled", cfg.sound_enabled),
            "sound_actions": dict(override.get("sound_actions", cfg.sound_actions)),
            "threshold_gb": override.get("threshold_gb", cfg.threshold_gb),
            "alert_enabled": override.get("alert_enabled", cfg.alert_enabled),
            "theme": override.get("theme", cfg.theme),
            "help_seen": override.get("help_seen", cfg.help_seen),
        }

    @property
    def control_timeout(self):
        with self._lock:
            if self._state["busy"]:
                return max(7.0, max(self.confirmation_timeout, self._drain_seconds) + 2 * self._settle_seconds + 8.0)
            return max(7.0, max(self.confirmation_timeout, self._drain_seconds) + self._settle_seconds + 10.0)

    def snapshot(self):
        with self._lock:
            self._snapshot_revision += 1
            state = dict(self._state)
            state["revision"] = self._snapshot_revision
            state["instance_id"] = self._instance_id
            sampled = self._sampled_at
            cfg = self._effective_config_values()
            has_sample = sampled is not None
            cache_key = (has_sample, state["bytes"], state["duration_ms"])
            if self._text_cache[:3] == cache_key:
                size_text, size_exact_text, duration_text = self._text_cache[3:]
            else:
                size_text = format_size(state["bytes"]) if has_sample else "—"
                size_exact_text = f"{state['bytes']:,} 字节" if has_sample else "尚未收到 OBS 数据"
                duration_text = format_duration(state["duration_ms"])
                self._text_cache = (*cache_key, size_text, size_exact_text, duration_text)
            state.update(sound_enabled=cfg["sound_enabled"],
                         sound_actions=cfg["sound_actions"],
                         threshold_gb=cfg["threshold_gb"],
                         threshold_bytes=int(cfg["threshold_gb"] * 1024**3),
                         alert_enabled=cfg["alert_enabled"],
                         alerted=bool(cfg["alert_enabled"] and self._alerted),
                         control_timeout_ms=int(self.control_timeout * 1000),
                         size_text=size_text,
                         size_exact_text=size_exact_text,
                         duration_text=duration_text)
        state["data_age_ms"] = None if sampled is None else max(0, int((time.monotonic() - sampled) * 1000))
        state["stale"] = not state["connected"] or sampled is None or state["data_age_ms"] > 3000
        state["state_text"] = "未连接" if not state["connected"] else "已暂停" if state["paused"] else "录制中" if state["active"] else "准备就绪"
        if state["connected"] and state["stale"]:
            state["state_text"] = "数据已过期"
        connected, active, paused = state["connected"] and not state["stale"], state["active"], state["paused"]
        state["allowed"] = {"start": connected and not active, "stop": connected and active,
                            "pause": connected and active and not paused, "resume": connected and active and paused}
        return state

    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="obs-controller")
        self._thread.start()

    def stop(self):
        with self._lock:
            self._stop.set()
            self._state.update(connected=False, busy=False, message="软件已关闭")
            client = self._client
        self._wake.set()
        if client:
            client.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=4)
        self._fail_pending("软件已关闭")

    def reconnect(self):
        with self._lock:
            self._reconnect.set()
            self._last_error = ""
            self._state.update(connected=False, message="正在重新连接 OBS…")
            self._fail_pending("OBS 连接正在重置，请确认当前状态后重试")
            client = None if self._state["busy"] else self._client
        if client:
            client.close()
        self._wake.set()

    def submit(self, action):
        future = Future()
        with self._lock:
            if action not in REQUESTS:
                future.set_exception(ValueError("未知操作"))
            elif self._stop.is_set() or self._reconnect.is_set() or not self._state["connected"]:
                future.set_exception(ConnectionError("OBS 尚未连接，请先在电脑上检查连接设置"))
            else:
                try:
                    self._commands.put_nowait((action, future, time.monotonic()))
                    self._wake.set()
                except queue.Full:
                    future.set_exception(RuntimeError("操作过于频繁，请稍后重试"))
        return future

    def _update_config_field(self, key, value, copy_dict=False, skip_if_unchanged=False):
        with self._config_lock:
            with self._lock:
                old = getattr(self.config, key)
                if copy_dict:
                    old = dict(old)
                    new_value = dict(value)
                else:
                    new_value = value
                if skip_if_unchanged and old == new_value:
                    return False
                self._config_override[key] = old
                setattr(self.config, key, new_value)
            try:
                self.config.save()
            except Exception:
                with self._lock:
                    setattr(self.config, key, old)
                    self._config_override.pop(key, None)
                raise
            with self._lock:
                self._config_override.pop(key, None)
            return True

    def set_sound_enabled(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("提示音开关必须是布尔值")
        self._update_config_field("sound_enabled", enabled)
        self.on_event("log", "操作提示音已" + ("开启" if enabled else "关闭"))

    def set_sound_actions(self, values):
        if set(values) != set(REQUESTS) or any(not isinstance(value, bool) for value in values.values()):
            raise ValueError("操作提示音设置无效")
        self._update_config_field("sound_actions", values, copy_dict=True)

    def set_theme(self, theme):
        if theme not in ("light", "dark"):
            raise ValueError("主题无效")
        self._update_config_field("theme", theme)

    def mark_help_seen(self):
        self._update_config_field("help_seen", True, skip_if_unchanged=True)

    def apply_connection_config(self, candidate):
        # Settings dialogs and phone sound updates share a transaction. A sound
        # toggle received while a dialog is open must not be lost when it saves.
        old_client = None
        changed = False
        with self._config_lock:
            with self._lock:
                candidate.sound_enabled = self.config.sound_enabled
                candidate.sound_actions = dict(self.config.sound_actions)
                candidate.theme = self.config.theme
                candidate.help_seen = self.config.help_seen
                previous = self.config
                changed = any(getattr(candidate, key) != getattr(previous, key)
                              for key in ("obs_host", "obs_port", "obs_password"))
                if changed and (self._state["busy"] or self._reconfiguring):
                    raise ValueError("正在确认录制操作，请等待操作完成后再修改 OBS 连接")
                if changed:
                    self._reconfiguring = True
            try:
                candidate.save(save_password=True)
                with self._lock:
                    candidate.sound_enabled = self.config.sound_enabled
                    candidate.sound_actions = dict(self.config.sound_actions)
                    candidate.theme = self.config.theme
                    candidate.help_seen = self.config.help_seen
                    self.config = candidate
                    if not candidate.alert_enabled or (
                        candidate.threshold_gb > previous.threshold_gb
                        and self._state["bytes"] < int(candidate.threshold_gb * 1024**3)
                    ):
                        self._alerted = False
                    if changed:
                        self._reconnect.set()
                        self._last_error = ""
                        self._state.update(connected=False, message="正在重新连接 OBS…")
                        if (candidate.obs_host, candidate.obs_port) != (previous.obs_host, previous.obs_port):
                            self._last_observed = self._sampled_at = None
                            self._alerted = False
                            self._state.update(active=False, paused=False, bytes=0, duration_ms=0, last_file="")
                        self._fail_pending("OBS 连接设置已更改，旧操作已取消，请确认新连接的状态")
                        old_client = self._client
                        self._wake.set()
            finally:
                if changed:
                    with self._lock:
                        self._reconfiguring = False
                    self._wake.set()
        if old_client:
            old_client.close()
        return previous

    def _observe_locked(self, data, notify=True):
        current = {"active": bool(data.get("outputActive")), "paused": bool(data.get("outputPaused")),
                   "bytes": max(0, int(data.get("outputBytes") or 0)),
                   "duration_ms": max(0, int(data.get("outputDuration") or 0))}
        events = []
        previous = self._last_observed
        action = None
        reset_counters = previous is not None and current["bytes"] < previous["bytes"] and current["duration_ms"] < previous["duration_ms"]
        new_session = current["active"] and (previous is None or not previous["active"] or reset_counters)
        if new_session or not current["active"]:
            self._alerted = False
        if previous is not None:
            if new_session:
                action = "start"
            elif previous["active"] and not current["active"]:
                action = "stop"
            elif current["active"] and current["paused"] != previous["paused"]:
                action = "pause" if current["paused"] else "resume"
        display = dict(current)
        if not current["active"] and previous and previous["active"]:
            display["bytes"] = max(current["bytes"], previous["bytes"])
            display["duration_ms"] = max(current["duration_ms"], previous["duration_ms"])
        elif not current["active"]:
            display["bytes"] = self._state["bytes"]
            display["duration_ms"] = self._state["duration_ms"]
        self._last_observed = current
        cfg = self._effective_config_values()
        threshold = int(cfg["threshold_gb"] * 1024**3)
        if not cfg["alert_enabled"] or (current["active"] and current["bytes"] < threshold):
            self._alerted = False
        fire_alert = cfg["alert_enabled"] and current["active"] and current["bytes"] >= threshold and not self._alerted
        if fire_alert:
            self._alerted = True
        self._sampled_at = time.monotonic()
        self._state.update(connected=True, message="", **display)
        if notify and action:
            events.append(("action", action))
        if fire_alert:
            events.append(("size_alert", current["bytes"]))
        return events, action

    def _observe(self, data, notify=True):
        with self._lock:
            events, _ = self._observe_locked(data, notify=notify)
        for kind, payload in events:
            self.on_event(kind, payload)

    def _execute(self, action, future, queued_at):
        if not future.set_running_or_notify_cancel():
            return
        request_sent = False
        try:
            if time.monotonic() - queued_at > 6:
                raise OBSRequestError("操作等待超时，请确认当前状态后重试")
            if self._stop.is_set() or self._reconnect.is_set():
                raise OBSRequestError("软件已关闭或 OBS 连接已重置，操作未执行")
            data = self._client.get_record_status()
            with self._lock:
                if self._stop.is_set() or self._reconnect.is_set():
                    raise OBSRequestError("软件已关闭或 OBS 连接已重置，操作未执行")
                preflight_events, _ = self._observe_locked(data)
                allowed_now = self.snapshot()["allowed"][action]
                if allowed_now:
                    self._state["busy"] = True
            for kind, payload in preflight_events:
                self.on_event(kind, payload)
            if not allowed_now:
                raise OBSRequestError("当前状态不能" + LABELS[action])
            # Video settings can change between recordings without reconnecting.
            frame_interval = self._client.get_frame_interval()
            with self._lock:
                self._settle_seconds = min(30.0, max(0.2, 3 * frame_interval))
                # OBS NVENC can queue up to 64 frames when stopping. For other
                # transitions, fractional frame rates still need at least two
                # frame intervals before the output flag flips.
                self._drain_seconds = min(600.0, (64 if action == "stop" else 2) * frame_interval)
            if self._stop.is_set() or self._reconnect.is_set():
                raise OBSRequestError("软件已关闭或 OBS 连接已重置，操作未执行")
            request_sent = True
            response = self._client.request(REQUESTS[action])
            if action == "stop" and isinstance(response, dict):
                output_path = response.get("outputPath")
                self._update(last_file=output_path if isinstance(output_path, str) else "")
            # OBS queues control requests. Confirm the actual output state before success.
            # An unpaused output flag can precede the encoder clearing its old pause
            # timestamps. A second pause during that interval is silently ignored by
            # OBS. Keep the transition busy for at least three video frame intervals,
            # and require the target state to stay stable throughout that window.
            flip_deadline = time.monotonic() + max(self.confirmation_timeout, self._drain_seconds)
            base_deadline = flip_deadline + self._settle_seconds
            confirmed_since = None
            while not self._stop.is_set() and not self._reconnect.is_set():
                data = self._client.get_record_status()
                with self._lock:
                    if self._stop.is_set() or self._reconnect.is_set():
                        raise OBSRequestError("软件已关闭或连接已重置，操作结果未确认")
                    pending_events, observed_action = self._observe_locked(data, notify=False)
                    if action in ("pause", "resume") and not self._state["active"]:
                        if observed_action == "stop":
                            pending_events.insert(0, ("action", "stop"))
                        aborted_by_stop = True
                    else:
                        aborted_by_stop = False
                    state = self.snapshot()
                for kind, payload in pending_events:
                    self.on_event(kind, payload)
                if aborted_by_stop:
                    raise OBSRequestError(f"OBS 录制已结束，无法{LABELS[action]}")
                confirmed = {"start": state["active"], "stop": not state["active"],
                             "pause": state["active"] and state["paused"],
                             "resume": state["active"] and not state["paused"]}[action]
                now = time.monotonic()
                if not confirmed:
                    confirmed_since = None
                elif confirmed_since is None:
                    confirmed_since = now
                if confirmed_since is not None and now - confirmed_since >= self._settle_seconds:
                    break
                effective_deadline = (
                    max(base_deadline, min(confirmed_since + self._settle_seconds + 0.1, flip_deadline + 2 * self._settle_seconds))
                    if confirmed_since is not None
                    else base_deadline
                )
                if now >= effective_deadline:
                    if action == "stop":
                        raise OBSRequestError("OBS 已接收结束录制请求，但尚未完成收尾；请检查 OBS 当前状态")
                    raise OBSRequestError("OBS 已接收请求，但录制状态未改变；请检查 OBS 中的对话框、录制编码器和当前状态")
                self._stop.wait(0.08)
            else:
                raise OBSRequestError("软件已关闭或连接已重置，操作结果未确认")
            with self._lock:
                if self._stop.is_set() or self._reconnect.is_set():
                    raise OBSRequestError("软件已关闭或连接已重置，操作结果未确认")
                self._drain_seconds = 0.0
                self._state["busy"] = False
                final_state = self.snapshot()
            self.on_event("action", action)
            future.set_result({"ok": True, "message": LABELS[action] + "成功", "state": final_state})
        except OBSRequestError as exc:
            with self._lock:
                self._drain_seconds = 0.0
                self._state["busy"] = False
            self.on_event("error", str(exc))
            future.set_exception(exc)
        except Exception as exc:
            with self._lock:
                self._drain_seconds = 0.0
                self._state["busy"] = False
            if request_sent:
                future.set_exception(ConnectionError("连接中断，操作结果未知；请查看 OBS 状态后再操作"))
            elif isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
                future.set_exception(OBSRequestError(str(exc)))
            else:
                future.set_exception(ConnectionError("OBS 连接中断，操作未执行"))
            raise exc
        finally:
            with self._lock:
                self._drain_seconds = 0.0
                self._state["busy"] = False

    def _fail_pending(self, message):
        while True:
            try:
                _, future, _ = self._commands.get_nowait()
            except queue.Empty:
                break
            if future.set_running_or_notify_cancel():
                future.set_exception(ConnectionError(message))

    def _run(self):
        while not self._stop.is_set():
            client = None
            try:
                with self._lock:
                    if self._stop.is_set():
                        break
                    self._reconnect.clear()
                    config = self.config
                    client = self.client_factory(config.obs_host, config.obs_port, config.obs_password)
                    self._client = client
                if self._stop.is_set() or self._reconnect.is_set():
                    continue
                client.connect()
                frame_interval = client.get_frame_interval()
                data = client.get_record_status()
                with self._lock:
                    if self._stop.is_set() or self._reconnect.is_set():
                        continue
                    self._settle_seconds = min(30.0, max(0.2, 3 * frame_interval))
                    initial_events, _ = self._observe_locked(data)
                for kind, payload in initial_events:
                    self.on_event(kind, payload)
                self.on_event("log", "OBS 已连接")
                self._last_error = ""
                next_poll = time.monotonic() + self.interval
                while not self._stop.is_set() and not self._reconnect.is_set():
                    self._wake.clear()
                    with self._lock:
                        reconfiguring = self._reconfiguring
                    if reconfiguring:
                        self._wake.wait(0.02)
                        continue
                    try:
                        command = self._commands.get_nowait()
                    except queue.Empty:
                        command = None
                    if command:
                        self._execute(*command)
                        continue
                    if time.monotonic() >= next_poll:
                        data = client.get_record_status()
                        poll_events = []
                        with self._lock:
                            if not self._stop.is_set() and not self._reconnect.is_set():
                                poll_events, _ = self._observe_locked(data)
                        for kind, payload in poll_events:
                            self.on_event(kind, payload)
                        next_poll = time.monotonic() + self.interval
                    if not self._stop.is_set() and not self._reconnect.is_set() and self._commands.empty():
                        self._wake.wait(max(0.01, min(self.interval, next_poll - time.monotonic())))
            except Exception as exc:
                message = f"无法连接 OBS：{exc}"
                self._update(connected=False, busy=False, message="未连接 OBS · 请打开 OBS 并检查 WebSocket 设置")
                self._fail_pending("OBS 连接已断开，操作未执行")
                if message != self._last_error and not self._stop.is_set():
                    self.on_event("error", message)
                    self._last_error = message
            finally:
                with self._lock:
                    if self._client is client:
                        self._client = None
                    self._state["connected"] = False
                if client:
                    client.close()
                self._fail_pending("OBS 连接已重置，请查看当前状态后重新操作")
            if not self._stop.is_set() and not self._reconnect.is_set():
                self._wake.wait(2)
                self._wake.clear()
