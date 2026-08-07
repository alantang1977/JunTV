import math
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QSettings, QTimer, Signal, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QMouseEvent, QPainter, QPalette, QPen, QPixmap, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QDialogButtonBox, QGraphicsDropShadowEffect, QHeaderView, QHBoxLayout, QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, EditableComboBox, IconWidget, LineEdit, MessageBox, PlainTextEdit, PrimaryPushButton, PushButton, SearchLineEdit, StrongBodyLabel, TableItemDelegate, isDarkTheme, qconfig, setCustomStyleSheet
from utils.i18n import t


def apply_dialog_theme(dialog):
    """Apply the application's light or dark surface colors to a native dialog."""
    dark = isDarkTheme()
    background = "#202020" if dark else "#FFFFFF"
    surface = "#27272A" if dark else "#F8FAFC"
    foreground = "#E2E8F0" if dark else "#1F2937"
    muted = "#CBD5E1" if dark else "#475569"
    border = "#3F3F46" if dark else "#E2E8F0"
    hover = "#323232" if dark else "#E2E8F0"
    dialog.setStyleSheet(
        f"""
        QDialog {{ background-color: {background}; color: {foreground}; }}
        QLabel {{ color: {foreground}; }}
        QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {{
            background-color: {surface}; color: {foreground}; border: 1px solid {border};
            border-radius: 5px; padding: 4px 6px;
        }}
        QDialogButtonBox QPushButton {{
            background-color: {surface}; color: {muted}; border: 1px solid {border};
            border-radius: 5px; padding: 5px 14px; min-width: 72px;
        }}
        QDialogButtonBox QPushButton:hover {{ background-color: {hover}; color: {foreground}; }}
        """
    )


def localize_dialog_buttons(buttons):
    """Use application translations instead of platform defaults for standard buttons."""
    labels = {
        QDialogButtonBox.StandardButton.Save: "desktop.save",
        QDialogButtonBox.StandardButton.Cancel: "desktop.cancel",
        QDialogButtonBox.StandardButton.Close: "desktop.close",
        QDialogButtonBox.StandardButton.Ok: "desktop.confirm",
        QDialogButtonBox.StandardButton.Yes: "desktop.yes",
        QDialogButtonBox.StandardButton.No: "desktop.no",
    }
    for standard, key in labels.items():
        try:
            button = buttons.button(standard)
        except TypeError:
            button = buttons.button(type(buttons).StandardButton(standard.value))
        if button:
            button.setText(t(key))


def apply_input_border_style(widget, selector):
    states = ",\n".join([
        selector,
        f"{selector}:hover",
        f"{selector}:focus",
        f"{selector}[transparent=true]:focus",
        f"{selector}[transparent=false]:focus",
        f"{selector}:disabled",
    ])
    light = f"{states} {{ border-bottom-color: rgba(0, 0, 0, 13); }}"
    dark = f"{states} {{ border-bottom-color: rgba(255, 255, 255, 0.08); }}"
    setCustomStyleSheet(widget, light, dark)


def _table_check_state(state):
    if isinstance(state, Qt.CheckState):
        return state
    if isinstance(state, bool):
        return Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
    try:
        return Qt.CheckState(int(state))
    except (TypeError, ValueError):
        return Qt.CheckState.Unchecked


def paint_table_checkbox(painter, rect, state):
    state = _table_check_state(state)
    size = min(16, rect.width(), rect.height())
    box = QRect(rect.center().x() - size // 2, rect.center().y() - size // 2, size, size)
    checked = state in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked)
    border = QColor("#60A5FA" if isDarkTheme() else "#2563EB") if checked else QColor(
        "#94A3B8" if isDarkTheme() else "#64748B"
    )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(border, 1.6))
    painter.setBrush(QBrush(border if checked else Qt.BrushStyle.NoBrush))
    painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 3, 3)
    painter.setPen(QPen(
        QColor("#FFFFFF"),
        1.8,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    ))
    if state == Qt.CheckState.Checked:
        painter.drawLine(box.left() + 4, box.center().y(), box.left() + 7, box.bottom() - 4)
        painter.drawLine(box.left() + 7, box.bottom() - 4, box.right() - 3, box.top() + 4)
    elif state == Qt.CheckState.PartiallyChecked:
        painter.drawLine(box.left() + 4, box.center().y(), box.right() - 4, box.center().y())
    painter.restore()


