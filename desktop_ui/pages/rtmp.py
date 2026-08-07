import math
from collections import deque
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QDateTime, QEasingCurve, QItemSelectionModel, QMargins, QSignalBlocker, Signal, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox, FluentIcon, InfoBar, InfoBarPosition, PushButton, StrongBodyLabel, TableView, ToolButton, isDarkTheme, qconfig

import utils.constants as constants
from desktop_ui.models import MappingTableModel
from desktop_ui.playback import play_url
from desktop_ui.widgets import AccentPushButton, AppSearchLineEdit, DangerPushButton, GlassCard, configure_table_columns
from utils.channel_repository import list_streamable_results
from utils.config import config
from utils.i18n import t
from utils.tools import get_public_url


def _idle_countdown(value, _):
    if value is None:
        return "--"
    seconds = max(0, int(math.ceil(float(value))))
    if seconds == 0:
        return t("desktop.releasing_stream")
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _stream_channel_name(value, row):
    if row.get("state") == "starting":
        return f"{value or '--'} · {t('desktop.stream_starting_badge')}"
    return value or "--"


class ChannelPickerDialog(QDialog):
    settings_requested = Signal()

    def __init__(
        self,
        rows: list[dict],
        selected_keys: set[str],
        limit: int,
        active_result_keys: set[str],
        starting_result_keys: set[str],
        available_slots: int,
        parent=None,
    ):
        super().__init__(parent)
        self.limit = max(1, int(limit))
        self.active_result_keys = set(active_result_keys)
        self.starting_result_keys = set(starting_result_keys)
        self.available_slots = max(0, int(available_slots))
        self.rows_by_channel = {row.get("channel_key"): row for row in rows}
        self._updating = False
        self.setWindowTitle(t("desktop.choose_stream_channels"))
        self.setMinimumSize(500, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        self.search = AppSearchLineEdit(self)
        self.search.setPlaceholderText(t("desktop.filter_playback_channels"))
        layout.addWidget(self.search)
        self.list = QListWidget(self)
        self.list.setAlternatingRowColors(True)
        for row in rows:
            status = ""
            if row.get("result_key") in self.active_result_keys:
                status = f" · {t('desktop.stream_running_badge')}"
            elif row.get("result_key") in self.starting_result_keys:
                status = f" · {t('desktop.stream_starting_badge')}"
            item = QListWidgetItem(
                f"{row.get('channel_name') or '--'} · {row.get('category') or '--'}{status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, row.get("channel_key"))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if row.get("channel_key") in selected_keys else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        action_row = QHBoxLayout()
        self.count_label = CaptionLabel("", self)
        self.capacity_warning = CaptionLabel("", self)
        self.capacity_warning.setStyleSheet("color: #D97706;")
        self.capacity_warning.hide()
        self.select_all = CheckBox(t("desktop.select_all_visible"), self)
        self.select_all.setTristate(True)
        self.settings_button = PushButton(FluentIcon.SETTING, t("desktop.adjust_concurrency_limit"), self)
        self.settings_button.hide()
        self.cancel_button = PushButton(t("desktop.cancel"), self)
        self.confirm_button = AccentPushButton(t("desktop.confirm"), self)
        action_row.addWidget(self.count_label)
        action_row.addWidget(self.select_all)
        action_row.addStretch(1)
        action_row.addWidget(self.settings_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.confirm_button)
        layout.addWidget(self.capacity_warning)
        layout.addLayout(action_row)
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(self._item_changed)
        self.select_all.pressed.connect(self._prepare_toggle_visible)
        self.select_all.clicked.connect(self._toggle_visible)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.settings_button.clicked.connect(self._open_settings)
        self._update_count()
        self._update_select_all()

    def selected_keys(self) -> list[str]:
        return [
            self.list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.list.count())
            if self.list.item(index).checkState() == Qt.CheckState.Checked
        ]

    def _filter(self, text: str):
        search = text.strip().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(search and search not in item.text().casefold()))
        self._update_select_all()

    def _item_changed(self, item: QListWidgetItem):
        if self._updating:
            return
        self._update_count()
        self._update_select_all()

    def _prepare_toggle_visible(self):
        items = self._visible_items()
        self._clear_visible_on_click = bool(items) and all(
            item.checkState() == Qt.CheckState.Checked for item in items
        )

    def _toggle_visible(self, _checked=False):
        if self._updating:
            return
        checked = not getattr(self, "_clear_visible_on_click", False)
        self._updating = True
        for item in self._visible_items():
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._updating = False
        self._update_count()
        self._update_select_all()

    def _update_count(self):
        selected = self.selected_keys()
        occupied = self.active_result_keys | self.starting_result_keys
        needed = sum(
            self.rows_by_channel.get(channel_key, {}).get("result_key") not in occupied
            for channel_key in selected
        )
        overage = max(0, needed - self.available_slots)
        self.count_label.setText(t("desktop.stream_selection_capacity").format(
            count=len(selected),
            needed=needed,
            available=self.available_slots,
        ))
        self.capacity_warning.setText(t("desktop.stream_capacity_exceeded").format(count=overage))
        self.capacity_warning.setVisible(overage > 0)
        self.settings_button.setVisible(overage > 0)

    def _open_settings(self):
        self.reject()
        self.settings_requested.emit()

    def _visible_items(self):
        return [
            self.list.item(index)
            for index in range(self.list.count())
            if not self.list.item(index).isHidden()
        ]

    def _update_select_all(self):
        items = self._visible_items()
        checked = sum(item.checkState() == Qt.CheckState.Checked for item in items)
        state = (
            Qt.CheckState.Unchecked if checked == 0
            else Qt.CheckState.Checked if checked == len(items)
            else Qt.CheckState.PartiallyChecked
        )
        with QSignalBlocker(self.select_all):
            self.select_all.setCheckState(state)
        self.select_all.setEnabled(bool(items))


