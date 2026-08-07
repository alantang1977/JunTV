import os
import re
from collections import OrderedDict, deque
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRect, QSize, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkRequest
from qfluentwidgets import FluentIcon
import pytz

from utils.config import config, resource_path
from utils.i18n import get_language, t


CONFIG_OPTIONS = {
    "speed_test_mode": ["quick", "full", "manual"],
    "update_time_position": ["top", "bottom"],
    "update_mode": ["interval", "time"],
    "public_scheme": ["http", "https"],
    "performance_mode": ["auto", "powersave", "balance", "fast"],
    "ipv_type": ["ipv4", "ipv6", "all"],
    "ipv_type_prefer": ["ipv4", "ipv6", "auto"],
    "logo_type": ["png", "jpg", "jpeg"],
    "rtmp_transcode_mode": ["copy", "auto"],
}

LEGACY_CONFIG_KEYS = {"urls_limit", "speed_test_target"}
PATH_CONFIG_KEYS = {"source_file", "final_file"}

ADVANCED_CONFIG_KEYS = {
    "app_port",
    "nginx_http_port",
    "nginx_rtmp_port",
    "public_domain",
    "public_scheme",
}


def _sortable_value(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 0, float(value)
    if isinstance(value, bool):
        return 0, int(value)
    text = str(value).strip()
    try:
        return 0, float(text)
    except (TypeError, ValueError):
        return 1, text.casefold()


def _sort_rows(rows, key, order):
    present = [row for row in rows if row.get(key) is not None]
    missing = [row for row in rows if row.get(key) is None]
    present.sort(
        key=lambda row: _sortable_value(row.get(key)),
        reverse=order == Qt.SortOrder.DescendingOrder,
    )
    return present + missing


def _framed_channel_icon(icon: QIcon) -> QIcon:
    rendered = icon.pixmap(QSize(64, 40))
    image = rendered.toImage()
    luminance = 0.0
    weight = 0
    left = image.width()
    top = image.height()
    right = -1
    bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            alpha = color.alpha()
            if alpha < 32:
                continue
            luminance += (0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()) * alpha
            weight += alpha
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if right >= left and bottom >= top:
        rendered = rendered.copy(QRect(left, top, right - left + 1, bottom - top + 1))
    source = rendered.scaled(
        QSize(28, 18),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    light_icon = weight == 0 or luminance / weight >= 165
    background = QColor("#334155" if light_icon else "#F8FAFC")
    border = QColor("#1E293B" if light_icon else "#CBD5E1")
    canvas = QPixmap(32, 24)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(border, 1))
    painter.setBrush(background)
    painter.drawRoundedRect(0.5, 0.5, 31, 23, 5, 5)
    painter.drawPixmap((32 - source.width()) // 2, (24 - source.height()) // 2, source)
    painter.end()
    return QIcon(canvas)


def _config_descriptions() -> dict[str, str]:
    path = resource_path("config/config.ini")
    descriptions = {}
    pending = ""
    english = get_language().startswith("en")
    try:
        with open(path, "r", encoding="utf-8") as file:
            for raw in file:
                line = raw.strip()
                if line.startswith("#"):
                    text = line[1:].strip()
                    parts = text.split(" | ", 1)
                    pending = parts[1] if english and len(parts) > 1 else parts[0]
                elif line.startswith(";"):
                    pending = ""
                elif "=" in line and not line.startswith("["):
                    key = line.split("=", 1)[0].strip()
                    descriptions[key] = pending
                    pending = ""
    except OSError:
        return {}
    return descriptions


def _config_kind(key: str, value: str) -> str:
    if key in PATH_CONFIG_KEYS:
        return "path"
    if key == "update_times":
        return "times"
    if key == "update_interval":
        return "hours"
    if key == "time_zone":
        return "timezone"
    if value.strip().lower() in {"true", "false"}:
        return "bool"
    if key in CONFIG_OPTIONS:
        return "options"
    if re.fullmatch(r"-?\d+", value.strip()):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", value.strip()):
        return "float"
    return "text"


class MappingTableModel(QAbstractTableModel):
    def __init__(self, columns: list[tuple[str, str, Callable | None]], parent=None, checkable_key: str | None = None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []
        self.checkable_key = checkable_key
        self._sort_column = None
        self._sort_order = Qt.SortOrder.AscendingOrder

    def set_rows(self, rows: list[dict]):
        rows = list(rows)
        if rows == self.rows:
            return
        self.beginResetModel()
        self.rows = rows
        if self._sort_column is not None:
            key = self.columns[self._sort_column][0]
            self.rows = _sort_rows(self.rows, key, self._sort_order)
        self.endResetModel()

    def set_columns(self, columns: list[tuple[str, str, Callable | None]]):
        if columns == self.columns:
            return
        self.beginResetModel()
        self.columns = columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            not index.isValid()
            or index.row() < 0
            or index.row() >= len(self.rows)
            or index.column() < 0
            or index.column() >= len(self.columns)
        ):
            return None
        row = self.rows[index.row()]
        key, _, formatter = self.columns[index.column()]
        value = row.get(key)
        if role == Qt.ItemDataRole.DisplayRole:
            return formatter(value, row) if formatter else "" if value is None else str(value)
        if role == Qt.ItemDataRole.CheckStateRole and key == self.checkable_key:
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.AccessibleDescriptionRole and key == "name" and row.get("streaming"):
            return row.get("stream_tooltip") or ""
        if role == Qt.ItemDataRole.ForegroundRole and key == "health":
            return {
                "healthy": QColor("#16a34a"),
                "warning": QColor("#d97706"),
                "offline": QColor("#dc2626"),
                "unknown": QColor("#64748b"),
            }.get(value)
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(value, (int, float)):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if (
            not index.isValid()
            or index.row() < 0
            or index.row() >= len(self.rows)
            or index.column() < 0
            or index.column() >= len(self.columns)
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        key = self.columns[index.column()][0]
        if key != self.checkable_key:
            return False
        self.rows[index.row()][key] = value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        return True

    def flags(self, index):
        flags = super().flags(index)
        if (
            index.isValid()
            and 0 <= index.row() < len(self.rows)
            and 0 <= index.column() < len(self.columns)
            and self.columns[index.column()][0] == self.checkable_key
        ):
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][1]
        return super().headerData(section, orientation, role)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column < 0 or column >= len(self.columns):
            return
        key = self.columns[column][0]
        if not self.rows:
            return
        self._sort_column = column
        self._sort_order = order
        self.layoutAboutToBeChanged.emit()
        self.rows = _sort_rows(self.rows, key, order)
        self.layoutChanged.emit()

    def row(self, index: QModelIndex | int) -> dict | None:
        row_index = index if isinstance(index, int) else index.row()
        return self.rows[row_index] if 0 <= row_index < len(self.rows) else None

    def check_state(self):
        if not self.checkable_key or not self.rows:
            return Qt.CheckState.Unchecked
        checked = sum(bool(row.get(self.checkable_key)) for row in self.rows)
        if checked == 0:
            return Qt.CheckState.Unchecked
        if checked == len(self.rows):
            return Qt.CheckState.Checked
        return Qt.CheckState.PartiallyChecked

    def set_all_checked(self, checked: bool):
        if not self.checkable_key or not self.rows:
            return
        for row in self.rows:
            row[self.checkable_key] = checked
        column = next((index for index, item in enumerate(self.columns) if item[0] == self.checkable_key), 0)
        self.dataChanged.emit(
            self.index(0, column),
            self.index(len(self.rows) - 1, column),
            [Qt.ItemDataRole.CheckStateRole],
        )


class ChannelLogoLoader(QObject):
    icon_ready = Signal(str)

    def __init__(self, parent=None, max_entries: int = 512, max_concurrent: int = 6):
        super().__init__(parent)
        self._icons = OrderedDict()
        self._display_icons = {}
        self._queued = set()
        self._queue = deque()
        self._active = {}
        self._max_entries = max_entries
        self._max_concurrent = max_concurrent
        self._fallback_source_icon = FluentIcon.VIDEO.icon()
        self._fallback_icon = _framed_channel_icon(self._fallback_source_icon)
        self._network = QNetworkAccessManager(self)
        cache = QNetworkDiskCache(self._network)
        cache_dir = os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation), "channel-logos")
        cache.setCacheDirectory(cache_dir)
        cache.setMaximumCacheSize(64 * 1024 * 1024)
        self._network.setCache(cache)

    @property
    def fallback_icon(self):
        return self._fallback_icon

    @property
    def fallback_source_icon(self):
        return self._fallback_source_icon

    def icon(self, logo: str):
        if self._ensure_icon(logo):
            return self._display_icons[logo]
        return None

    def source_icon(self, logo: str):
        if self._ensure_icon(logo):
            return self._icons[logo]
        return None

    def _ensure_icon(self, logo: str):
        if logo in self._icons:
            self._icons.move_to_end(logo)
            return True
        url = QUrl(logo)
        if url.isLocalFile():
            icon = QIcon(url.toLocalFile())
        elif not url.scheme():
            icon = QIcon(logo)
        else:
            self._enqueue(logo, url)
            return False
        if not icon.isNull():
            self._store(logo, icon)
        else:
            self._store_fallback(logo)
        return True

    def _enqueue(self, logo: str, url: QUrl):
        if logo in self._queued or logo in self._active:
            return
        self._queued.add(logo)
        self._queue.append((logo, url))
        self._start_next()

    def _start_next(self):
        while self._queue and len(self._active) < self._max_concurrent:
            logo, url = self._queue.popleft()
            self._queued.discard(logo)
            request = QNetworkRequest(url)
            request.setAttribute(
                QNetworkRequest.Attribute.CacheLoadControlAttribute,
                QNetworkRequest.CacheLoadControl.PreferCache,
            )
            reply = self._network.get(request)
            self._active[logo] = reply
            reply.finished.connect(lambda value=logo, item=reply: self._loaded(value, item))

    def _loaded(self, logo: str, reply):
        self._active.pop(logo, None)
        pixmap = QPixmap()
        if reply.error() == reply.NetworkError.NoError and pixmap.loadFromData(reply.readAll()):
            self._store(logo, QIcon(pixmap))
        else:
            self._store_fallback(logo)
        reply.deleteLater()
        self.icon_ready.emit(logo)
        self._start_next()

    def _store(self, logo: str, icon: QIcon):
        self._icons[logo] = icon
        self._display_icons[logo] = _framed_channel_icon(icon)
        self._trim_cache(logo)

    def _store_fallback(self, logo: str):
        self._icons[logo] = self._fallback_source_icon
        self._display_icons[logo] = self._fallback_icon
        self._trim_cache(logo)

    def _trim_cache(self, logo: str):
        self._icons.move_to_end(logo)
        while len(self._icons) > self._max_entries:
            expired, _ = self._icons.popitem(last=False)
            self._display_icons.pop(expired, None)