class ContinuousTreeItemDelegate(QStyledItemDelegate):
    """Paint tree selection as one uninterrupted rounded row across columns."""

    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            if selected:
                if isDarkTheme():
                    color = QColor("#1D4ED8")
                    color.setAlpha(175)
                else:
                    color = QColor("#BFDBFE")
                    color.setAlpha(230)
            else:
                color = QColor(255, 255, 255, 12) if isDarkTheme() else QColor(15, 23, 42, 10)
            painter.setBrush(color)
            self._draw_row_segment(painter, option, index)
            painter.restore()

        clean_option = QStyleOptionViewItem(option)
        clean_option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        if selected:
            text_color = QColor("#F8FAFC" if isDarkTheme() else "#1E3A8A")
        else:
            text_color = QColor("#E2E8F0" if isDarkTheme() else "#1F2937")
        clean_option.palette.setColor(QPalette.ColorRole.Text, text_color)
        super().paint(painter, clean_option, index)

    def _draw_row_segment(self, painter, option, index):
        view = self.parent()
        if index.column() != 0:
            return
        rect = view.viewport().rect()
        rect.setTop(option.rect.top() + 2)
        rect.setBottom(option.rect.bottom() - 2)
        rect.adjust(2, 0, -2, 0)
        painter.drawRoundedRect(rect, 4.0, 4.0)


