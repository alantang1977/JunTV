import datetime
import os
import sys
import threading

import pytz
from PySide6.QtCore import QSettings, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFontMetrics, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QMenu, QMessageBox, QSystemTrayIcon, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition, Theme, isDarkTheme, setTheme

from desktop_ui.controller import ChannelOperationController, RtmpMonitorController, ServiceProcessController, UpdateController
from desktop_ui.pages.channels import ChannelCenterPage
from desktop_ui.pages.about import AboutPage
from desktop_ui.pages.dashboard import DashboardPage, next_scheduled_update
from desktop_ui.pages.logs import LogsPage
from desktop_ui.pages.rtmp import RtmpPage
from desktop_ui.pages.settings import SettingsPage
from desktop_ui.pages.sources import SourcesPage
from desktop_ui.pages.tasks import TasksPage
from desktop_ui.models import ChannelLogoLoader
from desktop_ui.widgets import NavigationStatusIndicator, apply_dialog_theme, localize_dialog_buttons
from desktop_ui.platform_integration import set_macos_activation_policy, suspend_macos_window_flush
import utils.constants as constants
from utils.config import config, resource_path
from utils.i18n import get_language, set_language, t
from utils.rtmp_runtime import install_rtmp_runtime
from utils.tools import get_public_url, get_version_info


