import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import service.rtmp as rtmp


class HlsTempPathTests(unittest.TestCase):
    def test_linux_uses_docker_nginx_hls_directory(self):
        with patch.object(rtmp.sys, "platform", "linux"):
            self.assertEqual(rtmp._get_hls_temp_path("/runtime/nginx"), "/tmp/hls")


class RtmpServiceProbeTests(unittest.TestCase):
    def test_stats_probe_accepts_rtmp_xml(self):
        response = Mock(content=b"<rtmp><uptime>1</uptime></rtmp>")
        response.raise_for_status.return_value = None

        with patch("service.rtmp.requests.get", return_value=response) as get:
            self.assertTrue(rtmp._rtmp_stats_available(timeout=0.25))

        get.assert_called_once_with(
            "http://127.0.0.1:8080/stat",
            timeout=0.25,
            proxies={"http": None, "https": None, "all": None},
        )

    def test_stats_probe_rejects_invalid_response(self):
        response = Mock(content=b"<html>not rtmp</html>")
        response.raise_for_status.return_value = None

        with patch("service.rtmp.requests.get", return_value=response):
            self.assertFalse(rtmp._rtmp_stats_available())

    def test_stats_probe_handles_connection_failure(self):
        with patch(
            "service.rtmp.requests.get",
            side_effect=requests.ConnectionError("unreachable"),
        ):
            self.assertFalse(rtmp._rtmp_stats_available())


class RtmpServiceStartupTests(unittest.TestCase):
    def setUp(self):
        rtmp._nginx_started_by_app = False

    def tearDown(self):
        rtmp._nginx_started_by_app = False

    @patch("service.rtmp.rtmp_runtime_status")
    @patch("service.rtmp._rtmp_stats_available", return_value=True)
    @patch("service.rtmp._managed_nginx_running", return_value=False)
    @patch("service.rtmp.subprocess.run")
    def test_reuses_healthy_external_nginx(
        self,
        run,
        _managed,
        _probe,
        runtime_status,
    ):
        runtime_status.return_value = {
            "available": True,
            "executable": "/usr/local/bin/nginx",
            "module": "",
        }

        self.assertTrue(rtmp.start_rtmp_service())
        self.assertFalse(rtmp._nginx_started_by_app)
        run.assert_not_called()

    @patch("service.rtmp.rtmp_runtime_status")
    @patch("service.rtmp._rtmp_stats_available", return_value=True)
    @patch("service.rtmp._managed_nginx_running", return_value=True)
    @patch("service.rtmp.subprocess.run")
    def test_adopts_healthy_nginx_from_its_runtime_directory(
        self,
        run,
        _managed,
        _probe,
        runtime_status,
    ):
        runtime_status.return_value = {
            "available": True,
            "executable": "/usr/local/bin/nginx",
            "module": "",
        }

        self.assertTrue(rtmp.start_rtmp_service())
        self.assertTrue(rtmp._nginx_started_by_app)
        run.assert_not_called()

    @patch("service.rtmp.rtmp_runtime_status")
    @patch("service.rtmp._rtmp_stats_available", return_value=False)
    @patch("service.rtmp._wait_for_rtmp_service")
    @patch("service.rtmp.render_nginx_conf")
    @patch("service.rtmp.os.makedirs")
    @patch("service.rtmp.os.chdir")
    @patch("service.rtmp.os.getcwd", return_value="/workspace")
    @patch("service.rtmp.subprocess.run")
    def test_reports_nginx_launch_failure(
        self,
        run,
        _getcwd,
        _chdir,
        _makedirs,
        _render,
        wait_for_service,
        _probe,
        runtime_status,
    ):
        runtime_status.return_value = {
            "available": True,
            "executable": "/usr/local/bin/nginx",
            "module": "",
        }
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr="address already in use"),
        ]

        with patch.object(rtmp.sys, "platform", "darwin"):
            self.assertFalse(rtmp.start_rtmp_service())

        self.assertFalse(rtmp._nginx_started_by_app)
        wait_for_service.assert_not_called()

    @patch("service.rtmp.rtmp_runtime_status")
    @patch("service.rtmp._rtmp_stats_available", return_value=False)
    @patch("service.rtmp._wait_for_rtmp_service", return_value=False)
    @patch("service.rtmp.stop_rtmp_service")
    @patch("service.rtmp.render_nginx_conf")
    @patch("service.rtmp.os.makedirs")
    @patch("service.rtmp.os.chdir")
    @patch("service.rtmp.os.getcwd", return_value="/workspace")
    @patch("service.rtmp.subprocess.run")
    def test_stops_nginx_when_health_check_fails(
        self,
        run,
        _getcwd,
        _chdir,
        _makedirs,
        _render,
        stop_service,
        _wait,
        _probe,
        runtime_status,
    ):
        runtime_status.return_value = {
            "available": True,
            "executable": "/usr/local/bin/nginx",
            "module": "",
        }
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]

        with patch.object(rtmp.sys, "platform", "darwin"):
            self.assertFalse(rtmp.start_rtmp_service())

        stop_service.assert_called_once_with()

    @patch("service.rtmp.rtmp_runtime_status")
    @patch("service.rtmp._rtmp_stats_available", return_value=False)
    @patch("service.rtmp._wait_for_rtmp_service", return_value=True)
    @patch("service.rtmp.render_nginx_conf")
    @patch("service.rtmp.os.makedirs")
    @patch("service.rtmp.os.chdir")
    @patch("service.rtmp.os.getcwd", return_value="C:\\workspace")
    @patch("service.rtmp.subprocess.Popen")
    def test_windows_nginx_starts_without_a_console_or_command_shell(
        self,
        popen,
        _getcwd,
        _chdir,
        _makedirs,
        _render,
        _wait,
        _probe,
        runtime_status,
    ):
        runtime_status.return_value = {
            "available": True,
            "executable": "C:\\runtime\\nginx.exe",
            "module": "",
        }

        with patch.object(rtmp.sys, "platform", "win32"), patch.object(
            rtmp, "nginx_dir", "C:\\runtime"
        ):
            self.assertTrue(rtmp.start_rtmp_service())

        popen.assert_called_once_with(
            [
                "C:\\runtime\\nginx.exe",
                "-p",
                "C:\\runtime/",
                "-c",
                "conf/nginx.conf",
            ],
            stdin=rtmp.subprocess.DEVNULL,
            stdout=rtmp.subprocess.DEVNULL,
            stderr=rtmp.subprocess.DEVNULL,
            creationflags=0x08000000,
        )

    @patch("service.rtmp.os.chdir")
    @patch("service.rtmp.os.getcwd", return_value="C:\\workspace")
    @patch("service.rtmp.subprocess.run")
    def test_windows_nginx_stops_without_a_batch_shell(self, run, _getcwd, _chdir):
        rtmp._nginx_started_by_app = True
        with patch.object(rtmp.sys, "platform", "win32"), patch.object(
            rtmp, "nginx_dir", "C:\\runtime"
        ), patch.object(rtmp, "nginx_path", "C:\\runtime\\nginx.exe"):
            rtmp.stop_rtmp_service()

        run.assert_called_once_with(
            [
                "C:\\runtime\\nginx.exe",
                "-p",
                "C:\\runtime/",
                "-c",
                "conf/nginx.conf",
                "-s",
                "stop",
            ],
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,
        )
        self.assertFalse(rtmp._nginx_started_by_app)


if __name__ == "__main__":
    unittest.main()