class TableCheckBoxHeader(QHeaderView):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._state = Qt.CheckState.Unchecked
        self._resizing = False
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(True)

    def set_check_state(self, state):
        if state != self._state:
            self._state = state
            self.viewport().update()

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if logical_index == 0:
            paint_table_checkbox(painter, rect, self._state)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._resizing:
            self._resizing = False
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self.logicalIndexAt(event.position().toPoint()) == 0:
            self.toggled.emit(self._state != Qt.CheckState.Checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.logicalIndexAt(event.position().toPoint()) == 0:
            boundary = self.sectionViewportPosition(0) + self.sectionSize(0)
            if abs(event.position().x() - boundary) <= 6:
                self._resizing = True
                super().mousePressEvent(event)
                return
            event.accept()
            return
        super().mousePressEvent(event)


def warning_message_box(title: str, content: str, parent=None):
    box = MessageBox(title, content, parent)
    warning_icon = box.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
    box.setWindowIcon(warning_icon)
    title_label = box.findChild(QLabel, "titleLabel")
    if title_label and title_label.parentWidget() and title_label.parentWidget().layout():
        title_layout = title_label.parentWidget().layout()
        title_layout.removeWidget(title_label)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        icon_label = QLabel(title_label.parentWidget())
        icon_label.setObjectName("warningIcon")
        icon_label.setFixedSize(22, 22)
        icon_label.setPixmap(warning_icon.pixmap(20, 20))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setToolTip(title)
        title_row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        title_layout.insertLayout(0, title_row)
    return box


class TableCheckBoxDelegate(TableItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator

    def paint(self, painter, option, index):
        primary_delegate = getattr(self.parent(), "delegate", None)
        if primary_delegate is None:
            QStyledItemDelegate.paint(self, painter, option, index)
            paint_table_checkbox(
                painter,
                option.rect,
                index.data(Qt.ItemDataRole.CheckStateRole),
            )
            return
        self.hoverRow = primary_delegate.hoverRow
        self.pressedRow = primary_delegate.pressedRow
        self.selectedRows = primary_delegate.selectedRows
        super().paint(painter, option, index)

    def _drawCheckBox(self, painter, option, index):
        paint_table_checkbox(
            painter,
            option.rect,
            index.data(Qt.ItemDataRole.CheckStateRole),
        )

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if option.rect.contains(event.position().toPoint()):
                checked = (
                    _table_check_state(index.data(Qt.ItemDataRole.CheckStateRole))
                    == Qt.CheckState.Checked
                )
                return model.setData(
                    index,
                    Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,
                    Qt.ItemDataRole.CheckStateRole,
                )
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
            checked = (
                _table_check_state(index.data(Qt.ItemDataRole.CheckStateRole))
                == Qt.CheckState.Checked
            )
            return model.setData(
                index,
                Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked,
                Qt.ItemDataRole.CheckStateRole,
            )
        return False


class _AdaptiveTableColumns(QObject):
    """Keep interactive table columns fitted to the viewport."""

    def __init__(self, table, widths: list[int], state_key: str, fixed_widths=None, minimum_widths=None):
        super().__init__(table)
        self.table = table
        self.viewport = table.viewport()
        self.header = table.horizontalHeader()
        self.state_key = state_key
        self.fixed_widths = dict(fixed_widths or {})
        self.minimum_widths = {
            column: max(1, int(width))
            for column, width in (minimum_widths or {}).items()
        }
        self._applying = False
        self._weights = self._load_weights(widths)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit)

        self.viewport.installEventFilter(self)
        self.header.sectionMoved.connect(self._schedule_save)
        self.header.sectionResized.connect(self._section_resized)
        QTimer.singleShot(0, self.fit)

    def _load_weights(self, widths: list[int]) -> list[float]:
        count = self.header.count()
        defaults = [float(width) for width in widths[:count]]
        if len(defaults) < count:
            defaults.extend([100.0] * (count - len(defaults)))

        settings = QSettings()
        saved_state = settings.value(f"appearance/table_headers/{self.state_key}")
        if saved_state is not None:
            self.header.restoreState(saved_state)
        else:
            for column, width in enumerate(defaults):
                self.header.resizeSection(column, int(width))

        saved_weights = settings.value(f"appearance/table_column_weights/{self.state_key}")
        if isinstance(saved_weights, (list, tuple)) and len(saved_weights) == count:
            try:
                parsed = [max(1.0, float(value)) for value in saved_weights]
                if all(value > 0 for value in parsed):
                    return parsed
            except (TypeError, ValueError):
                pass
        if saved_state is not None:
            return [float(max(1, self.header.sectionSize(column))) for column in range(count)]
        return defaults

    def eventFilter(self, watched, event):
        if watched is self.viewport and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self._fit_timer.start(0)
        return super().eventFilter(watched, event)

    def fit(self):
        visible = [
            column
            for column in range(self.header.count())
            if not self.header.isSectionHidden(column)
        ]
        available = self.viewport.width()
        if not visible or available <= 0:
            return

        minimum = self.header.minimumSectionSize()
        minimums = {
            column: max(minimum, self.minimum_widths.get(column, minimum))
            for column in visible
        }
        fixed = {
            column: width
            for column, width in self.fixed_widths.items()
            if column in visible
        }
        sizes = dict(fixed)
        pending = [column for column in visible if column not in fixed]
        remaining = available - sum(fixed.values())
        minimum_total = sum(minimums[column] for column in pending)
        for column in pending:
            sizes[column] = minimums[column]

        # Keep important columns readable even when the viewport is too narrow.
        # The table can then scroll horizontally instead of eliding their data.
        if remaining > minimum_total and pending:
            extra = remaining - minimum_total
            weight_total = sum(self._weights[column] for column in pending)
            for position, column in enumerate(pending):
                if position == len(pending) - 1:
                    sizes[column] += extra
                else:
                    share = round(extra * self._weights[column] / weight_total)
                    sizes[column] += share
                    extra -= share
                    weight_total -= self._weights[column]

        self._applying = True
        self.header.blockSignals(True)
        try:
            for column in visible:
                self.header.resizeSection(column, sizes[column])
        finally:
            self.header.blockSignals(False)
            self._applying = False
        # Blocking section signals keeps an adaptive resize from being treated
        # as a user resize, but it also prevents persistent cell editors from
        # receiving updated geometry. Re-layout the view after the final column
        # sizes are applied so hidden pages restore with correctly placed
        # editors.
        self.table.doItemsLayout()

    def _section_resized(self, *_):
        if self._applying:
            return
        self._weights = [
            float(max(1, self.header.sectionSize(column)))
            for column in range(self.header.count())
        ]
        self._fit_timer.start(150)
        self._schedule_save()

    def _schedule_save(self, *_):
        self._save_timer.start()

    def _save(self):
        settings = QSettings()
        settings.setValue(
            f"appearance/table_headers/{self.state_key}",
            self.header.saveState(),
        )
        settings.setValue(
            f"appearance/table_column_weights/{self.state_key}",
            self._weights,
        )


def configure_table_columns(table, widths: list[int], state_key: str, fixed_widths=None, minimum_widths=None):
    header = table.horizontalHeader()
    header.setCascadingSectionResizes(False)
    header.setMinimumSectionSize(40)
    header.setStretchLastSection(False)
    header.setSectionsMovable(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    for column, width in (fixed_widths or {}).items():
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(column, width)
    existing = getattr(header, "_adaptive_columns", None)
    if (
        existing is not None
        and existing.state_key == state_key
        and existing.fixed_widths == dict(fixed_widths or {})
        and existing.minimum_widths == {
            column: max(1, int(width))
            for column, width in (minimum_widths or {}).items()
        }
    ):
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        return
    header._adaptive_columns = _AdaptiveTableColumns(
        table,
        widths,
        state_key,
        fixed_widths=fixed_widths,
        minimum_widths=minimum_widths,
    )
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)


class AppLineEdit(LineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "LineEdit")


class AppSearchLineEdit(SearchLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "LineEdit")


class AppEditableComboBox(EditableComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "LineEdit")


class AppPlainTextEdit(PlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_input_border_style(self, "PlainTextEdit")


class AccentPushButton(PrimaryPushButton):
    def _postInit(self):
        super()._postInit()
        qconfig.themeChangedFinished.connect(self._apply_semantic_style)
        self._apply_semantic_style()

    def _apply_semantic_style(self, *_):
        self.setStyleSheet(
            "QPushButton { color: #FFFFFF; background-color: #2563EB; border: none; border-radius: 6px; padding: 6px 13px; }"
            "QPushButton[hasIcon=true] { padding-left: 31px; }"
            "QPushButton:hover { background-color: #1D4ED8; }"
            "QPushButton:pressed { background-color: #1E40AF; }"
            "QPushButton:disabled { color: #CBD5E1; background-color: #64748B; }"
        )

    def setIcon(self, icon):
        super().setIcon(icon.icon(color=QColor("#FFFFFF")) if hasattr(icon, "icon") else icon)


class DangerPushButton(PushButton):
    def _postInit(self):
        super()._postInit()
        qconfig.themeChangedFinished.connect(self._apply_semantic_style)
        self._apply_semantic_style()

    def _apply_semantic_style(self, *_):
        color = "#F87171" if isDarkTheme() else "#DC2626"
        border = "#EF4444" if isDarkTheme() else "#FCA5A5"
        hover = "rgba(248, 113, 113, 28)" if isDarkTheme() else "rgba(220, 38, 38, 18)"
        pressed = "rgba(248, 113, 113, 48)" if isDarkTheme() else "rgba(220, 38, 38, 34)"
        self.setStyleSheet(
            f"QPushButton {{ color: {color}; background-color: transparent; border: 1px solid {border}; border-radius: 6px; padding: 5px 12px; }}"
            "QPushButton[hasIcon=true] { padding-left: 31px; }"
            f"QPushButton:hover {{ background-color: {hover}; }}"
            f"QPushButton:pressed {{ background-color: {pressed}; }}"
            "QPushButton:disabled { color: #94A3B8; border-color: #64748B; background-color: transparent; }"
        )
        if hasattr(self, "_semantic_icon"):
            icon = self._semantic_icon
            PushButton.setIcon(self, icon.icon(color=QColor(color)) if hasattr(icon, "icon") else icon)

    def setIcon(self, icon):
        self._semantic_icon = icon
        color = QColor("#F87171" if isDarkTheme() else "#DC2626")
        super().setIcon(icon.icon(color=color) if hasattr(icon, "icon") else icon)


def play_circle_icon(color="#FFFFFF"):
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 2.1))
    painter.drawEllipse(3.5, 3.5, 25, 25)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygonF([QPointF(13, 10), QPointF(13, 22), QPointF(23, 16)]))
    painter.end()
    return QIcon(pixmap)


