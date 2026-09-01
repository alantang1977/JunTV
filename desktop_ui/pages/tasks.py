from datetime import datetime

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, PushButton, TableView

import utils.constants as constants
from desktop_ui.models import MappingTableModel
from desktop_ui.widgets import AccentPushButton, configure_table_columns
from utils.channel_repository import list_channels, list_operations, list_runs, result_metadata_map
from utils.diagnostics import export_diagnostics
from utils.i18n import t


def _time(value, _):
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value else "--"


def _status(value, _):
    return t(f"desktop.status_{value}", value or "--")


def _task(value, _):
    return t(f"desktop.{value}", value or "--")


def _duration(value, _):
    return "--" if value is None else f"{float(value):.1f} s"


class TasksPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tasksPage")
        self.model = MappingTableModel(self._columns(), self)
        self.table = TableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        configure_table_columns(self.table, [170, 95, 110, 200, 85, 360], "tasks.history")
        self.refresh_button = PushButton(FluentIcon.SYNC, t("desktop.refresh"), self)
        self.export_button = AccentPushButton(FluentIcon.ZIP_FOLDER, t("desktop.export_diagnostics"), self)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.export_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self.export)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    @staticmethod
    def _columns():
        return [
            ("started_at", t("desktop.column_started"), _time),
            ("status", t("desktop.status"), _status),
            ("task", t("desktop.column_task"), _task),
            ("target", t("desktop.target"), None),
            ("duration", t("desktop.column_duration"), _duration),
            ("details", t("desktop.details"), None),
        ]

    def refresh(self):
        try:
            runs = [
                {
                    **row,
                    "task": "full_update",
                    "target": "--",
                    "duration": (row["finished_at"] - row["started_at"]) if row.get("finished_at") else None,
                    "details": row.get("error") or "",
                }
                for row in list_runs(constants.channel_results_path)
            ]
            operation_rows = list_operations(constants.channel_results_path)
            channel_names = {row["channel_key"]: row["name"] for row in list_channels(constants.channel_results_path)}
            result_keys = [row.get("target_key") for row in operation_rows if row.get("target_type") == "result"]
            result_names = result_metadata_map(constants.channel_results_path, result_keys)
            operations = []
            for row in operation_rows:
                target_type = row.get("target_type")
                target_key = row.get("target_key")
                target_name = {
                    "channel": channel_names.get(target_key),
                    "result": (result_names.get(target_key) or {}).get("name"),
                    "category": target_key,
                }.get(target_type)
                target = t(f"desktop.target_{target_type}", target_type or "--")
                if target_name:
                    target = f"{target} · {target_name}"
                operations.append({
                    **row,
                    "task": row.get("operation"),
                    "target": target,
                    "duration": (row["finished_at"] - row["started_at"]) if row.get("finished_at") else None,
                    "details": row.get("message") or "",
                })
            self.model.set_rows(sorted(runs + operations, key=lambda row: row.get("started_at") or 0, reverse=True))
        except Exception:
            self.model.set_rows([])

    def export(self):
        try:
            path = export_diagnostics()
            InfoBar.success(t("desktop.diagnostics_exported"), path, parent=self, position=InfoBarPosition.TOP)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as exc:
            InfoBar.error(t("desktop.diagnostics_failed"), str(exc), parent=self, position=InfoBarPosition.TOP)

    def retranslate(self):
        self.refresh_button.setText(t("desktop.refresh"))
        self.export_button.setText(t("desktop.export_diagnostics"))
        self.model.set_columns(self._columns())
