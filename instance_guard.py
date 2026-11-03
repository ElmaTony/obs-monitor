"""Use a user-scoped local socket to keep one desktop controller running."""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from platform_support import APP_DIR


class SingleInstance(QObject):
    activate = Signal()

    def __init__(self, name=None, parent=None):
        super().__init__(parent)
        identity = str(APP_DIR.resolve()).casefold().encode("utf-8")
        self.name = name or "obs-monitor-" + hashlib.sha256(identity).hexdigest()[:24]
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._receive)
        self.acquired = False

    def _notify_existing(self):
        connection = QLocalSocket(self)
        connection.connectToServer(self.name)
        connected = connection.waitForConnected(400)
        if connected:
            connection.write(b"activate")
            connection.waitForBytesWritten(300)
            connection.disconnectFromServer()
        connection.deleteLater()
        return connected

    def claim(self):
        if self._notify_existing():
            return False
        if not self.server.listen(self.name):
            if self._notify_existing():
                return False
            raise RuntimeError("无法创建应用实例，请关闭其他 OBS Monitor 后重试")
        self.acquired = True
        return True

    def _receive(self):
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            connection.readyRead.connect(connection.readAll)
            connection.readAll()
            connection.disconnectFromServer()
            self.activate.emit()

    def close(self):
        self.server.close()
        self.acquired = False