class MetricIconWidget(QWidget):
    """Icon renderer that can rotate without a costly graphics effect."""

    def __init__(self, icon=None, parent=None):
        super().__init__(parent)
        self._icon = icon or QIcon()
        self._angle = 0.0

    def setIcon(self, icon):
        self._icon = icon or QIcon()
        self.update()

    def set_angle(self, angle: float):
        self._angle = angle
        self.update()

    def paintEvent(self, _event):
        if self._icon.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)
        size = min(self.width(), self.height())
        self._icon.paint(painter, QRect(-size // 2, -size // 2, size, size))


class GlassCard(CardWidget):
    """A lightweight glass surface for focused, low-density dashboard content."""

    def __init__(self, parent=None, accent="#2563EB"):
        super().__init__(parent)
        self._accent = QColor(accent)
        self._pulse_until = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(33)
        self._pulse_timer.timeout.connect(self._advance_pulse)
        self.setBorderRadius(12)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(18)
        self._shadow.setOffset(0, 3)
        self.setGraphicsEffect(self._shadow)
        qconfig.themeChangedFinished.connect(self._apply_surface_style)
        self._apply_surface_style()

    def set_accent(self, accent: str):
        self._accent = QColor(accent)
        self.update()

    def pulse(self, duration_ms: int = 360):
        self._pulse_until = max(self._pulse_until, time.monotonic() + duration_ms / 1000)
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()
        self.update()

    def _advance_pulse(self):
        if time.monotonic() >= self._pulse_until:
            self._pulse_timer.stop()
        self.update()

    def _apply_surface_style(self, *_):
        shadow = QColor("#000000")
        shadow.setAlpha(70 if isDarkTheme() else 24)
        self._shadow.setColor(shadow)

    def paintEvent(self, _event):
        now = time.monotonic()
        pulse = 0.0
        if now < self._pulse_until:
            remaining = (self._pulse_until - now) / 0.36
            pulse = math.sin(max(0.0, min(1.0, remaining)) * math.pi)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = 12.0
        accent = QColor(self._accent)
        glass = QLinearGradient(0, rect.top(), 0, rect.bottom())
        if isDarkTheme():
            glass.setColorAt(0, QColor(39, 51, 70, 198 if self.isHover else 186))
            glass.setColorAt(1, QColor(22, 30, 45, 178 if self.isHover else 164))
            highlight = QColor(255, 255, 255, 42)
            border = QColor(255, 255, 255, 38)
        else:
            glass.setColorAt(0, QColor(255, 255, 255, 222 if self.isHover else 208))
            glass.setColorAt(1, QColor(241, 245, 249, 200 if self.isHover else 184))
            highlight = QColor(255, 255, 255, 178)
            border = QColor(148, 163, 184, 48)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glass)
        painter.drawRoundedRect(rect, radius, radius)
        radial = QRadialGradient(rect.right() - 12, rect.top() + 10, max(48, rect.width() * 0.36))
        tint = QColor(accent)
        tint.setAlpha(int((24 if isDarkTheme() else 14) + pulse * 24))
        radial.setColorAt(0, tint)
        radial.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setBrush(radial)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(highlight, 1.0))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        if pulse:
            border = QColor(accent)
            border.setAlpha(int(42 + pulse * 126))
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(rect, radius, radius)