class MainWindow(FluentWindow):
    MACOS_TITLE_BAR_HEIGHT = 32

    rtmp_install_finished = Signal(dict)
    rtmp_install_output = Signal(str)

    def __init__(self, start_runtime: bool = True):
        super().__init__()
        self._start_runtime = bool(start_runtime)
        info = get_version_info()
        self._version = str(info.get("version") or "--")
        self.setWindowTitle(str(info.get("name") or "IPTV-API"))
        icon_path = "static/images/macos_app_icon.icns" if sys.platform == "darwin" else "favicon.ico"
        self.setWindowIcon(QIcon(resource_path(icon_path)))
        self._window_geometry_timer = QTimer(self)
        self._window_geometry_timer.setSingleShot(True)
        self._window_geometry_timer.setInterval(300)
        self._window_geometry_timer.timeout.connect(self._save_window_geometry)
        self._configure_initial_geometry()
        # The navigation stack's pop-up animation offsets pages vertically.
        # Keep page changes stationary so they cannot be mistaken for window resizing.
        self.stackedWidget.setAnimationEnabled(False)
        self.navigationInterface.setIndicatorAnimationEnabled(False)
        self.navigationInterface.setReturnButtonVisible(False)
        if sys.platform == "darwin":
            self.setSystemTitleBarButtonVisible(True)
            self.titleBar.minBtn.hide()
            self.titleBar.maxBtn.hide()
            self.titleBar.closeBtn.hide()
            self.titleBar.setFixedHeight(self.MACOS_TITLE_BAR_HEIGHT)
            self.widgetLayout.setContentsMargins(
                0, self.MACOS_TITLE_BAR_HEIGHT, 0, 0
            )
            margins = self.titleBar.hBoxLayout.contentsMargins()
            self.titleBar.hBoxLayout.setContentsMargins(
                margins.left(),
                margins.top(),
                margins.right(),
                self.MACOS_TITLE_BAR_HEIGHT - 32,
            )
        self.navigationInterface.setExpandWidth(220)
        self.navigationInterface.setMinimumExpandWidth(840)
        self.channel_logo_loader = ChannelLogoLoader(self)
        self.dashboard = DashboardPage(self, self.channel_logo_loader)
        self.channels = ChannelCenterPage(self, self.channel_logo_loader)
        self.rtmp = RtmpPage(self)
        self.sources = SourcesPage(self)
        self.logs = LogsPage(self)
        self.tasks = TasksPage(self)
        self.settings = SettingsPage(self)
        self.about = AboutPage(self)
        self.dashboard_item = self.addSubInterface(self.dashboard, FluentIcon.HOME, t("desktop.dashboard"))
        self.channels_item = self.addSubInterface(self.channels, FluentIcon.LIBRARY, t("desktop.channel_center"))
        self.sources_item = self.addSubInterface(self.sources, FluentIcon.FOLDER, t("desktop.sources"))
        self.rtmp_item = self.addSubInterface(self.rtmp, FluentIcon.IOT, t("desktop.play_streaming"))
        self.logs_item = self.addSubInterface(self.logs, FluentIcon.COMMAND_PROMPT, t("desktop.logs"))
        self.tasks_item = self.addSubInterface(self.tasks, FluentIcon.HISTORY, t("desktop.task_history"))
        self.settings_item = self.addSubInterface(self.settings, FluentIcon.SETTING, t("desktop.settings"), NavigationItemPosition.BOTTOM)
        self.about_item = self.addSubInterface(self.about, FluentIcon.INFO, t("desktop.about"), NavigationItemPosition.BOTTOM)
        self._navigation_items = {
            "dashboard": (self.dashboard_item, "desktop.dashboard", self.dashboard),
            "channels": (self.channels_item, "desktop.channel_center", self.channels),
            "sources": (self.sources_item, "desktop.sources", self.sources),
            "rtmp": (self.rtmp_item, "desktop.play_streaming", self.rtmp),
            "logs": (self.logs_item, "desktop.logs", self.logs),
            "tasks": (self.tasks_item, "desktop.task_history", self.tasks),
            "settings": (self.settings_item, "desktop.settings", self.settings),
            "about": (self.about_item, "desktop.about", self.about),
        }
        self._navigation_statuses = {}
        self._navigation_indicators = {}
        for name, (item, _, _) in self._navigation_items.items():
            indicator = NavigationStatusIndicator(item)
            indicator.move(24, 3)
            self._navigation_indicators[name] = indicator
        self.language_item = self.navigationInterface.addItem(
            "languageToggle",
            FluentIcon.LANGUAGE,
            "",
            self.toggle_language,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self.language_item.setEnabled(not self._language_environment_override())
        self.theme_item = self.navigationInterface.addItem(
            "themeToggle",
            FluentIcon.CONSTRACT,
            "",
            self.toggle_theme,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        panel = self.navigationInterface.panel
        navigation_top = self.MACOS_TITLE_BAR_HEIGHT if sys.platform == "darwin" else 48
        panel.vBoxLayout.setContentsMargins(0, navigation_top, 0, 5)
        panel.topLayout.removeWidget(panel.menuButton)
        panel.bottomLayout.addWidget(panel.menuButton, 0, Qt.AlignmentFlag.AlignBottom)
        self._update_language_item()
        self._update_theme_item()
        self._remove_navigation_tooltips()
        self._fit_navigation_width()
        self.controller = UpdateController(self)
        self._update_activity_state = "idle"
        self._update_progress_value = 0
        self._service_status = "unknown"
        self._rtmp_snapshot = {}
        self._tray_refresh_timer = QTimer(self)
        self._tray_refresh_timer.setSingleShot(True)
        self._tray_refresh_timer.setInterval(500)
        self._tray_refresh_timer.timeout.connect(self._refresh_tray_menu)
        self.operation_controller = ChannelOperationController(self)
        self.rtmp_controller = RtmpMonitorController(self)
        self.service_controller = ServiceProcessController(self)
        self.dashboard.run_requested.connect(self._start_update)
        self.dashboard.pause_requested.connect(self._pause_update)
        self.dashboard.resume_requested.connect(self._resume_update)
        self.dashboard.cancel_requested.connect(self._cancel_update)
        self.dashboard.destination_requested.connect(self._navigate_from_dashboard)
        self.settings.settings_saved.connect(self.dashboard.refresh_schedule)
        self.settings.settings_saved.connect(self._refresh_tray_menu)
        self.controller.started.connect(self._update_started)
        self.controller.progress.connect(self.dashboard.set_progress)
        self.controller.progress.connect(self._update_tray_progress)
        self.controller.output.connect(self._on_runtime_output)
        self.controller.finished.connect(self._update_finished)
        self.controller.failed.connect(self._update_failed)
        self.channels.retest_channel_requested.connect(
            lambda row: self.operation_controller.enqueue("retest_channel", {"channel_key": row["channel_key"]})
        )
        self.channels.retest_result_requested.connect(
            lambda row: self.operation_controller.enqueue(
                "retest_result",
                {"channel_key": row["channel_key"], "result_key": row["result_key"]},
            )
        )
        self.channels.retest_results_requested.connect(
            lambda rows: self.operation_controller.enqueue(
                "retest_results",
                {
                    "channel_key": rows[0]["channel_key"],
                    "result_keys": [row["result_key"] for row in rows],
                },
            )
        )
        self.channels.capture_screenshot_requested.connect(
            lambda row: self.operation_controller.enqueue(
                "capture_result_screenshot",
                {"channel_key": row["channel_key"], "result_key": row["result_key"]},
            )
        )
        self.channels.capture_screenshots_requested.connect(
            lambda rows: self.operation_controller.enqueue(
                "capture_result_screenshots",
                {
                    "channel_key": rows[0]["channel_key"],
                    "result_keys": [row["result_key"] for row in rows],
                },
            )
        )
        self.operation_controller.task_started.connect(self.channels.set_task_started)
        self.operation_controller.task_started.connect(self._operation_started)
        self.operation_controller.task_progress.connect(self.channels.set_task_progress)
        self.operation_controller.task_succeeded.connect(self._operation_succeeded)
        self.operation_controller.task_failed.connect(self._operation_failed)
        self.rtmp_controller.snapshot.connect(self.rtmp.set_snapshot)
        self.rtmp_controller.snapshot.connect(self.dashboard.set_stream_snapshot)
        self.rtmp_controller.snapshot.connect(self.channels.set_stream_snapshot)
        self.rtmp_controller.snapshot.connect(self._update_rtmp_navigation_status)
        self.rtmp.stream_control_many_requested.connect(self.rtmp_controller.control_many)
        self.dashboard.stream_control_many_requested.connect(self.rtmp_controller.control_many)
        self.channels.stream_control_many_requested.connect(self.rtmp_controller.control_many)
        self.rtmp.refresh_requested.connect(self.rtmp_controller.refresh)
        self.rtmp.install_requested.connect(self._install_rtmp_from_ui)
        self.rtmp.settings_requested.connect(self._open_rtmp_limit_settings)
        self.channels.playback_workspace_requested.connect(self._open_playback_workspace)
        self.channels.playback_batch_requested.connect(self._open_playback_batch)
        self.channels.stream_monitor_requested.connect(lambda: self.switchTo(self.rtmp))
        self.rtmp_controller.control_finished.connect(self._stream_control_finished)
        self.rtmp_controller.batch_control_finished.connect(self._stream_batch_control_finished)
        self.service_controller.status_changed.connect(self._service_status_changed)
        self.service_controller.output.connect(self._on_runtime_output)
        self.about.status_changed.connect(self._update_about_navigation_status)
        self.rtmp_install_finished.connect(self._finish_rtmp_install)
        self.rtmp_install_output.connect(self._append_runtime_log)
        self.stackedWidget.currentChanged.connect(self._navigation_page_changed)
        if self._start_runtime and config.open_service:
            self._start_service()
        else:
            self._service_status_changed("stopped")
        if self._start_runtime:
            self.rtmp_controller.start()
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self._force_quit = False
        self.tray = None
        if self._start_runtime and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self._create_tray_menu()
            self.tray.activated.connect(lambda reason: self.show_and_raise() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self._refresh_tray_menu()
            self.tray.show()
        else:
            QApplication.instance().setQuitOnLastWindowClosed(True)

    def _updateSystemButtonRect(self):
        if sys.platform == "darwin":
            # AppKit already keeps the native traffic lights stable. Moving
            # them manually makes page layout events expose two positions.
            return
        updater = getattr(super(), "_updateSystemButtonRect", None)
        if updater is not None:
            return updater()

    def resizeEvent(self, event):
        if sys.platform == "darwin":
            # FluentWindow first lays the title bar out at x=46. Setting the
            # final macOS geometry directly avoids exposing that intermediate
            # frame when a page activation produces a same-size resize event.
            self.titleBar.setGeometry(90, 0, max(0, self.width() - 90), self.titleBar.height())
        else:
            super().resizeEvent(event)
        if self.isVisible():
            self._window_geometry_timer.start()

    def switchTo(self, interface):
        """Publish page layout and native title-bar changes in one frame."""
        with suspend_macos_window_flush(self):
            super().switchTo(interface)
            if sys.platform == "darwin":
                QApplication.sendPostedEvents()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible():
            self._window_geometry_timer.start()

    def _configure_initial_geometry(self):
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry()
        minimum_width = min(960, available.width())
        minimum_height = min(640, available.height())
        self.setMinimumSize(minimum_width, minimum_height)

        saved_geometry = QSettings().value("appearance/window_geometry")
        if saved_geometry is not None and self.restoreGeometry(saved_geometry):
            return

        width = max(minimum_width, min(1280, round(available.width() * 0.9)))
        height = max(minimum_height, min(800, round(available.height() * 0.9)))
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    def _save_window_geometry(self):
        QSettings().setValue("appearance/window_geometry", self.saveGeometry())

    def toggle_theme(self):
        self.set_dark_theme(not isDarkTheme())

    @staticmethod
    def _language_environment_override():
        names = ("language", "LANGUAGE", "Settings_language", "SETTINGS_LANGUAGE")
        return any(os.getenv(name) is not None for name in names)

    def toggle_language(self):
        language = "zh_CN" if get_language().startswith("en") else "en"
        config.set("Settings", "language", language)
        config.save()
        set_language(language)
        self.retranslate()

    def set_dark_theme(self, dark: bool):
        setTheme(Theme.DARK if dark else Theme.LIGHT)
        QSettings().setValue("appearance/theme", "dark" if dark else "light")
        self._update_theme_item()

    def _update_theme_item(self):
        text = t("desktop.light_mode" if isDarkTheme() else "desktop.dark_mode")
        self.theme_item.setText(text)

    def _update_language_item(self):
        text = t("desktop.chinese" if get_language().startswith("en") else "desktop.english")
        self.language_item.setText(text)

    def _fit_navigation_width(self):
        """Fit the expanded rail to its longest current label without wasting content space."""
        items = [item for item, _, _ in self._navigation_items.values()]
        items.extend((self.language_item, self.theme_item))
        longest = max(QFontMetrics(item.font()).horizontalAdvance(item.text()) for item in items)
        width = max(164, longest + 83)  # 44px icon inset, 13px end inset, plus panel padding.
        self.navigationInterface.setExpandWidth(width)
        panel = self.navigationInterface.panel
        if not panel.isCollapsed():
            panel.resize(width, panel.height())
            for item in items:
                if not item.isCompacted:
                    item.setFixedWidth(width - 10)

    def _remove_navigation_tooltips(self):
        """Remove qfluent's default hover filters and native tooltip text from the rail."""
        items = [item for item, _, _ in self._navigation_items.values()]
        items.extend((self.language_item, self.theme_item))
        for item in items:
            item.setToolTip("")
            for child in item.children():
                if child.__class__.__name__ == "NavigationToolTipFilter":
                    item.removeEventFilter(child)
                    child.deleteLater()

    def retranslate(self, _language=None):
        navigation_items = (
            (self.dashboard_item, "desktop.dashboard"),
            (self.channels_item, "desktop.channel_center"),
            (self.sources_item, "desktop.sources"),
            (self.rtmp_item, "desktop.play_streaming"),
            (self.logs_item, "desktop.logs"),
            (self.tasks_item, "desktop.task_history"),
            (self.settings_item, "desktop.settings"),
            (self.about_item, "desktop.about"),
        )
        for item, key in navigation_items:
            text = t(key)
            item.setText(text)
        for page in (
            self.dashboard,
            self.channels,
            self.sources,
            self.rtmp,
            self.logs,
            self.tasks,
            self.settings,
            self.about,
        ):
            page.retranslate()
        if self.tray:
            self._refresh_tray_menu()
        self._update_language_item()
        self._update_theme_item()
        self._fit_navigation_width()
        self._refresh_navigation_statuses()

    def _set_navigation_status(
            self,
            name: str,
            icon,
            color: str,
            status_key: str,
            status_args: dict | None = None,
            dismiss_on_visit: bool = False,
    ):
        if name not in self._navigation_items:
            return
        _, _, page = self._navigation_items[name]
        if dismiss_on_visit and self.stackedWidget.currentWidget() is page:
            self._clear_navigation_status(name)
            return
        self._navigation_statuses[name] = {
            "icon": icon,
            "color": color,
            "status_key": status_key,
            "status_args": status_args or {},
            "dismiss_on_visit": dismiss_on_visit,
        }
        self._refresh_navigation_status(name)

    def _clear_navigation_status(self, name: str):
        self._navigation_statuses.pop(name, None)
        indicator = self._navigation_indicators.get(name)
        if indicator:
            indicator.hide()
        if name in self._navigation_items:
            item, _, _ = self._navigation_items[name]
            item.setToolTip("")

    def _refresh_navigation_status(self, name: str):
        status = self._navigation_statuses.get(name)
        if not status:
            return
        item, _, _ = self._navigation_items[name]
        detail = t(status["status_key"]).format(**status["status_args"])
        item.setToolTip("")
        indicator = self._navigation_indicators[name]
        indicator.setToolTip("")
        if self.stackedWidget.currentWidget() is self._navigation_items[name][2]:
            indicator.hide()
        else:
            indicator.set_status(status["icon"], status["color"])

    def _refresh_navigation_statuses(self):
        for name in self._navigation_statuses:
            self._refresh_navigation_status(name)

    def _navigation_page_changed(self, _index: int):
        current = self.stackedWidget.currentWidget()
        for name, (_, _, page) in self._navigation_items.items():
            status = self._navigation_statuses.get(name)
            if page is current and status and status.get("dismiss_on_visit"):
                self._clear_navigation_status(name)
        self._refresh_navigation_statuses()

    def show_and_raise(self):
        if sys.platform == "darwin":
            set_macos_activation_policy(False)
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        if self._has_active_work() and not self._confirm_tray_action(
            t("desktop.tray_quit_busy_title"),
            t("desktop.tray_quit_busy_prompt"),
        ):
            return
        self._force_quit = True
        QApplication.quit()

    def _create_tray_menu(self):
        menu = QMenu(self)
        self.tray_title_action = QAction(self)
        self.tray_service_status_action = QAction(self)
        self.tray_update_status_action = QAction(self)
        self.tray_schedule_action = QAction(self)
        self.tray_stream_status_action = QAction(self)
        for action in (
            self.tray_title_action,
            self.tray_service_status_action,
            self.tray_update_status_action,
            self.tray_schedule_action,
            self.tray_stream_status_action,
        ):
            action.setEnabled(False)
            menu.addAction(action)
        menu.addSeparator()

        self.show_action = QAction(self)
        self.show_action.triggered.connect(self.show_and_raise)
        menu.addAction(self.show_action)
        menu.setDefaultAction(self.show_action)

        self.tray_task_menu = QMenu(self)
        self.tray_run_update_action = QAction(self)
        self.tray_pause_update_action = QAction(self)
        self.tray_resume_update_action = QAction(self)
        self.tray_cancel_update_action = QAction(self)
        self.tray_task_history_action = QAction(self)
        self.tray_run_update_action.triggered.connect(self._start_update)
        self.tray_pause_update_action.triggered.connect(self._pause_update)
        self.tray_resume_update_action.triggered.connect(self._resume_update)
        self.tray_cancel_update_action.triggered.connect(self._cancel_update_from_tray)
        self.tray_task_history_action.triggered.connect(lambda: self._open_tray_page(self.tasks))
        for action in (
            self.tray_run_update_action,
            self.tray_pause_update_action,
            self.tray_resume_update_action,
            self.tray_cancel_update_action,
        ):
            self.tray_task_menu.addAction(action)
        self.tray_task_menu.addSeparator()
        self.tray_task_menu.addAction(self.tray_task_history_action)
        menu.addMenu(self.tray_task_menu)

        self.tray_result_menu = QMenu(self)
        self.tray_open_results_action = QAction(self)
        self.tray_copy_service_url_action = QAction(self)
        self.tray_open_output_action = QAction(self)
        self.tray_start_service_action = QAction(self)
        self.tray_restart_service_action = QAction(self)
        self.tray_stop_service_action = QAction(self)
        self.tray_open_results_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(self._service_url()))
        )
        self.tray_copy_service_url_action.triggered.connect(self._copy_service_url_from_tray)
        self.tray_open_output_action.triggered.connect(self.dashboard.open_output)
        self.tray_start_service_action.triggered.connect(self._start_service_from_tray)
        self.tray_restart_service_action.triggered.connect(self._restart_service_from_tray)
        self.tray_stop_service_action.triggered.connect(self._stop_service_from_tray)
        for action in (
            self.tray_open_results_action,
            self.tray_copy_service_url_action,
            self.tray_open_output_action,
        ):
            self.tray_result_menu.addAction(action)
        self.tray_result_menu.addSeparator()
        for action in (
            self.tray_start_service_action,
            self.tray_restart_service_action,
            self.tray_stop_service_action,
        ):
            self.tray_result_menu.addAction(action)
        menu.addMenu(self.tray_result_menu)

        menu.addSeparator()
        self.tray_logs_action = QAction(self)
        self.tray_settings_action = QAction(self)
        self.tray_logs_action.triggered.connect(lambda: self._open_tray_page(self.logs))
        self.tray_settings_action.triggered.connect(lambda: self._open_tray_page(self.settings))
        menu.addAction(self.tray_logs_action)
        menu.addAction(self.tray_settings_action)
        menu.addSeparator()

        self.quit_action = QAction(self)
        self.quit_action.triggered.connect(self.quit_app)
        menu.addAction(self.quit_action)
        menu.aboutToShow.connect(self._refresh_tray_menu)
        self.tray.setContextMenu(menu)

    def _refresh_tray_menu(self):
        if not getattr(self, "tray", None):
            return
        self.tray_title_action.setText(
            t("desktop.tray_title").format(version=getattr(self, "_version", "--"))
        )

        service_label = {
            "running": t("desktop.running"),
            "external": t("desktop.external_service"),
            "stopped": t("desktop.stopped"),
            "failed": t("desktop.unavailable"),
        }.get(self._service_status, t("desktop.unknown"))
        self.tray_service_status_action.setText(t("desktop.tray_service_status").format(
            status=service_label,
        ))

        update_key = {
            "running": "desktop.tray_update_running",
            "paused": "desktop.tray_update_paused",
            "stopping": "desktop.tray_update_stopping",
            "failed": "desktop.tray_update_failed",
        }.get(self._update_activity_state, "desktop.tray_update_idle")
        update_args = {"progress": self._update_progress_value}
        self.tray_update_status_action.setText(t(update_key).format(**update_args))
        schedule_text, schedule_detail = self._tray_schedule_text()
        self.tray_schedule_action.setText(schedule_text)

        active_streams = max(0, int(self._rtmp_snapshot.get("active_count") or 0))
        self.tray_stream_status_action.setVisible(active_streams > 0)
        self.tray_stream_status_action.setText(
            t("desktop.tray_active_streams").format(count=active_streams)
        )

        is_running = self._update_activity_state == "running"
        is_paused = self._update_activity_state == "paused"
        is_stopping = self._update_activity_state == "stopping"
        is_idle = not (is_running or is_paused or is_stopping)
        self.tray_run_update_action.setVisible(is_idle)
        self.tray_run_update_action.setEnabled(is_idle and not self.operation_controller.is_busy)
        self.tray_pause_update_action.setVisible(is_running)
        self.tray_resume_update_action.setVisible(is_paused)
        self.tray_cancel_update_action.setVisible(is_running or is_paused or is_stopping)
        self.tray_cancel_update_action.setEnabled(not is_stopping)

        service_available = self._service_status in {"running", "external"}
        service_owned = self.service_controller.owns_process and self._service_status == "running"
        self.tray_open_results_action.setEnabled(service_available)
        self.tray_copy_service_url_action.setEnabled(service_available)
        self.tray_start_service_action.setVisible(not service_available)
        self.tray_start_service_action.setEnabled(self._service_status != "unknown")
        self.tray_restart_service_action.setVisible(service_owned)
        self.tray_stop_service_action.setVisible(service_owned)
        self.tray_logs_action.setText(
            t("desktop.tray_view_logs_error")
            if "logs" in self._navigation_statuses
            else t("desktop.tray_view_logs")
        )

        tooltip_lines = [
            "IPTV API",
            t("desktop.tray_tooltip_service").format(status=service_label),
            self.tray_update_status_action.text(),
            schedule_detail,
        ]
        if active_streams:
            tooltip_lines.append(t("desktop.tray_active_streams").format(count=active_streams))
        self.tray.setToolTip("\n".join(tooltip_lines))
        self._update_tray_icon()
        self._retranslate_tray_actions()

    def _update_tray_icon(self):
        if self._service_status == "failed" or "logs" in self._navigation_statuses:
            color = "#DC2626"
        elif (
            self._update_activity_state in {"running", "paused", "stopping"}
            or int(self._rtmp_snapshot.get("starting_count") or 0) > 0
        ):
            color = "#2563EB"
        elif self._service_status not in {"running", "external"}:
            color = "#D97706"
        else:
            color = "#059669"
        pixmap = self.windowIcon().pixmap(32, 32)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(20, 20, 12, 12)
        painter.setBrush(QColor(color))
        painter.drawEllipse(22, 22, 8, 8)
        painter.end()
        self.tray.setIcon(QIcon(pixmap))

    def _retranslate_tray_actions(self):
        self.show_action.setText(t("desktop.show_window"))
        self.tray_task_menu.setTitle(t("desktop.tray_update_tasks"))
        self.tray_run_update_action.setText(t("desktop.run_once"))
        self.tray_pause_update_action.setText(t("desktop.pause"))
        self.tray_resume_update_action.setText(t("desktop.resume"))
        self.tray_cancel_update_action.setText(t("desktop.tray_cancel_update"))
        self.tray_task_history_action.setText(t("desktop.task_history"))
        self.tray_result_menu.setTitle(t("desktop.tray_results_service"))
        self.tray_open_results_action.setText(t("desktop.tray_open_results"))
        self.tray_copy_service_url_action.setText(t("desktop.tray_copy_service_url"))
        self.tray_open_output_action.setText(t("desktop.open_output"))
        self.tray_start_service_action.setText(t("desktop.tray_start_service"))
        self.tray_restart_service_action.setText(t("desktop.tray_restart_service"))
        self.tray_stop_service_action.setText(t("desktop.tray_stop_service"))
        self.tray_settings_action.setText(t("desktop.settings"))
        self.quit_action.setText(t("desktop.quit"))

    def _tray_schedule_text(self):
        try:
            next_time = next_scheduled_update()
            if next_time is None:
                value = t("desktop.tray_schedule_disabled")
                return value, value
            timezone = pytz.timezone(config.time_zone)
            now = datetime.datetime.now(timezone)
            next_time = next_time.astimezone(timezone)
            delta_days = (next_time.date() - now.date()).days
            if delta_days == 0:
                key = "desktop.tray_next_update_today"
                value = next_time.strftime("%H:%M")
            elif delta_days == 1:
                key = "desktop.tray_next_update_tomorrow"
                value = next_time.strftime("%H:%M")
            else:
                key = "desktop.tray_next_update_date"
                value = next_time.strftime("%m-%d %H:%M")
            return (
                t(key).format(time=value),
                t("desktop.next_update_time").format(time=next_time.strftime("%Y-%m-%d %H:%M:%S")),
            )
        except Exception:
            value = t("desktop.tray_schedule_unavailable")
            return value, value

    def _open_tray_page(self, page):
        self.show_and_raise()
        self.switchTo(page)

    def _copy_service_url_from_tray(self):
        QGuiApplication.clipboard().setText(self._service_url())
        self.tray.showMessage(
            t("desktop.tray_copy_service_url"),
            t("desktop.tray_service_url_copied"),
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )

    def _service_port(self) -> int:
        use_rtmp_proxy = (
            self._service_status in {"running", "external"}
            and bool(self._rtmp_snapshot.get("available"))
        )
        return config.service_port if use_rtmp_proxy else config.app_port

    def _service_url(self) -> str:
        if config.public_url and self._service_status in {"running", "external"}:
            return get_public_url()
        return get_public_url(self._service_port())

    def _start_service_from_tray(self):
        if self.service_controller.process is not None:
            self.service_controller.stop()
        self._start_service()

    def _update_tray_progress(self, _title, progress, _finished=False, _metadata=None, _now=None):
        self._update_progress_value = max(0, min(100, int(progress)))
        if not self._tray_refresh_timer.isActive():
            self._tray_refresh_timer.start()

    def _cancel_update_from_tray(self):
        if self._confirm_tray_action(
            t("desktop.tray_cancel_update"),
            t("desktop.tray_cancel_update_prompt"),
        ):
            self._cancel_update()

    def _stop_service_from_tray(self):
        if not self._confirm_tray_action(
            t("desktop.tray_stop_service"),
            t("desktop.tray_stop_service_prompt"),
        ):
            return
        self.service_controller.stop()
        self._service_status_changed("stopped")

    def _restart_service_from_tray(self):
        active_streams = max(0, int(self._rtmp_snapshot.get("active_count") or 0))
        message = (
            t("desktop.tray_restart_service_prompt").format(count=active_streams)
            if active_streams
            else t("desktop.tray_restart_service_prompt_idle")
        )
        if not self._confirm_tray_action(
            t("desktop.tray_restart_service"),
            message,
        ):
            return
        self.service_controller.stop()
        self._service_status_changed("stopped")
        self._start_service()

    def _confirm_tray_action(self, title: str, message: str):
        result = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def _has_active_work(self):
        return (
            self._update_activity_state in {"running", "paused", "stopping"}
            or self.operation_controller.is_busy
            or int(self._rtmp_snapshot.get("active_count") or 0) > 0
            or int(self._rtmp_snapshot.get("starting_count") or 0) > 0
        )

    def _navigate_from_dashboard(self, destination: str):
        page = {
            "channels": self.channels,
            "rtmp": self.rtmp,
            "sources": self.sources,
            "tasks": self.tasks,
        }.get(destination)
        if page:
            self.switchTo(page)

    def _open_playback_workspace(self, row: dict):
        self.rtmp.select_result(row)
        self.switchTo(self.rtmp)

    def _open_playback_batch(self, rows: list[dict]):
        channel_keys = [row["channel_key"] for row in rows if row.get("channel_key")]
        selected_count = self.rtmp.select_channels(channel_keys)
        expected_count = len(channel_keys)
        if selected_count < expected_count:
            InfoBar.warning(
                t("desktop.start_selected_streams"),
                t("desktop.channels_without_output").format(count=expected_count - selected_count),
                parent=self,
                position=InfoBarPosition.TOP,
            )
        self.switchTo(self.rtmp)

    def _open_rtmp_limit_settings(self):
        self.switchTo(self.settings)
        self.settings.focus_setting("rtmp_max_streams")
        InfoBar.info(
            t("desktop.adjust_concurrency_limit"),
            t("desktop.concurrency_limit_restart_notice"),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=8000,
        )

    def _update_finished(self):
        outcome = self.dashboard.last_update_outcome or {}
        if self._update_activity_state == "failed":
            self._set_update_navigation_status("failed")
        elif self._update_activity_state == "stopping":
            self._set_update_navigation_status("cancelled")
        elif outcome.get("status") == "empty":
            self._set_update_navigation_status("warning")
            detail = " ".join(
                part for part in (
                    str(outcome.get("message") or "").strip(),
                    t("desktop.update_needs_sources_detail"),
                )
                if part
            )
            InfoBar.warning(
                t("desktop.update_needs_sources"),
                detail,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=10000,
            )
        else:
            self._set_update_navigation_status("completed")
        self.dashboard.set_running(False)
        self.dashboard.refresh_metrics()
        self.channels.reload()
        self.rtmp.reload_channels()
        self.operation_controller.resume()

    def _start_service(self):
        if config.open_rtmp and not config.rtmp_available:
            if self._install_rtmp_from_ui(start_service=True):
                return
            InfoBar.warning(
                t("name.error"),
                t("msg.rtmp_unavailable_fallback"),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=-1,
            )
        self.service_controller.start()
        self._refresh_tray_menu()

    def _install_rtmp_from_ui(self, start_service=False):
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(t("name.error"))
        dialog.setWindowIcon(self.windowIcon())
        layout = QVBoxLayout(dialog)
        logo = QLabel(dialog)
        logo.setPixmap(self.windowIcon().pixmap(64, 64))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        message = QLabel(t("msg.rtmp_install_confirm"), dialog)
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No,
            parent=dialog,
        )
        localize_dialog_buttons(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(logo)
        layout.addWidget(message)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self._start_service_after_rtmp_install = start_service
        self.rtmp.set_installing(True)
        self._append_runtime_log(f"{t('msg.rtmp_installing')}\n")
        threading.Thread(
            target=lambda: self.rtmp_install_finished.emit(
                install_rtmp_runtime(self.rtmp_install_output.emit)
            ),
            daemon=True,
        ).start()
        return True

    def _finish_rtmp_install(self, result: dict):
        self.rtmp.set_installing(False)
        if result.get("available"):
            self._append_runtime_log(f"{t('msg.rtmp_install_success')}\n")
            InfoBar.success(
                t("desktop.rtmp_service"),
                t("msg.rtmp_install_success"),
                parent=self,
                position=InfoBarPosition.TOP,
            )
        else:
            self._mark_logs_error()
            info = result.get("output") or t(
                f"msg.rtmp_{result.get('error_code')}", result.get("error_code")
            ) or t("desktop.unknown")
            self._append_runtime_log(f"{t('msg.rtmp_install_failed').format(info=info[-1000:])}\n")
            InfoBar.error(
                t("name.error"),
                t("msg.rtmp_install_failed").format(info=info[-1000:]),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=-1,
            )
        self.rtmp_controller.refresh()
        if getattr(self, "_start_service_after_rtmp_install", False):
            self._start_service_after_rtmp_install = False
            self.service_controller.start()
        elif result.get("available") and self.service_controller.owns_process:
            self.service_controller.stop()
            self.service_controller.start()

    def _append_runtime_log(self, content: str):
        self._on_runtime_output(content)
        os.makedirs(os.path.dirname(constants.log_path), exist_ok=True)
        with open(constants.log_path, "a", encoding="utf-8") as file:
            file.write(content)

    def _on_runtime_output(self, content: str):
        self.logs.append_runtime(content)

    def _mark_logs_error(self):
        self._set_navigation_status(
            "logs",
            FluentIcon.CANCEL,
            "#DC2626",
            "desktop.nav_runtime_error",
            dismiss_on_visit=True,
        )

    def _service_status_changed(self, status: str):
        self._service_status = status
        self.dashboard.set_service_status(status)
        if status == "failed":
            self._mark_logs_error()
        self._refresh_tray_menu()

    def _update_rtmp_navigation_status(self, snapshot: dict):
        self._rtmp_snapshot = snapshot if isinstance(snapshot, dict) else {}
        active_count = max(0, int(snapshot.get("active_count") or 0))
        starting_count = max(0, int(snapshot.get("starting_count") or 0))
        if active_count:
            self._set_navigation_status(
                "rtmp",
                FluentIcon.PLAY,
                "#059669",
                "desktop.nav_streaming",
                {"count": active_count},
            )
        elif starting_count:
            self._set_navigation_status(
                "rtmp",
                FluentIcon.SYNC,
                "#2563EB",
                "desktop.nav_stream_starting",
                {"count": starting_count},
            )
        else:
            self._clear_navigation_status("rtmp")
        self._refresh_tray_menu()

    def _update_about_navigation_status(self, state: str, payload):
        payload = payload if isinstance(payload, dict) else {}
        if state == "checking":
            self._set_navigation_status(
                "about", FluentIcon.SYNC, "#2563EB", "desktop.nav_checking_version",
            )
        elif state == "available":
            self._set_navigation_status(
                "about",
                FluentIcon.DOWNLOAD,
                "#D97706",
                "desktop.nav_version_available",
                {"version": payload.get("version", "")},
            )
        elif state == "downloading":
            self._set_navigation_status(
                "about", FluentIcon.DOWNLOAD, "#2563EB", "desktop.nav_downloading_update",
            )
        elif state == "downloaded":
            self._set_navigation_status(
                "about",
                FluentIcon.COMPLETED,
                "#059669",
                "desktop.nav_update_downloaded",
                dismiss_on_visit=True,
            )
        elif state == "failed":
            self._set_navigation_status(
                "about",
                FluentIcon.CANCEL,
                "#DC2626",
                "desktop.nav_version_check_failed",
                dismiss_on_visit=True,
            )
        else:
            self._clear_navigation_status("about")

    def _start_update(self):
        if self.operation_controller.is_busy:
            InfoBar.warning(t("desktop.task_running"), t("desktop.wait_for_task"), parent=self, position=InfoBarPosition.TOP)
            return
        self.controller.start()

    def _update_started(self):
        self._update_progress_value = 0
        self._set_update_navigation_status("running")
        self.operation_controller.suspend()
        self.dashboard.set_running(True)

    def _update_failed(self, message: str):
        self._set_update_navigation_status("failed")
        self._mark_logs_error()
        InfoBar.error(t("desktop.update_failed"), message.splitlines()[-1] if message else t("name.error"), parent=self, position=InfoBarPosition.TOP, duration=8000)

    def _pause_update(self):
        self.controller.pause()
        self._set_update_navigation_status("paused")

    def _resume_update(self):
        self.controller.resume()
        self._set_update_navigation_status("running")

    def _cancel_update(self):
        self.controller.cancel()
        self._set_update_navigation_status("stopping")

    def _set_update_navigation_status(self, state: str):
        self._update_activity_state = state
        icon, color, key, dismiss = {
            "running": (FluentIcon.SYNC, "#2563EB", "desktop.nav_update_running", False),
            "paused": (FluentIcon.PAUSE, "#D97706", "desktop.nav_update_paused", False),
            "stopping": (FluentIcon.CLOSE, "#DC2626", "desktop.nav_update_stopping", False),
            "completed": (FluentIcon.COMPLETED, "#059669", "desktop.nav_update_completed", True),
            "warning": (FluentIcon.INFO, "#D97706", "desktop.nav_update_needs_sources", True),
            "cancelled": (FluentIcon.CANCEL, "#64748B", "desktop.nav_update_cancelled", True),
            "failed": (FluentIcon.CANCEL, "#DC2626", "desktop.nav_update_failed", True),
        }[state]
        self._set_navigation_status("dashboard", icon, color, key, dismiss_on_visit=dismiss)
        self._clear_navigation_status("tasks")
        self._refresh_tray_menu()

    def _operation_started(self, operation: str):
        self._set_navigation_status(
            "channels",
            FluentIcon.SYNC,
            "#7C3AED",
            "desktop.nav_channel_task_running",
            {"operation": t(f"desktop.{operation}", operation)},
        )
        self._clear_navigation_status("tasks")

    def _operation_succeeded(self, operation: str, result):
        args = {"operation": t(f"desktop.{operation}", operation)}
        self._set_navigation_status(
            "channels", FluentIcon.COMPLETED, "#059669",
            "desktop.nav_channel_task_completed", args, dismiss_on_visit=True,
        )
        self.channels.set_task_finished()
        self.dashboard.refresh_metrics()
        if operation == "capture_result_screenshot" and isinstance(result, dict):
            QTimer.singleShot(
                0,
                lambda: self.channels.show_result_screenshot(
                    result.get("result_key", ""),
                    notify=True,
                ),
            )
        elif (
            operation == "capture_result_screenshots"
            and isinstance(result, dict)
            and result.get("failed")
        ):
            InfoBar.warning(
                t("desktop.task_completed"),
                t("desktop.screenshot_batch_result").format(
                    success=result.get("success", 0),
                    failed=result.get("failed", 0),
                ),
                parent=self,
                position=InfoBarPosition.TOP,
            )
        else:
            InfoBar.success(t("desktop.task_completed"), t(f"desktop.{operation}", operation), parent=self, position=InfoBarPosition.TOP)

    def _operation_failed(self, operation: str, message: str):
        operation_label = t(f"desktop.{operation}", operation)
        failure_detail = message.strip() if message else operation
        self._append_runtime_log(
            f"{t('desktop.task_failed')}: {operation_label}\n"
            f"{failure_detail}\n"
        )
        self._mark_logs_error()
        args = {"operation": operation_label}
        self._set_navigation_status(
            "channels", FluentIcon.CANCEL, "#DC2626",
            "desktop.nav_channel_task_failed", args, dismiss_on_visit=True,
        )
        self.channels.set_task_finished()
        if operation == "capture_result_screenshot":
            QTimer.singleShot(
                0,
                lambda: self.channels.set_screenshot_capture_failed(
                    message,
                    notify=True,
                ),
            )
        else:
            InfoBar.error(t("desktop.task_failed"), message.splitlines()[-1] if message else operation, parent=self, position=InfoBarPosition.TOP, duration=8000)

    def _stream_control_finished(self, action: str, success: bool, message: str):
        if success:
            InfoBar.success(t("desktop.stream_action_sent"), t(f"desktop.{action}_stream", action), parent=self, position=InfoBarPosition.TOP)
        else:
            self._mark_logs_error()
            InfoBar.error(t("desktop.stream_action_failed"), message or action, parent=self, position=InfoBarPosition.TOP)

    def _stream_batch_control_finished(self, action: str, success: int, total: int, message: str):
        if success == total:
            InfoBar.success(
                t("desktop.stream_action_sent"),
                t("desktop.batch_stream_action_success").format(count=success),
                parent=self,
                position=InfoBarPosition.TOP,
            )
        else:
            self._mark_logs_error()
            InfoBar.error(
                t("desktop.stream_action_failed"),
                t("desktop.batch_stream_action_partial").format(success=success, total=total, info=message),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=8000,
            )

    def shutdown(self):
        self._save_window_geometry()
        self.rtmp_controller.shutdown()
        self.service_controller.stop()
        self.operation_controller.shutdown()
        self.controller.shutdown()

    def closeEvent(self, event):
        if self._force_quit:
            event.accept()
            return
        if sys.platform == "win32" and self.tray and self.tray.isVisible():
            action = str(QSettings().value("behavior/windows_close_action", "ask"))
            if action == "ask":
                action = self._ask_windows_close_action()
            if action == "cancel":
                event.ignore()
                return
            if action == "quit":
                self._force_quit = True
                event.accept()
                QTimer.singleShot(0, QApplication.quit)
                return
            self.hide()
            event.ignore()
            return
        if self.tray and self.tray.isVisible():
            self.hide()
            event.ignore()
            if sys.platform == "darwin":
                QTimer.singleShot(0, lambda: set_macos_activation_policy(True))
            return
        super().closeEvent(event)

    def _ask_windows_close_action(self):
        dialog = QMessageBox(QMessageBox.Icon.Question, t("desktop.close_window"), t("desktop.close_window_prompt"), parent=self)
        minimize_button = dialog.addButton(t("desktop.minimize_to_tray"), QMessageBox.ButtonRole.AcceptRole)
        quit_button = dialog.addButton(t("desktop.quit_app"), QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(t("desktop.cancel"), QMessageBox.ButtonRole.RejectRole)
        remember = QCheckBox(t("desktop.remember_close_action"), dialog)
        dialog.setCheckBox(remember)
        dialog.exec()
        clicked = dialog.clickedButton()
        action = "tray" if clicked is minimize_button else "quit" if clicked is quit_button else "cancel"
        if remember.isChecked() and action in {"tray", "quit"}:
            QSettings().setValue("behavior/windows_close_action", action)
        return action