class RtmpPage(QWidget):
    refresh_requested = Signal()
    stream_control_many_requested = Signal(str, list)
    install_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rtmpPage")
        self.samples = deque(maxlen=180)
        self._available = False
        self._snapshot_received = False
        self._error_code = None
        self._error = ""
        self._installing = False
        self._streamable_rows = []
        self._channel_rows = []
        self._source_rows = []
        self._selected_channel_keys = set()
        self._max_streams = max(1, config.rtmp_max_streams)
        self._active_count = 0
        self._starting_count = 0
        self._available_slots = self._max_streams
        self._active_result_keys = set()
        self._starting_result_keys = set()

        self.quick_card = CardWidget(self)
        self.quick_card.setBorderRadius(10)
        quick_layout = QVBoxLayout(self.quick_card)
        quick_layout.setContentsMargins(16, 12, 16, 12)
        quick_layout.setSpacing(8)
        selection_row = QHBoxLayout()
        selection_row.setSpacing(8)
        self.channel_picker_button = PushButton(FluentIcon.LIBRARY, t("desktop.choose_stream_channels"), self.quick_card)
        self.selection_label = CaptionLabel("", self.quick_card)
        self.source_label = BodyLabel(t("desktop.select_output_source"), self.quick_card)
        self.source_selector = ComboBox(self.quick_card)
        self.source_selector.setMinimumWidth(270)
        selection_row.addWidget(self.channel_picker_button)
        selection_row.addWidget(self.selection_label, 1)
        selection_row.addWidget(self.source_label)
        selection_row.addWidget(self.source_selector)
        quick_layout.addLayout(selection_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.start_button = AccentPushButton(FluentIcon.SEND, t("desktop.open_selected_streams"), self.quick_card)
        self.direct_button = PushButton(FluentIcon.PLAY, t("desktop.direct_play"), self.quick_card)
        self.capacity_label = CaptionLabel("", self.quick_card)
        self.service_label = StrongBodyLabel(t("desktop.checking"), self.quick_card)
        self.install_button = PushButton(FluentIcon.DOWNLOAD, t("desktop.install_nginx_rtmp"), self.quick_card)
        self.install_button.hide()
        self.refresh_button = ToolButton(FluentIcon.SYNC, self.quick_card)
        self.refresh_button.setToolTip(t("desktop.refresh"))
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.direct_button)
        action_row.addStretch(1)
        action_row.addWidget(self.capacity_label)
        action_row.addWidget(self.service_label)
        action_row.addWidget(self.install_button)
        action_row.addWidget(self.refresh_button)
        quick_layout.addLayout(action_row)

        self.capacity_warning_row = QWidget(self.quick_card)
        capacity_warning_layout = QHBoxLayout(self.capacity_warning_row)
        capacity_warning_layout.setContentsMargins(0, 0, 0, 0)
        capacity_warning_layout.setSpacing(8)
        self.capacity_warning = CaptionLabel("", self.capacity_warning_row)
        self.capacity_warning.setStyleSheet("color: #D97706;")
        self.adjust_limit_button = PushButton(
            FluentIcon.SETTING,
            t("desktop.adjust_concurrency_limit"),
            self.capacity_warning_row,
        )
        capacity_warning_layout.addWidget(self.capacity_warning, 1)
        capacity_warning_layout.addWidget(self.adjust_limit_button)
        self.capacity_warning_row.hide()
        quick_layout.addWidget(self.capacity_warning_row)

        self.error_label = CaptionLabel("", self)
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        monitor_header = QHBoxLayout()
        monitor_header.setSpacing(8)
        self.monitor_title = StrongBodyLabel(t("desktop.current_streams"), self)
        self.session_summary = CaptionLabel(t("desktop.no_stream_selected"), self)
        self.open_button = PushButton(FluentIcon.PLAY, t("desktop.open_stream"), self)
        self.copy_active_button = PushButton(FluentIcon.LINK, t("desktop.copy_stream_url"), self)
        self.restart_button = PushButton(FluentIcon.ROTATE, t("desktop.restart_selected_streams"), self)
        self.stop_button = DangerPushButton(FluentIcon.PAUSE_BOLD, t("desktop.stop_selected_streams"), self)
        monitor_header.addWidget(self.monitor_title)
        monitor_header.addWidget(self.session_summary, 1)
        monitor_header.addWidget(self.open_button)
        monitor_header.addWidget(self.copy_active_button)
        monitor_header.addWidget(self.restart_button)
        monitor_header.addWidget(self.stop_button)

        self.stream_model = MappingTableModel(self._stream_columns(), self)
        self.table = TableView(self)
        self.table.setModel(self.stream_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setMinimumHeight(300)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        configure_table_columns(self.table, [150, 60, 110, 125, 90, 90], "rtmp.streams.compact.v2")

        self.empty_card = CardWidget(self)
        self.empty_card.setBorderRadius(10)
        empty_layout = QVBoxLayout(self.empty_card)
        empty_layout.setContentsMargins(20, 20, 20, 20)
        empty_layout.setSpacing(8)
        self.empty_title = StrongBodyLabel(t("desktop.no_active_streams"), self.empty_card)
        self.empty_description = CaptionLabel(t("desktop.no_active_streams_compact"), self.empty_card)
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.choose_button = AccentPushButton(FluentIcon.LIBRARY, t("desktop.choose_channel_to_start"), self.empty_card)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self.empty_title, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(self.empty_description, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)

        self.stream_stack = QStackedWidget(self)
        self.stream_stack.addWidget(self.empty_card)
        self.stream_stack.addWidget(self.table)
        self.stream_stack.setMinimumHeight(300)

        self.chart_card = GlassCard(self)
        self.chart_card.setFixedWidth(240)
        self.chart_card.setFixedHeight(240)
        chart_layout = QVBoxLayout(self.chart_card)
        chart_layout.setContentsMargins(14, 12, 14, 10)
        chart_layout.setSpacing(4)
        self.chart_title = StrongBodyLabel(t("desktop.bandwidth_trend"), self.chart_card)
        self.chart_status = CaptionLabel(t("desktop.not_streaming"), self.chart_card)
        chart_header = QHBoxLayout()
        chart_header.setContentsMargins(0, 0, 0, 0)
        chart_header.addWidget(self.chart_title)
        chart_header.addStretch(1)
        chart_header.addWidget(self.chart_status)
        self.chart_value = StrongBodyLabel("0.0 Kbit/s", self.chart_card)
        chart_value_font = self.chart_value.font()
        chart_value_font.setPointSize(18)
        chart_value_font.setBold(True)
        self.chart_value.setFont(chart_value_font)
        self.chart_meta = CaptionLabel(t("desktop.stream_chart_meta").format(streams=0, clients=0), self.chart_card)
        chart_layout.addLayout(chart_header)
        chart_layout.addWidget(self.chart_value)
        chart_layout.addWidget(self.chart_meta)

        self.series = QLineSeries(self)
        self.chart = QChart()
        self.chart.addSeries(self.series)
        self.chart.legend().hide()
        self.chart.setMargins(QMargins(0, 0, 0, 0))
        self.chart.layout().setContentsMargins(0, 0, 0, 0)
        self.time_axis = QDateTimeAxis()
        self.value_axis = QValueAxis()
        for axis in (self.time_axis, self.value_axis):
            axis.setLabelsVisible(False)
            axis.setGridLineVisible(False)
            axis.setLineVisible(False)
        self.chart.addAxis(self.time_axis, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.value_axis, Qt.AlignmentFlag.AlignLeft)
        self.series.attachAxis(self.time_axis)
        self.series.attachAxis(self.value_axis)
        self.chart_view = QChartView(self.chart, self.chart_card)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.chart_view.setMinimumHeight(120)
        chart_layout.addWidget(self.chart_view, 1)
        self.chart_card.hide()
        self._displayed_bandwidth = 0.0
        self._bandwidth_target = 0.0
        self.bandwidth_animation = QVariantAnimation(self)
        self.bandwidth_animation.setDuration(220)
        self.bandwidth_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.bandwidth_animation.valueChanged.connect(self._set_displayed_bandwidth)

        main_row = QHBoxLayout()
        main_row.setSpacing(10)
        main_row.addWidget(self.stream_stack, 1)
        main_row.addWidget(self.chart_card, 0, Qt.AlignmentFlag.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.quick_card)
        layout.addWidget(self.error_label)
        layout.addLayout(monitor_header)
        layout.addLayout(main_row, 1)

        self.channel_picker_button.clicked.connect(self._open_channel_picker)
        self.source_selector.currentIndexChanged.connect(self._update_quick_actions)
        self.start_button.clicked.connect(lambda: self._request_selected_control("start"))
        self.direct_button.clicked.connect(self._play_direct)
        self.refresh_button.clicked.connect(self._refresh_all)
        self.adjust_limit_button.clicked.connect(self.settings_requested)
        self.install_button.clicked.connect(self.install_requested)
        self.choose_button.clicked.connect(self._open_channel_picker)
        self.open_button.clicked.connect(self._open_active_stream)
        self.copy_active_button.clicked.connect(self._copy_active_stream)
        self.stop_button.clicked.connect(lambda: self._request_active_control("stop"))
        self.restart_button.clicked.connect(lambda: self._request_active_control("restart"))
        self.table.doubleClicked.connect(lambda _: self._open_active_stream())
        self.table.selectionModel().selectionChanged.connect(self._stream_changed)
        qconfig.themeChanged.connect(self._apply_chart_theme)
        qconfig.themeChangedFinished.connect(self._apply_chart_theme)
        self.reload_channels()
        self._set_monitor_visibility(False)
        self._apply_chart_theme()
        self._set_bandwidth_idle_state()

    @staticmethod
    def _stream_columns():
        return [
            ("channel_name", t("name.channel"), _stream_channel_name),
            ("clients", t("desktop.connections_short"), None),
            ("resolution", t("desktop.column_resolution"), None),
            ("bw_out", t("desktop.column_bandwidth"), lambda value, _: f"{float(value or 0) / 1000:.1f} Kbit/s"),
            ("uptime", t("desktop.column_uptime"), None),
            ("idle_remaining", t("desktop.idle_release"), _idle_countdown),
        ]

    def reload_channels(self, preferred_result_key: str | None = None, allow_default: bool = True):
        current_result = self._selected_result()
        target_result_key = preferred_result_key or (current_result.get("result_key") if current_result else None)
        self._streamable_rows = list_streamable_results(constants.channel_results_path)
        known = set()
        self._channel_rows = []
        for row in self._streamable_rows:
            channel_key = row.get("channel_key")
            if channel_key not in known:
                known.add(channel_key)
                self._channel_rows.append(row)
        self._selected_channel_keys.intersection_update(known)
        if allow_default and not self._selected_channel_keys and self._channel_rows:
            self._selected_channel_keys.add(self._channel_rows[0]["channel_key"])
        self._update_channel_selection(target_result_key)

    def select_result(self, row: dict):
        channel_key = row.get("channel_key")
        self._selected_channel_keys = {channel_key} if channel_key else set()
        self.reload_channels(row.get("result_key"), allow_default=False)

    def select_channels(self, channel_keys: list[str]):
        self._selected_channel_keys = set(channel_keys)
        self.reload_channels(allow_default=False)
        return len(self._selected_channel_keys)

    def set_snapshot(self, snapshot: dict):
        self._snapshot_received = True
        streams = snapshot.get("streams", [])
        clients = sum(int(stream.get("clients") or 0) for stream in streams)
        bw_out = float(snapshot.get("bw_out") or sum(float(stream.get("bw_out") or 0) for stream in streams))
        self._available = bool(snapshot.get("available"))
        self._error_code = snapshot.get("error_code")
        self._error = snapshot.get("error") or ""
        self._max_streams = max(1, int(snapshot.get("max_streams") or config.rtmp_max_streams))
        self._active_count = max(0, int(snapshot.get("active_count") or 0))
        self._starting_count = max(0, int(snapshot.get("starting_count") or 0))
        self._available_slots = max(0, int(snapshot.get("available_slots") or 0))
        self._active_result_keys = set(snapshot.get("active_streams") or [])
        self._starting_result_keys = set(snapshot.get("starting_streams") or [])
        self.capacity_label.setText(t("desktop.stream_capacity_summary").format(
            active=self._active_count,
            starting=self._starting_count,
            limit=self._max_streams,
            available=self._available_slots,
        ))
        self.service_label.setText(
            t("desktop.streaming_service_running") if self._available else t("desktop.streaming_service_unavailable")
        )
        self._apply_service_style()
        if self._available:
            self.error_label.hide()
            self.install_button.hide()
        else:
            self.error_label.setText(t(
                f"desktop.rtmp_error_{self._error_code}",
                self._error or t("desktop.rtmp_unavailable_hint"),
            ))
            self.error_label.show()
            self.install_button.setVisible(self._error_code in {"nginx_missing", "rtmp_module_missing"})
        selected_keys = {row.get("result_key") for row in self._selected_streams()}
        self.stream_model.set_rows(streams)
        selection = self.table.selectionModel()
        for index, row in enumerate(streams):
            if row.get("result_key") in selected_keys:
                selection.select(
                    self.stream_model.index(index, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
        self._set_monitor_visibility(bool(streams))
        self._stream_changed()
        self._update_quick_actions()
        now = datetime.now().timestamp() * 1000
        self.samples.append((now, bw_out / 1000 if self._available else 0))
        self.series.clear()
        for timestamp, value in self.samples:
            self.series.append(timestamp, value)
        self.series.setPointsVisible(len(self.samples) < 2)
        if self.samples:
            self.time_axis.setRange(
                QDateTime.fromMSecsSinceEpoch(int(self.samples[0][0])),
                QDateTime.fromMSecsSinceEpoch(int(self.samples[-1][0] + 1000)),
            )
        self.value_axis.setRange(0, max(10, max((value for _, value in self.samples), default=0) * 1.15))
        if streams:
            target = bw_out / 1000
            self.chart_card.show()
            self.chart_value.show()
            self.chart_meta.show()
            self.chart_status.setText(t("desktop.stream_running_badge"))
            self.chart_status.setStyleSheet("color: #059669;")
            self.chart_meta.setText(t("desktop.stream_chart_meta").format(streams=len(streams), clients=clients))
            self.chart_card.set_accent("#2563EB")
            if abs(target - self._bandwidth_target) >= 0.1:
                self._bandwidth_target = target
                self.bandwidth_animation.stop()
                self.bandwidth_animation.setStartValue(self._displayed_bandwidth)
                self.bandwidth_animation.setEndValue(target)
                self.bandwidth_animation.start()
                self.chart_card.pulse()
        else:
            self._set_bandwidth_idle_state()

    def _set_displayed_bandwidth(self, value):
        self._displayed_bandwidth = float(value)
        self.chart_value.setText(f"{self._displayed_bandwidth:.1f} Kbit/s")

    def _set_bandwidth_idle_state(self):
        self.bandwidth_animation.stop()
        self._displayed_bandwidth = 0.0
        self._bandwidth_target = 0.0
        self.chart_card.hide()

    def set_installing(self, installing: bool):
        self._installing = installing
        self.install_button.setEnabled(not installing)
        self.install_button.setText(
            t("desktop.installing_nginx_rtmp") if installing else t("desktop.install_nginx_rtmp")
        )

    def retranslate(self):
        self.channel_picker_button.setText(t("desktop.choose_stream_channels"))
        self.source_label.setText(t("desktop.select_output_source"))
        self.start_button.setText(t("desktop.open_selected_streams"))
        self.direct_button.setText(t("desktop.direct_play"))
        self.adjust_limit_button.setText(t("desktop.adjust_concurrency_limit"))
        self.refresh_button.setToolTip(t("desktop.refresh"))
        self.monitor_title.setText(t("desktop.current_streams"))
        self.open_button.setText(t("desktop.open_stream"))
        self.copy_active_button.setText(t("desktop.copy_stream_url"))
        self.restart_button.setText(t("desktop.restart_selected_streams"))
        self.stop_button.setText(t("desktop.stop_selected_streams"))
        self.empty_title.setText(t("desktop.no_active_streams"))
        self.empty_description.setText(t("desktop.no_active_streams_compact"))
        self.choose_button.setText(t("desktop.choose_channel_to_start"))
        self.chart_title.setText(t("desktop.bandwidth_trend"))
        self.set_installing(self._installing)
        self.stream_model.set_columns(self._stream_columns())
        self.reload_channels()
        self._stream_changed()

    def _open_channel_picker(self):
        dialog = ChannelPickerDialog(
            self._channel_rows,
            self._selected_channel_keys,
            self._max_streams,
            self._active_result_keys,
            self._starting_result_keys,
            self._available_slots,
            self,
        )
        dialog.settings_requested.connect(self.settings_requested)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._selected_channel_keys = set(dialog.selected_keys())
        self._update_channel_selection()

    def _update_channel_selection(self, preferred_result_key: str | None = None):
        selected_channels = [
            row for row in self._channel_rows if row.get("channel_key") in self._selected_channel_keys
        ]
        count = len(selected_channels)
        self.channel_picker_button.setText(t("desktop.selected_stream_channels").format(count=count))
        names = [str(row.get("channel_name") or "--") for row in selected_channels]
        summary = ", ".join(names[:2])
        if count > 2:
            summary = t("desktop.channel_selection_more").format(names=summary, count=count - 2)
        self.selection_label.setText(summary or t("desktop.no_channel_selected"))
        single = selected_channels[0] if count == 1 else None
        channel_key = single.get("channel_key") if single else None
        self._source_rows = [row for row in self._streamable_rows if row.get("channel_key") == channel_key]
        with QSignalBlocker(self.source_selector):
            self.source_selector.clear()
            for row in self._source_rows:
                rank = row.get("selected_rank") or 1
                resolution = row.get("resolution") or t("desktop.unknown_resolution")
                host = urlparse(row.get("url") or "").hostname or t("desktop.unknown_source")
                self.source_selector.addItem(t("desktop.output_source_label").format(
                    rank=rank,
                    resolution=resolution,
                    host=host,
                ))
            source_index = next(
                (index for index, row in enumerate(self._source_rows) if row.get("result_key") == preferred_result_key),
                0 if self._source_rows else -1,
            )
            self.source_selector.setCurrentIndex(source_index)
        self.source_label.setVisible(bool(single))
        self.source_selector.setVisible(bool(single))
        self._update_quick_actions()

    def _selected_result(self):
        index = self.source_selector.currentIndex() if hasattr(self, "source_selector") else -1
        return self._source_rows[index] if 0 <= index < len(self._source_rows) else None

    def _selected_results(self):
        if len(self._selected_channel_keys) == 1:
            row = self._selected_result()
            return [row] if row else []
        results = []
        seen = set()
        for row in self._streamable_rows:
            channel_key = row.get("channel_key")
            if channel_key in self._selected_channel_keys and channel_key not in seen:
                seen.add(channel_key)
                results.append(row)
        return results

    def _selected_streams(self):
        return [
            row
            for index in self.table.selectionModel().selectedRows()
            if (row := self.stream_model.row(index))
        ]

    def _update_quick_actions(self, _index=None):
        rows = self._selected_results()
        single = len(rows) == 1
        needed, overage = self._selection_capacity(rows)
        self.start_button.setEnabled(bool(rows) and self._available and overage == 0)
        self.direct_button.setEnabled(single)
        self.capacity_warning.setText(t("desktop.stream_capacity_inline").format(
            needed=needed,
            available=self._available_slots,
            count=overage,
        ))
        self.capacity_warning_row.setVisible(overage > 0)

    def _selection_capacity(self, rows: list[dict] | None = None):
        rows = self._selected_results() if rows is None else rows
        occupied = self._active_result_keys | self._starting_result_keys
        needed = sum(row.get("result_key") not in occupied for row in rows)
        return needed, max(0, needed - self._available_slots)

    def _play_direct(self):
        rows = self._selected_results()
        if len(rows) == 1:
            play_url(rows[0]["url"], self)

    def _request_selected_control(self, action: str):
        keys = [row["result_key"] for row in self._selected_results()]
        needed, overage = self._selection_capacity()
        if action == "start" and overage:
            InfoBar.warning(
                t("desktop.start_selected_streams"),
                t("desktop.stream_capacity_inline").format(
                    needed=needed,
                    available=self._available_slots,
                    count=overage,
                ),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        if keys:
            self.stream_control_many_requested.emit(action, keys)

    def _request_active_control(self, action: str):
        keys = [row["result_key"] for row in self._selected_streams()]
        if keys:
            self.stream_control_many_requested.emit(action, keys)

    def _open_active_stream(self):
        rows = self._selected_streams()
        if len(rows) == 1:
            play_url(self._stream_url(rows[0]["result_key"]), self)

    def _copy_active_stream(self):
        rows = self._selected_streams()
        if len(rows) == 1:
            self._copy_stream_url(rows[0]["result_key"])

    def _copy_stream_url(self, result_key: str):
        url = self._stream_url(result_key)
        QGuiApplication.clipboard().setText(url)
        InfoBar.success(t("desktop.copied"), url, parent=self, position=InfoBarPosition.TOP)

    @staticmethod
    def _stream_url(result_key: str):
        return f"{get_public_url().rstrip('/')}/hls_proxy/{result_key}"

    def _refresh_all(self):
        self.reload_channels()
        self.refresh_requested.emit()

    def _set_monitor_visibility(self, has_streams: bool):
        self.stream_stack.setCurrentWidget(self.table if has_streams else self.empty_card)

    def _stream_changed(self, *_):
        rows = self._selected_streams()
        count = len(rows)
        self.open_button.setEnabled(count == 1)
        self.copy_active_button.setEnabled(count == 1)
        self.restart_button.setEnabled(count > 0)
        self.stop_button.setEnabled(count > 0)
        self.session_summary.setText(
            t("desktop.active_streams_selected").format(count=count)
            if count else t("desktop.no_stream_selected")
        )

    def _apply_service_style(self):
        color = "#10B981" if self._available else "#F59E0B"
        self.service_label.setStyleSheet(f"color: {color};")

    def _apply_chart_theme(self, *_):
        dark = isDarkTheme()
        line = QColor("#60A5FA" if dark else "#2563EB")
        # Let the chart canvas inherit the rounded CardWidget surface.  Filling it
        # with white here previously left a square background inside the card.
        self.chart.setBackgroundVisible(False)
        self.chart.setPlotAreaBackgroundVisible(False)
        self.chart_view.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.chart_view.setStyleSheet("QChartView { background: transparent; border: none; }")
        self.chart_view.viewport().setAutoFillBackground(False)
        self.series.setPen(QPen(line, 2.2))
        self.chart_value.setStyleSheet(f"color: {line.name()};")
        self.chart_view.viewport().update()