class MetricCard(CardWidget):
    def __init__(
        self,
        title: str,
        value: str = "--",
        detail: str = "",
        icon=None,
        parent=None,
        accent="#0E5CAD",
        animate_value_updates: bool = False,
    ):
        super().__init__(parent)
        self._accent = QColor(accent)
        self._animate_value_updates = animate_value_updates
        self._activity = False
        self._rotate_icon = False
        self._activity_delay = 0.0
        self._activity_started_at = 0.0
        self._pulse_until = 0.0
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(33)  # 30 fps is ample for small dashboard accents.
        self._animation_timer.timeout.connect(self._advance_animation)
        self.setMinimumHeight(104)
        self.setBorderRadius(12)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(18)
        self._shadow.setOffset(0, 3)
        self.setGraphicsEffect(self._shadow)
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(14)
        self.icon_container = QWidget(self) if icon else None
        self.icon_widget = MetricIconWidget(icon.icon(color=QColor(accent)) if hasattr(icon, "icon") else icon, self.icon_container) if icon else None
        if self.icon_widget:
            self.icon_container.setFixedSize(42, 42)
            color = QColor(accent)
            self.icon_container.setStyleSheet(
                f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 38); border-radius: 10px;"
            )
            icon_layout = QHBoxLayout(self.icon_container)
            icon_layout.setContentsMargins(9, 9, 9, 9)
            self.icon_widget.setFixedSize(24, 24)
            icon_layout.addWidget(self.icon_widget)
            root.addWidget(self.icon_container, 0, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title_label = BodyLabel(title, self)
        self.value_label = StrongBodyLabel(value, self)
        font = self.value_label.font()
        font.setPointSize(20)
        font.setBold(True)
        self.value_label.setFont(font)
        self.detail_label = BodyLabel(detail, self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)
        root.addLayout(layout, 1)
        qconfig.themeChangedFinished.connect(self._apply_text_colors)
        qconfig.themeChangedFinished.connect(self._apply_surface_style)
        self._apply_text_colors()
        self._apply_surface_style()

    def _apply_text_colors(self, *_):
        dark = isDarkTheme()
        title = "#CBD5E1" if dark else "#475569"
        detail = "#94A3B8" if dark else "#64748B"
        value = self._accent.lighter(135).name() if dark else self._accent.name()
        self.title_label.setStyleSheet(f"color: {title};")
        self.value_label.setStyleSheet(f"color: {value};")
        self.detail_label.setStyleSheet(f"color: {detail};")

    def _apply_surface_style(self, *_):
        shadow = QColor("#000000")
        shadow.setAlpha(70 if isDarkTheme() else 24)
        self._shadow.setColor(shadow)

    def set_clickable(self, clickable: bool = True):
        self.setClickEnabled(clickable)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def set_value(self, value, detail: str | None = None):
        changed = self.value_label.text() != str(value)
        self.value_label.setText(str(value))
        if detail is not None:
            self.detail_label.setText(detail)
        if changed and self._activity and self._animate_value_updates:
            self.pulse()

    def set_activity(self, active: bool, *, delay_ms: int = 0, rotate_icon: bool = False):
        """Enable the restrained running-state animation for this dashboard card."""
        self._activity = active
        self._rotate_icon = active and rotate_icon
        self._activity_delay = delay_ms / 1000
        if active:
            self._activity_started_at = time.monotonic()
            if not self._animation_timer.isActive():
                self._animation_timer.start()
        elif time.monotonic() >= self._pulse_until:
            self._animation_timer.stop()
        self.update()

    def pulse(self, duration_ms: int = 360):
        """Briefly acknowledge a metric change without changing the layout."""
        self._pulse_until = max(self._pulse_until, time.monotonic() + duration_ms / 1000)
        if not self._animation_timer.isActive():
            self._animation_timer.start()

    def _advance_animation(self):
        now = time.monotonic()
        self._advance_icon_rotation()
        if not self._activity and now >= self._pulse_until:
            self._animation_timer.stop()
        self.update()

    def paintEvent(self, event):
        now = time.monotonic()
        elapsed = now - self._activity_started_at - self._activity_delay
        phase = max(0.0, elapsed) / 2.8
        pulse = 0.0
        if now < self._pulse_until:
            remaining = (self._pulse_until - now) / 0.36
            pulse = math.sin(max(0.0, min(1.0, remaining)) * math.pi)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = 12.0
        rect = self.rect().adjusted(1, 1, -1, -1)
        accent = QColor(self._accent)

        # A translucent two-layer surface reads as glass while keeping the
        # rendering path cheap and identical on every supported platform.
        glass = QLinearGradient(0, rect.top(), 0, rect.bottom())
        if isDarkTheme():
            glass.setColorAt(0, QColor(39, 51, 70, 204 if self.isHover else 190))
            glass.setColorAt(1, QColor(22, 30, 45, 184 if self.isHover else 170))
            highlight = QColor(255, 255, 255, 42)
            border_base = QColor(255, 255, 255, 38)
        else:
            glass.setColorAt(0, QColor(255, 255, 255, 224 if self.isHover else 210))
            glass.setColorAt(1, QColor(241, 245, 249, 202 if self.isHover else 186))
            highlight = QColor(255, 255, 255, 178)
            border_base = QColor(148, 163, 184, 48)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glass)
        painter.drawRoundedRect(rect, radius, radius)

        radial = QRadialGradient(rect.right() - 12, rect.top() + 10, max(48, rect.width() * 0.36))
        tint = QColor(accent)
        tint.setAlpha(24 if isDarkTheme() else 14)
        tint.setAlpha(int(tint.alpha() + pulse * 20))
        radial.setColorAt(0, tint)
        radial.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setBrush(radial)
        painter.drawRoundedRect(rect, radius, radius)

        # An inner top highlight makes the material feel layered rather than flat.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(highlight, 1.0))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        if self._activity:
            # A very low-opacity travelling wash gives activity context without obscuring text.
            width = max(1, rect.width())
            offset = (phase % 1.0) * width * 1.8 - width * 0.8
            glow = QLinearGradient(offset, 0, offset + width * 0.7, 0)
            transparent = QColor(accent)
            transparent.setAlpha(0)
            highlight = QColor(accent)
            highlight.setAlpha(16 if not isDarkTheme() else 24)
            glow.setColorAt(0, transparent)
            glow.setColorAt(0.5, highlight)
            glow.setColorAt(1, transparent)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(rect, radius, radius)
        border = QColor(accent)
        border.setAlpha(int(28 + pulse * 105 + (math.sin(phase * math.tau) + 1) * 8 if self._activity else 32 + pulse * 120))
        if not self._activity and pulse == 0:
            border = border_base
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(rect, radius, radius)

    def set_visual(self, icon=None, accent=None):
        """Update the card icon and accent colors without rebuilding its layout."""
        if accent is not None:
            self._accent = QColor(accent)
        if self.icon_widget and icon is not None:
            rendered_icon = icon.icon(color=self._accent) if hasattr(icon, "icon") else icon
            self.icon_widget.setIcon(rendered_icon)
        if self.icon_container:
            color = self._accent
            self.icon_container.setStyleSheet(
                f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 38); border-radius: 10px;"
            )
        self._apply_text_colors()

    def _advance_icon_rotation(self):
        if self.icon_widget:
            elapsed = max(0.0, time.monotonic() - self._activity_started_at - self._activity_delay)
            self.icon_widget.set_angle((elapsed * 90) % 360 if self._rotate_icon else 0)


class NavigationStatusIndicator(QWidget):
    """Small status glyph overlaid on a navigation icon without affecting layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.icon_widget = IconWidget(self)
        self.icon_widget.setFixedSize(8, 8)
        self.icon_widget.move(3, 3)
        self.hide()

    def set_status(self, icon, color: str):
        accent = QColor(color)
        rendered_icon = icon.icon(color=QColor("#FFFFFF")) if hasattr(icon, "icon") else icon
        self.icon_widget.setIcon(rendered_icon)
        self.setStyleSheet(
            f"background-color: {accent.name()}; border: 1px solid rgba(255, 255, 255, 190); border-radius: 7px;"
        )
        self.show()
        self.raise_()


def metric_row(cards: list[MetricCard]) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    for card in cards:
        layout.addWidget(card, 1)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    return widget
