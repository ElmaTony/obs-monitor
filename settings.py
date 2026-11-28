from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from dataclasses import asdict, dataclass, field

from platform_support import APP_DIR, WindowsCredentialStore, detect_obs_websocket_settings

CONFIG_PATH = APP_DIR / "config.json"
ACTIONS = {"start": "开始录制", "stop": "结束录制", "pause": "暂停录制", "resume": "继续录制"}
LOCAL_OBS_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_save_lock = threading.Lock()


def validate_obs_host(raw_host):
    address = raw_host.strip() or "127.0.0.1"
    if any(ord(c) < 32 or c.isspace() or c in "/?#@\\%" for c in address):
        raise ValueError("OBS 地址请只填写 IP 或主机名")
    if "[" in address or "]" in address or ":" in address:
        candidate = address[1:-1] if address.startswith("[") and address.endswith("]") else address
        if "[" in candidate or "]" in candidate:
            raise ValueError("OBS 地址请只填写 IP 或主机名，端口请在下方单独填写")
        try:
            ipaddress.IPv6Address(candidate)
        except ValueError as exc:
            raise ValueError("OBS 地址请只填写 IP 或主机名，端口请在下方单独填写") from exc
    return address


@dataclass
class AppConfig:
    obs_host: str = "127.0.0.1"
    obs_port: int = 4455
    obs_password: str = field(default="", repr=False)
    remote_enabled: bool = True
    remote_port: int = 8765
    sound_enabled: bool = True
    sound_actions: dict = field(default_factory=lambda: dict.fromkeys(ACTIONS, True))
    alert_enabled: bool = True
    threshold_gb: float = 2.0
    theme: str = "light"
    help_seen: bool = False

    @classmethod
    def load(cls):
        host, port, detected_password = detect_obs_websocket_settings()
        try:
            stored_password = WindowsCredentialStore().read()
        except (OSError, ValueError):
            stored_password = ""
        result = cls(obs_host=host, obs_port=port, obs_password=stored_password or detected_password)
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return result
        if not isinstance(raw, dict):
            return result
        if raw.get("theme") in ("light", "dark"):
            result.theme = raw["theme"]
        if isinstance(raw.get("obs_host"), str):
            try:
                result.obs_host = validate_obs_host(raw["obs_host"])
            except ValueError:
                result.obs_host = host
        if not stored_password and result.obs_host.casefold() not in LOCAL_OBS_HOSTS:
            result.obs_password = ""
        for key in ("obs_port", "remote_port"):
            raw_value = raw.get(key, getattr(result, key))
            if isinstance(raw_value, bool):
                continue
            try:
                value = int(raw_value)
                if 1 <= value <= 65535:
                    setattr(result, key, value)
            except (TypeError, ValueError, OverflowError):
                pass
        for key in ("remote_enabled", "sound_enabled", "alert_enabled", "help_seen"):
            if isinstance(raw.get(key), bool):
                setattr(result, key, raw[key])
        if isinstance(raw.get("sound_actions"), dict):
            for key in ACTIONS:
                if isinstance(raw["sound_actions"].get(key), bool):
                    result.sound_actions[key] = raw["sound_actions"][key]
        raw_threshold = raw.get("threshold_gb", result.threshold_gb)
        if not isinstance(raw_threshold, bool):
            try:
                threshold = float(raw_threshold)
                if math.isfinite(threshold) and 0.1 <= threshold <= 102400:
                    result.threshold_gb = threshold
            except (TypeError, ValueError, OverflowError):
                pass
        if result.remote_enabled and result.obs_port == result.remote_port and result.obs_host.casefold() in LOCAL_OBS_HOSTS:
            result.remote_port = 8765 if result.remote_port != 8765 else 8766
        return result

    def save(self, save_password=False):
        # Keep OBS credentials out of JSON, logs, URLs and mobile API responses.
        with _save_lock:
            payload = asdict(self)
            payload.pop("obs_password")
            try:
                previous = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(previous, dict):
                    # Preserve and normalize legacy hotkey preferences on migration.
                    for action in ACTIONS:
                        value = previous.get(action + "_hotkey", previous.get(action))
                        if isinstance(value, str):
                            payload[action + "_hotkey"] = value
            except (OSError, ValueError):
                pass
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            credential_store = WindowsCredentialStore() if save_password else None
            previous_password = None
            previous_password_valid = False
            if credential_store:
                try:
                    previous_password = credential_store.read()
                    previous_password_valid = True
                except ValueError:
                    previous_password = ""
            handle, filename = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=CONFIG_PATH.parent)
            os.close(handle)
            temporary = Path(filename)
            credential_written = False
            try:
                # Prepare JSON before changing a credential. If the final replace
                # fails, restore the previous credential and preserve the old JSON.
                temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                if credential_store:
                    credential_store.write(self.obs_password)
                    credential_written = True
                temporary.replace(CONFIG_PATH)
            except Exception:
                if credential_written:
                    try:
                        if previous_password_valid:
                            credential_store.write(previous_password)
                        else:
                            credential_store.delete()
                    except OSError as rollback_error:
                        raise OSError("配置保存失败，OBS 密码也未能恢复，请在连接设置中重新填写密码") from rollback_error
                raise
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
