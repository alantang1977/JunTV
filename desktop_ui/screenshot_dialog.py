import datetime
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QStackedLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, FluentIcon, IndeterminateProgressRing, PushButton, StrongBodyLabel, isDarkTheme

import utils.constants as constants
from desktop_ui.playback import play_url
from utils.i18n import t


class StreamScreenshotDialog(QDialog):
    capture_requested = Signal(dict)

    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self.result = result
        self.is_loading = False
        self.setObjectName("streamScreenshotDialog")
        dialog_background = "#202020" if isDarkTheme() else "#FFFFFF"
        self.setStyleSheet(
            "QDialog#streamScreenshotDialog {"
            f"background-color: {dialog_background};"
            "}"
        )
        self.setWindowTitle(t("desktop.stream_screenshot"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(720, 520)
        self.resize(760, 560)

        self.title = StrongBodyLabel(t("desktop.stream_screenshot_preview"), self)
        self.preview = QLabel(self)
        self.preview.setMinimumSize(QSize(640, 360))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName(t("desktop.stream_screenshot_preview"))
        background = "#171717" if isDarkTheme() else "#F1F5F9"
        border = "#3A3A3A" if isDarkTheme() else "#CBD5E1"
        foreground = "#F5F5F5" if isDarkTheme() else "#334155"
        self.preview.setStyleSheet(
            f"QLabel {{ background-color: {background}; border: 1px solid {border}; "
            f"color: {foreground}; border-radius: 8px; padding: 8px; }}"
        )
        self.loading_page = QWidget(self)
        loading_layout = QVBoxLayout(self.loading_page)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_ring = IndeterminateProgressRing(self.loading_page, start=False)
        self.loading_ring.setFixedSize(44, 44)
        self.loading_label = BodyLabel(t("desktop.screenshot_loading"), self.loading_page)
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addStretch(1)
        loading_layout.addWidget(self.loading_ring, 0, Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addWidget(self.loading_label, 0, Qt.AlignmentFlag.AlignHCenter)
        loading_layout.addStretch(1)
        self.preview_stack = QStackedLayout()
        self.preview_stack.addWidget(self.preview)
        self.preview_stack.addWidget(self.loading_page)

        details = QFormLayout()
        details.setHorizontalSpacing(16)
        details.setVerticalSpacing(6)
        self.status_value = BodyLabel(self._status_text(), self)
        self.time_value = BodyLabel(self._captured_at_text(), self)
        self.resolution_value = BodyLabel(self._resolution_text(), self)
        details.addRow(
            BodyLabel(t("desktop.screenshot_status"), self),
            self.status_value,
        )
        details.addRow(
            BodyLabel(t("desktop.screenshot_time"), self),
            self.time_value,
        )
        details.addRow(
            BodyLabel(t("desktop.column_resolution"), self),
            self.resolution_value,
        )

        self.capture_button = PushButton(
            FluentIcon.PHOTO,
            t(
                "desktop.refresh_screenshot"
                if result.get("screenshot_status") == "success"
                else "desktop.capture_screenshot"
            ),
            self,
        )
        self.play_button = PushButton(FluentIcon.PLAY, t("desktop.play"), self)
        self.close_button = PushButton(FluentIcon.CLOSE, t("desktop.close"), self)
        self.capture_button.setToolTip(t("desktop.capture_screenshot_hint"))
        self.capture_button.clicked.connect(self.request_capture)
        self.play_button.clicked.connect(lambda: play_url(self.result.get("url") or "", self))
        self.close_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.capture_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.title)
        layout.addLayout(self.preview_stack, 1)
        layout.addLayout(details)
        layout.addLayout(buttons)
        self._load_preview()

    def _screenshot_path(self) -> str:
        filename = os.path.basename(str(self.result.get("screenshot_filename") or ""))
        return os.path.join(constants.screenshot_dir, filename) if filename else ""

    def has_screenshot(self) -> bool:
        return (
            self.result.get("screenshot_status") == "success"
            and os.path.isfile(self._screenshot_path())
        )

    def _load_preview(self):
        self.preview.clear()
        path = self._screenshot_path()
        if self.result.get("screenshot_status") == "success" and os.path.isfile(path):
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.preview.setPixmap(
                    pixmap.scaled(
                        self.preview.minimumSize() - QSize(18, 18),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        self.preview.setText(self._status_text())

    def set_result(self, result: dict):
        self.result = result
        self.status_value.setText(self._status_text())
        self.time_value.setText(self._captured_at_text())
        self.resolution_value.setText(self._resolution_text())
        self.capture_button.setText(
            t(
                "desktop.refresh_screenshot"
                if result.get("screenshot_status") == "success"
                else "desktop.capture_screenshot"
            )
        )
        self._load_preview()
        self.set_loading(False)

    def set_loading(self, loading: bool):
        self.is_loading = bool(loading)
        self.preview_stack.setCurrentWidget(
            self.loading_page if self.is_loading else self.preview
        )
        self.capture_button.setEnabled(not self.is_loading)
        self.play_button.setEnabled(not self.is_loading)
        if self.is_loading:
            self.status_value.setText(t("desktop.screenshot_loading"))
            self.loading_ring.start()
        else:
            self.loading_ring.stop()

    def set_capture_enabled(self, enabled: bool):
        self.capture_button.setEnabled(bool(enabled) and not self.is_loading)

    def set_error(self, message: str):
        self.set_loading(False)
        self.status_value.setText(message or t("desktop.screenshot_failed"))
        self.preview.clear()
        self.preview.setText(message or t("desktop.screenshot_failed"))
        self.preview_stack.setCurrentWidget(self.preview)

    def _status_text(self) -> str:
        status = self.result.get("screenshot_status") or "not_captured"
        if status == "success" and not os.path.isfile(self._screenshot_path()):
            return t("desktop.screenshot_missing")
        if status in {"failed", "unavailable"} and self.result.get("screenshot_error"):
            error = t(
                f"desktop.screenshot_error_{self.result['screenshot_error']}",
                self.result["screenshot_error"],
            )
            return f"{t(f'desktop.screenshot_{status}', status)} · {error}"
        return t(f"desktop.screenshot_{status}", status)

    def _captured_at_text(self) -> str:
        value = self.result.get("screenshot_captured_at")
        if not value:
            return "--"
        return datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")

    def _resolution_text(self) -> str:
        width = self.result.get("screenshot_width")
        height = self.result.get("screenshot_height")
        return f"{width}x{height}" if width and height else (self.result.get("resolution") or "--")

    def request_capture(self):
        if self.is_loading:
            return
        self.set_loading(True)
        self.capture_requested.emit(dict(self.result))
