import datetime
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytz
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget
from PySide6.QtNetwork import QNetworkProxy

from desktop_ui.controller import RtmpMonitorController
from desktop_ui.main_window import MainWindow
from desktop_ui.pages.dashboard import DashboardPage, next_scheduled_update


class TrayMenuStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_menu_groups_status_tasks_results_and_exit(self):
        host = QWidget()
        host.tray = QSystemTrayIcon(host)
        host.dashboard = SimpleNamespace(open_output=lambda: None)
        host.tasks = host.logs = host.settings = object()
        host._refresh_tray_menu = lambda: None
        for name in (
            "show_and_raise",
            "_start_update",
            "_pause_update",
            "_resume_update",
            "_cancel_update_from_tray",
            "_copy_service_url_from_tray",
            "_start_service_from_tray",
            "_restart_service_from_tray",
            "_stop_service_from_tray",
            "quit_app",
        ):
            setattr(host, name, lambda: None)

        MainWindow._create_tray_menu(host)
        MainWindow._retranslate_tray_actions(host)

        menu = host.tray.contextMenu()
        self.assertIs(menu.defaultAction(), host.show_action)
        self.assertIn(host.tray_task_menu.menuAction(), menu.actions())
        self.assertIn(host.tray_result_menu.menuAction(), menu.actions())
        self.assertIs(menu.actions()[-1], host.quit_action)


class TrayScheduleTests(unittest.TestCase):
    def test_time_schedule_uses_the_next_configured_time_today(self):
        timezone = pytz.timezone("Asia/Shanghai")
        now = timezone.localize(datetime.datetime(2026, 7, 29, 10, 0))
        schedule_config = SimpleNamespace(
            time_zone="Asia/Shanghai",
            update_mode="time",
            update_times="06:00, 18:30",
            update_interval=0,
        )

        with patch("desktop_ui.pages.dashboard.config", schedule_config):
            next_time = next_scheduled_update(now)

        self.assertEqual(next_time, timezone.localize(datetime.datetime(2026, 7, 29, 18, 30)))

    def test_time_schedule_rolls_past_times_to_tomorrow(self):
        timezone = pytz.timezone("Asia/Shanghai")
        now = timezone.localize(datetime.datetime(2026, 7, 29, 20, 0))
        schedule_config = SimpleNamespace(
            time_zone="Asia/Shanghai",
            update_mode="time",
            update_times="06:00, 18:30",
            update_interval=0,
        )

        with patch("desktop_ui.pages.dashboard.config", schedule_config):
            next_time = next_scheduled_update(now)

        self.assertEqual(next_time, timezone.localize(datetime.datetime(2026, 7, 30, 6, 0)))

    def test_disabled_schedule_returns_none(self):
        schedule_config = SimpleNamespace(
            time_zone="Asia/Shanghai",
            update_mode="time",
            update_times="",
            update_interval=0,
        )

        with patch("desktop_ui.pages.dashboard.config", schedule_config):
            self.assertIsNone(next_scheduled_update())

    def test_interval_schedule_advances_from_the_latest_successful_run(self):
        timezone = pytz.timezone("Asia/Shanghai")
        now = timezone.localize(datetime.datetime(2026, 7, 29, 10, 0))
        last_run = timezone.localize(datetime.datetime(2026, 7, 29, 0, 0))
        schedule_config = SimpleNamespace(
            time_zone="Asia/Shanghai",
            update_mode="interval",
            update_times="",
            update_interval=6,
        )

        with (
            patch("desktop_ui.pages.dashboard.config", schedule_config),
            patch(
                "desktop_ui.pages.dashboard.latest_successful_run",
                return_value={"finished_at": last_run.timestamp()},
            ),
        ):
            next_time = next_scheduled_update(now)

        self.assertEqual(next_time, timezone.localize(datetime.datetime(2026, 7, 29, 12, 0)))


class TrayActivityTests(unittest.TestCase):
    @staticmethod
    def _host(update_state="idle", operation_busy=False, active_streams=0):
        return SimpleNamespace(
            _update_activity_state=update_state,
            operation_controller=SimpleNamespace(is_busy=operation_busy),
            _rtmp_snapshot={"active_count": active_streams},
        )

    def test_update_marks_tray_as_busy(self):
        self.assertTrue(MainWindow._has_active_work(self._host(update_state="running")))

    def test_channel_operation_marks_tray_as_busy(self):
        self.assertTrue(MainWindow._has_active_work(self._host(operation_busy=True)))

    def test_active_stream_marks_tray_as_busy(self):
        self.assertTrue(MainWindow._has_active_work(self._host(active_streams=1)))

    def test_starting_stream_marks_tray_as_busy(self):
        host = self._host()
        host._rtmp_snapshot["starting_count"] = 1
        self.assertTrue(MainWindow._has_active_work(host))

    def test_idle_tray_is_not_busy(self):
        self.assertFalse(MainWindow._has_active_work(self._host()))


class TrayServicePortTests(unittest.TestCase):
    @staticmethod
    def _host(status="running", rtmp_available=False):
        return SimpleNamespace(
            _service_status=status,
            _rtmp_snapshot={"available": rtmp_available},
        )

    def test_uses_public_proxy_port_when_rtmp_is_healthy(self):
        self.assertEqual(MainWindow._service_port(self._host(rtmp_available=True)), 8080)

    def test_falls_back_to_app_port_when_rtmp_is_unavailable(self):
        self.assertEqual(MainWindow._service_port(self._host(rtmp_available=False)), 5180)

    def test_stopped_service_does_not_advertise_proxy_port(self):
        self.assertEqual(
            MainWindow._service_port(self._host(status="stopped", rtmp_available=True)),
            5180,
        )


class DashboardServiceUrlTests(unittest.TestCase):
    @staticmethod
    def _host(status="running", rtmp_available=False):
        return SimpleNamespace(
            _service_status=status,
            _stream_snapshot={"available": rtmp_available},
        )

    @patch("desktop_ui.pages.dashboard.get_public_url", side_effect=lambda port: f"http://host:{port}")
    def test_uses_proxy_url_only_while_proxy_is_healthy(self, _public_url):
        self.assertEqual(
            DashboardPage._service_url(self._host(rtmp_available=True)),
            "http://host:8080",
        )
        self.assertEqual(
            DashboardPage._service_url(self._host(rtmp_available=False)),
            "http://host:5180",
        )
        self.assertEqual(
            DashboardPage._service_url(self._host(status="stopped", rtmp_available=True)),
            "http://host:5180",
        )

    @patch("desktop_ui.pages.dashboard.get_public_url", return_value="https://iptv.example.com")
    @patch(
        "desktop_ui.pages.dashboard.config",
        SimpleNamespace(public_url="https://iptv.example.com"),
    )
    def test_prefers_explicit_public_url_while_service_is_running(
        self,
        _public_url,
    ):
        self.assertEqual(
            DashboardPage._service_url(self._host(rtmp_available=True)),
            "https://iptv.example.com",
        )


class RtmpControlNetworkTests(unittest.TestCase):
    def test_loopback_control_requests_bypass_system_proxy(self):
        controller = RtmpMonitorController()
        self.assertEqual(
            controller.network.proxy().type(),
            QNetworkProxy.ProxyType.NoProxy,
        )
