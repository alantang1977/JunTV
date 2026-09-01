import unittest
from unittest.mock import Mock, patch

from desktop_ui import single_instance


class SingleInstanceTests(unittest.TestCase):
    @patch("desktop_ui.single_instance.QLocalSocket")
    def test_notifies_an_existing_instance(self, socket_type):
        socket = socket_type.return_value
        socket.waitForConnected.return_value = True

        self.assertTrue(single_instance.notify_existing_instance(250))

        socket.connectToServer.assert_called_once_with(single_instance.SERVER_NAME)
        socket.write.assert_called_once_with(b"activate")
        socket.waitForBytesWritten.assert_called_once_with(250)

    @patch("desktop_ui.single_instance.notify_existing_instance", return_value=False)
    @patch("desktop_ui.single_instance.QLocalServer")
    def test_creates_server_after_removing_stale_endpoint(self, server_type, _notify):
        server = server_type.return_value
        server.listen.return_value = True

        self.assertIs(single_instance.create_single_instance_server(250), server)

        server_type.removeServer.assert_called_once_with(single_instance.SERVER_NAME)
        server.setSocketOptions.assert_called_once_with(
            single_instance.QLocalServer.SocketOption.UserAccessOption
        )
        server.listen.assert_called_once_with(single_instance.SERVER_NAME)

    def test_activation_request_raises_the_existing_window(self):
        server = Mock()
        connection = Mock()
        server.hasPendingConnections.side_effect = [True, False]
        server.nextPendingConnection.return_value = connection
        callback = Mock()

        single_instance.bind_activation(server, callback)
        handler = server.newConnection.connect.call_args.args[0]
        handler()

        callback.assert_called_once_with()
        connection.disconnectFromServer.assert_called_once_with()
        connection.deleteLater.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
