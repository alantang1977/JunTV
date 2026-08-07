import math

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QToolTip
from qfluentwidgets import FluentIcon, TableItemDelegate

from utils.i18n import t


def _idle_countdown(value):
    if value is None:
        return "--"
    seconds = max(0, int(math.ceil(float(value))))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def build_channel_stream_states(snapshot: dict) -> dict[str, dict]:
    states = {}
    for stream in snapshot.get("streams", []):
        channel_key = stream.get("channel_key")
        result_key = stream.get("result_key")
        if not channel_key or not result_key:
            continue
        state = states.setdefault(channel_key, {
            "streaming": True,
            "stream_count": 0,
            "stream_active_count": 0,
            "stream_starting_count": 0,
            "stream_clients": 0,
            "stream_bw_out": 0.0,
            "stream_idle_remaining": None,
            "stream_result_keys": [],
        })
        state["stream_count"] += 1
        state["stream_starting_count"] += int(stream.get("state") == "starting")
        state["stream_active_count"] += int(stream.get("state") != "starting")
        state["stream_clients"] += int(stream.get("clients") or 0)
        state["stream_bw_out"] += float(stream.get("bw_out") or 0)
        state["stream_result_keys"].append(result_key)
        idle_remaining = stream.get("idle_remaining")
        if idle_remaining is not None:
            current = state["stream_idle_remaining"]
            state["stream_idle_remaining"] = float(idle_remaining) if current is None else min(
                current,
                float(idle_remaining),
            )
    for state in states.values():
        status = (
            t("desktop.stream_starting_badge")
            if state["stream_active_count"] == 0
            else t("desktop.stream_running_badge")
        )
        state["stream_indicator_state"] = "starting" if state["stream_active_count"] == 0 else "active"
        state["stream_tooltip"] = t("desktop.channel_stream_tooltip").format(
            status=status,
            streams=state["stream_count"],
            clients=state["stream_clients"],
            bandwidth=f"{state['stream_bw_out'] / 1000:.1f} Kbit/s",
            idle=_idle_countdown(state["stream_idle_remaining"]),
        )
    return states


def build_result_stream_states(snapshot: dict) -> dict[str, dict]:
    states = {}
    for stream in snapshot.get("streams", []):
        result_key = stream.get("result_key")
        if not result_key:
            continue
        state = states.setdefault(result_key, {
            "streaming": True,
            "stream_state": "starting",
        })
        if stream.get("state") != "starting":
            state["stream_state"] = "active"
    return states


def apply_channel_stream_state(row: dict, states: dict[str, dict]) -> dict:
    clean = {
        key: value
        for key, value in row.items()
        if not key.startswith("stream_") and key != "streaming"
    }
    return {**clean, **states.get(row.get("channel_key"), {"streaming": False})}


def apply_result_stream_state(row: dict, states: dict[str, dict]) -> dict:
    clean = {
        key: value
        for key, value in row.items()
        if not key.startswith("stream_") and key != "streaming"
    }
    return {
        **clean,
        **states.get(
            row.get("result_key"),
            {"streaming": False, "stream_state": "idle"},
        ),
    }


class StreamingStatusDelegate(TableItemDelegate):
    def __init__(self, callback, parent=None, trailing_width: int = 0):
        super().__init__(parent)
        self._callback = callback
        self._trailing_width = trailing_width
        self._active_icon = FluentIcon.IOT.icon(color=QColor("#10B981"))
        self._starting_icon = FluentIcon.IOT.icon(color=QColor("#F59E0B"))

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        if row.get("streaming"):
            option.rect.adjust(0, 0, -24, 0)

    def paint(self, painter: QPainter, option, index):
        primary_delegate = self.parent().delegate
        self.hoverRow = primary_delegate.hoverRow
        self.pressedRow = primary_delegate.pressedRow
        self.selectedRows = primary_delegate.selectedRows
        super().paint(painter, option, index)
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        if not row.get("streaming"):
            return
        icon = self._starting_icon if row.get("stream_indicator_state") == "starting" else self._active_icon
        icon.paint(painter, self._indicator_rect(option.rect), Qt.AlignmentFlag.AlignCenter)

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            row = index.data(Qt.ItemDataRole.UserRole) or {}
            if row.get("streaming") and self._indicator_rect(option.rect).contains(event.position().toPoint()):
                point = self.parent().viewport().mapToGlobal(
                    QPoint(self._indicator_rect(option.rect).left(), self._indicator_rect(option.rect).bottom())
                )
                self._callback(row, point)
                return True
        return False

    def helpEvent(self, event, view, option, index):
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        if (
            event.type() == QEvent.Type.ToolTip
            and row.get("streaming")
            and self._indicator_rect(option.rect).contains(event.pos())
        ):
            QToolTip.showText(event.globalPos(), row.get("stream_tooltip") or "", view)
            return True
        QToolTip.hideText()
        return False

    def _indicator_rect(self, rect: QRect):
        size = min(18, max(14, rect.height() - 14))
        right = rect.right() - self._trailing_width - 6
        top = rect.center().y() - size // 2
        return QRect(right - size + 1, top, size, size)