class ChannelTableModel(MappingTableModel):
    def __init__(self, columns, parent=None, checkable_key: str | None = None, logo_loader: ChannelLogoLoader | None = None):
        super().__init__(columns, parent, checkable_key)
        self._logo_loader = logo_loader or ChannelLogoLoader(self)
        self._logo_loader.icon_ready.connect(self._logo_loaded)

    @property
    def logo_loader(self):
        return self._logo_loader

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            index.isValid()
            and 0 <= index.row() < len(self.rows)
            and 0 <= index.column() < len(self.columns)
            and role == Qt.ItemDataRole.DecorationRole
        ):
            key = self.columns[index.column()][0]
            if key == "name":
                logo = str(self.rows[index.row()].get("logo") or "")
                if logo:
                    icon = self._logo_loader.icon(logo)
                    if icon:
                        return icon
                return self._logo_loader.fallback_icon
        if role == Qt.ItemDataRole.SizeHintRole and index.isValid() and self.columns[index.column()][0] == "name":
            return QSize(180, 38)
        return super().data(index, role)

    def _logo_loaded(self, logo: str):
        for row_index, row in enumerate(self.rows):
            if row.get("logo") == logo:
                column = next((i for i, item in enumerate(self.columns) if item[0] == "name"), 0)
                index = self.index(row_index, column)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])


