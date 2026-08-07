from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, PushButton, TableView

from desktop_ui.delegates import ConfigValueDelegate, ElidedDescriptionDelegate
from desktop_ui.models import ConfigTableModel
from desktop_ui.playback import PlaybackPreferencesDialog
from desktop_ui.widgets import AccentPushButton, AppSearchLineEdit, apply_dialog_theme, configure_table_columns, localize_dialog_buttons
from utils.config import ConfigValidationError
from utils.i18n import t


class SettingsPage(QWidget):
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.model = ConfigTableModel(self)
        self.search = AppSearchLineEdit(self)
        self.search.setPlaceholderText(t("desktop.search_settings"))
        self.save_button = AccentPushButton(FluentIcon.SAVE, t("desktop.save_settings"), self)
        self.reload_button = PushButton(FluentIcon.SYNC, t("desktop.reload"), self)
        self.playback_button = PushButton(FluentIcon.PLAY, t("desktop.playback_preferences"), self)
        self.table = TableView(self)
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(1, ConfigValueDelegate(self.table))
        self.table.setItemDelegateForColumn(2, ElidedDescriptionDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        configure_table_columns(self.table, [190, 420, 520], "settings.config")
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.clicked.connect(self._show_description)
        actions = QHBoxLayout()
        actions.addWidget(self.search, 1)
        actions.addWidget(self.reload_button)
        actions.addWidget(self.playback_button)
        actions.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)
        self.search.textChanged.connect(self.model.filter)
        self.reload_button.clicked.connect(self._reload_settings)
        self.playback_button.clicked.connect(self._open_playback_preferences)
        self.save_button.clicked.connect(self.save)
        self.model.modelReset.connect(lambda: QTimer.singleShot(0, self._open_editors))
        QTimer.singleShot(0, self._open_editors)

    def retranslate(self):
        self.search.setPlaceholderText(t("desktop.search_settings"))
        self.save_button.setText(t("desktop.save_settings"))
        self.reload_button.setText(t("desktop.reload"))
        self.playback_button.setText(t("desktop.playback_preferences"))
        self.model.reload()

    def _open_editors(self):
        for row in range(self.model.rowCount()):
            index = self.model.index(row, 1)
            if index.flags() & Qt.ItemFlag.ItemIsEditable:
                self.table.openPersistentEditor(index)
        QTimer.singleShot(0, self._clear_editor_selection)

    def _reload_settings(self):
        search_text = self.search.text()
        self.model.reload()
        if search_text.strip():
            self.model.filter(search_text)

    def _open_playback_preferences(self):
        PlaybackPreferencesDialog(self).exec()

    def _clear_editor_selection(self):
        for editor in self.table.findChildren(QLineEdit):
            editor.setCursorPosition(0)
            editor.deselect()
            editor.clearFocus()
        self.table.clearSelection()
        self.search.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_description(self, index):
        if not index.isValid() or index.column() != 2:
            return
        description = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        if not description:
            return
        key = str(self.model.index(index.row(), 0).data(Qt.ItemDataRole.DisplayRole) or "")
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(key)
        dialog.setMinimumWidth(520)
        dialog.resize(680, 220)
        layout = QVBoxLayout(dialog)
        label = QLabel(description, dialog)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        localize_dialog_buttons(buttons)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(label)
        layout.addWidget(buttons)
        dialog.exec()

    def save(self):
        try:
            self.model.save()
        except ConfigValidationError as exc:
            InfoBar.error(
                t("name.error"),
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
                duration=10000,
            )
            return
        self.settings_saved.emit()
        InfoBar.success(t("desktop.saved"), t("desktop.restart_hint"), parent=self, position=InfoBarPosition.TOP)

    def focus_setting(self, key: str):
        self.search.setText(key)
        QTimer.singleShot(50, lambda: self._focus_setting_editor(key))

    def _focus_setting_editor(self, key: str):
        row = next((index for index, item in enumerate(self.model.rows) if item.get("key") == key), -1)
        if row < 0:
            return
        index = self.model.index(row, 1)
        self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        editor = self.table.indexWidget(index)
        if editor:
            editor.setFocus(Qt.FocusReason.OtherFocusReason)
