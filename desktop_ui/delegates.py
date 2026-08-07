from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QStyledItemDelegate, QToolTip, QWidget
from qfluentwidgets import ComboBox, DoubleSpinBox, FluentIcon, SpinBox, SwitchButton, TableItemDelegate, TimeEdit, ToolButton

from desktop_ui.models import ConfigTableModel
from desktop_ui.widgets import AppEditableComboBox, AppLineEdit, apply_input_border_style
from utils.i18n import t


class _WheelGuard:
    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class SettingsLineEdit(AppLineEdit):
    pass


class SettingsEditableComboBox(AppEditableComboBox):
    pass


class SettingsPathEditor(QWidget):
    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.line_edit = SettingsLineEdit(self)
        self.browse_button = ToolButton(FluentIcon.FOLDER, self)
        self.browse_button.setToolTip(t("desktop.select_file"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(4)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.browse_button)
        self.browse_button.clicked.connect(self._browse)

    def _browse(self):
        current = self.line_edit.text().strip()
        if self.key == "source_file":
            value, _ = QFileDialog.getOpenFileName(self, t("desktop.select_source_file"), current)
        else:
            value, _ = QFileDialog.getSaveFileName(self, t("desktop.select_output_file"), current)
        if value:
            self.line_edit.setText(value)

    def text(self):
        return self.line_edit.text()

    def setText(self, value):
        self.line_edit.setText(str(value or ""))

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason):
        self.line_edit.setFocus(reason)


class SettingsSpinBox(_WheelGuard, SpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "SpinBox")


class SettingsDoubleSpinBox(_WheelGuard, DoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "DoubleSpinBox")


class SettingsTimeEdit(_WheelGuard, TimeEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "TimeEdit")


class TimeListEditor(QWidget):
    valueChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._times = []
        self.display = SettingsLineEdit(self)
        self.display.setPlaceholderText("08:00, 18:30")
        self.picker = SettingsTimeEdit(self)
        self.picker.setDisplayFormat("HH:mm")
        self.add_button = ToolButton(FluentIcon.ADD, self)
        self.clear_button = ToolButton(FluentIcon.DELETE, self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.display, 1)
        layout.addWidget(self.picker)
        layout.addWidget(self.add_button)
        layout.addWidget(self.clear_button)
        self.add_button.clicked.connect(self._add_time)
        self.clear_button.clicked.connect(self._clear)
        self.display.editingFinished.connect(self._commit_text)

    def setValue(self, value):
        self._times = sorted({part.strip() for part in str(value or "").split(",") if part.strip()})
        self._sync()

    def value(self):
        return ",".join(self._times)

    def _add_time(self):
        value = self.picker.time().toString("HH:mm")
        if value not in self._times:
            self._times.append(value)
            self._times.sort()
            self._sync(True)

    def _clear(self):
        if self._times:
            self._times = []
            self._sync(True)

    def _commit_text(self):
        values = []
        for part in self.display.text().split(","):
            pieces = part.strip().split(":", 1)
            if len(pieces) != 2:
                continue
            try:
                hour, minute = (int(value) for value in pieces)
            except ValueError:
                continue
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                values.append(f"{hour:02d}:{minute:02d}")
        self._times = sorted(set(values))
        self._sync(True)

    def _sync(self, emit=False):
        self.display.setText(", ".join(self._times))
        if emit:
            self.valueChanged.emit(self.value())


