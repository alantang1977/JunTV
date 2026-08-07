import csv
import datetime
import io
import math
import os
import re

from PySide6.QtCore import QEasingCurve, QEvent, QIODevice, QItemSelectionModel, QPoint, QPropertyAnimation, QRect, QRectF, QSaveFile, QSettings, QSize, QSignalBlocker, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QRubberBand, QSizePolicy, QSplitter, QStackedWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import Action, BodyLabel, CardWidget, ComboBox, DropDownPushButton, FluentIcon, IconWidget, IndeterminateProgressRing, InfoBar, InfoBarPosition, ProgressRing, PushButton, RoundMenu, SegmentedWidget, StrongBodyLabel, TableView, ToolButton, TreeWidget, isDarkTheme, qconfig

import utils.constants as constants
from desktop_ui.models import ChannelLogoLoader, ChannelTableModel, MappingTableModel
from desktop_ui.playback import play_url
from desktop_ui.logo_dialog import ChannelLogoDialog, is_channel_logo_click
from desktop_ui.screenshot_dialog import StreamScreenshotDialog
from desktop_ui.dialogs.source_import import SourceImportDialog
from desktop_ui.stream_status import (
    StreamingStatusDelegate,
    apply_channel_stream_state,
    apply_result_stream_state,
    build_channel_stream_states,
    build_result_stream_states,
)
from desktop_ui.widgets import AccentPushButton, AppEditableComboBox, AppLineEdit, AppSearchLineEdit, ContinuousTreeItemDelegate, DangerPushButton, TableCheckBoxDelegate, TableCheckBoxHeader, apply_dialog_theme, configure_table_columns, localize_dialog_buttons, warning_message_box
from utils.channel import write_channel_to_file
from utils.channel_repository import add_manual_result, delete_channel_records, delete_channel_results, get_channel, list_categories, list_channel_results, list_channels, list_result_urls_by_channel, load_selected_snapshot, reset_channel_selection, set_channel_logo, set_channel_selection, upsert_manual_channel
from utils.config import config, resource_path
from utils.i18n import t
from utils.local_source_importer import _decode, parse_local_source_file
from utils.tools import check_url_by_keywords, get_public_url, get_urls_from_file
from utils.user_actions import add_channel, add_manual_channel_result, add_to_blacklist, add_to_whitelist, delete_channels, delete_manual_channel_results
from utils.whitelist import is_url_whitelisted, load_whitelist_maps


def _health(value, _):
    return {
        "healthy": t("desktop.health_healthy"),
        "warning": t("desktop.health_warning"),
        "offline": t("desktop.health_offline"),
        "unknown": t("desktop.health_unknown"),
    }.get(value, value or "--")


def _speed(value, _):
    return "--" if value is None else f"{float(value):.2f} M/s"


def _delay(value, _):
    return "--" if value is None else f"{float(value):.0f} ms"


def _origin(value, _):
    return t(f"name.{value}", value or "--")


def _updated_at(value, _):
    return "--" if not value else datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")


def _channel_name(value, row):
    name = str(value or "--")
    if row.get("operation_state") == "testing":
        name = f"{name} · {t('desktop.status_testing')}"
    if not row.get("streaming"):
        return name
    status = (
        t("desktop.stream_starting_badge")
        if row.get("stream_indicator_state") == "starting"
        else t("desktop.stream_running_badge")
    )
    return f"{name} · {status}"


def _stream_status(value, _):
    return {
        "active": t("desktop.stream_running_badge"),
        "starting": t("desktop.stream_starting_badge"),
        "idle": t("desktop.not_streaming"),
    }.get(value, t("desktop.not_streaming"))


def _category_label(value):
    label = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff]+", "", str(value or "")).strip()
    return label or str(value or "")


def _template_category_order():
    try:
        with open(resource_path(config.source_file), "r", encoding="utf-8") as file:
            lines = file
            categories = []
            seen = set()
            for raw in lines:
                match = re.match(r"^(.*?)[,，]\s*#genre#\s*$", raw.strip())
                if not match:
                    continue
                category = match.group(1).strip()
                if category and category not in seen:
                    seen.add(category)
                    categories.append(category)
            return categories
    except OSError:
        return []


