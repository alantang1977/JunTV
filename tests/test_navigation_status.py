import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QStackedWidget, QWidget
from qfluentwidgets import FluentIcon, NavigationPushButton

from desktop_ui.pages.about import AboutPage
from desktop_ui.changelog_dialog import ChangelogDialog, extract_release_notes
from desktop_ui.main_window import MainWindow
from desktop_ui.widgets import NavigationStatusIndicator
from utils.i18n import get_language, set_language


class NavigationStatusIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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

    def test_about_page_emits_available_version_status(self):
        page = AboutPage()
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

        self.assertEqual(statuses[-1], ("available", {"version": "9.9.9"}))

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
    def _status_host():
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
            "dashboard": (item, "desktop.dashboard", dashboard_page),
        }
        host._navigation_statuses = {}
        host._navigation_indicators = {"dashboard": indicator}
        return host
