from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket


SERVER_NAME = "IPTV-API-Desktop"


def notify_existing_instance(timeout: int = 500) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(timeout):
        return False
    socket.write(b"activate")
    socket.waitForBytesWritten(timeout)
    socket.disconnectFromServer()
    return True


def create_single_instance_server(timeout: int = 500) -> QLocalServer | None:
    if notify_existing_instance(timeout):
        return None
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if server.listen(SERVER_NAME):
        return server
    if notify_existing_instance(timeout):
        return None
    raise RuntimeError(server.errorString())


def bind_activation(server: QLocalServer, callback: Callable[[], None]) -> None:
    def activate():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            callback()
            socket.disconnectFromServer()
            socket.deleteLater()

    server.newConnection.connect(activate)
