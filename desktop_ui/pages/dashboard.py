import datetime
import os

import pytz
from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import Action, BodyLabel, CardWidget, DropDownPushButton, FluentIcon, IconWidget, InfoBar, ProgressBar, PushButton, RoundMenu, StrongBodyLabel, TableView, isDarkTheme

import utils.constants as constants
from desktop_ui.models import ChannelLogoLoader, ChannelTableModel
from desktop_ui.playback import play_url
from desktop_ui.logo_dialog import ChannelLogoDialog, is_channel_logo_click
from desktop_ui.stream_status import StreamingStatusDelegate, apply_channel_stream_state, build_channel_stream_states
from desktop_ui.widgets import AccentPushButton, AppSearchLineEdit, DangerPushButton, MetricCard, configure_table_columns, metric_row, play_circle_icon, warning_message_box
from utils.channel_repository import latest_successful_run, list_categories, list_channel_results, list_channels, set_channel_logo
from utils.config import config
from utils.i18n import t
from utils.tools import get_public_url, parse_times, resource_path
from utils.run_state import read_run_state


def next_scheduled_update(now: datetime.datetime | None = None):
    """Return the next configured update time, or None when scheduling is disabled."""
    timezone = pytz.timezone(config.time_zone)
    if now is None:
        now = datetime.datetime.now(timezone)
    elif now.tzinfo is None:
        now = timezone.localize(now)
    else:
        now = now.astimezone(timezone)
    times = parse_times(config.update_times)
    if config.update_mode == "time" and times:
        candidates = []
        for hour, minute in times:
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            candidates.append(candidate if candidate > now else candidate + datetime.timedelta(days=1))
        return min(candidates)
    if config.update_interval:
        run = latest_successful_run(constants.channel_results_path)
        base = datetime.datetime.fromtimestamp(float(run["finished_at"]), timezone) if run and run.get("finished_at") else now
        interval = datetime.timedelta(hours=config.update_interval)
        next_time = base + interval
        while next_time <= now:
            next_time += interval
        return next_time
    return None