class RubberBandTableView(TableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_origin = QPoint()
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self._rubber_band.setGeometry(QRect(self._drag_origin, QSize()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            rect = QRect(self._drag_origin, event.position().toPoint()).normalized()
            if rect.width() + rect.height() >= QApplication.startDragDistance():
                self._rubber_band.setGeometry(rect)
                self._rubber_band.show()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._rubber_band.hide()


class ResultDrawerCard(CardWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor("#202020" if isDarkTheme() else "#FFFFFF")
        border = QColor("#3A3A3A" if isDarkTheme() else "#D9DFE7")
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        radius = self.getBorderRadius()
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), radius, radius)


class DrawerResizeHandle(QWidget):
    resize_requested = Signal(int)
    resize_finished = Signal()
    full_screen_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_global_y = None
        self.setFixedHeight(12)
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#666666" if isDarkTheme() else "#A8B0BB")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        rect = QRectF((self.width() - 42) / 2, 4, 42, 4)
        painter.drawRoundedRect(rect, 2, 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_global_y = int(event.globalPosition().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._last_global_y is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            global_y = int(event.globalPosition().y())
            self.resize_requested.emit(self._last_global_y - global_y)
            self._last_global_y = global_y
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._last_global_y is not None
        ):
            self._last_global_y = None
            self.resize_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.full_screen_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ChannelCenterPage(QWidget):
    retest_channel_requested = Signal(dict)
    retest_result_requested = Signal(dict)
    retest_results_requested = Signal(list)
    capture_screenshot_requested = Signal(dict)
    capture_screenshots_requested = Signal(list)
    playback_workspace_requested = Signal(dict)
    playback_batch_requested = Signal(list)
    stream_monitor_requested = Signal()
    stream_control_many_requested = Signal(str, list)

    def __init__(self, parent=None, logo_loader: ChannelLogoLoader | None = None):
        super().__init__(parent)
        self.setObjectName("channelCenterPage")
        self._task_operation = None
        self._task_name = None
        self._task_context_queue = []
        self._task_context = "page"
        self._drawer_channel_key = None
        try:
            self._drawer_height = int(
                QSettings().value("appearance/channel_result_drawer_height", 360)
            )
        except (TypeError, ValueError):
            self._drawer_height = 360
        self._drawer_height = max(250, self._drawer_height)
        self._drawer_fullscreen = False
        self._screenshot_dialog = None
        self._checked_channel_keys = set()
        self._checked_result_keys = set()
        self._result_filter = "all"
        self._result_search_query = ""
        self._result_operation_states = {}
        self._channel_operation_states = set()
        self._all_result_rows = []
        self._stream_snapshot = {"streams": []}
        self._stream_states = {}
        self._stream_result_states = {}
        self._suppress_channel_click = False
        self._view_mode = str(QSettings().value("appearance/channel_center_view", "category"))
        if self._view_mode not in {"category", "list"}:
            self._view_mode = "category"
        self._category_order = []
        self._category_filter = None
        self._health_filter = None
        self._streaming_filter = False
        self._category_items = {}
        self._smart_items = {}
        self.view_switch = SegmentedWidget(self)
        self.view_switch.addItem("category", t("desktop.channel_view_category"), icon=FluentIcon.FOLDER)
        self.view_switch.addItem("list", t("desktop.channel_view_list"), icon=FluentIcon.MENU)
        self.view_switch.setMinimumWidth(210)
        self.view_switch.setCurrentItem(self._view_mode)
        self.category_selector = ComboBox(self)
        self.category_selector.setMinimumWidth(170)
        self.search = AppSearchLineEdit(self)
        self.search.setPlaceholderText(t("desktop.search_channels"))
        self.refresh_button = ToolButton(FluentIcon.SYNC, self)
        self.refresh_button.setToolTip(t("desktop.refresh"))
        self.import_button = DropDownPushButton(FluentIcon.FOLDER, t("desktop.import_files"), self)
        self.export_button = DropDownPushButton(FluentIcon.DOCUMENT, t("desktop.export"), self)
        self.add_channel_button = PushButton(FluentIcon.ADD, t("desktop.add_channel"), self)
        self.add_channel_button.hide()
        self.add_result_button = PushButton(FluentIcon.LINK, t("desktop.add_result"), self)
        self.add_result_button.hide()
        self.delete_channel_button = DangerPushButton(FluentIcon.DELETE, t("desktop.delete_channel"), self)
        self.delete_channel_button.hide()
        self.retest_channel_button = AccentPushButton(FluentIcon.SPEED_HIGH, t("desktop.retest_channel"), self)
        self.retest_channel_button.hide()
        self.play_selected_button = AccentPushButton(FluentIcon.PLAY, t("desktop.play"), self)
        self.play_selected_button.hide()
        self.stop_stream_button = DangerPushButton(FluentIcon.PAUSE_BOLD, t("desktop.stop_stream"), self)
        self.stop_stream_button.hide()
        self.stream_selected_button = PushButton(FluentIcon.VIDEO, t("desktop.open_selected_streams"), self)
        self.stream_selected_button.hide()
        self.channel_more_button = DropDownPushButton(FluentIcon.MORE, t("desktop.more_actions"), self)
        self.selection_label = BodyLabel("", self)
        self.selection_label.hide()
        self.task_label = BodyLabel("", self)
        self.task_progress = ProgressRing(self, useAni=False)
        self.task_percent_label = BodyLabel("0%", self)
        self.task_label.hide()
        self.task_progress.hide()
        self.task_percent_label.hide()

        self.channel_model = ChannelTableModel(
            self._channel_columns(), self, checkable_key="batch_selected", logo_loader=logo_loader
        )
        self.logo_loader = self.channel_model.logo_loader
        self.result_model = MappingTableModel(self._result_columns(), self, checkable_key="batch_selected")
        self.channel_table = self._table(self.channel_model, multiple=True)
        self.channel_header = TableCheckBoxHeader(self.channel_table)
        self.channel_table.setHorizontalHeader(self.channel_header)
        self.channel_header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        configure_table_columns(
            self.channel_table,
            [42, 190, 78, 68, 68, 96, 78, 78, 158],
            "channel_center.channels.v2",
            fixed_widths={0: 42},
            minimum_widths={1: 175, 2: 68, 3: 58, 4: 58, 5: 90, 8: 158},
        )
        self.channel_table.setIconSize(QSize(32, 24))
        self.channel_table.setItemDelegateForColumn(0, TableCheckBoxDelegate(self.channel_table))
        self.stream_status_delegate = StreamingStatusDelegate(self._show_stream_menu, self.channel_table)
        self.channel_table.setItemDelegateForColumn(1, self.stream_status_delegate)
        self.channel_header.toggled.connect(self._toggle_all_channels)
        self.channel_model.modelReset.connect(self._update_channel_header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self.view_switch)
        toolbar.addWidget(self.category_selector)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.selection_label)
        toolbar.addWidget(self.play_selected_button)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.retest_channel_button)
        toolbar.addWidget(self.channel_more_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(toolbar)
        self.task_row = QWidget(self)
        self.task_row.setObjectName("channelTaskRow")
        task_row = QHBoxLayout(self.task_row)
        task_row.setContentsMargins(10, 5, 10, 5)
        task_row.setSpacing(10)
        self.task_icon = IndeterminateProgressRing(self.task_row, start=False)
        self.task_icon.setFixedSize(16, 16)
        self.task_icon.setStrokeWidth(2)
        self.task_icon.setCustomBarColor("#2563EB", "#60A5FA")
        task_row.addWidget(self.task_icon)
        task_row.addWidget(self.task_label)
        self.task_progress.setFixedSize(26, 26)
        self.task_progress.setStrokeWidth(3)
        self.task_progress.setTextVisible(False)
        self.task_progress.setCustomBarColor("#2563EB", "#60A5FA")
        task_row.addWidget(self.task_progress)
        task_row.addWidget(self.task_percent_label)
        task_row.addStretch(1)
        self.task_row.hide()
        layout.addWidget(self.task_row)

        self.channel_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.category_sidebar = self._create_category_sidebar()
        self.channel_splitter.addWidget(self.category_sidebar)
        self.channel_stack = QStackedWidget(self.channel_splitter)
        self.channel_stack.addWidget(self.channel_table)
        self.empty_state = self._create_empty_state()
        self.channel_stack.addWidget(self.empty_state)
        self.channel_splitter.addWidget(self.channel_stack)
        self.channel_splitter.setCollapsible(0, False)
        self.channel_splitter.setStretchFactor(0, 0)
        self.channel_splitter.setStretchFactor(1, 1)
        category_width = max(190, min(320, int(QSettings().value("appearance/channel_category_width", 228))))
        self.channel_splitter.setSizes([category_width, 900])
        self.channel_splitter.splitterMoved.connect(self._save_category_width)
        layout.addWidget(self.channel_splitter, 1)

        self._create_result_drawer()
        self._set_task_label_style()
        self.view_switch.currentItemChanged.connect(self._set_view_mode)
        self.refresh_button.clicked.connect(self.reload)
        self.category_selector.currentIndexChanged.connect(self._category_changed)
        self.search.textChanged.connect(self._search_changed)
        self.channel_table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.channel_table.selectionModel().currentChanged.connect(self._current_channel_changed)
        self.channel_model.dataChanged.connect(self._channel_data_changed)
        self.channel_table.clicked.connect(self._channel_clicked)
        self.channel_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.channel_table.customContextMenuRequested.connect(self._show_channel_menu)
        self.add_channel_button.clicked.connect(self._add_channel)
        self.add_result_button.clicked.connect(self._add_manual_result)
        self.delete_channel_button.clicked.connect(self._delete_selected_channels)
        self.retest_channel_button.clicked.connect(self._request_channel_retest)
        self.play_selected_button.clicked.connect(self._play_selected_channels)
        self.stop_stream_button.clicked.connect(self._stop_selected_channel_streams)
        self.stream_selected_button.clicked.connect(self._open_selected_in_playback)
        QApplication.instance().installEventFilter(self)
        self._apply_view_mode()
        self.reload()

    def _create_category_sidebar(self):
        sidebar = CardWidget(self)
        sidebar.setObjectName("channelCategorySidebar")
        sidebar.setMinimumWidth(190)
        sidebar.setMaximumWidth(320)
        sidebar.setBorderRadius(8)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self.category_heading = StrongBodyLabel(t("desktop.channel_categories"), sidebar)
        layout.addWidget(self.category_heading)
        self.category_tree = self._directory_tree(sidebar)
        self.category_tree.itemClicked.connect(self._category_item_clicked)
        layout.addWidget(self.category_tree, 1)

        self.smart_heading = StrongBodyLabel(t("desktop.smart_collections"), sidebar)
        layout.addWidget(self.smart_heading)
        self.smart_tree = self._directory_tree(sidebar)
        self.smart_tree.setMinimumHeight(210)
        self.smart_tree.setMaximumHeight(240)
        self.smart_tree.itemClicked.connect(self._smart_item_clicked)
        layout.addWidget(self.smart_tree)
        return sidebar

    @staticmethod
    def _directory_tree(parent):
        tree = TreeWidget(parent)
        tree.setColumnCount(2)
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(False)
        tree.setIndentation(0)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setBorderVisible(False)
        tree.setItemDelegate(ContinuousTreeItemDelegate(tree))
        tree.setStyleSheet(
            """
            QTreeWidget, QTreeWidget::viewport { background-color: transparent; border: none; }
            QTreeWidget::item, QTreeWidget::item:selected { background-color: transparent; margin: 0; }
            """
        )
        tree.setAutoFillBackground(False)
        tree.viewport().setAutoFillBackground(False)
        qconfig.themeChanged.connect(lambda *_: tree.viewport().update())
        qconfig.themeChangedFinished.connect(lambda *_: tree.viewport().update())
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        return tree

    def _create_empty_state(self):
        state = QWidget(self)
        layout = QVBoxLayout(state)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(10)
        layout.addStretch(1)
        icon = IconWidget(FluentIcon.SEARCH.icon(color=QColor("#64748B")), state)
        icon.setFixedSize(44, 44)
        self.empty_title = StrongBodyLabel(t("desktop.channel_center_empty"), state)
        self.empty_hint = BodyLabel(t("desktop.channel_center_empty_hint"), state)
        self.empty_clear_button = PushButton(FluentIcon.CANCEL, t("desktop.clear_filters"), state)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_clear_button.clicked.connect(self._clear_channel_filters)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.empty_title)
        layout.addWidget(self.empty_hint)
        layout.addWidget(self.empty_clear_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return state

    def _create_result_drawer(self):
        self.result_drawer = ResultDrawerCard(self)
        self.result_drawer.setObjectName("resultDrawer")
        self.result_drawer.setBorderRadius(12)
        drawer_layout = QVBoxLayout(self.result_drawer)
        drawer_layout.setContentsMargins(18, 2, 18, 16)
        drawer_layout.setSpacing(6)
        self.drawer_resize_handle = DrawerResizeHandle(self.result_drawer)
        self.drawer_resize_handle.setAccessibleName(t("desktop.resize_result_drawer"))
        self.drawer_resize_handle.setToolTip(t("desktop.resize_result_drawer_hint"))
        drawer_layout.addWidget(self.drawer_resize_handle)
        header = QHBoxLayout()
        self.results_title = BodyLabel(t("desktop.results"), self.result_drawer)
        self.result_search = AppSearchLineEdit(self.result_drawer)
        self.result_search.setPlaceholderText(t("desktop.search_results"))
        self.result_search.setMinimumWidth(180)
        self.result_filter = ComboBox(self.result_drawer)
        self.result_filter.setMinimumWidth(120)
        self.result_filter.addItems([
            t("desktop.result_filter_all"),
            t("desktop.result_filter_untested"),
            t("desktop.result_filter_tested"),
            t("desktop.result_filter_valid"),
            t("desktop.result_filter_unavailable"),
            t("desktop.result_filter_selected"),
        ])
        self.play_button = AccentPushButton(FluentIcon.PLAY, t("desktop.play"), self.result_drawer)
        self.retest_result_button = AccentPushButton(FluentIcon.SPEED_HIGH, t("desktop.retest_result"), self.result_drawer)
        self.screenshot_button = PushButton(FluentIcon.PHOTO, t("desktop.preview_screenshot"), self.result_drawer)
        self.stream_button = PushButton(FluentIcon.VIDEO, t("desktop.open_selected_streams"), self.result_drawer)
        self.stop_result_stream_button = DangerPushButton(
            FluentIcon.PAUSE_BOLD,
            t("desktop.stop_stream"),
            self.result_drawer,
        )
        self.stream_button.hide()
        self.stop_result_stream_button.hide()
        self.copy_button = DropDownPushButton(FluentIcon.COPY, t("desktop.copy_url"), self.result_drawer)
        self.more_button = DropDownPushButton(FluentIcon.MORE, t("desktop.more_actions"), self.result_drawer)
        self.fullscreen_drawer_button = ToolButton(FluentIcon.FULL_SCREEN, self.result_drawer)
        self.close_drawer_button = ToolButton(FluentIcon.CLOSE, self.result_drawer)
        header.addWidget(self.results_title)
        header.addStretch(1)
        header.addWidget(self.result_search)
        header.addWidget(self.result_filter)
        header.addWidget(self.play_button)
        header.addWidget(self.retest_result_button)
        header.addWidget(self.screenshot_button)
        header.addWidget(self.copy_button)
        header.addWidget(self.more_button)
        header.addWidget(self.fullscreen_drawer_button)
        header.addWidget(self.close_drawer_button)
        drawer_layout.addLayout(header)
        self.drawer_task_row = QWidget(self.result_drawer)
        self.drawer_task_row.setObjectName("drawerTaskRow")
        drawer_task_layout = QHBoxLayout(self.drawer_task_row)
        drawer_task_layout.setContentsMargins(8, 4, 8, 4)
        drawer_task_layout.setSpacing(10)
        self.drawer_task_icon = IndeterminateProgressRing(
            self.drawer_task_row,
            start=False,
        )
        self.drawer_task_icon.setFixedSize(16, 16)
        self.drawer_task_icon.setStrokeWidth(2)
        self.drawer_task_icon.setCustomBarColor("#2563EB", "#60A5FA")
        self.drawer_task_label = BodyLabel("", self.drawer_task_row)
        self.drawer_task_progress = ProgressRing(self.drawer_task_row, useAni=False)
        self.drawer_task_progress.setFixedSize(26, 26)
        self.drawer_task_progress.setStrokeWidth(3)
        self.drawer_task_progress.setTextVisible(False)
        self.drawer_task_progress.setCustomBarColor("#2563EB", "#60A5FA")
        self.drawer_task_percent_label = BodyLabel("0%", self.drawer_task_row)
        drawer_task_layout.addWidget(self.drawer_task_icon)
        drawer_task_layout.addWidget(self.drawer_task_label)
        drawer_task_layout.addWidget(self.drawer_task_progress)
        drawer_task_layout.addWidget(self.drawer_task_percent_label)
        drawer_task_layout.addStretch(1)
        self.drawer_task_row.hide()
        drawer_layout.addWidget(self.drawer_task_row)
        self.result_table = self._table(self.result_model, multiple=True)
        self.result_header = TableCheckBoxHeader(self.result_table)
        self.result_table.setHorizontalHeader(self.result_header)
        self.result_header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        configure_table_columns(
            self.result_table,
            [42, 90, 95, 85, 105, 105, 105, 85, 95, 250, 72],
            "channel_center.results",
            fixed_widths={0: 42},
        )
        result_header = self.result_table.horizontalHeader()
        host_column = next(
            index
            for index, column in enumerate(self.result_model.columns)
            if column[0] == "host"
        )
        result_header.moveSection(result_header.visualIndex(host_column), 1)
        stream_column = next(
            index
            for index, column in enumerate(self.result_model.columns)
            if column[0] == "stream_state"
        )
        output_column = next(
            index
            for index, column in enumerate(self.result_model.columns)
            if column[0] == "output_selected"
        )
        result_header.moveSection(
            result_header.visualIndex(stream_column),
            max(1, result_header.visualIndex(output_column) - 1),
        )
        self.result_table.setItemDelegateForColumn(0, TableCheckBoxDelegate(self.result_table))
        self.result_header.toggled.connect(self._toggle_all_results)
        self.result_model.modelReset.connect(self._update_result_header)
        self.result_table.selectionModel().selectionChanged.connect(self._result_selection_changed)
        self.result_table.selectionModel().currentChanged.connect(self._result_selection_changed)
        drawer_layout.addWidget(self.result_table, 1)
        self.result_drawer.hide()
        self.drawer_animation = QPropertyAnimation(self.result_drawer, b"geometry", self)
        self.drawer_animation.setDuration(180)
        self.drawer_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._drawer_hiding = False
        self.drawer_animation.finished.connect(self._drawer_animation_finished)
        self.play_button.clicked.connect(self._open_result)
        self.retest_result_button.clicked.connect(self._request_result_retest)
        self.result_filter.currentIndexChanged.connect(self._result_filter_changed)
        self.result_search.textChanged.connect(self._result_search_changed)
        self.screenshot_button.clicked.connect(self._preview_result_screenshot)
        self.stream_button.clicked.connect(self._start_selected_result_streams)
        self.stop_result_stream_button.clicked.connect(self._stop_selected_result_streams)
        self.fullscreen_drawer_button.clicked.connect(self._toggle_result_drawer_fullscreen)
        self.close_drawer_button.clicked.connect(self.hide_result_drawer)
        self.drawer_resize_handle.resize_requested.connect(self._resize_result_drawer)
        self.drawer_resize_handle.resize_finished.connect(self._save_result_drawer_height)
        self.drawer_resize_handle.full_screen_requested.connect(self._toggle_result_drawer_fullscreen)
        self.result_table.doubleClicked.connect(lambda _: self._open_result())
        self.result_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(self._show_result_menu)
        self.result_model.dataChanged.connect(self._result_data_changed)
        self._create_menus()
        self._update_drawer_mode_button()
        self._update_drawer_style()
        self._update_result_actions()

    def _create_menus(self):
        self.import_channels_action = Action(
            FluentIcon.LAYOUT,
            t("desktop.import_channels"),
            self,
            triggered=self._import_channels,
        )
        self.import_playback_sources_action = Action(
            FluentIcon.FOLDER,
            t("desktop.import_playback_sources"),
            self,
            triggered=self._import_playback_sources,
        )
        self.import_menu = RoundMenu(parent=self)
        self.import_menu.addAction(self.import_channels_action)
        self.import_menu.addAction(self.import_playback_sources_action)
        self.import_button.setMenu(self.import_menu)

        self.export_channel_template_action = Action(
            FluentIcon.LAYOUT,
            t("desktop.export_channel_template"),
            self,
            triggered=self._export_channel_template,
        )
        self.export_playback_sources_action = Action(
            FluentIcon.FOLDER,
            t("desktop.export_playback_sources"),
            self,
            triggered=self._export_playback_sources,
        )
        self.export_diagnostics_action = Action(
            FluentIcon.DOCUMENT,
            t("desktop.export_diagnostics_csv"),
            self,
            triggered=self._export_channel_diagnostics,
        )
        self.export_menu = RoundMenu(parent=self)
        self.export_menu.addAction(self.export_channel_template_action)
        self.export_menu.addAction(self.export_playback_sources_action)
        self.export_menu.addAction(self.export_diagnostics_action)
        self.export_button.setMenu(self.export_menu)

        self.copy_action = Action(FluentIcon.COPY, t("desktop.copy_source_url"), self, triggered=self._copy_result)
        self.drawer_add_result_action = Action(FluentIcon.ADD, t("desktop.add_result"), self, triggered=self._add_manual_result)
        self.copy_stream_action = Action(FluentIcon.LINK, t("desktop.copy_stream_url"), self, triggered=self._copy_stream_url)
        self.play_result_action = Action(FluentIcon.PLAY, t("desktop.play"), self, triggered=self._open_result)
        self.retest_result_action = Action(FluentIcon.SPEED_HIGH, t("desktop.retest_result"), self, triggered=self._request_result_retest)
        self.include_output_action = Action(FluentIcon.ADD, t("desktop.include_output"), self, triggered=self._include_selected_output)
        self.exclude_output_action = Action(FluentIcon.REMOVE_FROM, t("desktop.exclude_output"), self, triggered=self._exclude_selected_output)
        self.pin_output_action = Action(FluentIcon.UP, t("desktop.pin_output"), self, triggered=self._pin_selected_output)
        self.auto_output_action = Action(FluentIcon.SYNC, t("desktop.auto_select_output"), self, triggered=self._restore_auto_output)
        self.whitelist_action = Action(FluentIcon.ADD_TO, t("desktop.add_whitelist"), self, triggered=self._add_whitelist)
        self.blacklist_action = Action(FluentIcon.REMOVE_FROM, t("desktop.add_blacklist"), self, triggered=self._add_blacklist)
        self.preview_screenshot_action = Action(FluentIcon.PHOTO, t("desktop.preview_screenshot"), self, triggered=self._preview_result_screenshot)
        self.capture_screenshot_action = Action(FluentIcon.SYNC, t("desktop.refresh_screenshot"), self, triggered=self._request_result_screenshot)
        self.start_result_stream_action = Action(
            FluentIcon.VIDEO,
            t("desktop.open_selected_streams"),
            self,
            triggered=self._start_selected_result_streams,
        )
        self.stop_result_stream_action = Action(
            FluentIcon.PAUSE_BOLD.icon(color=QColor("#DC2626")),
            t("desktop.stop_stream"),
            self,
            triggered=self._stop_selected_result_streams,
        )
        self.delete_result_action = Action(
            FluentIcon.DELETE.icon(color=QColor("#DC2626")),
            t("desktop.delete_result"),
            self,
            triggered=self._delete_selected_results,
        )
        self.copy_menu = RoundMenu(parent=self)
        self.copy_menu.addAction(self.copy_action)
        self.copy_menu.addAction(self.copy_stream_action)
        self.copy_button.setMenu(self.copy_menu)

        # Keep the full set available from the result context menu. The
        # drawer's More menu only contains actions that are not exposed in
        # the header.
        self.result_menu = RoundMenu(parent=self)
        for action in (
            self.play_result_action,
            self.retest_result_action,
            self.include_output_action,
            self.exclude_output_action,
            self.pin_output_action,
            self.auto_output_action,
            self.preview_screenshot_action,
            self.capture_screenshot_action,
            self.copy_action,
            self.copy_stream_action,
            self.whitelist_action,
            self.blacklist_action,
            self.start_result_stream_action,
            self.stop_result_stream_action,
            self.delete_result_action,
        ):
            self.result_menu.addAction(action)
        self.result_more_menu = RoundMenu(parent=self)
        for action in (
            self.drawer_add_result_action,
            self.start_result_stream_action,
            self.stop_result_stream_action,
            self.capture_screenshot_action,
            self.whitelist_action,
            self.blacklist_action,
            self.include_output_action,
            self.exclude_output_action,
            self.pin_output_action,
            self.auto_output_action,
            self.delete_result_action,
        ):
            self.result_more_menu.addAction(action)
        self.more_button.setMenu(self.result_more_menu)

        self.channel_retest_action = Action(FluentIcon.SPEED_HIGH, t("desktop.retest_channel"), self, triggered=self._request_channel_retest)
        self.channel_add_action = Action(FluentIcon.ADD, t("desktop.add_channel"), self, triggered=self._add_channel)
        self.channel_add_result_action = Action(FluentIcon.LINK, t("desktop.add_result"), self, triggered=self._add_manual_result)
        self.channel_edit_logo_action = Action(FluentIcon.PHOTO, t("desktop.edit_channel_logo"), self, triggered=self._edit_channel_logo)
        self.channel_play_action = Action(FluentIcon.PLAY, t("desktop.play"), self, triggered=self._play_selected_channels)
        self.channel_delete_action = Action(FluentIcon.DELETE.icon(color=QColor("#DC2626")), t("desktop.delete_channel"), self, triggered=self._delete_selected_channels)
        self.channel_stream_action = Action(FluentIcon.VIDEO, t("desktop.open_selected_streams"), self, triggered=self._open_selected_in_playback)
        self.channel_stop_stream_action = Action(
            FluentIcon.PAUSE_BOLD.icon(color=QColor("#DC2626")),
            t("desktop.stop_stream"),
            self,
            triggered=self._stop_selected_channel_streams,
        )
        self.channel_more_menu = RoundMenu(parent=self)
        for action in (
            self.channel_retest_action,
            self.channel_add_action,
            self.channel_add_result_action,
            self.channel_stream_action,
            self.channel_stop_stream_action,
            self.channel_delete_action,
        ):
            self.channel_more_menu.addAction(action)
        self.channel_more_button.setMenu(self.channel_more_menu)

        self.channel_menu = RoundMenu(parent=self)
        for action in (
            self.channel_add_action,
            self.channel_play_action,
            self.channel_retest_action,
            self.channel_add_result_action,
            self.channel_stream_action,
            self.channel_stop_stream_action,
            self.channel_edit_logo_action,
            self.channel_delete_action,
        ):
            self.channel_menu.addAction(action)

    @staticmethod
    def _channel_columns():
        return [
            ("batch_selected", "", None),
            ("name", t("name.channel"), _channel_name),
            ("health", t("desktop.status"), _health),
            ("valid_results", t("desktop.column_valid"), None),
            ("total_results", t("desktop.column_results"), None),
            ("category", t("desktop.column_category"), None),
            ("whitelist_count", t("desktop.column_whitelist"), None),
            ("blacklist_count", t("desktop.column_blacklist"), None),
            ("updated_at", t("desktop.column_updated"), _updated_at),
        ]

    @staticmethod
    def _result_columns():
        return [
            ("batch_selected", "", None),
            ("valid", t("desktop.status"), lambda value, row: (
                t("desktop.status_testing")
                if row.get("operation_state") == "testing"
                else t("desktop.untested")
                if row.get("test_state") == "untested"
                else t(
                    f"status.{row.get('test_status')}",
                    t("name.valid") if value else t("desktop.unavailable"),
                )
            )),
            ("stream_state", t("desktop.stream_status"), _stream_status),
            ("speed", t("desktop.column_speed"), _speed),
            ("delay", t("desktop.column_delay"), _delay),
            ("resolution", t("desktop.column_resolution"), None),
            ("screenshot_status", t("desktop.screenshot"), lambda value, _: t(f"desktop.screenshot_{value or 'not_captured'}", value or "--")),
            ("ipv_type", t("desktop.column_protocol"), None),
            ("origin", t("name.from"), _origin),
            ("host", t("desktop.host"), None),
            ("output_selected", t("desktop.output"), lambda value, _: t("desktop.yes") if value else t("desktop.no")),
        ]

    @staticmethod
    def _is_valid_result(row: dict | None) -> bool:
        if not row or not row.get("url"):
            return False
        value = row.get("valid")
        return value is True or value == 1 or str(value).strip().lower() in {
            "1",
            "true",
            "yes",
        }

    @classmethod
    def _best_valid_result(cls, rows: list[dict]) -> dict | None:
        candidates = [row for row in rows if cls._is_valid_result(row)]
        if not candidates:
            return None

        def sort_key(row):
            rank = row.get("selected_rank")
            try:
                rank = float(rank)
            except (TypeError, ValueError):
                rank = math.inf
            if not math.isfinite(rank):
                rank = math.inf

            speed = row.get("speed")
            try:
                speed = float(speed)
            except (TypeError, ValueError):
                speed = -math.inf
            if not math.isfinite(speed):
                speed = -math.inf

            delay = row.get("delay")
            try:
                delay = float(delay)
            except (TypeError, ValueError):
                delay = math.inf
            if not math.isfinite(delay) or delay < 0:
                delay = math.inf
            return rank == math.inf, rank, -speed, delay

        return min(candidates, key=sort_key)

    def _best_channel_playback_result(self, channel: dict) -> dict | None:
        best_url = str(channel.get("best_url") or "").strip()
        if best_url:
            return {
                "channel_key": channel.get("channel_key"),
                "url": best_url,
                "valid": 1,
            }
        try:
            rows = list_channel_results(
                constants.channel_results_path,
                channel.get("channel_key"),
            )
        except Exception:
            rows = []
        return self._best_valid_result(rows)

    def _table(self, model, multiple=False):
        table = RubberBandTableView(self)
        table.setModel(model)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection if multiple
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        table.setBorderVisible(True)
        table.setBorderRadius(8)
        return table

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.result_drawer.isVisible() and self.drawer_animation.state() != QPropertyAnimation.State.Running:
            self.result_drawer.setGeometry(self._drawer_geometry())

    def _drawer_geometry(self):
        if self._drawer_fullscreen:
            return self.rect()
        horizontal_margin = 12
        bottom_margin = 8
        maximum_height = max(250, self.height() - 28)
        height = min(maximum_height, max(250, self._drawer_height))
        return QRect(
            horizontal_margin,
            self.height() - height - bottom_margin,
            max(320, self.width() - horizontal_margin * 2),
            height,
        )

    def _resize_result_drawer(self, delta: int):
        if not delta:
            return
        if self._drawer_fullscreen:
            self._drawer_fullscreen = False
            self.result_drawer.setBorderRadius(12)
            self._drawer_height = max(250, self.height() - 28)
            self._update_drawer_mode_button()
        maximum_height = max(250, self.height() - 28)
        self._drawer_height = min(
            maximum_height,
            max(250, self._drawer_height + int(delta)),
        )
        self.drawer_animation.stop()
        self.result_drawer.setGeometry(self._drawer_geometry())

    def _save_result_drawer_height(self):
        if not self._drawer_fullscreen:
            QSettings().setValue(
                "appearance/channel_result_drawer_height",
                self._drawer_height,
            )

    def _toggle_result_drawer_fullscreen(self):
        self._drawer_fullscreen = not self._drawer_fullscreen
        self.result_drawer.setBorderRadius(0 if self._drawer_fullscreen else 12)
        self.drawer_animation.stop()
        self.result_drawer.setGeometry(self._drawer_geometry())
        self.result_drawer.raise_()
        self._update_drawer_mode_button()

    def _update_drawer_mode_button(self):
        if self._drawer_fullscreen:
            self.fullscreen_drawer_button.setIcon(FluentIcon.MINIMIZE)
            text = t("desktop.restore_result_drawer")
        else:
            self.fullscreen_drawer_button.setIcon(FluentIcon.FULL_SCREEN)
            text = t("desktop.fullscreen_result_drawer")
        self.fullscreen_drawer_button.setToolTip(text)
        self.fullscreen_drawer_button.setAccessibleName(text)

    def show_result_drawer(self):
        target = self._drawer_geometry()
        self._update_drawer_style()
        if self.result_drawer.isVisible() and not self._drawer_hiding:
            self.drawer_animation.stop()
            self.result_drawer.setGeometry(target)
            self.result_drawer.raise_()
            return
        start = QRect(target.x(), self.height() + 4, target.width(), target.height())
        self.result_drawer.setGeometry(start)
        self.result_drawer.show()
        self.result_drawer.raise_()
        self._drawer_hiding = False
        self.drawer_animation.stop()
        self.drawer_animation.setStartValue(start)
        self.drawer_animation.setEndValue(target)
        self.drawer_animation.start()

    def hide_result_drawer(self):
        if not self.result_drawer.isVisible():
            return
        self.drawer_animation.stop()
        self._drawer_hiding = True
        self.drawer_animation.setStartValue(self.result_drawer.geometry())
        self.drawer_animation.setEndValue(
            QRect(
                self.result_drawer.x(),
                self.height() + 4,
                self.result_drawer.width(),
                self.result_drawer.height(),
            )
        )
        self.drawer_animation.start()

    def _drawer_animation_finished(self):
        if self._drawer_hiding:
            self.result_drawer.hide()
            self._drawer_hiding = False

    def _update_drawer_style(self):
        self.result_drawer.update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.hide_result_drawer()
        if event.type() == QEvent.Type.MouseButtonPress and self.result_drawer.isVisible():
            widget = watched if isinstance(watched, QWidget) else None
            if (
                not self._event_position_in_drawer(event)
                and widget
                and not self._is_drawer_interaction_widget(widget)
            ):
                self.hide_result_drawer()
        return super().eventFilter(watched, event)

    def _event_position_in_drawer(self, event):
        global_position = getattr(event, "globalPosition", None)
        if not callable(global_position):
            return False
        point = self.result_drawer.mapFromGlobal(global_position().toPoint())
        return self.result_drawer.rect().contains(point)

    def _is_drawer_interaction_widget(self, widget):
        if (
            self._is_child_of(widget, self.result_drawer)
            or self._is_child_of(widget, self.channel_table)
        ):
            return True
        return widget.window() is not self.window()

    @staticmethod
    def _is_child_of(widget, parent):
        current = widget
        while current:
            if current is parent:
                return True
            current = current.parentWidget()
        return False

    def _set_view_mode(self, mode: str):
        if mode not in {"list", "category"} or mode == self._view_mode:
            return
        if self.view_switch.currentRouteKey() != mode:
            blocker = QSignalBlocker(self.view_switch)
            self.view_switch.setCurrentItem(mode)
            del blocker
        self._view_mode = mode
        QSettings().setValue("appearance/channel_center_view", mode)
        self.hide_result_drawer()
        self._apply_view_mode()
        self._load_channels()

    def _apply_view_mode(self):
        categorized = self._view_mode == "category"
        self.category_selector.setVisible(not categorized)
        self.category_sidebar.setVisible(categorized)
        self.channel_table.setColumnHidden(5, categorized)
        adaptive_columns = getattr(self.channel_table.horizontalHeader(), "_adaptive_columns", None)
        if adaptive_columns:
            adaptive_columns.fit()

    def _save_category_width(self, *_):
        if self._view_mode != "category":
            return
        sizes = self.channel_splitter.sizes()
        if sizes and sizes[0] > 0:
            QSettings().setValue("appearance/channel_category_width", sizes[0])

    @staticmethod
    def _directory_item(tree, text: str, count: int, icon, route):
        item = QTreeWidgetItem([text, str(int(count or 0))])
        item.setData(0, Qt.ItemDataRole.UserRole, route)
        item.setIcon(0, icon.icon() if hasattr(icon, "icon") else icon)
        item.setSizeHint(0, QSize(0, 36))
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tree.addTopLevelItem(item)
        return item

    def _populate_category_directory(self, categories=None):
        try:
            full_categories = (
                categories
                if categories is not None
                else self._sort_category_rows(list_categories(constants.channel_results_path))
            )
            matched_categories = (
                list_categories(constants.channel_results_path, self.search.text())
                if self.search.text()
                else full_categories
            )
        except Exception:
            full_categories, matched_categories = [], []

        full_by_name = {row["category"]: row for row in full_categories}
        matched_by_name = {row["category"]: row for row in matched_categories}
        matched_count = sum(int(row.get("channel_count") or 0) for row in matched_categories)
        matched_health = {
            "healthy": sum(int(row.get("healthy_count") or 0) for row in matched_categories),
            "warning": sum(int(row.get("warning_count") or 0) for row in matched_categories),
            "offline": sum(int(row.get("offline_count") or 0) for row in matched_categories),
            "unknown": matched_count - sum(
                int(row.get(key) or 0)
                for row in matched_categories
                for key in ("healthy_count", "warning_count", "offline_count")
            ),
        }
        try:
            matched_channels = list_channels(
                constants.channel_results_path,
                search=self.search.text(),
            )
        except Exception:
            matched_channels = []
        matched_streaming_count = sum(
            row.get("channel_key") in self._stream_states
            for row in matched_channels
        )

        category_blocker = QSignalBlocker(self.category_tree)
        smart_blocker = QSignalBlocker(self.smart_tree)
        self.category_tree.clear()
        self.smart_tree.clear()
        self._category_items = {}
        self._smart_items = {}

        self._category_items[("all", None)] = self._directory_item(
            self.category_tree,
            t("desktop.all_channels"),
            matched_count,
            FluentIcon.LIBRARY,
            ("all", None),
        )
        for category in full_by_name:
            matched_row = matched_by_name.get(category, {})
            route = ("category", category)
            self._category_items[route] = self._directory_item(
                self.category_tree,
                _category_label(category),
                int(matched_row.get("channel_count") or 0),
                FluentIcon.FOLDER,
                route,
            )
            self._category_items[route].setToolTip(0, category)

        for health, label, icon in (
            ("healthy", t("desktop.health_healthy"), FluentIcon.ACCEPT),
            ("warning", t("desktop.health_warning"), FluentIcon.INFO),
            ("offline", t("desktop.health_offline"), FluentIcon.CANCEL),
            ("unknown", t("desktop.health_unknown"), FluentIcon.QUESTION),
        ):
            route = ("health", health)
            self._smart_items[route] = self._directory_item(
                self.smart_tree,
                label,
                matched_health[health],
                icon,
                route,
            )

        streaming_route = ("streaming", True)
        self._smart_items[streaming_route] = self._directory_item(
            self.smart_tree,
            t("desktop.stream_active"),
            matched_streaming_count,
            FluentIcon.IOT,
            streaming_route,
        )

        current_route = (
            ("streaming", True)
            if self._streaming_filter
            else
            ("health", self._health_filter)
            if self._health_filter
            else ("category", self._category_filter)
            if self._category_filter
            else ("all", None)
        )
        item = self._category_items.get(current_route) or self._smart_items.get(current_route)
        if item is None:
            self._category_filter = None
            self._health_filter = None
            self._streaming_filter = False
            current_route = ("all", None)
            item = self._category_items.get(current_route)
        if item:
            tree = self.smart_tree if current_route[0] in {"health", "streaming"} else self.category_tree
            tree.setCurrentItem(item)
        del category_blocker, smart_blocker

    def _sort_category_rows(self, rows):
        positions = {category: index for index, category in enumerate(self._category_order)}
        fallback = len(positions)
        return [
            row
            for _, row in sorted(
                enumerate(rows),
                key=lambda item: (
                    0 if item[1].get("category") in positions else 1,
                    positions.get(item[1].get("category"), fallback),
                    item[0],
                ),
            )
        ]

    def _sort_channels_by_category(self, rows):
        positions = {category: index for index, category in enumerate(self._category_order)}
        fallback = len(positions)
        rows.sort(
            key=lambda row: (
                0 if row.get("category") in positions else 1,
                positions.get(row.get("category"), fallback),
                row.get("category") or "",
                row.get("name") or "",
            )
        )
        return rows

    def _category_item_clicked(self, item, _column):
        route = item.data(0, Qt.ItemDataRole.UserRole)
        if not route:
            return
        self._category_filter = route[1] if route[0] == "category" else None
        self._health_filter = None
        self._streaming_filter = False
        self.smart_tree.clearSelection()
        self.hide_result_drawer()
        self._load_channels()

    def _smart_item_clicked(self, item, _column):
        route = item.data(0, Qt.ItemDataRole.UserRole)
        if not route:
            return
        self._category_filter = None
        self._streaming_filter = route[0] == "streaming"
        self._health_filter = route[1] if route[0] == "health" else None
        self.category_tree.clearSelection()
        self.hide_result_drawer()
        self._load_channels()

    def _clear_channel_filters(self):
        search_blocker = QSignalBlocker(self.search)
        self.search.clear()
        del search_blocker
        if self._view_mode == "list":
            category_blocker = QSignalBlocker(self.category_selector)
            self.category_selector.setCurrentIndex(0)
            del category_blocker
        self._category_filter = None
        self._health_filter = None
        self._streaming_filter = False
        self._populate_category_directory()
        self.hide_result_drawer()
        self._load_channels()

    def reload(self):
        selected_category = self.category_selector.currentData()
        selected_channels = self._selected_channel_keys() - self._checked_channel_keys
        checked_channels = set(self._checked_channel_keys)
        selected_result_keys = {
            row["result_key"]
            for index in self.result_table.selectionModel().selectedRows()
            if (row := self.result_model.row(index))
        }
        current_result = self.result_model.row(self.result_table.currentIndex())
        result_key = current_result.get("result_key") if current_result else None
        self._category_order = _template_category_order()
        try:
            categories = self._sort_category_rows(list_categories(constants.channel_results_path))
        except Exception:
            categories = []
        blocker = QSignalBlocker(self.category_selector)
        self.category_selector.clear()
        self.category_selector.addItem(t("desktop.all_categories"), userData=None)
        target_index = 0
        for category in categories:
            self.category_selector.addItem(
                f"{category['category']}  {category['channel_count']}",
                userData=category["category"],
            )
            if category["category"] == selected_category:
                target_index = self.category_selector.count() - 1
        self.category_selector.setCurrentIndex(target_index)
        del blocker
        self._populate_category_directory(categories)
        self._load_channels(selected_channels, checked_channels)
        if self._drawer_channel_key:
            self._load_results(
                self._drawer_channel_key,
                result_key,
                selected_result_keys,
            )

    def _load_channels(self, selected_keys=None, checked_keys=None):
        category = self.category_selector.currentData() if self._view_mode == "list" else self._category_filter
        health = self._health_filter if self._view_mode == "category" else None
        try:
            rows = list_channels(
                constants.channel_results_path,
                category,
                self.search.text(),
                health=health,
            )
        except Exception:
            rows = []
        if self._streaming_filter:
            rows = [
                row
                for row in rows
                if row.get("channel_key") in self._stream_states
            ]
        self._sort_channels_by_category(rows)
        self._prepare_channel_rows(rows, checked_keys)
        self.channel_model.set_rows(rows)
        self.channel_stack.setCurrentWidget(self.channel_table if rows else self.empty_state)
        has_filter = bool(
            self.search.text().strip()
            or category
            or health
            or self._streaming_filter
        )
        self.empty_title.setText(t("desktop.channel_center_no_match" if has_filter else "desktop.channel_center_empty"))
        self.empty_hint.setText(t("desktop.channel_center_no_match_hint" if has_filter else "desktop.channel_center_empty_hint"))
        self.empty_clear_button.setVisible(has_filter)

        selection = self.channel_table.selectionModel()
        selection.clearSelection()
        for index, row in enumerate(self.channel_model.rows):
            if row.get("channel_key") in (selected_keys or set()):
                selection.select(
                    self.channel_model.index(index, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
        self._update_selection_label()

    def _prepare_channel_rows(self, rows, checked_keys=None):
        selected_keys = self._checked_channel_keys if checked_keys is None else checked_keys
        try:
            result_urls = list_result_urls_by_channel(constants.channel_results_path)
            whitelist_maps = load_whitelist_maps(constants.whitelist_path)
            blacklist = get_urls_from_file(constants.blacklist_path, pattern_search=False)
        except Exception:
            result_urls, whitelist_maps, blacklist = {}, ({}, {}), []
        for row in rows:
            urls = result_urls.get(row["channel_key"], [])
            row["whitelist_count"] = sum(is_url_whitelisted(whitelist_maps, url, row["name"]) for url in urls)
            row["blacklist_count"] = sum(check_url_by_keywords(url, blacklist) for url in urls)
            row["batch_selected"] = row.get("channel_key") in selected_keys
            row["operation_state"] = (
                "testing" if row.get("channel_key") in self._channel_operation_states else None
            )
            row.update(apply_channel_stream_state(row, self._stream_states))
        return rows

    def _load_results(self, channel_key: str, result_key=None, selected_keys=None):
        if channel_key != self._drawer_channel_key:
            self._checked_result_keys.clear()
        try:
            results = list_channel_results(constants.channel_results_path, channel_key)
        except Exception:
            results = []
        self._all_result_rows = results
        results = [
            row for row in results
            if self._result_matches_filter(row) and self._result_matches_search(row)
        ]
        for result in results:
            result.update(apply_result_stream_state(result, self._stream_result_states))
            result["batch_selected"] = result.get("result_key") in self._checked_result_keys
            result["output_selected"] = result.get("selected_rank") is not None
            result["operation_state"] = self._result_operation_states.get(result.get("result_key"))
        self.result_model.set_rows(results)
        selection = self.result_table.selectionModel()
        selection.clearSelection()
        restore_keys = set(selected_keys or ())
        if result_key:
            restore_keys.add(result_key)
        first_restored_row = -1
        current_row = -1
        for index, row in enumerate(self.result_model.rows):
            if row.get("result_key") not in restore_keys:
                continue
            selection.select(
                self.result_model.index(index, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            if first_restored_row < 0:
                first_restored_row = index
            if row.get("result_key") == result_key:
                current_row = index
        target_row = current_row if current_row >= 0 else first_restored_row
        if target_row < 0 and self.result_model.rows:
            default_result = self._best_valid_result(self.result_model.rows)
            target_row = next(
                (
                    index
                    for index, row in enumerate(self.result_model.rows)
                    if row is default_result
                ),
                -1,
            )
            if target_row >= 0:
                selection.select(
                    self.result_model.index(target_row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        if target_row < 0 and self.result_model.rows:
            target_row = 0
            selection.select(
                self.result_model.index(target_row, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        if target_row >= 0:
            selection.setCurrentIndex(
                self.result_model.index(target_row, 1),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        self._update_result_actions()

    def _result_filter_changed(self, index):
        filters = ("all", "untested", "tested", "valid", "unavailable", "selected")
        self._result_filter = filters[index] if 0 <= index < len(filters) else "all"
        if self._drawer_channel_key:
            self._load_results(self._drawer_channel_key)
            self._update_result_actions()

    def _result_search_changed(self, value):
        self._result_search_query = str(value or "").strip().lower()
        if self._drawer_channel_key:
            self._load_results(self._drawer_channel_key)
            self._update_result_actions()

    def _result_matches_filter(self, row):
        selected = row.get("selected_rank") is not None
        tested = row.get("test_state") == "tested" or row.get("tested_at") is not None
        valid = self._is_valid_result(row)
        return {
            "all": True,
            "untested": not tested,
            "tested": tested,
            "valid": valid,
            "unavailable": tested and not valid,
            "selected": selected,
        }.get(self._result_filter, True)

    def _result_matches_search(self, row):
        query = self._result_search_query
        if not query:
            return True
        values = (
            row.get("url"),
            row.get("host"),
            row.get("origin"),
            row.get("ipv_type"),
            row.get("test_status"),
            row.get("error_type"),
        )
        return any(query in str(value or "").lower() for value in values)

    def _publish_output_selection(self):
        snapshot = load_selected_snapshot(constants.channel_results_path)
        write_channel_to_file(
            snapshot,
            ipv6=config.ipv6_support,
            skip_print=True,
            is_last=True,
        )

    def _category_changed(self, *_):
        if self._view_mode != "list":
            return
        self.hide_result_drawer()
        self._load_channels()

    def _search_changed(self, *_):
        self.hide_result_drawer()
        self._populate_category_directory()
        self._load_channels()

    def _current_channel_changed(self, current, _):
        row = self.channel_model.row(current)
        if row:
            if self._drawer_channel_key != row["channel_key"]:
                self._checked_result_keys.clear()
            self._drawer_channel_key = row["channel_key"]
            self._load_results(row["channel_key"])
            self.results_title.setText(row["name"])

    def _selection_changed(self, *_):
        self._update_selection_label()

    def _channel_data_changed(self, *_):
        visible_keys = {row["channel_key"] for row in self.channel_model.rows}
        self._checked_channel_keys.difference_update(visible_keys)
        self._checked_channel_keys.update({
            row["channel_key"] for row in self.channel_model.rows if row.get("batch_selected")
        })
        self._update_selection_label()
        self._update_channel_header()

    def _result_data_changed(self, *_):
        self._checked_result_keys = {
            row["result_key"] for row in self.result_model.rows if row.get("batch_selected")
        }
        self._update_result_header()
        self._update_result_actions()

    def _result_selection_changed(self, *_):
        self._update_result_actions()

    def _update_result_actions(self):
        rows = self.selected_results()
        count = len(rows)
        single = count == 1
        playback_row = (
            rows[0]
            if single and self._is_valid_result(rows[0])
            else self._best_valid_result(self.result_model.rows)
            if single
            else None
        )
        playable = playback_row is not None
        streamable_rows = [
            row
            for row in rows
            if self._is_valid_result(row) and row.get("selected_rank") is not None
        ]
        streaming_rows = [row for row in rows if row.get("streaming")]
        streamable = bool(streamable_rows)
        task_idle = self._task_operation is None

        self.play_button.setEnabled(playable)
        self.retest_result_button.setEnabled(count > 0 and task_idle)
        self.screenshot_button.setEnabled(single)
        self.stream_button.setEnabled(streamable and task_idle)
        self.stop_result_stream_button.setEnabled(bool(streaming_rows))
        self.start_result_stream_action.setEnabled(streamable and task_idle)
        self.stop_result_stream_action.setEnabled(bool(streaming_rows))
        self.more_button.setEnabled(count > 0)
        self.copy_button.setEnabled(count > 0)

        self.preview_screenshot_action.setEnabled(single)
        self.play_result_action.setEnabled(playable)
        self.retest_result_action.setEnabled(count > 0 and task_idle)
        self.capture_screenshot_action.setEnabled(count > 0 and task_idle)
        self.copy_action.setEnabled(count > 0)
        self.copy_stream_action.setEnabled(streamable)
        self.whitelist_action.setEnabled(count > 0)
        self.blacklist_action.setEnabled(count > 0)
        self.delete_result_action.setEnabled(bool(rows) and not streaming_rows)
        self.include_output_action.setEnabled(bool(rows))
        self.exclude_output_action.setEnabled(bool(rows))
        self.pin_output_action.setEnabled(bool(rows))
        self.auto_output_action.setEnabled(self.selected_channel() is not None)
        if self._screenshot_dialog and self._screenshot_dialog.isVisible():
            self._screenshot_dialog.set_capture_enabled(task_idle)

    def _toggle_all_channels(self, checked: bool):
        self.channel_model.set_all_checked(checked)

    def _update_channel_header(self):
        self.channel_header.set_check_state(self.channel_model.check_state())

    def _toggle_all_results(self, checked: bool):
        self.result_model.set_all_checked(checked)

    def _update_result_header(self):
        self.result_header.set_check_state(self.result_model.check_state())

    def _update_selection_label(self):
        count = len(self._selected_channel_keys())
        self.selection_label.setText(t("desktop.channels_selected").format(count=count))
        self.selection_label.setVisible(count > 0)
        self.add_result_button.setEnabled(count > 0)
        selected_channels = self.selected_channels()
        streaming_channels = [row for row in selected_channels if row.get("streaming")]
        delete_allowed = count > 0 and not streaming_channels
        self.delete_channel_button.setEnabled(delete_allowed)
        self.retest_channel_button.setEnabled(count > 0)
        self.channel_add_action.setEnabled(True)
        self.channel_add_result_action.setEnabled(count > 0 or bool(self._drawer_channel_key))
        self.channel_delete_action.setEnabled(delete_allowed)
        self.channel_retest_action.setEnabled(count > 0)
        self.channel_stream_action.setEnabled(count > 0)
        self.channel_play_action.setEnabled(
            any(self._best_channel_playback_result(row) for row in selected_channels)
        )
        self.play_selected_button.setVisible(count > 0)
        self.play_selected_button.setEnabled(
            any(self._best_channel_playback_result(row) for row in selected_channels)
        )
        stream_result_keys = {
            result_key
            for row in streaming_channels
            for result_key in row.get("stream_result_keys") or []
            if result_key
        }
        self.channel_stop_stream_action.setEnabled(bool(stream_result_keys))
        self.stop_stream_button.hide()
        self.stop_stream_button.setEnabled(bool(stream_result_keys))
        self.stream_selected_button.setVisible(False)

    def _channel_clicked(self, index):
        if self._suppress_channel_click:
            self._suppress_channel_click = False
            return
        row = self.channel_model.row(index)
        if row and self.channel_model.columns[index.column()][0] == "name" and is_channel_logo_click(self.channel_table, index):
            self._edit_channel_logo(row)
            return
        if row and self.channel_model.columns[index.column()][0] != "batch_selected":
            if self._drawer_channel_key != row["channel_key"]:
                self._checked_result_keys.clear()
            self._drawer_channel_key = row["channel_key"]
            self._load_results(row["channel_key"])
            self.results_title.setText(row["name"])
            self.show_result_drawer()

    def _selected_channel_keys(self):
        keys = {
            row["channel_key"]
            for index in self.channel_table.selectionModel().selectedRows()
            if (row := self.channel_model.row(index))
        }
        keys.update(self._checked_channel_keys)
        return keys

    def selected_channels(self):
        keys = self._selected_channel_keys()
        if not keys:
            return []
        current_rows = {
            row["channel_key"]: row
            for row in self.channel_model.rows
            if row.get("channel_key") in keys
        }
        missing = keys - current_rows.keys()
        if missing:
            try:
                rows = [
                    row for row in list_channels(constants.channel_results_path)
                    if row.get("channel_key") in missing
                ]
            except Exception:
                rows = []
            self._prepare_channel_rows(rows)
            current_rows.update({row["channel_key"]: row for row in rows})
        return self._sort_channels_by_category(
            [current_rows[key] for key in keys if key in current_rows]
        )

    def selected_channel(self):
        current = self.channel_table.currentIndex()
        return self.channel_model.row(current) if current.isValid() else (self.selected_channels()[0] if self.selected_channels() else None)

    def selected_result(self):
        rows = self.selected_results()
        current = self.result_model.row(self.result_table.currentIndex())
        return current if current in rows else (rows[0] if rows else None)

    def selected_results(self):
        indexes = self.result_table.selectionModel().selectedRows()
        keys = {
            row["result_key"]
            for index in indexes
            if (row := self.result_model.row(index))
        }
        keys.update(self._checked_result_keys)
        return [row for row in self.result_model.rows if row.get("result_key") in keys]

    def _show_channel_menu(self, position):
        index = self.channel_table.indexAt(position)
        if index.isValid():
            if not self.channel_table.selectionModel().isRowSelected(index.row(), index.parent()):
                self.channel_table.selectRow(index.row())
            self.channel_menu.exec(self.channel_table.viewport().mapToGlobal(position))

    def set_stream_snapshot(self, snapshot: dict):
        self._stream_snapshot = snapshot
        self._stream_states = build_channel_stream_states(snapshot)
        self._stream_result_states = build_result_stream_states(snapshot)
        for index, row in enumerate(self.channel_model.rows):
            self.channel_model.rows[index] = apply_channel_stream_state(row, self._stream_states)
        for index, row in enumerate(self.result_model.rows):
            self.result_model.rows[index] = apply_result_stream_state(row, self._stream_result_states)
        if self.channel_model.rows:
            self.channel_model.dataChanged.emit(
                self.channel_model.index(0, 1),
                self.channel_model.index(len(self.channel_model.rows) - 1, 1),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole],
            )
        if self.result_model.rows:
            stream_column = next(
                index
                for index, column in enumerate(self.result_model.columns)
                if column[0] == "stream_state"
            )
            self.result_model.dataChanged.emit(
                self.result_model.index(0, stream_column),
                self.result_model.index(len(self.result_model.rows) - 1, stream_column),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole],
            )
        self._populate_category_directory()
        if self._streaming_filter:
            self._load_channels(
                selected_keys=self._selected_channel_keys() - self._checked_channel_keys,
                checked_keys=set(self._checked_channel_keys),
            )

    def _show_stream_menu(self, row: dict, position):
        self._suppress_channel_click = True
        menu = RoundMenu(parent=self)
        menu.addAction(Action(
            FluentIcon.IOT,
            t("desktop.view_stream_details"),
            self,
            triggered=lambda _checked=False: self.stream_monitor_requested.emit(),
        ))
        menu.addAction(Action(
            FluentIcon.PAUSE_BOLD.icon(color=QColor("#DC2626")),
            t("desktop.stop_channel_streams"),
            self,
            triggered=lambda _checked=False: self._stop_channel_streams(row),
        ))
        menu.exec(position)
        QTimer.singleShot(0, self._clear_stream_indicator_click)

    def _clear_stream_indicator_click(self):
        self._suppress_channel_click = False

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

    def _stop_selected_channel_streams(self):
        rows = self.selected_channels()
        result_keys = list(dict.fromkeys(
            result_key
            for row in rows
            if row.get("streaming")
            for result_key in row.get("stream_result_keys") or []
            if result_key
        ))
        if not result_keys:
            return
        box = warning_message_box(
            t("desktop.stop_channel_streams"),
            t("desktop.stop_selected_streams_confirm").format(count=len(result_keys)),
            self,
        )
        if box.exec():
            self.stream_control_many_requested.emit("stop", result_keys)

    def _show_result_menu(self, position):
        index = self.result_table.indexAt(position)
        if index.isValid():
            if not self.result_table.selectionModel().isRowSelected(index.row(), index.parent()):
                self.result_table.selectRow(index.row())
            self._update_result_actions()
            self.result_menu.exec(self.result_table.viewport().mapToGlobal(position))

    def _request_channel_retest(self):
        rows = self.selected_channels()
        self._channel_operation_states.update(
            row.get("channel_key") for row in rows if row.get("channel_key")
        )
        self._load_channels(
            selected_keys=self._selected_channel_keys(),
            checked_keys=self._checked_channel_keys,
        )
        for row in rows:
            if row.get("channel_key") == self._drawer_channel_key:
                self._mark_results_testing()
            self._task_context_queue.append({"surface": "page", "name": row.get("name")})
            self.retest_channel_requested.emit(row)

    def _open_selected_in_playback(self):
        rows = self.selected_channels()
        if rows:
            self.playback_batch_requested.emit(rows)

    def _play_selected_channels(self):
        for channel in self.selected_channels():
            row = self._best_channel_playback_result(channel)
            if row:
                play_url(row["url"], self)

    def _request_result_retest(self):
        rows = self.selected_results()
        self._mark_results_testing(rows)
        if len(rows) > 1:
            self._task_context_queue.append(
                {"surface": "drawer", "name": self._drawer_channel_name()}
            )
            self.retest_results_requested.emit(rows)
            return
        for row in rows:
            self._task_context_queue.append(
                {"surface": "drawer", "name": self._drawer_channel_name()}
            )
            self.retest_result_requested.emit(row)

    def _mark_results_testing(self, rows=None):
        if not self._drawer_channel_key:
            return
        keys = {
            row.get("result_key")
            for row in (rows or self._all_result_rows)
            if row.get("result_key")
        }
        self._result_operation_states.update({key: "testing" for key in keys})
        for row in self.result_model.rows:
            if row.get("result_key") in keys:
                row["operation_state"] = "testing"
        if self.result_model.rows:
            status_column = next(
                index
                for index, column in enumerate(self.result_model.columns)
                if column[0] == "valid"
            )
            self.result_model.dataChanged.emit(
                self.result_model.index(0, status_column),
                self.result_model.index(len(self.result_model.rows) - 1, status_column),
                [Qt.ItemDataRole.DisplayRole],
            )

    def _request_result_screenshot(self):
        rows = list(self.selected_results())
        if len(rows) == 1:
            self._task_context_queue.append(
                {"surface": "drawer", "name": self._drawer_channel_name()}
            )
            self.capture_screenshot_requested.emit(rows[0])
        elif rows:
            self._task_context_queue.append(
                {"surface": "drawer", "name": self._drawer_channel_name()}
            )
            self.capture_screenshots_requested.emit(rows)

    def _preview_result_screenshot(self):
        rows = self.selected_results()
        if len(rows) != 1:
            return
        row = rows[0]
        if self._screenshot_dialog and self._screenshot_dialog.isVisible():
            if not self._screenshot_dialog.is_loading:
                self._screenshot_dialog.set_result(row)
                self._auto_capture_missing_screenshot(
                    self._screenshot_dialog,
                    row,
                )
            self._screenshot_dialog.raise_()
            self._screenshot_dialog.activateWindow()
            return
        dialog = StreamScreenshotDialog(row, self)
        dialog.capture_requested.connect(self._request_dialog_screenshot)
        dialog.finished.connect(self._screenshot_dialog_finished)
        self._screenshot_dialog = dialog
        dialog.set_capture_enabled(self._task_operation is None)
        dialog.open()
        self._auto_capture_missing_screenshot(dialog, row)

    def _auto_capture_missing_screenshot(self, dialog, row: dict):
        status = row.get("screenshot_status") or "not_captured"
        missing = status == "not_captured" or (
            status == "success" and not dialog.has_screenshot()
        )
        if missing and self._task_operation is None:
            dialog.request_capture()

    def _request_dialog_screenshot(self, row: dict):
        if self._task_operation is not None:
            if self._screenshot_dialog:
                self._screenshot_dialog.set_loading(False)
            return
        self._task_context_queue.append(
            {"surface": "drawer", "name": self._drawer_channel_name()}
        )
        self.capture_screenshot_requested.emit(row)

    def _screenshot_dialog_finished(self, *_):
        self._screenshot_dialog = None

    def show_result_screenshot(self, result_key: str, notify=False):
        row = next(
            (item for item in self.result_model.rows if item.get("result_key") == result_key),
            None,
        )
        if (
            row
            and self._screenshot_dialog
            and self._screenshot_dialog.isVisible()
            and self._screenshot_dialog.result.get("result_key") == result_key
        ):
            self._screenshot_dialog.set_result(row)
            self._update_result_actions()
        if notify:
            InfoBar.success(
                t("desktop.task_completed"),
                t("desktop.capture_result_screenshot"),
                parent=self._screenshot_notification_parent(),
                position=InfoBarPosition.TOP,
            )

    def set_screenshot_capture_failed(self, message: str, notify=False):
        if self._screenshot_dialog and self._screenshot_dialog.isVisible():
            result_key = self._screenshot_dialog.result.get("result_key")
            row = next(
                (
                    item
                    for item in self.result_model.rows
                    if item.get("result_key") == result_key
                ),
                None,
            )
            if row:
                self._screenshot_dialog.set_result(row)
                self._update_result_actions()
            else:
                self._screenshot_dialog.set_error(message)
        if notify:
            InfoBar.error(
                t("desktop.task_failed"),
                message.splitlines()[-1] if message else t("desktop.screenshot_failed"),
                parent=self._screenshot_notification_parent(),
                position=InfoBarPosition.TOP,
                duration=8000,
            )

    def _screenshot_notification_parent(self):
        if self._screenshot_dialog and self._screenshot_dialog.isVisible():
            return self._screenshot_dialog
        return self

    def _copy_result(self):
        rows = self.selected_results()
        if rows:
            value = "\n".join(row["url"] for row in rows)
            QGuiApplication.clipboard().setText(value)
            InfoBar.success(t("desktop.copied"), value, parent=self, position=InfoBarPosition.TOP)

    def _update_manual_output(self, include: bool):
        channel = (
            get_channel(constants.channel_results_path, self._drawer_channel_key)
            if self._drawer_channel_key
            else self.selected_channel()
        )
        rows = self.selected_results()
        if not channel or not rows:
            return
        all_rows = list_channel_results(constants.channel_results_path, channel["channel_key"])
        selected = [
            row for row in all_rows
            if row.get("selected_rank") is not None
        ]
        selected_keys = {row.get("result_key") for row in selected}
        target_keys = {row.get("result_key") for row in rows}
        if include:
            selected.extend(
                row for row in rows
                if row.get("result_key") not in selected_keys
            )
        else:
            selected = [row for row in selected if row.get("result_key") not in target_keys]
        set_channel_selection(constants.channel_results_path, channel["channel_key"], selected)
        self._publish_output_selection()
        self._load_results(channel["channel_key"], selected_keys=target_keys)
        self._update_result_actions()
        InfoBar.success(
            t("desktop.output_selection_updated"),
            t("desktop.output_selection_count").format(count=len(selected)),
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _include_selected_output(self):
        self._update_manual_output(True)

    def _exclude_selected_output(self):
        self._update_manual_output(False)

    def _pin_selected_output(self):
        channel = (
            get_channel(constants.channel_results_path, self._drawer_channel_key)
            if self._drawer_channel_key
            else self.selected_channel()
        )
        rows = self.selected_results()
        if not channel or not rows:
            return
        all_rows = list_channel_results(constants.channel_results_path, channel["channel_key"])
        target_keys = {row.get("result_key") for row in rows}
        selected = sorted(
            (row for row in all_rows if row.get("selected_rank") is not None),
            key=lambda row: row.get("selected_rank") or 0,
        )
        pinned = [row for row in selected if row.get("result_key") in target_keys]
        remaining = [row for row in selected if row.get("result_key") not in target_keys]
        unselected = [row for row in rows if row.get("result_key") not in {item.get("result_key") for item in selected}]
        set_channel_selection(
            constants.channel_results_path,
            channel["channel_key"],
            pinned + unselected + remaining,
        )
        self._publish_output_selection()
        self._load_results(channel["channel_key"], selected_keys=target_keys)
        self._update_result_actions()
        InfoBar.success(
            t("desktop.output_selection_updated"),
            t("desktop.pin_output"),
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _restore_auto_output(self):
        channel = (
            get_channel(constants.channel_results_path, self._drawer_channel_key)
            if self._drawer_channel_key
            else self.selected_channel()
        )
        if not channel:
            return
        reset_channel_selection(constants.channel_results_path, channel["channel_key"])
        self._publish_output_selection()
        self._load_results(channel["channel_key"])
        self._update_result_actions()
        InfoBar.success(
            t("desktop.output_selection_updated"),
            t("desktop.auto_select_output"),
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _open_result(self):
        row = self.selected_result()
        if not self._is_valid_result(row):
            row = self._best_valid_result(self.result_model.rows)
        if row:
            play_url(row["url"], self)

    def _stream_playback(self):
        row = self.selected_result()
        if row and row.get("selected_rank") is not None:
            self.playback_workspace_requested.emit(row)
        elif row:
            InfoBar.warning(t("desktop.not_selected_result"), t("desktop.select_result_hint"), parent=self, position=InfoBarPosition.TOP)

    def _start_selected_result_streams(self):
        result_keys = list(dict.fromkeys(
            row.get("result_key")
            for row in self.selected_results()
            if self._is_valid_result(row)
            and row.get("selected_rank") is not None
            and row.get("result_key")
        ))
        if result_keys:
            self.stream_control_many_requested.emit("start", result_keys)

    def _stop_selected_result_streams(self):
        rows = [row for row in self.selected_results() if row.get("streaming")]
        result_keys = list(dict.fromkeys(row.get("result_key") for row in rows if row.get("result_key")))
        if not result_keys:
            return
        box = warning_message_box(
            t("desktop.stop_stream"),
            t("desktop.stop_selected_streams_confirm").format(count=len(result_keys)),
            self,
        )
        if box.exec():
            self.stream_control_many_requested.emit("stop", result_keys)

    def _copy_stream_url(self):
        row = self.selected_result()
        if row and row.get("selected_rank") is not None:
            url = f"{get_public_url().rstrip('/')}/hls_proxy/{row['result_key']}"
            QGuiApplication.clipboard().setText(url)
            InfoBar.success(t("desktop.copied"), url, parent=self, position=InfoBarPosition.TOP)
        elif row:
            InfoBar.warning(t("desktop.not_selected_result"), t("desktop.select_result_hint"), parent=self, position=InfoBarPosition.TOP)

    def _add_whitelist(self):
        channel = self.selected_channel()
        results = self.selected_results()
        if channel and results:
            changes = [add_to_whitelist(channel["name"], result["url"]) for result in results]
            changed = any(changes)
            InfoBar.success(t("desktop.whitelist_updated"), t("desktop.next_update_effect" if changed else "desktop.already_exists"), parent=self, position=InfoBarPosition.TOP)
            self.reload()

    def _add_blacklist(self):
        results = self.selected_results()
        if results:
            changes = [add_to_blacklist(result["url"]) for result in results]
            changed = any(changes)
            InfoBar.success(t("desktop.blacklist_updated"), t("desktop.next_update_effect" if changed else "desktop.already_exists"), parent=self, position=InfoBarPosition.TOP)
            self.reload()

    def _delete_selected_results(self):
        rows = self.selected_results()
        if not rows:
            return
        if any(row.get("streaming") for row in rows):
            InfoBar.warning(
                t("desktop.delete_result"),
                t("desktop.delete_blocked_streaming"),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        box = warning_message_box(
            t("desktop.delete_result"),
            t("desktop.delete_results_confirm").format(count=len(rows)),
            self,
        )
        if not box.exec():
            return
        channel_key = self._drawer_channel_key
        result_keys = [row.get("result_key") for row in rows if row.get("result_key")]
        manual_urls = [
            row.get("url")
            for row in rows
            if row.get("origin") == "local" and row.get("url")
        ]
        channel = next(
            (
                row
                for row in self.channel_model.rows
                if row.get("channel_key") == channel_key
            ),
            None,
        )
        deleted_keys = delete_channel_results(
            constants.channel_results_path,
            channel_key,
            result_keys,
        )
        if not deleted_keys:
            return
        if channel and manual_urls:
            delete_manual_channel_results(channel.get("name") or "", manual_urls)
        self._checked_result_keys.difference_update(deleted_keys)
        self._load_results(channel_key)
        self.reload()
        InfoBar.success(
            t("desktop.results_deleted"),
            str(len(deleted_keys)),
            parent=self,
            position=InfoBarPosition.TOP,
        )

    @staticmethod
    def _import_record(file_name, line_number, row, status="new", reason="", selected=True):
        return {
            "file_name": file_name,
            "line_number": line_number,
            "row": row,
            "status": status,
            "reason": reason,
            "selected": selected,
        }

    def _import_channels(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("desktop.import_channels"),
            "",
            t("desktop.import_channel_file_filter"),
        )
        if not paths:
            return

        records = []
        for path in paths:
            file_name = os.path.basename(path)
            try:
                with open(path, "rb") as source:
                    content = _decode(source.read())
            except (OSError, UnicodeDecodeError) as error:
                records.append(self._import_record(file_name, 0, {}, "invalid", str(error), False))
                continue
            category = ""
            for line_number, raw in enumerate(content.splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                match = re.match(r"^(.*?)[,，]\s*#genre#\s*$", line)
                if match:
                    category = match.group(1).strip()
                    continue
                if not category:
                    records.append(self._import_record(
                        file_name, line_number, {"category": "", "name": line},
                        "invalid", "missing_category", False,
                    ))
                    continue
                records.append(self._import_record(
                    file_name, line_number, {"category": category, "name": line},
                ))

        known_names = {row.get("name", "").strip() for row in list_channels(constants.channel_results_path)}
        seen_names = set(known_names)
        for record in records:
            if record["status"] != "new":
                continue
            name = record["row"]["name"].strip()
            if name in seen_names:
                record.update(status="duplicate", reason="duplicate", selected=False)
            else:
                seen_names.add(name)

        dialog = SourceImportDialog(
            t("desktop.import_channels"),
            records,
            [(t("desktop.column_category"), "category"), (t("name.channel"), "name")],
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        imported = 0
        for record in dialog.selected_records():
            row = record["row"]
            if add_channel(row["category"], row["name"]):
                upsert_manual_channel(constants.channel_results_path, row["category"], row["name"])
                imported += 1
        if imported:
            self.reload()
            InfoBar.success(
                t("desktop.import_completed"),
                t("desktop.import_channels_completed").format(count=imported),
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _import_playback_sources(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("desktop.import_playback_sources"),
            "",
            t("desktop.import_file_filter"),
        )
        if not paths:
            return

        channels = {
            row.get("name", "").strip(): row
            for row in list_channels(constants.channel_results_path)
        }
        urls_by_channel = list_result_urls_by_channel(constants.channel_results_path)
        records = []
        for path in paths:
            parsed, errors = parse_local_source_file(path)
            for item in parsed:
                channel = channels.get(item.channel)
                row = {"channel": item.channel, "url": item.url}
                if not channel:
                    records.append(self._import_record(
                        item.file_name, item.line_number, row,
                        "invalid", t("desktop.import_unknown_channel"), False,
                    ))
                elif item.url in urls_by_channel.get(channel["channel_key"], []):
                    records.append(self._import_record(
                        item.file_name, item.line_number, row,
                        "duplicate", "duplicate", False,
                    ))
                else:
                    urls_by_channel.setdefault(channel["channel_key"], []).append(item.url)
                    records.append(self._import_record(item.file_name, item.line_number, row))
            records.extend(
                self._import_record(
                    item.file_name,
                    item.line_number,
                    {"channel": item.channel, "url": item.url},
                    item.status,
                    item.reason,
                    item.selected,
                )
                for item in errors
            )

        dialog = SourceImportDialog(
            t("desktop.import_playback_sources"),
            records,
            [(t("name.channel"), "channel"), (t("desktop.source_url"), "url")],
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        imported = 0
        for record in dialog.selected_records():
            row = record["row"]
            channel = channels[row["channel"]]
            add_manual_channel_result(channel["name"], row["url"])
            add_manual_result(constants.channel_results_path, channel["channel_key"], row["url"])
            imported += 1
        if imported:
            self.reload()
            InfoBar.success(
                t("desktop.import_completed"),
                t("desktop.import_playback_sources_completed").format(count=imported),
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _export_channels(self):
        return self.selected_channels() or list(self.channel_model.rows)

    def _write_export(self, title, suggested_name, file_filter, content):
        path, _ = QFileDialog.getSaveFileName(self, title, suggested_name, file_filter)
        if not path:
            return
        target = QSaveFile(path)
        if target.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Text):
            target.write(content.encode("utf-8"))
            if target.commit():
                InfoBar.success(t("desktop.export_completed"), path, parent=self, position=InfoBarPosition.TOP)
                return
        InfoBar.error(t("name.error"), target.errorString(), parent=self, position=InfoBarPosition.TOP)

    def _export_channel_template(self):
        grouped = {}
        for row in self._export_channels():
            grouped.setdefault(row.get("category") or t("desktop.uncategorized"), []).append(row.get("name") or "")
        lines = []
        for category in self._category_order + [value for value in grouped if value not in self._category_order]:
            names = grouped.get(category, [])
            if not names:
                continue
            lines.append(f"{category},#genre#")
            lines.extend(sorted(name for name in names if name))
            lines.append("")
        self._write_export(
            t("desktop.export_channel_template"),
            "channels.txt",
            t("desktop.export_source_file_filter"),
            "\n".join(lines).rstrip() + "\n",
        )

    def _export_playback_sources(self):
        entries = []
        for channel in self._export_channels():
            for result in list_channel_results(constants.channel_results_path, channel["channel_key"]):
                if result.get("url"):
                    entries.append((channel.get("name") or "", result["url"]))
        path, _ = QFileDialog.getSaveFileName(
            self,
            t("desktop.export_playback_sources"),
            "playback-sources.txt",
            t("desktop.export_channel_file_filter"),
        )
        if not path:
            return
        if path.lower().endswith(".m3u"):
            lines = ["#EXTM3U"]
            for name, url in entries:
                lines.extend((f"#EXTINF:-1,{name}", url))
        else:
            lines = [f"{name},{url}" for name, url in entries]
        target = QSaveFile(path)
        if target.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Text):
            target.write(("\n".join(lines).rstrip() + "\n").encode("utf-8"))
            if target.commit():
                InfoBar.success(t("desktop.export_completed"), path, parent=self, position=InfoBarPosition.TOP)
                return
        InfoBar.error(t("name.error"), target.errorString(), parent=self, position=InfoBarPosition.TOP)

    def _export_channel_diagnostics(self):
        output = io.StringIO()
        fields = ("category", "name", "health", "valid_results", "total_results", "updated_at")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in self._export_channels())
        self._write_export(
            t("desktop.export_diagnostics_csv"),
            "channel-diagnostics.csv",
            t("desktop.export_csv_file_filter"),
            output.getvalue(),
        )

    def _add_channel(self):
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(t("desktop.add_channel"))
        form = QFormLayout(dialog)
        category = AppEditableComboBox(dialog)
        category.addItems([
            row["category"]
            for row in self._sort_category_rows(list_categories(constants.channel_results_path))
        ])
        name = AppLineEdit(dialog)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
        localize_dialog_buttons(buttons)
        form.addRow(t("desktop.categories"), category)
        form.addRow(t("name.channel"), name)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted or not category.currentText().strip() or not name.text().strip():
            return
        if add_channel(category.currentText(), name.text()):
            upsert_manual_channel(constants.channel_results_path, category.currentText().strip(), name.text().strip())
            self.reload()
            InfoBar.success(t("desktop.channel_added"), name.text().strip(), parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.warning(t("desktop.channel_exists"), name.text().strip(), parent=self, position=InfoBarPosition.TOP)

    def _edit_channel_logo(self, channel=None):
        channel = channel if isinstance(channel, dict) else self.selected_channel()
        if not channel:
            return
        dialog = ChannelLogoDialog(channel, self.logo_loader, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        set_channel_logo(constants.channel_results_path, channel["channel_key"], dialog.logo_value())
        self.reload()
        InfoBar.success(t("desktop.channel_logo_updated"), channel["name"], parent=self, position=InfoBarPosition.TOP)

    def _delete_selected_channels(self):
        rows = self.selected_channels()
        if not rows:
            return
        if any(row.get("streaming") for row in rows):
            InfoBar.warning(
                t("desktop.delete_channel"),
                t("desktop.delete_blocked_streaming"),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        box = warning_message_box(
            t("desktop.delete_channel"),
            t("desktop.delete_channels_confirm").format(count=len(rows)),
            self,
        )
        if not box.exec():
            return
        deleted_keys = {row["channel_key"] for row in rows}
        manual_sources = {}
        for row in rows:
            try:
                manual_sources[row["name"]] = [
                    result.get("url")
                    for result in list_channel_results(
                        constants.channel_results_path,
                        row["channel_key"],
                    )
                    if result.get("origin") == "local" and result.get("url")
                ]
            except Exception:
                manual_sources[row["name"]] = []
        delete_channels([row["name"] for row in rows])
        delete_channel_records(constants.channel_results_path, list(deleted_keys))
        for channel_name, urls in manual_sources.items():
            if urls:
                delete_manual_channel_results(channel_name, urls)
        self._checked_channel_keys.difference_update(deleted_keys)
        self.hide_result_drawer()
        self.reload()
        InfoBar.success(t("desktop.channels_deleted"), str(len(rows)), parent=self, position=InfoBarPosition.TOP)

    def _add_manual_result(self):
        channel = (
            get_channel(constants.channel_results_path, self._drawer_channel_key)
            if self._drawer_channel_key
            else self.selected_channel()
        )
        if not channel:
            return
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(t("desktop.add_result"))
        dialog.setMinimumWidth(560)
        dialog.resize(600, 150)
        form = QFormLayout(dialog)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        channel_label = QLabel(channel["name"], dialog)
        url = AppLineEdit(dialog)
        url.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        url.setMinimumWidth(0)
        url.setPlaceholderText("https://example.com/live.m3u8")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dialog)
        localize_dialog_buttons(buttons)
        form.addRow(t("name.channel"), channel_label)
        form.addRow(t("desktop.source_url"), url)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted or not url.text().strip():
            return
        add_manual_channel_result(channel["name"], url.text())
        result_key = add_manual_result(constants.channel_results_path, channel["channel_key"], url.text().strip())
        self._load_results(channel["channel_key"], result_key)
        self.show_result_drawer()
        row = next((item for item in self.result_model.rows if item.get("result_key") == result_key), None)
        if row:
            self._task_context_queue.append(
                {"surface": "drawer", "name": channel.get("name")}
            )
            self.retest_result_requested.emit(row)

    def _drawer_channel_name(self):
        row = next(
            (
                item
                for item in self.channel_model.rows
                if item.get("channel_key") == self._drawer_channel_key
            ),
            None,
        )
        return row.get("name") if row else ""

    def set_task_started(self, operation: str):
        self._task_operation = operation
        context = self._task_context_queue.pop(0) if self._task_context_queue else None
        if context is None:
            surface = (
                "drawer"
                if operation in {
                    "retest_result",
                    "retest_results",
                    "capture_result_screenshot",
                    "capture_result_screenshots",
                }
                and not self.result_drawer.isHidden()
                else "page"
            )
            context = {
                "surface": surface,
                "name": self._drawer_channel_name() if surface == "drawer" else "",
            }
        if isinstance(context, str):
            context = {"surface": context, "name": ""}
        self._task_context = (
            context.get("surface")
            if context.get("surface") in {"page", "drawer"}
            else "page"
        )
        self._task_name = context.get("name") or (
            self._drawer_channel_name()
            if self._task_context == "drawer"
            else next(
                (
                    row.get("name")
                    for row in self.selected_channels()
                    if row.get("name")
                ),
                "",
            )
        )
        operation_label = t(f"desktop.{operation}", operation)
        initial_label = (
            operation_label
            if self._task_context == "drawer" and self._drawer_channel_key
            else f"{operation_label} · {self._task_name}"
            if self._task_name
            else operation_label
        )
        self.task_icon.stop()
        self.drawer_task_icon.stop()
        self.task_label.setText(initial_label)
        self.task_progress.setValue(0)
        self.task_percent_label.setText("0%")
        self.task_label.show()
        self.task_progress.show()
        self.task_percent_label.show()
        self.drawer_task_label.setText(initial_label)
        self.drawer_task_progress.setValue(0)
        self.drawer_task_percent_label.setText("0%")
        self.drawer_task_label.show()
        self.drawer_task_progress.show()
        self.drawer_task_percent_label.show()
        self.task_row.hide()
        self.drawer_task_row.hide()
        if (
            self._task_context == "drawer"
            and not self.result_drawer.isHidden()
            and not (self._screenshot_dialog and self._screenshot_dialog.isVisible())
        ):
            self.drawer_task_row.show()
            self.drawer_task_icon.start()
        elif self._task_context == "drawer" and self._screenshot_dialog:
            self._screenshot_dialog.set_loading(True)
        else:
            self.task_row.show()
            self.task_icon.start()
        self._update_result_actions()

    def _set_task_label_style(self):
        color = "#60A5FA" if isDarkTheme() else "#2563EB"
        self.task_label.setStyleSheet(f"color: {color};")
        self.drawer_task_label.setStyleSheet(f"color: {color};")

    def set_task_progress(self, name: str, value: int):
        self._task_name = name
        percent = max(0, min(100, int(value)))
        operation_label = t(
            f"desktop.{self._task_operation}",
            self._task_operation or t("desktop.task_running"),
        )
        label = (
            operation_label
            if self._task_context == "drawer" and self._drawer_channel_key
            else f"{operation_label} · {name}"
            if name
            else operation_label
        )
        if self._task_context == "drawer" and not self.drawer_task_row.isHidden():
            self.drawer_task_label.setText(label)
            self.drawer_task_progress.setValue(percent)
            self.drawer_task_percent_label.setText(f"{percent}%")
        elif self._task_context == "drawer" and self._screenshot_dialog:
            self._screenshot_dialog.set_loading(True)
        else:
            self.task_label.setText(label)
            self.task_progress.setValue(percent)
            self.task_percent_label.setText(f"{percent}%")

    def set_task_finished(self):
        self._result_operation_states.clear()
        self._channel_operation_states.clear()
        self._task_operation = None
        self._task_name = None
        self._task_context = "page"
        self.task_icon.stop()
        self.drawer_task_icon.stop()
        self.task_row.hide()
        self.drawer_task_row.hide()
        self.task_label.hide()
        self.task_progress.hide()
        self.task_percent_label.hide()
        self.drawer_task_label.hide()
        self.drawer_task_progress.hide()
        self.drawer_task_percent_label.hide()
        if self._screenshot_dialog and self._screenshot_dialog.is_loading:
            self._screenshot_dialog.set_loading(False)
        self.reload()
        self._update_result_actions()

    def retranslate(self):
        self._set_task_label_style()
        self.view_switch.items["list"].setText(t("desktop.channel_view_list"))
        self.view_switch.items["category"].setText(t("desktop.channel_view_category"))
        self.category_heading.setText(t("desktop.channel_categories"))
        self.smart_heading.setText(t("desktop.smart_collections"))
        self.search.setPlaceholderText(t("desktop.search_channels"))
        self.result_search.setPlaceholderText(t("desktop.search_results"))
        self.refresh_button.setToolTip(t("desktop.refresh"))
        self.import_button.setText(t("desktop.import_files"))
        self.export_button.setText(t("desktop.export"))
        self.empty_clear_button.setText(t("desktop.clear_filters"))
        self.add_channel_button.setText(t("desktop.add_channel"))
        self.add_result_button.setText(t("desktop.add_result"))
        self.delete_channel_button.setText(t("desktop.delete_channel"))
        self.retest_channel_button.setText(t("desktop.retest_channel"))
        self.play_selected_button.setText(t("desktop.play"))
        self.stop_stream_button.setText(t("desktop.stop_stream"))
        self.stream_selected_button.setText(t("desktop.open_selected_streams"))
        self.channel_more_button.setText(t("desktop.more_actions"))
        self.play_button.setText(t("desktop.play"))
        self.retest_result_button.setText(t("desktop.retest_result"))
        self.screenshot_button.setText(t("desktop.preview_screenshot"))
        self.stream_button.setText(t("desktop.open_selected_streams"))
        self.stop_result_stream_button.setText(t("desktop.stop_stream"))
        self.copy_button.setText(t("desktop.copy_url"))
        self.more_button.setText(t("desktop.more_actions"))
        current_filter = self._result_filter
        self.result_filter.blockSignals(True)
        self.result_filter.setItemText(0, t("desktop.result_filter_all"))
        self.result_filter.setItemText(1, t("desktop.result_filter_untested"))
        self.result_filter.setItemText(2, t("desktop.result_filter_tested"))
        self.result_filter.setItemText(3, t("desktop.result_filter_valid"))
        self.result_filter.setItemText(4, t("desktop.result_filter_unavailable"))
        self.result_filter.setItemText(5, t("desktop.result_filter_selected"))
        self.result_filter.setCurrentIndex(("all", "untested", "tested", "valid", "unavailable", "selected").index(current_filter))
        self.result_filter.blockSignals(False)
        self.drawer_resize_handle.setAccessibleName(t("desktop.resize_result_drawer"))
        self.drawer_resize_handle.setToolTip(t("desktop.resize_result_drawer_hint"))
        self._update_drawer_mode_button()
        for action, key in (
            (self.import_channels_action, "desktop.import_channels"),
            (self.import_playback_sources_action, "desktop.import_playback_sources"),
            (self.export_channel_template_action, "desktop.export_channel_template"),
            (self.export_playback_sources_action, "desktop.export_playback_sources"),
            (self.export_diagnostics_action, "desktop.export_diagnostics_csv"),
            (self.copy_action, "desktop.copy_source_url"),
            (self.drawer_add_result_action, "desktop.add_result"),
            (self.copy_stream_action, "desktop.copy_stream_url"),
            (self.play_result_action, "desktop.play"),
            (self.retest_result_action, "desktop.retest_result"),
            (self.include_output_action, "desktop.include_output"),
            (self.exclude_output_action, "desktop.exclude_output"),
            (self.pin_output_action, "desktop.pin_output"),
            (self.auto_output_action, "desktop.auto_select_output"),
            (self.whitelist_action, "desktop.add_whitelist"),
            (self.blacklist_action, "desktop.add_blacklist"),
            (self.preview_screenshot_action, "desktop.preview_screenshot"),
            (self.capture_screenshot_action, "desktop.refresh_screenshot"),
            (self.start_result_stream_action, "desktop.open_selected_streams"),
            (self.stop_result_stream_action, "desktop.stop_stream"),
            (self.delete_result_action, "desktop.delete_result"),
            (self.channel_retest_action, "desktop.retest_channel"),
            (self.channel_add_action, "desktop.add_channel"),
            (self.channel_add_result_action, "desktop.add_result"),
            (self.channel_edit_logo_action, "desktop.edit_channel_logo"),
            (self.channel_play_action, "desktop.play"),
            (self.channel_delete_action, "desktop.delete_channel"),
            (self.channel_stream_action, "desktop.open_selected_streams"),
            (self.channel_stop_stream_action, "desktop.stop_stream"),
        ):
            action.setText(t(key))
        self.channel_model.set_columns(self._channel_columns())
        self.result_model.set_columns(self._result_columns())
        self.reload()
        self.set_stream_snapshot(self._stream_snapshot)
