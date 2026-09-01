import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import utils.constants as constants
from desktop_ui.pages.logs import LogsPage
from desktop_ui.pages.sources import SourcesPage
from utils.i18n import t


class LogsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.runtime_log = os.path.join(self.temp_dir.name, "runtime.log")
        with patch.object(constants, "log_path", self.runtime_log):
            self.page = LogsPage()
        self.page.timer.stop()
        self.page.resize(640, 240)
        self.page.show()
        self.app.processEvents()
        self.addCleanup(self.page.deleteLater)

    def _write_runtime_log(self, lines):
        with open(self.runtime_log, "w", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")

    def test_refresh_preserves_manual_position_when_auto_scroll_is_disabled(self):
        self._write_runtime_log([f"line {index}" for index in range(100)])
        self.page.refresh()
        scrollbar = self.page.viewer.verticalScrollBar()
        scrollbar.setValue(20)
        self.page.autoscroll.setChecked(False)

        self._write_runtime_log([f"line {index}" for index in range(101)])
        self.page.refresh()

        self.assertEqual(scrollbar.value(), 20)

    def test_initial_load_scrolls_to_latest_log_and_keeps_setting_label(self):
        self._write_runtime_log([f"line {index}" for index in range(100)])
        with patch.object(constants, "log_path", self.runtime_log):
            page = LogsPage()
        page.timer.stop()
        page.resize(640, 240)
        page.show()
        self.app.processEvents()
        self.addCleanup(page.deleteLater)

        scrollbar = page.viewer.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        self.assertEqual(page.autoscroll.getText(), t("desktop.auto_scroll"))
        self.assertEqual(page.autoscroll.getOnText(), t("desktop.auto_scroll"))
        self.assertEqual(page.autoscroll.getOffText(), t("desktop.auto_scroll"))

    def test_log_display_options_toggle_wrapping_and_timestamps(self):
        timestamped_lines = [
            "2026-08-06T12:00:00+08:00 INFO    first line",
            "plain line",
        ]
        self._write_runtime_log(timestamped_lines)
        self.page.refresh()

        self.assertIn("2026-08-06T12:00:00+08:00", self.page.viewer.toPlainText())
        self.assertEqual(
            self.page.viewer.lineWrapMode(),
            self.page.viewer.LineWrapMode.NoWrap,
        )

        self.page.show_timestamps.setChecked(False)
        self.assertNotIn("2026-08-06T12:00:00+08:00", self.page.viewer.toPlainText())
        self.assertIn("INFO    first line", self.page.viewer.toPlainText())

        self.page.wrap_lines.setChecked(True)
        self.assertEqual(
            self.page.viewer.lineWrapMode(),
            self.page.viewer.LineWrapMode.WidgetWidth,
        )

    def test_toolbar_groups_display_and_secondary_actions(self):
        self.assertEqual(self.page.display_menu.actions(), [
            self.page.autoscroll_action,
            self.page.wrap_lines_action,
            self.page.show_timestamps_action,
        ])
        self.assertEqual(
            self.page.more_menu.actions(),
            [self.page.clear_action, self.page.delete_runtime_action],
        )
        self.assertTrue(self.page.delete_runtime_action.isVisible())
        self.assertFalse(self.page.autoscroll.isVisible())
        self.assertFalse(self.page.wrap_lines.isVisible())
        self.assertFalse(self.page.show_timestamps.isVisible())

    def test_delete_runtime_action_remains_available_for_other_log_tabs(self):
        self.page.selector.setCurrentIndex(1)
        self.assertTrue(self.page.delete_runtime_action.isVisible())
        self.assertFalse(self.page.delete_runtime_action.property("item").isHidden())
        self.page.selector.setCurrentIndex(0)
        self.assertTrue(self.page.delete_runtime_action.isVisible())
        self.assertFalse(self.page.delete_runtime_action.property("item").isHidden())

    def test_delete_runtime_log_truncates_only_runtime_log_after_confirmation(self):
        self._write_runtime_log(["runtime entry"])
        other_log = os.path.join(self.temp_dir.name, "result.log")
        with open(other_log, "w", encoding="utf-8") as file:
            file.write("result entry")

        with patch("desktop_ui.pages.logs.warning_message_box") as message_box_factory:
            message_box_factory.return_value.exec.return_value = True
            self.page.delete_runtime_log()

        message_box_factory.return_value.yesButton.setText.assert_called_once_with(
            t("desktop.confirm")
        )
        message_box_factory.return_value.cancelButton.setText.assert_called_once_with(
            t("desktop.cancel")
        )

        self.assertEqual(os.path.getsize(self.runtime_log), 0)
        with open(other_log, encoding="utf-8") as file:
            self.assertEqual(file.read(), "result entry")
        self.assertEqual(self.page.viewer.toPlainText(), "")

    def test_auto_scroll_does_not_interrupt_manual_browsing(self):
        self._write_runtime_log([f"line {index}" for index in range(100)])
        self.page.refresh()
        scrollbar = self.page.viewer.verticalScrollBar()
        scrollbar.setValue(20)

        self._write_runtime_log([f"line {index}" for index in range(101)])
        self.page.refresh()

        self.assertEqual(scrollbar.value(), 20)

    def test_source_actions_share_the_tab_row(self):
        with patch.object(SourcesPage, "load"):
            page = SourcesPage()
        self.addCleanup(page.deleteLater)
        page.resize(1000, 720)
        page.show()
        self.app.processEvents()

        actions = page.tabs.cornerWidget()
        self.assertIsNotNone(actions)
        self.assertIs(actions.parent(), page.tabs)
        self.assertEqual(actions.layout().count(), 3)
        self.assertIs(actions.layout().itemAt(0).widget(), page.reload_button)
        self.assertIs(actions.layout().itemAt(1).widget(), page.export_button)
        self.assertIs(actions.layout().itemAt(2).widget(), page.save_button)
        self.assertLessEqual(actions.y(), page.tabs.tabBar().height())
        self.assertGreaterEqual(actions.x(), page.tabs.tabBar().width())
