from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import math
import os
from functools import lru_cache
from pathlib import Path
import struct
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
    config_root = Path(os.environ.get("APPDATA", "")) / "obs-studio" / "plugin_config"
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
            continue
        try:
            port = int(raw.get("server_port", raw.get("port", 4455)))
        except (TypeError, ValueError, OverflowError):
            port = 4455
        password = raw.get("server_password", raw.get("password", ""))
        return "127.0.0.1", max(1, min(65535, port)), str(password or "")
    return "127.0.0.1", 4455, ""


class WindowsCredentialStore:
    TARGET = "OBSAudioMonitor/OBSWebSocket"
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    class CREDENTIALW(ctypes.Structure):
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

    def __init__(self, target=None) -> None:
        self.TARGET = target or self.TARGET
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(self.CREDENTIALW)),
        ]
        self._advapi32.CredWriteW.argtypes = [
            ctypes.POINTER(self.CREDENTIALW),
            wintypes.DWORD,
        ]
        self._advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

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
            blob = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            return blob.decode("utf-16-le").rstrip("\x00")
        finally:
            self._advapi32.CredFree(pointer)

    def write(self, password: str) -> None:
        if not password:
            self.delete()
            return
        blob = password.encode("utf-16-le")
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
        SOUND_DIR.mkdir(parents=True, exist_ok=True)
        for name, recipe in self.SOUND_RECIPES.items():
            path = SOUND_DIR / f"{name}.wav"
            if not path.is_file():
                self._write_tone(path, recipe)

    @staticmethod
    def _write_tone(path: Path, recipe: list[tuple[float, float]]) -> None:
        sample_rate = 44100
        samples = bytearray()
        for frequency, duration in recipe:
            count = int(sample_rate * duration)
            for index in range(count):
                if frequency <= 0:
                    value = 0.0
                else:
                    position = index / sample_rate
                    attack = min(1.0, index / max(1, sample_rate * 0.008))
                    release = min(1.0, (count - index) / max(1, sample_rate * 0.018))
                    envelope = attack * release
                    wave_value = math.sin(2 * math.pi * frequency * position)
                    wave_value += 0.16 * math.sin(
                        2 * math.pi * frequency * 2 * position
                    )
                    value = 0.32 * envelope * wave_value
                integer = max(-32767, min(32767, int(value * 32767)))
                samples.extend(struct.pack("<h", integer))

        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(samples)

    def play(self, name: str) -> None:
        path = SOUND_DIR / f"{name}.wav"
        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )

    @staticmethod
    def stop() -> None:
        winsound.PlaySound(None, 0)
