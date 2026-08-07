from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QVBoxLayout
from qfluentwidgets import BodyLabel

from desktop_ui.widgets import apply_dialog_theme, localize_dialog_buttons
from utils.i18n import t


class SourceImportDialog(QDialog):
    """Preview and select parsed records before adding them to a source editor."""

    def __init__(self, title, records, columns, parent=None):
        super().__init__(parent)
        self.records = list(records)
        self.columns = list(columns)
        apply_dialog_theme(self)
        self.setWindowTitle(title)
        self.resize(920, 520)

        new_count = sum(record["status"] == "new" for record in self.records)
        duplicate_count = sum(record["status"] == "duplicate" for record in self.records)
        invalid_count = sum(record["status"] == "invalid" for record in self.records)
        self.summary = BodyLabel(
            t("desktop.import_summary").format(
                total=len(self.records),
                new=new_count,
                duplicate=duplicate_count,
                invalid=invalid_count,
            ),
            self,
        )
        self.table = QTableWidget(self)
        self.table.setColumnCount(3 + len(self.columns))
        self.table.setHorizontalHeaderLabels([
            "",
            t("desktop.import_file"),
            t("desktop.import_line"),
            *(label for label, _key in self.columns),
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.records))
        self._populate()
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setUpdatesEnabled(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            self,
        )
        localize_dialog_buttons(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(t("desktop.import_confirm"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

    def _populate(self):
        for row, record in enumerate(self.records):
            checkbox = QTableWidgetItem()
            checkbox.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checkbox.setCheckState(
                Qt.CheckState.Checked if record["selected"] else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, checkbox)

            values = [record["file_name"], str(record["line_number"] or "-")]
            values.extend(str(record["row"].get(key, "")) for _label, key in self.columns)
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                if record["status"] == "invalid":
                    item.setToolTip(record["reason"])
                    item.setForeground(Qt.GlobalColor.red)
                elif record["status"] == "duplicate":
                    item.setToolTip(t("desktop.import_duplicate"))
                    item.setForeground(Qt.GlobalColor.darkGray)
                self.table.setItem(row, column, item)

    def _accept(self):
        self._selected_records = [
            record
            for row, record in enumerate(self.records)
            if record["status"] == "new"
            and self.table.item(row, 0)
            and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]
        self.accept()

    def selected_records(self):
        return getattr(self, "_selected_records", [])
