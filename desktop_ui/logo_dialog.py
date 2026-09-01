from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, FluentIcon, ToolButton

from desktop_ui.widgets import AppLineEdit, apply_dialog_theme, localize_dialog_buttons
from utils.i18n import t


def is_channel_logo_click(table, index) -> bool:
    if not index.isValid():
        return False
    position = table.viewport().mapFromGlobal(QCursor.pos())
    rect = table.visualRect(index)
    return rect.contains(position) and rect.x() + 6 <= position.x() <= rect.x() + table.iconSize().width() + 16


class ChannelLogoDialog(QDialog):
    def __init__(self, channel: dict, logo_loader, parent=None):
        super().__init__(parent)
        self._logo_loader = logo_loader
        apply_dialog_theme(self)
        self.setWindowTitle(t("desktop.edit_channel_logo"))
        self.setMinimumWidth(440)

        self.preview = QLabel(self)
        self.preview.setFixedSize(400, 220)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "QLabel { color: #F8FAFC; background-color: #64748B; border: 1px solid #475569; border-radius: 8px; }"
        )
        self.preview.setText(t("desktop.logo_preview"))

        self.logo = AppLineEdit(self)
        self.logo.setText(str(channel.get("logo") or ""))
        self.logo.setPlaceholderText(t("desktop.logo_path_or_url"))
        browse = ToolButton(FluentIcon.FOLDER, self)
        browse.setToolTip(t("desktop.choose_logo_file"))
        logo_row = QWidget(self)
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.addWidget(self.logo, 1)
        logo_layout.addWidget(browse)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        localize_dialog_buttons(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(BodyLabel(channel.get("name") or "", self))
        layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(BodyLabel(t("desktop.channel_logo"), self))
        layout.addWidget(logo_row)
        layout.addWidget(buttons)

        browse.clicked.connect(self._choose_file)
        self.logo.editingFinished.connect(self._update_preview)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._logo_loader.icon_ready.connect(self._icon_ready)
        self._update_preview()

    def logo_value(self) -> str:
        return self.logo.text().strip()

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            t("desktop.choose_logo_file"),
            "",
            t("desktop.logo_image_filter"),
        )
        if path:
            self.logo.setText(path)
            self._update_preview()

    def _update_preview(self):
        value = self.logo_value()
        icon = self._logo_loader.source_icon(value) if value else self._logo_loader.fallback_source_icon
        if icon is None:
            self.preview.setPixmap(self._logo_loader.fallback_source_icon.pixmap(QSize(120, 120)))
            return
        pixmap = icon.pixmap(self.preview.size() - QSize(28, 28))
        self.preview.setPixmap(pixmap)

    def _icon_ready(self, logo: str):
        if logo == self.logo_value():
            self._update_preview()
