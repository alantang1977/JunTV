import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QStackedWidget, QWidget
from qfluentwidgets import FluentIcon, NavigationPushButton

from desktop_ui.pages.about import AboutPage
from desktop_ui.changelog_dialog import ChangelogDialog, extract_release_notes
from desktop_ui.main_window import MainWindow, _update_notification_icon
from desktop_ui.widgets import NavigationStatusIndicator
from utils.i18n import get_language, set_language


class NavigationStatusIndicatorTests(unittest.TestCase):
    UPDATE_SETTING_KEYS = (
        AboutPage.AUTO_CHECK_SETTING,
        AboutPage.READ_UPDATE_SETTING,
        AboutPage.IGNORED_UPDATE_SETTING,
    )

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        settings = QSettings()
        self._update_setting_values = {
            key: (settings.contains(key), settings.value(key))
            for key in self.UPDATE_SETTING_KEYS
        }

    def tearDown(self):
        settings = QSettings()
        for key, (exists, value) in self._update_setting_values.items():
            if exists:
                settings.setValue(key, value)
            else:
                settings.remove(key)
        settings.sync()

    def test_indicator_fits_expanded_and_compact_navigation_items(self):
        item = NavigationPushButton(FluentIcon.HOME, "Overview", True)
        indicator = NavigationStatusIndicator(item)
        indicator.move(24, 3)
        indicator.set_status(FluentIcon.PAUSE, "#D97706")

        item.setCompacted(False)
        self.assertFalse(indicator.isHidden())
        self.assertLess(indicator.geometry().right(), item.width())
        item.setCompacted(True)
        self.assertLess(indicator.geometry().right(), item.width())

    def test_update_notification_icon_is_a_colored_upward_badge(self):
        icon = _update_notification_icon()

        self.assertEqual((icon.width(), icon.height()), (40, 40))
        self.assertNotEqual(icon.toImage().pixelColor(20, 11), icon.toImage().pixelColor(20, 2))

    def test_about_page_emits_available_version_status(self):
        page = AboutPage()
        self.addCleanup(page.deleteLater)
        statuses = []
        page.status_changed.connect(lambda state, payload: statuses.append((state, payload)))

        page._checked({
            "newer": True,
            "latest": "9.9.9",
            "current": "1.0.0",
            "release_url": "https://example.com/release",
            "asset_url": "",
            "asset_name": "",
        })

        self.assertEqual(statuses[-1], ("available", {"version": "9.9.9", "unread": False}))

    def test_available_update_uses_dialog_copy_and_decorated_notice(self):
        page = AboutPage()
        self.addCleanup(page.deleteLater)

        with patch("desktop_ui.pages.about.isDarkTheme", return_value=False):
            page._checked({
                "newer": True,
                "latest": "9.9.9",
                "current": "1.0.0",
                "release_url": "https://example.com/release",
                "asset_url": "https://example.com/update.zip",
                "asset_name": "update.zip",
            })

        self.assertEqual(page.version_status.text(), "发现新版本：9.9.9")
        self.assertFalse(page.update_icon.isHidden())
        self.assertFalse(page.update_badge.isHidden())
        self.assertEqual(page.update_badge.text(), "可下载")
        self.assertFalse(page.download_button.isHidden())
        image = page.update_icon.pixmap().toImage()
        icon_colors = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha()
        }
        self.assertIn("#047857", icon_colors)

    def test_automatic_update_is_unread_once_and_can_be_ignored(self):
        settings = QSettings()
        for key in (
            AboutPage.READ_UPDATE_SETTING,
            AboutPage.IGNORED_UPDATE_SETTING,
        ):
            settings.remove(key)
        page = AboutPage()
        self.addCleanup(page.deleteLater)
        statuses = []
        notifications = []
        page.status_changed.connect(lambda state, payload: statuses.append((state, payload)))
        page.update_notification_requested.connect(notifications.append)

        page._automatic_check = True
        page._checked({
            "newer": True,
            "latest": "9.9.9",
            "current": "1.0.0",
            "release_url": "https://example.com/release",
            "asset_url": "",
            "asset_name": "",
        })

        self.assertEqual(statuses[-1], ("available", {"version": "9.9.9", "unread": True}))
        self.assertEqual(notifications, [{"version": "9.9.9"}])

        page.ignore_available_update()
        self.assertEqual(statuses[-1], ("available", {"version": "9.9.9", "unread": False}))
        self.assertEqual(settings.value(AboutPage.IGNORED_UPDATE_SETTING), "9.9.9")

        notifications.clear()
        page._automatic_check = True
        page._checked({
            "newer": True,
            "latest": "9.9.9",
            "current": "1.0.0",
            "release_url": "https://example.com/release",
            "asset_url": "",
            "asset_name": "",
        })
        self.assertEqual(statuses[-1], ("available", {"version": "9.9.9", "unread": False}))
        self.assertEqual(notifications, [])

    def test_auto_check_switch_controls_the_check_timer(self):
        settings = QSettings()
        settings.remove(AboutPage.AUTO_CHECK_SETTING)
        page = AboutPage()
        self.addCleanup(page.deleteLater)

        self.assertTrue(page.auto_check_timer.isActive())
        page.auto_check_switch.setChecked(False)
        self.assertFalse(page.auto_check_timer.isActive())
        self.assertFalse(settings.value(AboutPage.AUTO_CHECK_SETTING, True, bool))

        page.auto_check_switch.setChecked(True)
        self.assertTrue(page.auto_check_timer.isActive())

    def test_auto_check_is_queued_without_startup_delay(self):
        QSettings().setValue(AboutPage.AUTO_CHECK_SETTING, True)
        with patch("desktop_ui.pages.about.QTimer.singleShot") as single_shot:
            page = AboutPage()
        self.addCleanup(page.deleteLater)

        self.assertEqual(single_shot.call_args.args[0], 0)

    def test_available_update_navigation_status_is_green_and_dismissible(self):
        host = self._status_host(name="about")
        host.stackedWidget.setCurrentWidget(host.other_page)

        MainWindow._update_about_navigation_status(
            host, "available", {"version": "9.9.9", "unread": True}
        )

        status = host._navigation_statuses["about"]
        self.assertEqual(status["color"], "#059669")
        self.assertEqual(status["icon_color"], "#D1FAE5")
        self.assertTrue(status["dismiss_on_visit"])

        MainWindow._update_about_navigation_status(
            host, "available", {"version": "9.9.9", "unread": False}
        )
        self.assertNotIn("about", host._navigation_statuses)

    def test_visiting_about_marks_update_notification_as_read(self):
        host = self._status_host(name="about")
        host.stackedWidget.setCurrentWidget(host.other_page)
        calls = []

        class About:
            def mark_update_read(self):
                calls.append(True)

        host.about = About()
        MainWindow._update_about_navigation_status(
            host, "available", {"version": "9.9.9", "unread": True}
        )
        host.stackedWidget.setCurrentWidget(host._navigation_items["about"][2])
        MainWindow._navigation_page_changed(host, 0)

        self.assertNotIn("about", host._navigation_statuses)
        self.assertEqual(calls, [True])

    def test_visiting_logs_marks_runtime_error_as_read(self):
        host = self._status_host(name="logs")
        host.stackedWidget.setCurrentWidget(host.other_page)

        MainWindow._mark_logs_error(host)
        host.stackedWidget.setCurrentWidget(host._navigation_items["logs"][2])
        MainWindow._navigation_page_changed(host, 0)

        self.assertNotIn("logs", host._navigation_statuses)

    def test_dismissing_update_dialog_keeps_update_unread(self):
        class Stack:
            def currentWidget(self):
                return object()

        class About:
            asset_url = ""
            release_url = "https://example.com/release"

            def __init__(self):
                self.marked_read = False
                self.ignored = False

            def mark_update_read(self):
                self.marked_read = True

            def ignore_available_update(self):
                self.ignored = True

        class Host(QWidget):
            _show_update_notification = MainWindow._show_update_notification

            def __init__(self):
                super().__init__()
                self.statuses = []

            def _update_about_navigation_status(self, state, payload):
                self.statuses.append((state, payload))

        host = Host()
        self.addCleanup(host.deleteLater)
        host.stackedWidget = Stack()
        host.about = About()
        action_sizes = []

        def dismiss(dialog):
            dialog.show()
            self.app.processEvents()
            action_sizes.extend(
                (button.text(), button.width(), button.height())
                for button in dialog.findChildren(QPushButton)
            )
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", dismiss):
            host._show_update_notification({"version": "9.9.9"})

        self.assertFalse(host.about.marked_read)
        self.assertFalse(host.about.ignored)
        self.assertEqual(host.statuses, [("available", {"version": "9.9.9", "unread": True})])
        self.assertEqual(action_sizes, [("忽略此版本", 112, 24), ("立即升级", 112, 24)])

    def test_about_page_displays_hotfix_revision(self):
        page = AboutPage()
        self.addCleanup(page.deleteLater)
        statuses = []
        page.status_changed.connect(lambda state, payload: statuses.append((state, payload)))

        page._checked({
            "newer": True,
            "latest": "3.0.0",
            "latest_revision": 20260810123045,
            "current": "3.0.0",
            "release_url": "https://example.com/release",
            "asset_url": "https://example.com/hotfix.zip",
            "asset_name": "hotfix.zip",
        })

        self.assertEqual(
            statuses[-1],
            ("available", {"version": "3.0.0 (r20260810123045)", "unread": False}),
        )
        self.assertIn("r20260810123045", page.version_status.text())
        page.retranslate()
        self.assertIn("r20260810123045", page.version_status.text())

    def test_about_page_silently_ignores_automatic_check_failure(self):
        page = AboutPage()
        statuses = []
        page.status_changed.connect(lambda state, payload: statuses.append((state, payload)))
        initial_state = page._update_state
        page._automatic_check = True

        page._check_failed("offline")

        self.assertEqual(page._update_state, initial_state)
        self.assertEqual(statuses, [])
        self.assertTrue(page.auto_check_timer.isActive())
        self.assertEqual(page.auto_check_timer.interval(), page.AUTO_CHECK_INTERVAL_MS)

    def test_about_page_exposes_changelog_action(self):
        page = AboutPage()
        self.addCleanup(page.deleteLater)

        self.assertEqual(page.changelog_button.text(), "查看更新日志")
        dialog = ChangelogDialog(str(page.info.get("version")), page)
        self.addCleanup(dialog.deleteLater)
        self.assertIn("v3.0.0", dialog.viewer.toPlainText())

    def test_update_install_dialog_tracks_app_language_and_theme(self):
        language = get_language()
        self.addCleanup(set_language, language)
        page = AboutPage()
        self.addCleanup(page.deleteLater)

        set_language("en")
        dialog = page._install_confirmation_dialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.button(QMessageBox.StandardButton.Yes).text(), "Yes")
        self.assertEqual(dialog.button(QMessageBox.StandardButton.No).text(), "No")
        self.assertIn("The app will quit", dialog.text())

        set_language("zh_CN")
        with patch("desktop_ui.widgets.isDarkTheme", return_value=True):
            dialog = page._install_confirmation_dialog()
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.button(QMessageBox.StandardButton.Yes).text(), "是")
        self.assertEqual(dialog.button(QMessageBox.StandardButton.No).text(), "否")
        self.assertIn("#202020", dialog.styleSheet())

    def test_changelog_extracts_only_the_current_release_and_language(self):
        markdown = """## v2.0.8

中文内容

<details>
<summary>English</summary>

English content
</details>

## v2.0.1

旧版本内容
"""
        self.assertIn("中文内容", extract_release_notes(markdown, "2.0.8", "zh_CN"))
        self.assertNotIn("English content", extract_release_notes(markdown, "2.0.8", "zh_CN"))
        self.assertIn("English content", extract_release_notes(markdown, "2.0.8", "en"))
        self.assertNotIn("旧版本内容", extract_release_notes(markdown, "2.0.8", "en"))

    def test_changelog_dialog_themes_content_and_translates_close_button(self):
        language = get_language()
        self.addCleanup(set_language, language)
        set_language("en")
        dialog = ChangelogDialog("2.0.8")
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.close_button.text(), "Close")
        self.assertIn("QTextBrowser", dialog.styleSheet())

        set_language("zh_CN")
        dialog.retranslate()
        self.assertEqual(dialog.close_button.text(), "关闭")

    def test_active_status_is_hidden_on_its_page_and_shown_elsewhere(self):
        host = self._status_host()
        MainWindow._set_navigation_status(
            host, "dashboard", FluentIcon.SYNC, "#2563EB", "desktop.nav_update_running",
        )

        self.assertIn("dashboard", host._navigation_statuses)
        self.assertTrue(host._navigation_indicators["dashboard"].isHidden())

        host.stackedWidget.setCurrentWidget(host.other_page)
        MainWindow._navigation_page_changed(host, 1)
        self.assertFalse(host._navigation_indicators["dashboard"].isHidden())

    def test_terminal_status_is_not_shown_when_page_is_already_open(self):
        host = self._status_host()
        MainWindow._set_navigation_status(
            host,
            "dashboard",
            FluentIcon.COMPLETED,
            "#059669",
            "desktop.nav_update_completed",
            dismiss_on_visit=True,
        )

        self.assertNotIn("dashboard", host._navigation_statuses)
        self.assertTrue(host._navigation_indicators["dashboard"].isHidden())

    @staticmethod
    def _status_host(name="dashboard"):
        dashboard_page = QWidget()
        other_page = QWidget()
        stack = QStackedWidget()
        stack.addWidget(dashboard_page)
        stack.addWidget(other_page)
        stack.setCurrentWidget(dashboard_page)
        item = NavigationPushButton(FluentIcon.HOME, "Overview", True)
        indicator = NavigationStatusIndicator(item)

        class Host:
            _set_navigation_status = MainWindow._set_navigation_status
            _clear_navigation_status = MainWindow._clear_navigation_status
            _refresh_navigation_status = MainWindow._refresh_navigation_status
            _refresh_navigation_statuses = MainWindow._refresh_navigation_statuses

        host = Host()
        host.stackedWidget = stack
        host.other_page = other_page
        host._navigation_items = {
            name: (item, "desktop.dashboard", dashboard_page),
        }
        host._navigation_statuses = {}
        host._navigation_indicators = {name: indicator}
        return host
