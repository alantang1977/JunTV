import os
import re

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import Action, ComboBox, DropDownPushButton, FluentIcon, InfoBar, InfoBarPosition, PushButton, RoundMenu, SwitchButton

import utils.constants as constants
from desktop_ui.widgets import AccentPushButton, AppLineEdit, AppPlainTextEdit, warning_message_box
from utils.diagnostics import export_logs
from utils.i18n import t
from utils.run_state import read_run_state


_TIMESTAMP_PREFIX = re.compile(
    r"^\s*\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+"
)


class LogsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logsPage")
        self.path_specs = [
            ("desktop.runtime_log", constants.log_path),
            ("desktop.result_log", constants.result_log_path),
            ("desktop.speed_log", constants.speed_test_log_path),
            ("desktop.statistics_log", constants.statistic_log_path),
            ("desktop.unmatched_log", constants.unmatch_log_path),
        ]
        self.paths = [(t(key), path) for key, path in self.path_specs]
        self.selector = ComboBox(self)
        self.selector.addItems([item[0] for item in self.paths])
        self.search = AppLineEdit(self)
        self.search.setPlaceholderText(t("desktop.search_logs"))
        auto_scroll_text = t("desktop.auto_scroll")
        self.autoscroll = SwitchButton(auto_scroll_text, self)
        # SwitchButton uses separate labels for the checked and unchecked
        # states. Keep the setting name visible instead of showing its
        # default checked-state label ("On").
        self.autoscroll.setOnText(auto_scroll_text)
        self.autoscroll.setOffText(auto_scroll_text)
        self.autoscroll.setChecked(True)
        self.wrap_lines = SwitchButton(t("desktop.wrap_lines"), self)
        self._configure_switch_text(self.wrap_lines, t("desktop.wrap_lines"))
        self.show_timestamps = SwitchButton(t("desktop.show_timestamps"), self)
        self._configure_switch_text(self.show_timestamps, t("desktop.show_timestamps"))
        self.show_timestamps.setChecked(True)
        self.refresh_button = PushButton(FluentIcon.SYNC, t("desktop.refresh"), self)
        self.display_button = DropDownPushButton(FluentIcon.SETTING, t("desktop.display_settings"), self)
        self.more_button = DropDownPushButton(FluentIcon.MORE, t("desktop.more_actions"), self)
        self.export_button = AccentPushButton(FluentIcon.ZIP_FOLDER, t("desktop.export_logs"), self)
        self._setup_menus()
        self.autoscroll.setVisible(False)
        self.wrap_lines.setVisible(False)
        self.show_timestamps.setVisible(False)
        self.viewer = AppPlainTextEdit(self)
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(AppPlainTextEdit.LineWrapMode.NoWrap)
        self._last_viewed_path = None
        self.cleared_offsets = {}
        actions = QHBoxLayout()
        actions.addWidget(self.selector)
        actions.addWidget(self.search, 1)
        actions.addWidget(self.display_button)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.more_button)
        actions.addWidget(self.export_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(actions)
        layout.addWidget(self.viewer, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.selector.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.wrap_lines.checkedChanged.connect(self._set_line_wrap)
        self.show_timestamps.checkedChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self.export)
        self.refresh()

    def _setup_menus(self):
        self.display_menu = RoundMenu(parent=self)
        self.autoscroll_action = self._add_toggle_action(
            self.display_menu, t("desktop.auto_scroll"), self.autoscroll
        )
        self.wrap_lines_action = self._add_toggle_action(
            self.display_menu, t("desktop.wrap_lines"), self.wrap_lines
        )
        self.show_timestamps_action = self._add_toggle_action(
            self.display_menu, t("desktop.show_timestamps"), self.show_timestamps
        )
        self.display_button.setMenu(self.display_menu)

        self.more_menu = RoundMenu(parent=self)
        self.clear_action = Action(FluentIcon.BROOM, t("desktop.clear_view"), self, triggered=self.clear_view)
        self.more_menu.addAction(self.clear_action)
        self.delete_runtime_action = Action(
            FluentIcon.DELETE,
            t("desktop.delete_runtime_log"),
            self,
            triggered=self.delete_runtime_log,
        )
        self.more_menu.addAction(self.delete_runtime_action)
        self.more_button.setMenu(self.more_menu)
        self._update_more_actions()

    @staticmethod
    def _add_toggle_action(menu, text, switch):
        action = Action(text, menu)
        action.setCheckable(True)
        action.setChecked(switch.isChecked())
        action.triggered.connect(switch.setChecked)
        switch.checkedChanged.connect(action.setChecked)
        menu.addAction(action)
        return action

    def refresh(self, *_):
        self._update_more_actions()
        path = self.paths[max(0, self.selector.currentIndex())][1]
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            content = "" if path in self.cleared_offsets else self._empty_log_message(path)
        else:
            size = os.path.getsize(path)
            offset = self.cleared_offsets.get(path, max(0, size - 1024 * 1024))
            if offset > size:
                offset = 0
                self.cleared_offsets[path] = 0
            with open(path, "rb") as file:
                file.seek(offset)
                content = file.read().decode("utf-8", errors="replace")
        # QPlainTextEdit does not need a trailing empty line, and normalizing it
        # keeps file-backed refreshes consistent with append_runtime().
        content = content.rstrip("\r\n")
        if not self.show_timestamps.isChecked():
            content = "\n".join(_TIMESTAMP_PREFIX.sub("", line) for line in content.splitlines())
        term = self.search.text().strip().lower()
        if term:
            content = "\n".join(line for line in content.splitlines() if term in line.lower())
        is_new_log = path != self._last_viewed_path
        self._set_viewer_content(content, force_scroll=is_new_log)
        self._last_viewed_path = path

    def _empty_log_message(self, path):
        status = read_run_state().get("status", "never_run")
        if path == self.paths[0][1]:
            key = {
                "never_run": "desktop.runtime_log_empty_never",
                "running": "desktop.runtime_log_empty_running",
                "completed_empty": "desktop.runtime_log_empty_after_run",
                "failed": "desktop.runtime_log_empty_failed",
                "cancelled": "desktop.runtime_log_empty_cancelled",
            }.get(status, "desktop.runtime_log_empty")
        else:
            key = "desktop.other_log_empty_running" if status == "running" else "desktop.other_log_empty"
        return t(key)

    @staticmethod
    def _configure_switch_text(switch, text):
        # SwitchButton has independent labels for checked and unchecked states.
        switch.setOnText(text)
        switch.setOffText(text)

    def _set_line_wrap(self, enabled):
        scrollbar = self.viewer.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum()
        mode = (
            AppPlainTextEdit.LineWrapMode.WidgetWidth
            if enabled
            else AppPlainTextEdit.LineWrapMode.NoWrap
        )
        self.viewer.setLineWrapMode(mode)
        if self.autoscroll.isChecked() and was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _set_viewer_content(self, content: str, force_scroll=False):
        if self.viewer.toPlainText() == content:
            if force_scroll and self.autoscroll.isChecked():
                self.viewer.verticalScrollBar().setValue(self.viewer.verticalScrollBar().maximum())
            return
        scrollbar = self.viewer.verticalScrollBar()
        value = scrollbar.value()
        was_at_bottom = value >= scrollbar.maximum()
        self.viewer.setPlainText(content)
        if self.autoscroll.isChecked() and (force_scroll or was_at_bottom):
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(value, scrollbar.maximum()))

    def clear_view(self):
        path = self.paths[max(0, self.selector.currentIndex())][1]
        self.cleared_offsets[path] = os.path.getsize(path) if os.path.exists(path) else 0
        self.viewer.clear()

    def _update_more_actions(self):
        self.delete_runtime_action.setVisible(True)
        item = self.delete_runtime_action.property("item")
        if item is not None:
            item.setHidden(False)

    def delete_runtime_log(self):
        runtime_path = self.paths[0][1]
        box = warning_message_box(
            t("desktop.delete_runtime_log"),
            t("desktop.delete_runtime_log_confirm"),
            self,
        )
        box.yesButton.setText(t("desktop.confirm"))
        box.cancelButton.setText(t("desktop.cancel"))
        if not box.exec():
            return
        try:
            os.makedirs(os.path.dirname(runtime_path), exist_ok=True)
            with open(runtime_path, "w", encoding="utf-8"):
                pass
            self.cleared_offsets[runtime_path] = 0
            self.viewer.clear()
            self.refresh()
            InfoBar.success(
                t("desktop.runtime_log_deleted"),
                t("desktop.runtime_log_deleted_detail"),
                parent=self,
                position=InfoBarPosition.TOP,
            )
        except OSError as exc:
            InfoBar.error(
                t("desktop.runtime_log_delete_failed"),
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def export(self):
        try:
            path = export_logs()
            InfoBar.success(t("desktop.logs_exported"), path, parent=self, position=InfoBarPosition.TOP)
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        except Exception as exc:
            InfoBar.error(t("desktop.logs_export_failed"), str(exc), parent=self, position=InfoBarPosition.TOP)

    def append_runtime(self, content: str):
        if self.selector.currentIndex() != 0 or self.search.text().strip():
            return
        text = content.rstrip("\r\n")
        if not text:
            return
        if not self.show_timestamps.isChecked():
            text = "\n".join(_TIMESTAMP_PREFIX.sub("", line) for line in text.splitlines())
        scrollbar = self.viewer.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum()
        self.viewer.appendPlainText(text)
        if self.autoscroll.isChecked() and was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def retranslate(self):
        index = self.selector.currentIndex()
        self.paths = [(t(key), path) for key, path in self.path_specs]
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([item[0] for item in self.paths])
        self.selector.setCurrentIndex(index)
        self.selector.blockSignals(False)
        self.search.setPlaceholderText(t("desktop.search_logs"))
        auto_scroll_text = t("desktop.auto_scroll")
        self.autoscroll.setOnText(auto_scroll_text)
        self.autoscroll.setOffText(auto_scroll_text)
        wrap_lines_text = t("desktop.wrap_lines")
        self._configure_switch_text(self.wrap_lines, wrap_lines_text)
        show_timestamps_text = t("desktop.show_timestamps")
        self._configure_switch_text(self.show_timestamps, show_timestamps_text)
        self.display_button.setText(t("desktop.display_settings"))
        self.more_button.setText(t("desktop.more_actions"))
        self.autoscroll_action.setText(auto_scroll_text)
        self.wrap_lines_action.setText(wrap_lines_text)
        self.show_timestamps_action.setText(show_timestamps_text)
        self.clear_action.setText(t("desktop.clear_view"))
        self.delete_runtime_action.setText(t("desktop.delete_runtime_log"))
        self.refresh_button.setText(t("desktop.refresh"))
        self.export_button.setText(t("desktop.export_logs"))
        self._update_more_actions()
        self.refresh()
