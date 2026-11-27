from __future__ import annotations
import array
import ctypes
from ctypes import wintypes
import json
import math
import os
from functools import lru_cache
from pathlib import Path
import sys
import tempfile
import wave
import winsound

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "OBSAudioMonitor"
SOUND_DIR = APP_DIR / "sounds"


@lru_cache(maxsize=1)
def _dwm_setter():
    setter = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
    setter.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    setter.restype = ctypes.c_long
    return setter


def set_title_bar_theme(handle, dark, background, foreground):
    """Keep the native caption; older Windows versions ignore unsupported colors."""
    try:
        setter = _dwm_setter()
        colors = [int(value.lstrip("#"), 16) for value in (background, foreground)]
        values = [(20, int(dark))]  # DWMWA_USE_IMMERSIVE_DARK_MODE
        for attribute, rgb in zip((35, 36), colors):  # CAPTION_COLOR / TEXT_COLOR
            values.append((attribute, (rgb & 255) << 16 | rgb & 0xff00 | rgb >> 16))
        for attribute, value in values:
            data = wintypes.DWORD(value)
            setter(handle, attribute, ctypes.byref(data), ctypes.sizeof(data))
    except (AttributeError, OSError):
        pass


def animations_enabled():
    try:
        enabled = wintypes.BOOL(True)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)  # SPI_GETCLIENTAREAANIMATION
    except (AttributeError, OSError):
        pass
    return True

def detect_obs_websocket_settings() -> tuple[str, int, str]:
    """Read local obs-websocket settings when the user has enabled the server."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return "127.0.0.1", 4455, ""
    config_root = Path(appdata) / "obs-studio" / "plugin_config"
    candidates = (
        config_root / "obs-websocket" / "config.json",
        config_root / "obs-websocket.json",
    )
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        enabled = raw.get("server_enabled", raw.get("enabled", True))
        if not enabled:
            if path == candidates[0]:
                return "127.0.0.1", 4455, ""
            continue
        raw_port = raw.get("server_port", raw.get("port", 4455))
        port = 4455
        if not isinstance(raw_port, bool):
            try:
                parsed = int(raw_port)
                if 1 <= parsed <= 65535:
                    port = parsed
            except (TypeError, ValueError, OverflowError):
                pass
        if raw.get("auth_required") is False:
            password = ""
        else:
            password = raw.get("server_password", raw.get("password", ""))
        return "127.0.0.1", port, str(password or "")
    return "127.0.0.1", 4455, ""


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


@lru_cache(maxsize=1)
def _advapi32():
    dll = ctypes.WinDLL("advapi32", use_last_error=True)
    dll.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    dll.CredWriteW.argtypes = [
        ctypes.POINTER(_CREDENTIALW),
        wintypes.DWORD,
    ]
    dll.CredDeleteW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    dll.CredFree.argtypes = [ctypes.c_void_p]
    dll.CredFree.restype = None
    return dll


class WindowsCredentialStore:
    TARGET = "OBSAudioMonitor/OBSWebSocket"
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    CREDENTIALW = _CREDENTIALW

    def __init__(self, target=None) -> None:
        self.TARGET = target or self.TARGET
        self._advapi32 = _advapi32()

    def read(self) -> str:
        pointer = ctypes.POINTER(self.CREDENTIALW)()
        if not self._advapi32.CredReadW(
            self.TARGET, self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            error = ctypes.get_last_error()
            if error == 1168:  # ERROR_NOT_FOUND: no previously saved credential.
                return ""
            raise OSError(error, "无法读取 Windows 连接凭据")
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not credential.CredentialBlobSize:
                return ""
            blob = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            try:
                return blob.decode("utf-16-le").rstrip("\x00")
            except UnicodeDecodeError as exc:
                raise ValueError("Windows 连接凭据数据已损坏") from exc
        finally:
            self._advapi32.CredFree(pointer)

    def write(self, password: str) -> None:
        if not password:
            self.delete()
            return
        blob = password.encode("utf-16-le")
        if len(blob) > 2560:
            raise ValueError("连接凭据过长，无法保存到 Windows 凭据管理器")
        buffer = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
        credential = self.CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = self.TARGET
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(
            buffer, ctypes.POINTER(ctypes.c_byte)
        )
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "OBS"
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise OSError(
                ctypes.get_last_error(), "无法将连接凭据保存到 Windows 凭据管理器"
            )

    def delete(self) -> None:
        if not self._advapi32.CredDeleteW(self.TARGET, self.CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != 1168:
                raise OSError(error, "无法移除 Windows 连接凭据")



class AudioPlayer:
    """Generate small original PCM tones locally; no media files are required."""

    SOUND_RECIPES = {
        "start": [(523.25, 0.10), (0, 0.025), (783.99, 0.17)],
        "stop": [(659.25, 0.12), (0, 0.025), (392.00, 0.22)],
        "pause": [(440.00, 0.09), (0, 0.065), (440.00, 0.09)],
        "resume": [(523.25, 0.075), (659.25, 0.075), (880.00, 0.16)],
        "size_alert": [
            (987.77, 0.12),
            (0, 0.055),
            (987.77, 0.12),
            (0, 0.055),
            (1174.66, 0.18),
        ],
    }

    def __init__(self) -> None:
        self._verified: dict[Path, tuple[int, int]] = {}
        SOUND_DIR.mkdir(parents=True, exist_ok=True)
        for name in self.SOUND_RECIPES:
            self._ensure_tone(name, SOUND_DIR / f"{name}.wav")

    def _ensure_tone(self, name: str, path: Path) -> None:
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp is not None and self._verified.get(path) == stamp:
            return
        if stamp is not None and self._valid_tone(path):
            self._verified[path] = stamp
            return
        if name in self.SOUND_RECIPES:
            SOUND_DIR.mkdir(parents=True, exist_ok=True)
            self._write_tone(path, self.SOUND_RECIPES[name])
            try:
                stat = path.stat()
                self._verified[path] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                self._verified.pop(path, None)

    @staticmethod
    def _valid_tone(path):
        try:
            with wave.open(str(path), "rb") as tone:
                frames = tone.getnframes()
                if frames <= 0:
                    return False
                tone.setpos(frames - 1)
                return len(tone.readframes(1)) == tone.getnchannels() * tone.getsampwidth()
        except (OSError, EOFError, wave.Error):
            return False

    @staticmethod
    def _write_tone(path: Path, recipe: list[tuple[float, float]]) -> None:
        sample_rate = 44100
        sin = math.sin
        tau = 2 * math.pi
        attack_samples = max(1, sample_rate * 0.008)
        release_samples = max(1, sample_rate * 0.018)
        samples = array.array("h")
        for frequency, duration in recipe:
            count = int(sample_rate * duration)
            if frequency <= 0:
                samples.extend(0 for _ in range(count))
                continue
            for index in range(count):
                position = index / sample_rate
                attack = min(1.0, index / attack_samples)
                release = min(1.0, (count - index) / release_samples)
                envelope = attack * release
                phase = tau * frequency * position
                wave_value = sin(phase) + 0.16 * sin(2 * phase)
                value = 0.32 * envelope * wave_value
                samples.append(max(-32767, min(32767, int(value * 32767))))
        if sys.byteorder != "little":
            samples.byteswap()

        handle, filename = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(filename)
        try:
            with wave.open(str(temporary), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(samples.tobytes())
            temporary.replace(path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def play(self, name: str) -> None:
        path = SOUND_DIR / f"{name}.wav"
        if name in self.SOUND_RECIPES:
            self._ensure_tone(name, path)
        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )

    @staticmethod
    def stop() -> None:
        winsound.PlaySound(None, 0)