class EditorFocusFilter(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._owners = {}
        self._active_editors = set()

    def register(self, editor):
        widgets = [editor]
        if hasattr(editor, "lineEdit"):
            widgets.append(editor.lineEdit())
        for widget in widgets:
            self._owners[widget] = editor
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        editor = self._owners.get(watched)
        if not editor:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.FocusIn:
            keyboard_reasons = {
                Qt.FocusReason.TabFocusReason,
                Qt.FocusReason.BacktabFocusReason,
                Qt.FocusReason.ShortcutFocusReason,
            }
            restore_reasons = {
                Qt.FocusReason.ActiveWindowFocusReason,
                Qt.FocusReason.PopupFocusReason,
            }
            mouse_pressed = QApplication.mouseButtons() != Qt.MouseButton.NoButton
            restoring = editor in self._active_editors and event.reason() in restore_reasons
            if mouse_pressed or event.reason() in keyboard_reasons or restoring:
                self._active_editors.add(editor)
            else:
                watched.clearFocus()
                editor.clearFocus()
                return True
        elif event.type() == QEvent.Type.FocusOut and event.reason() not in {
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
        }:
            self._active_editors.discard(editor)
        return super().eventFilter(watched, event)


class ConfigValueDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._focus_filter = EditorFocusFilter(self)

    def _prepare_editor(self, editor):
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if hasattr(editor, "lineEdit"):
            editor.lineEdit().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._focus_filter.register(editor)
        return editor

    def createEditor(self, parent, option, index):
        kind = index.data(ConfigTableModel.KindRole)
        if kind == "bool":
            editor = SwitchButton(parent)
            editor.setOnText("")
            editor.setOffText("")
            editor.setText("")
            editor.checkedChanged.connect(lambda checked: index.model().setData(index, checked))
            return self._prepare_editor(editor)
        if kind == "options":
            editor = ComboBox(parent)
            editor.addItems(index.data(ConfigTableModel.OptionsRole) or [])
            editor.currentTextChanged.connect(lambda value: index.model().setData(index, value))
            return self._prepare_editor(editor)
        if kind == "path":
            key = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            editor = SettingsPathEditor(index.data(Qt.ItemDataRole.UserRole + 2) or key, parent)
            # The model exposes the configuration key in column 0; use it
            # directly so source_file opens and final_file saves.
            editor.key = str(index.siblingAtColumn(0).data(Qt.ItemDataRole.DisplayRole))
            editor.line_edit.textChanged.connect(lambda value: index.model().setData(index, value))
            self._focus_filter.register(editor.line_edit)
            return editor
        if kind == "int":
            editor = SettingsSpinBox(parent)
            editor.setRange(-1_000_000, 1_000_000)
            editor.valueChanged.connect(lambda value: index.model().setData(index, value))
            return self._prepare_editor(editor)
        if kind == "float":
            editor = SettingsDoubleSpinBox(parent)
            editor.setRange(-1_000_000, 1_000_000)
            editor.setDecimals(3)
            editor.valueChanged.connect(lambda value: index.model().setData(index, value))
            return self._prepare_editor(editor)
        if kind == "hours":
            editor = SettingsDoubleSpinBox(parent)
            editor.setRange(0, 8760)
            editor.setSingleStep(0.25)
            editor.setDecimals(2)
            editor.setSuffix(" h")
            editor.valueChanged.connect(lambda value: index.model().setData(index, value))
            return self._prepare_editor(editor)
        if kind == "times":
            editor = TimeListEditor(parent)
            editor.valueChanged.connect(lambda value: index.model().setData(index, value))
            self._prepare_editor(editor.display)
            self._prepare_editor(editor.picker)
            return editor
        if kind == "timezone":
            editor = SettingsEditableComboBox(parent)
            editor.addItems(index.data(ConfigTableModel.OptionsRole) or [])
            editor.currentTextChanged.connect(lambda value: index.model().setData(index, value))
            return self._prepare_editor(editor)
        editor = SettingsLineEdit(parent)
        editor.textChanged.connect(lambda value: index.model().setData(index, value))
        return self._prepare_editor(editor)

    def setEditorData(self, editor, index):
        kind = index.data(ConfigTableModel.KindRole)
        value = index.data(Qt.ItemDataRole.EditRole)
        if kind == "bool":
            checked = str(value).lower() == "true"
            editor.setChecked(checked)
            editor.setText("")
        elif kind == "options":
            editor.setCurrentText(str(value))
        elif kind == "int":
            editor.setValue(int(value or 0))
        elif kind == "float":
            editor.setValue(float(value or 0))
        elif kind == "hours":
            editor.setValue(float(value or 12))
        elif kind == "times":
            editor.setValue(value)
        elif kind == "timezone":
            editor.setCurrentText(str(value))
        elif kind == "path":
            text = str(value or "")
            if editor.text() != text:
                editor.setText(text)
        else:
            text = str(value or "")
            # textChanged writes through to the model immediately.  That emits
            # dataChanged and calls setEditorData again, so replacing identical
            # text here would reset the cursor to the beginning after every key.
            if editor.text() != text:
                editor.setText(text)
                editor.setCursorPosition(0)
                editor.deselect()

    def setModelData(self, editor, model, index):
        kind = index.data(ConfigTableModel.KindRole)
        if kind == "bool":
            value = editor.isChecked()
        elif kind == "options":
            value = editor.currentText()
        elif kind in {"int", "float", "hours"}:
            value = editor.value()
        elif kind == "times":
            value = editor.value()
        elif kind == "timezone":
            value = editor.currentText()
        elif kind == "path":
            value = editor.text()
        else:
            value = editor.text()
        model.setData(index, value)

    def updateEditorGeometry(self, editor, option, index):
        if index.data(ConfigTableModel.KindRole) == "path":
            editor.setGeometry(option.rect.adjusted(2, 1, -2, -1))
        else:
            editor.setGeometry(option.rect.adjusted(6, 3, -6, -3))


class ElidedDescriptionDelegate(TableItemDelegate):
    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.Type.ToolTip or not index.isValid():
            return super().helpEvent(event, view, option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        available_width = max(0, option.rect.width() - 12)
        if option.fontMetrics.horizontalAdvance(text) <= available_width:
            QToolTip.hideText()
            event.ignore()
            return False
        QToolTip.showText(event.globalPos(), text, view, option.rect)
        return True