class ConfigTableModel(QAbstractTableModel):
    KindRole = Qt.ItemDataRole.UserRole + 1
    OptionsRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all_rows = []
        self.rows = []
        self._sort_column = None
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.reload()

    def reload(self):
        self.beginResetModel()
        self.all_rows = []
        descriptions = _config_descriptions()
        for key, value in config.config.items("Settings"):
            if key == "language" or key in LEGACY_CONFIG_KEYS:
                continue
            if key == "service_port":
                value = str(config.service_port)
            env_name = config.environment_override_name(key)
            description = descriptions.get(key, "")
            advanced = key in ADVANCED_CONFIG_KEYS
            read_only = key == "nginx_http_port"
            if advanced:
                description = f"{t('desktop.advanced_setting')} · {description}"
            if read_only:
                description = f"{t('desktop.legacy_read_only')} · {description}"
            if env_name:
                description = f"{description} · {t('desktop.environment_override')}: {env_name}"
            self.all_rows.append({
                "key": key,
                "value": value,
                "description": description,
                "env_name": env_name,
                "kind": _config_kind(key, value),
                "options": pytz.common_timezones if key == "time_zone" else CONFIG_OPTIONS.get(key, []),
                "advanced": advanced,
                "read_only": read_only,
            })
        self.rows = [row for row in self.all_rows if not row["advanced"]]
        self._apply_sort()
        self.endResetModel()

    def filter(self, text: str):
        term = text.strip().lower()
        self.beginResetModel()
        self.rows = [
            row
            for row in self.all_rows
            if (
                (
                    term
                    and (
                        term in row["key"].lower()
                        or term in str(row["value"]).lower()
                        or term in row["description"].lower()
                    )
                )
                or (not term and not row["advanced"])
            )
        ]
        self._apply_sort()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            not index.isValid()
            or index.row() < 0
            or index.row() >= len(self.rows)
            or index.column() < 0
            or index.column() >= 3
        ):
            return None
        row = self.rows[index.row()]
        key = ("key", "value", "description")[index.column()]
        if (
            role == Qt.ItemDataRole.DisplayRole
            and index.column() == 1
            and not row["env_name"]
            and not row["read_only"]
        ):
            return ""
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return row[key]
        if role == self.KindRole:
            return row["kind"]
        if role == self.OptionsRole:
            return row["options"]
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 2:
            return row["description"]
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if (
            role != Qt.ItemDataRole.EditRole
            or not index.isValid()
            or index.row() < 0
            or index.row() >= len(self.rows)
            or index.column() != 1
        ):
            return False
        row = self.rows[index.row()]
        if row["env_name"] or row["read_only"]:
            return False
        if row["kind"] == "bool":
            enabled = value if isinstance(value, bool) else str(value).lower() == "true"
            row["value"] = "True" if enabled else "False"
        else:
            row["value"] = str(value)
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def flags(self, index):
        flags = super().flags(index)
        if (
            index.isValid()
            and 0 <= index.row() < len(self.rows)
            and index.column() == 1
            and not self.rows[index.row()]["env_name"]
            and not self.rows[index.row()]["read_only"]
        ):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return (t("desktop.config_key"), t("desktop.config_value"), t("desktop.config_description"))[section]
        return super().headerData(section, orientation, role)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column < 0 or column >= self.columnCount():
            return
        if not self.rows:
            return
        key = ("key", "value", "description")[column]
        self._sort_column = column
        self._sort_order = order
        self.layoutAboutToBeChanged.emit()
        self.rows = _sort_rows(self.rows, key, order)
        self.layoutChanged.emit()

    def _apply_sort(self):
        if self._sort_column is None:
            return
        key = ("key", "value", "description")[self._sort_column]
        self.rows = _sort_rows(self.rows, key, self._sort_order)

    def save(self):
        for row in self.all_rows:
            if not row["env_name"] and not row["read_only"]:
                config.set("Settings", row["key"], row["value"])
        config.save()