class DashboardProgressBar(ProgressBar):
    """Compact pill-shaped progress track with theme-safe idle colors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(QColor("#3F3F46" if isDarkTheme() else "#E2E8F0"))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)

        total = self.maximum() - self.minimum()
        if total <= 0:
            return
        ratio = max(0.0, min(1.0, (self.getVal() - self.minimum()) / total))
        if ratio <= 0:
            return
        width = max(float(self.height()), self.width() * ratio)
        painter.setBrush(self.barColor())
        painter.drawRoundedRect(QRectF(0, 0, width, self.height()), radius, radius)


def _speed(value, _):
    return "--" if value is None else f"{float(value):.2f} M/s"


def _channel_status(value, _):
    return {
        "pending": t("desktop.status_pending"),
        "testing": t("desktop.status_testing"),
        "completed": t("desktop.status_completed"),
        "healthy": t("desktop.health_healthy"),
        "warning": t("desktop.health_warning"),
        "offline": t("desktop.health_offline"),
        "unknown": t("desktop.health_unknown"),
    }.get(value, value or "--")


def _updated_at(value, _):
    return "--" if not value else datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")


def _channel_logo(name, logo):
    if logo:
        return logo
    local_logo = os.path.join(resource_path(constants.channel_logo_path), f"{name}.{config.logo_type}")
    if os.path.isfile(local_logo):
        return local_logo
    if config.logo_url:
        return f"{config.logo_url.rstrip('/')}/{name}.{config.logo_type}"
    return None


class ChannelNamePlayDelegate(StreamingStatusDelegate):
    def __init__(self, play_callback, stream_callback, parent=None):
        super().__init__(stream_callback, parent, trailing_width=42)
        self._play_callback = play_callback
        self._icon = play_circle_icon()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        if row.get("best_url") and int(row.get("valid_results") or 0) > 0:
            option.rect.adjust(0, 0, -42, 0)

    def paint(self, painter: QPainter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        playable = bool(row.get("best_url") and int(row.get("valid_results") or 0) > 0)
        super().paint(painter, option, index)
        if not playable:
            return
        rect = self._button_rect(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2563EB"))
        painter.drawRoundedRect(rect, 6, 6)
        self._icon.paint(painter, rect.adjusted(5, 2, -5, -2), Qt.AlignmentFlag.AlignCenter)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if super().editorEvent(event, model, option, index):
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            row = index.data(Qt.ItemDataRole.UserRole) or {}
            if row.get("best_url") and int(row.get("valid_results") or 0) > 0 and self._button_rect(option.rect).contains(event.position().toPoint()):
                self._play_callback(row)
                return True
        return False

    @staticmethod
    def _button_rect(rect):
        size = min(30, rect.height() - 8)
        return rect.adjusted(rect.width() - size - 8, (rect.height() - size) // 2, -8, -(rect.height() - size) // 2)


class DashboardPage(QWidget):
    run_requested = Signal()
    cancel_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    destination_requested = Signal(str)
    stream_control_many_requested = Signal(str, list)

    def __init__(self, parent=None, logo_loader: ChannelLogoLoader | None = None):
        super().__init__(parent)
        self.setObjectName("dashboardPage")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._running = False
        self._cancelling = False
        self._paused = False
        self._service_status = "unknown"
        self._runtime_rows = []
        self._stream_snapshot = {"streams": []}
        self._stream_states = {}
        self._active_channel = None
        self.last_update_outcome = None
        self.run_state = read_run_state()
        self.status_card = MetricCard(t("desktop.run_status"), t("desktop.idle"), icon=FluentIcon.POWER_BUTTON, accent="#64748B")
        self.channel_card = MetricCard(
            t("desktop.channels"), "0", icon=FluentIcon.LIBRARY, accent="#7C3AED", animate_value_updates=True,
        )
        self.valid_card = MetricCard(
            t("desktop.valid_results"), "0", icon=FluentIcon.COMPLETED, accent="#059669", animate_value_updates=True,
        )
        self.service_card = MetricCard(
            t("desktop.service"),
            t("desktop.unknown"),
            get_public_url(config.app_port),
            FluentIcon.GLOBE,
            accent="#EA580C",
        )
        for card in (self.status_card, self.channel_card, self.valid_card, self.service_card):
            card.set_clickable()

        self.progress_card = CardWidget(self)
        progress_layout = QVBoxLayout(self.progress_card)
        progress_layout.setContentsMargins(18, 16, 18, 16)
        progress_layout.setSpacing(10)
        self.progress_title = BodyLabel(t("desktop.ready"), self.progress_card)
        self.progress = DashboardProgressBar(self.progress_card)
        self.progress.setValue(0)
        actions = QHBoxLayout()
        self.run_button = AccentPushButton(FluentIcon.POWER_BUTTON, t("desktop.run_once"), self.progress_card)
        self.pause_button = PushButton(FluentIcon.PAUSE, t("desktop.pause"), self.progress_card)
        self.pause_button.hide()
        self.cancel_button = DangerPushButton(FluentIcon.CLOSE, t("desktop.cancel"), self.progress_card)
        self.cancel_button.hide()
        self.output_button = PushButton(FluentIcon.FOLDER, t("desktop.open_output"), self.progress_card)
        self.service_button = DropDownPushButton(FluentIcon.GLOBE, t("desktop.browse_results"), self.progress_card)
        self._create_service_menu()
        actions.addWidget(self.run_button)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        actions.addWidget(self.output_button)
        actions.addWidget(self.service_button)
        progress_layout.addWidget(self.progress_title)
        progress_layout.addWidget(self.progress)
        progress_layout.addLayout(actions)

        self.channels_card = CardWidget(self)
        channel_layout = QVBoxLayout(self.channels_card)
        channel_layout.setContentsMargins(18, 16, 18, 16)
        channel_layout.setSpacing(8)
        self.channels_title = BodyLabel(t("desktop.channel_result_status"), self.channels_card)
        self.channel_search = AppSearchLineEdit(self.channels_card)
        self.channel_search.setPlaceholderText(t("desktop.search_channel_names"))
        self.channel_model = ChannelTableModel(self._channel_columns(), self, logo_loader=logo_loader)
        self.logo_loader = self.channel_model.logo_loader
        self.channel_table = TableView(self.channels_card)
        self.channel_table.setModel(self.channel_model)
        self.channel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.channel_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.channel_table.setSortingEnabled(True)
        self.channel_table.verticalHeader().setVisible(False)
        configure_table_columns(self.channel_table, [260, 100, 85, 85, 105, 115, 85, 170], "dashboard.channels.v2")
        self.channel_table.setBorderVisible(False)
        self.channel_table.setIconSize(QSize(32, 24))
        self.play_delegate = ChannelNamePlayDelegate(
            self._play_channel,
            self._show_stream_menu,
            self.channel_table,
        )
        self.channel_table.setItemDelegateForColumn(0, self.play_delegate)
        self.channel_stack = QStackedWidget(self.channels_card)
        self.channel_stack.addWidget(self.channel_table)
        self.empty_state = QWidget(self.channel_stack)
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(20, 32, 20, 32)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(1)
        self.empty_icon = IconWidget(FluentIcon.UPDATE.icon(color=QColor("#64748B")), self.empty_state)
        self.empty_icon.setFixedSize(46, 46)
        self.empty_title = StrongBodyLabel(t("desktop.channel_results_empty"), self.empty_state)
        self.empty_hint = BodyLabel(t("desktop.channel_results_empty_hint"), self.empty_state)
        self.configure_sources_button = PushButton(
            FluentIcon.FOLDER,
            t("desktop.configure_sources"),
            self.empty_state,
        )
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet(f"color: {'#94A3B8' if isDarkTheme() else '#64748B'}")
        empty_layout.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addWidget(self.configure_sources_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        self.channel_stack.addWidget(self.empty_state)
        channel_header = QHBoxLayout()
        channel_header.addWidget(self.channels_title)
        channel_header.addWidget(self.channel_search)
        self.channel_search.setMaximumWidth(320)
        channel_header.addStretch(1)
        channel_layout.addLayout(channel_header)
        channel_layout.addWidget(self.channel_stack, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(metric_row([self.status_card, self.channel_card, self.valid_card, self.service_card]))
        layout.addWidget(self.progress_card)
        layout.addWidget(self.channels_card, 1)

        self.run_button.clicked.connect(self.run_requested)
        self.pause_button.clicked.connect(self._toggle_paused)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.output_button.clicked.connect(self.open_output)
        self.channel_search.textChanged.connect(self._apply_runtime_rows)
        self.channel_table.clicked.connect(self._channel_clicked)
        self.configure_sources_button.clicked.connect(lambda: self.destination_requested.emit("sources"))
        self.status_card.clicked.connect(lambda: self.destination_requested.emit("tasks"))
        self.channel_card.clicked.connect(lambda: self.destination_requested.emit("channels"))
        self.valid_card.clicked.connect(lambda: self.destination_requested.emit("channels"))
        self.service_card.clicked.connect(lambda: self.destination_requested.emit("rtmp"))
        self.schedule_timer = QTimer(self)
        self.schedule_timer.setInterval(30_000)
        self.schedule_timer.timeout.connect(self.refresh_schedule)
        self.schedule_timer.start()
        self.refresh_metrics()
        self.refresh_schedule()

    def _create_service_menu(self):
        self.service_menu = RoundMenu(parent=self)
        self.service_actions = []
        for key, route, icon in (
            ("desktop.result_all", "/", FluentIcon.GLOBE),
            ("desktop.result_content", "/content", FluentIcon.VIEW),
            ("desktop.result_ipv4", "/ipv4", FluentIcon.CONNECT),
            ("desktop.result_ipv6", "/ipv6", FluentIcon.CONNECT),
            ("desktop.result_hls", "/hls", FluentIcon.VIDEO),
            ("desktop.result_epg", "/epg/epg.xml", FluentIcon.CALENDAR),
        ):
            action = Action(icon, t(key), self, triggered=lambda _checked=False, value=route: self._open_service_route(value))
            self.service_actions.append((action, key))
            self.service_menu.addAction(action)
        self.service_button.setMenu(self.service_menu)

    @staticmethod
    def _channel_columns():
        return [
            ("name", t("name.channel"), None),
            ("display_status", t("desktop.status"), _channel_status),
            ("valid_results", t("desktop.column_valid"), None),
            ("selected_results", t("desktop.column_output"), None),
            ("best_speed", t("desktop.column_best_speed"), _speed),
            ("max_resolution", t("desktop.column_resolution"), None),
            ("total_results", t("desktop.column_results"), None),
            ("updated_at", t("desktop.column_updated"), _updated_at),
        ]

    def refresh_metrics(self):
        try:
            categories = list_categories(constants.channel_results_path)
            all_channels = list_channels(constants.channel_results_path)
        except Exception:
            categories = []
            all_channels = []
        self.channel_card.set_value(len(all_channels), t("desktop.category_count").format(count=len(categories)))
        self.valid_card.set_value(sum(int(row.get("valid_results") or 0) for row in all_channels), t("desktop.selected_count").format(
            count=sum(int(row.get("selected_results") or 0) for row in all_channels)
        ))
        if not self._running:
            self._runtime_rows = [
                apply_channel_stream_state({**row, "display_status": "completed"}, self._stream_states)
                for row in all_channels
            ]
        self._apply_runtime_rows()

    def _apply_runtime_rows(self):
        self.run_state = read_run_state()
        term = self.channel_search.text().strip().lower()
        rows = [
            row for row in self._runtime_rows
            if not term or term in str(row.get("name", "")).lower()
        ]
        self.channel_model.set_rows(rows)
        self.channel_stack.setCurrentWidget(self.channel_table if rows else self.empty_state)
        if term:
            title_key = "desktop.channel_results_no_match"
            hint_key = "desktop.channel_results_no_match_hint"
        elif self.last_update_outcome and self.last_update_outcome.get("status") == "empty":
            title_key = "desktop.channel_results_empty_after_run"
            hint_key = "desktop.channel_results_empty_after_run_hint"
        elif self.run_state.get("status") == "running":
            title_key = "desktop.channel_results_empty_running"
            hint_key = "desktop.channel_results_empty_running_hint"
        elif self.run_state.get("status") == "failed":
            title_key = "desktop.channel_results_empty_failed"
            hint_key = "desktop.channel_results_empty_failed_hint"
        elif self.run_state.get("status") == "cancelled":
            title_key = "desktop.channel_results_empty_cancelled"
            hint_key = "desktop.channel_results_empty_cancelled_hint"
        elif self.run_state.get("status") == "completed_empty":
            title_key = "desktop.channel_results_empty_after_run"
            hint_key = "desktop.channel_results_empty_after_run_hint"
        else:
            title_key = "desktop.channel_results_empty"
            hint_key = "desktop.channel_results_empty_hint"
        self.empty_title.setText(t(title_key))
        self.empty_hint.setText(t(hint_key))
        self.configure_sources_button.setVisible(not term)
        self._scroll_to_active()

    def _scroll_to_active(self):
        if not self._active_channel:
            return
        row_index = next(
            (index for index, row in enumerate(self.channel_model.rows)
             if (row.get("category"), row.get("name")) == self._active_channel),
            -1,
        )
        if row_index >= 0:
            index = self.channel_model.index(row_index, 0)
            self.channel_table.setCurrentIndex(index)
            self.channel_table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def set_running(self, running: bool):
        was_cancelling = self._cancelling
        self._running = running
        self._cancelling = False
        self._paused = False
        if running:
            self.last_update_outcome = None
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.run_button.setVisible(not running)
        self.run_button.setEnabled(not running)
        self.pause_button.setVisible(running)
        self.pause_button.setEnabled(running)
        self.pause_button.setIcon(FluentIcon.PAUSE)
        self.pause_button.setText(t("desktop.pause"))
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setText(t("desktop.cancel"))
        self._set_run_status("running" if running else "idle")
        self._set_metric_activity("running" if running else "idle")
        if running:
            self._active_channel = None
            self._apply_runtime_rows()
        else:
            if was_cancelling:
                self.progress_title.setText(t("desktop.status_cancelled"))
            elif self.last_update_outcome:
                self.channel_card.pulse()
                self.valid_card.pulse()
            self.refresh_metrics()

    def _toggle_paused(self):
        if not self._running or self._cancelling:
            return
        self._paused = not self._paused
        self.pause_button.setIcon(FluentIcon.PLAY if self._paused else FluentIcon.PAUSE)
        self.pause_button.setText(t("desktop.resume" if self._paused else "desktop.pause"))
        self._set_run_status("paused" if self._paused else "running")
        self._set_metric_activity("paused" if self._paused else "running")
        self.progress_title.setText(t("desktop.paused" if self._paused else "desktop.resumed"))
        if self._paused:
            self.pause_requested.emit()
        else:
            self.resume_requested.emit()

    def _request_cancel(self):
        if not self._running or self._cancelling:
            return
        self._cancelling = True
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(t("desktop.stopping"))
        self._set_run_status("stopping")
        self._set_metric_activity("stopping")
        self.progress_title.setText(t("desktop.stopping"))
        self.cancel_requested.emit()

    def _set_run_status(self, state: str):
        value_key, icon, accent = {
            "running": ("desktop.running", FluentIcon.SYNC, "#2563EB"),
            "paused": ("desktop.paused", FluentIcon.PAUSE, "#D97706"),
            "stopping": ("desktop.stopping", FluentIcon.CLOSE, "#DC2626"),
            "idle": ("desktop.idle", FluentIcon.POWER_BUTTON, "#64748B"),
        }.get(state, ("desktop.unknown", FluentIcon.INFO, "#64748B"))
        self.status_card.set_visual(icon, accent)
        self.status_card.set_value(t(value_key))

    def _set_metric_activity(self, state: str):
        """Keep dashboard motion tied to real update work, never to passive refreshes."""
        active = state == "running"
        cards = (self.status_card, self.channel_card, self.valid_card, self.service_card)
        for index, card in enumerate(cards):
            card.set_activity(active, delay_ms=index * 140, rotate_icon=card is self.status_card)

    def set_progress(self, title: str, value: int, finished: bool = False, metadata=None, _now=None):
        if self._cancelling:
            return
        self.progress.setValue(max(0, min(100, int(value))))
        if isinstance(metadata, dict) and metadata.get("channel"):
            key = (metadata.get("category"), metadata["channel"])
            self._active_channel = key
            if not self._paused:
                self.progress_title.setText(t("desktop.testing_channel").format(name=metadata["channel"]))
            self._update_runtime_row(key, metadata)
        elif not self._paused and (not self._active_channel or not self._running):
            self.progress_title.setText(title)
        if finished:
            self.last_update_outcome = metadata if isinstance(metadata, dict) else None
            empty = self.last_update_outcome and self.last_update_outcome.get("status") == "empty"
            self.progress_title.setText(t("desktop.update_empty_gui" if empty else "desktop.update_completed_gui"))
            self._apply_runtime_rows()

    def _update_runtime_row(self, key, metadata):
        if metadata.get("status") != "completed":
            return
        runtime_row = {
            "name": metadata.get("channel"),
            "category": metadata.get("category"),
            "display_status": "completed",
            "total_results": metadata.get("total_results", 0),
            "valid_results": metadata.get("valid_count", 0),
            "selected_results": None,
            "best_speed": metadata.get("best_speed"),
            "max_resolution": metadata.get("max_resolution"),
            "best_url": metadata.get("best_url"),
            "playable_results": metadata.get("playable_results", []),
            "updated_at": metadata.get("updated_at"),
            "logo": _channel_logo(metadata.get("channel"), metadata.get("logo")),
        }
        existing = next(
            (index for index, row in enumerate(self._runtime_rows)
             if (row.get("category"), row.get("name")) == key),
            -1,
        )
        if existing < 0:
            self._runtime_rows.append(apply_channel_stream_state(runtime_row, self._stream_states))
        else:
            runtime_row["channel_key"] = self._runtime_rows[existing].get("channel_key")
            self._runtime_rows[existing] = apply_channel_stream_state(runtime_row, self._stream_states)
        self.channel_card.set_value(len(self._runtime_rows))
        self.valid_card.set_value(sum(int(row.get("valid_results") or 0) for row in self._runtime_rows))
        self._apply_runtime_rows()

    def set_service_status(self, status: str):
        self._service_status = status
        label = {
            "running": t("desktop.running"),
            "external": t("desktop.external_service"),
            "stopped": t("desktop.stopped"),
            "failed": t("desktop.unavailable"),
        }.get(status, t("desktop.unknown"))
        self.service_card.set_value(label, self._service_url())

    def set_stream_snapshot(self, snapshot: dict):
        self._stream_snapshot = snapshot
        self._stream_states = build_channel_stream_states(snapshot)
        self._runtime_rows = [
            apply_channel_stream_state(row, self._stream_states)
            for row in self._runtime_rows
        ]
        self._apply_runtime_rows()
        self.set_service_status(self._service_status)

    def _service_url(self) -> str:
        if config.public_url and self._service_status in {"running", "external"}:
            return get_public_url()
        use_rtmp_proxy = (
            self._service_status in {"running", "external"}
            and bool(self._stream_snapshot.get("available"))
        )
        port = config.service_port if use_rtmp_proxy else config.app_port
        return get_public_url(port)

    def _show_stream_menu(self, row: dict, position):
        menu = RoundMenu(parent=self)
        menu.addAction(Action(
            FluentIcon.IOT,
            t("desktop.view_stream_details"),
            self,
            triggered=lambda _checked=False: self.destination_requested.emit("rtmp"),
        ))
        menu.addAction(Action(
            FluentIcon.PAUSE_BOLD.icon(color=QColor("#DC2626")),
            t("desktop.stop_channel_streams"),
            self,
            triggered=lambda _checked=False: self._stop_channel_streams(row),
        ))
        menu.exec(position)

    def _stop_channel_streams(self, row: dict):
        result_keys = list(row.get("stream_result_keys") or [])
        if not result_keys:
            return
        box = warning_message_box(
            t("desktop.stop_channel_streams"),
            t("desktop.stop_channel_streams_confirm").format(
                name=row.get("name") or "--",
                count=len(result_keys),
            ),
            self,
        )
        if box.exec():
            self.stream_control_many_requested.emit("stop", result_keys)

    def _play_channel(self, row):
        url = row.get("best_url")
        results = list(row.get("playable_results") or [])
        if not url and not results and row.get("channel_key"):
            try:
                results = [item for item in list_channel_results(constants.channel_results_path, row["channel_key"]) if item.get("valid") and item.get("url")]
            except Exception:
                results = []
        if not url and results:
            def speed_score(item):
                try:
                    return float(item.get("speed") or 0)
                except (TypeError, ValueError):
                    return 0

            best = max(results, key=speed_score, default=None)
            url = best.get("url") if best else None
        if url:
            play_url(url, self)
        else:
            return

    def _channel_clicked(self, index):
        if self.channel_model.columns[index.column()][0] != "name" or not is_channel_logo_click(self.channel_table, index):
            return
        row = self.channel_model.row(index)
        if not row:
            return
        channel_key = row.get("channel_key")
        if not channel_key:
            try:
                match = next(
                    item for item in list_channels(constants.channel_results_path)
                    if item.get("name") == row.get("name") and item.get("category") == row.get("category")
                )
                channel_key = match["channel_key"]
            except (StopIteration, OSError):
                return
        dialog = ChannelLogoDialog(row, self.logo_loader, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        set_channel_logo(constants.channel_results_path, channel_key, dialog.logo_value())
        self.refresh_metrics()
        InfoBar.success(t("desktop.channel_logo_updated"), row["name"], parent=self, position=InfoBarPosition.TOP)

    def refresh_schedule(self):
        try:
            next_time = next_scheduled_update()
            if next_time is None:
                self.status_card.detail_label.setText(t("desktop.schedule_disabled"))
                return
            self.status_card.detail_label.setText(t("desktop.next_update_time").format(time=next_time.strftime("%Y-%m-%d %H:%M:%S")))
        except Exception:
            self.status_card.detail_label.setText(t("desktop.schedule_unavailable"))

    def retranslate(self):
        self.status_card.title_label.setText(t("desktop.run_status"))
        self.channel_card.title_label.setText(t("desktop.channels"))
        self.valid_card.title_label.setText(t("desktop.valid_results"))
        self.service_card.title_label.setText(t("desktop.service"))
        self.run_button.setText(t("desktop.run_once"))
        self.pause_button.setIcon(FluentIcon.PLAY if self._paused else FluentIcon.PAUSE)
        self.pause_button.setText(t("desktop.resume" if self._paused else "desktop.pause"))
        self.cancel_button.setText(t("desktop.cancel"))
        self.output_button.setText(t("desktop.open_output"))
        self.service_button.setText(t("desktop.browse_results"))
        self.channels_title.setText(t("desktop.channel_result_status"))
        self.channel_search.setPlaceholderText(t("desktop.search_channel_names"))
        self.empty_title.setText(t("desktop.channel_results_empty"))
        self.empty_hint.setText(t("desktop.channel_results_empty_hint"))
        self.configure_sources_button.setText(t("desktop.configure_sources"))
        self.channel_model.set_columns(self._channel_columns())
        for action, key in self.service_actions:
            action.setText(t(key))
        if not self._running and self.progress.value() == 0:
            self.progress_title.setText(t("desktop.ready"))
        if self._cancelling:
            self._set_run_status("stopping")
        elif self._paused:
            self._set_run_status("paused")
            self.progress_title.setText(t("desktop.paused"))
        elif self._running:
            self._set_run_status("running")
        else:
            self._set_run_status("idle")
        self.set_service_status(self._service_status)
        self.refresh_metrics()
        self.set_stream_snapshot(self._stream_snapshot)
        self.refresh_schedule()

    def _open_service_route(self, route: str):
        QDesktopServices.openUrl(QUrl(f"{self._service_url().rstrip('/')}{route}"))

    def open_output(self):
        path = os.path.abspath(constants.output_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
