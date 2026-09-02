import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("IPTV_API_SKIP_VERSION_CHECK", "1")

from service import app as service_app
from utils.process import no_window_process_kwargs


class ServiceLifecycleTests(unittest.TestCase):
    def test_child_can_probe_parent_without_terminating_it(self):
        environment = {**os.environ, "IPTV_API_SKIP_VERSION_CHECK": "1"}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os; "
                    "from service.app import _process_exists; "
                    "print(_process_exists(os.getppid()))"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
            **no_window_process_kwargs(),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def test_parent_monitor_cleans_up_before_exit(self):
        with patch.object(
            service_app, "_process_exists", side_effect=[True, False]
        ) as process_exists, patch.object(
            service_app.time, "sleep"
        ) as sleep, patch.object(
            service_app, "stop_all_streams"
        ) as stop_streams, patch.object(
            service_app, "stop_rtmp_service"
        ) as stop_rtmp, patch.object(
            service_app.os, "_exit"
        ) as exit_process:
            service_app._watch_parent(1234, interval=0.1)

        self.assertEqual(process_exists.call_count, 2)
        sleep.assert_called_once_with(0.1)
        stop_streams.assert_called_once_with()
        stop_rtmp.assert_called_once_with()
        exit_process.assert_called_once_with(0)

    def test_parent_monitor_is_disabled_for_standalone_service(self):
        with patch.object(service_app.threading, "Thread") as thread:
            self.assertIsNone(service_app._start_parent_monitor(0))

        thread.assert_not_called()

    def test_managed_service_starts_parent_monitor(self):
        runtime_config = SimpleNamespace(
            app_port=5180,
            rtmp_available=False,
            public_url="http://127.0.0.1:5180",
        )
        with patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), patch.object(
            service_app, "config", runtime_config
        ), patch.object(
            service_app, "_configure_service_output"
        ), patch.object(
            service_app, "_service_port_is_open", return_value=False
        ), patch.object(
            service_app, "_start_parent_monitor"
        ) as monitor, patch.object(
            service_app, "get_public_url", return_value="http://127.0.0.1:5180"
        ), patch.object(service_app.app, "run"), patch("builtins.print"):
            service_app.run_service(prompt_for_install=False, parent_pid=4321)

        monitor.assert_called_once_with(4321)
        self.assertEqual(service_app._service_parent_pid, 4321)

    def test_identity_reports_service_owner(self):
        client = service_app.app.test_client()
        with patch.object(service_app, "_service_parent_pid", 4321), patch.object(
            service_app, "_service_started_at", 123.5
        ), patch.object(
            service_app, "_process_exists", return_value=True
        ), patch.object(
            service_app.os, "getpid", return_value=8765
        ), patch.object(
            service_app,
            "get_version_info",
            return_value={"version": "3.0.0", "build_revision": "r1"},
        ):
            response = client.get(
                "/api/runtime/identity",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "service": "iptv-api-desktop-service",
            "pid": 8765,
            "parent_pid": 4321,
            "parent_alive": True,
            "started_at": 123.5,
            "version": "3.0.0",
            "build_revision": "r1",
        })


if __name__ == "__main__":
    unittest.main()
