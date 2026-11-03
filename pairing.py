"""Persistent phone pairing, kept out of source and ordinary JSON settings."""
from __future__ import annotations

import re
import hashlib
import secrets
import threading

from platform_support import APP_DIR, WindowsCredentialStore


class PairingIdentity:
    def __init__(self, token=None, store=None):
        self._lock = threading.RLock()
        self._store = store
        value = token if token is not None else store.read() if store else ""
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
            value = secrets.token_urlsafe(32)
            if store:
                store.write(value)
        self._token = value

    @classmethod
    def persistent(cls):
        return cls(store=WindowsCredentialStore(target=cls.credential_target()))

    @staticmethod
    def credential_target(directory=None):
        identity = str((directory or APP_DIR).resolve()).casefold().encode("utf-8")
        return "OBSAudioMonitor/MobilePairing/" + hashlib.sha256(identity).hexdigest()[:24]

    @property
    def token(self):
        with self._lock:
            return self._token

    def rotate(self):
        replacement = secrets.token_urlsafe(32)
        with self._lock:
            if self._store:
                self._store.write(replacement)
            self._token = replacement
        return replacement
