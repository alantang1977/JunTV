import io
import os
import sys
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QProcess, QProcessEnvironment

from desktop_ui.controller import ServiceProcessController
from utils.process import no_window_process_kwargs


class WindowsProcessOutputTests(unittest.TestCase):
    def test_windows_console_processes_use_no_window_flag(self):
        with patch("utils.process.sys.platform", "win32"):
            self.assertEqual(
                no_window_process_kwargs(),
                {"creationflags": 0x08000000},
            )

    def test_non_windows_processes_are_unchanged(self):
        with patch("utils.process.sys.platform", "darwin"):
            self.assertEqual(no_window_process_kwargs(), {})

    def test_service_output_is_reconfigured_from_gbk_to_utf8(self):
        with patch.dict(os.environ, {"IPTV_API_SKIP_VERSION_CHECK": "1"}):
            from service import app as service_app

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="gbk")
        with patch.object(service_app.sys, "stdout", stream):
            service_app._configure_service_output()
            print("🌐 直连", file=stream)
            stream.flush()

        self.assertEqual(stream.encoding.lower(), "utf-8")
        self.assertEqual(
            raw.getvalue().decode("utf-8"),
            f"🌐 直连{os.linesep}",
        )

    def test_service_child_receives_utf8_output_environment(self):
        process = Mock()
        process.processEnvironment.return_value = QProcessEnvironment()
        with patch.object(
            ServiceProcessController, "_port_open", return_value=False
        ), patch("desktop_ui.controller.QProcess", return_value=process), patch.object(
            sys, "frozen", True, create=True
        ):
            controller = ServiceProcessController()
            controller.start()

        environment = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(
            environment.value("PYTHONIOENCODING"),
            "utf-8:replace",
        )
        process.setArguments.assert_called_once_with([
            "--service",
            "--parent-pid",
            str(os.getpid()),
        ])

    def test_running_service_child_is_not_started_twice(self):
        controller = ServiceProcessController()
        controller.process = Mock()
        controller.process.state.return_value = QProcess.ProcessState.Running

        with patch.object(
            ServiceProcessController,
            "_port_open",
            side_effect=AssertionError("port should not be probed"),
        ):
            controller.start()


if __name__ == "__main__":
    unittest.main()
