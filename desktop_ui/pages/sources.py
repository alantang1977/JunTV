import os
import re
from collections import defaultdict

from PySide6.QtCore import QIODevice, QSaveFile, QSettings, QSignalBlocker, QSize, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QPalette, QTextCursor
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHeaderView, QHBoxLayout, QLabel, QSplitter, QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, ComboBox, FlowLayout, FluentIcon, InfoBar, InfoBarPosition, PushButton, SegmentedWidget, StrongBodyLabel, ToolButton, TreeWidget, isDarkTheme, qconfig

import utils.constants as constants
from desktop_ui.widgets import AccentPushButton, AppLineEdit, AppPlainTextEdit, AppSearchLineEdit, ContinuousTreeItemDelegate, DangerPushButton, TableCheckBoxDelegate, TableCheckBoxHeader, apply_dialog_theme, configure_table_columns, localize_dialog_buttons, warning_message_box
from desktop_ui.dialogs.local_source_import import LocalSourceImportDialog
from desktop_ui.dialogs.source_import import SourceImportDialog
from utils.config import config, resource_path
from utils.i18n import t
from utils.local_source_importer import merge_records, parse_local_source_file


class AliasTagsEditor(QWidget):
    changed = Signal()
    colors = ("#2563EB", "#7C3AED", "#DB2777", "#059669", "#D97706", "#0891B2")

    def __init__(self, aliases=None, parent=None):
        super().__init__(parent)
        self.setObjectName("aliasTagsEditor")
        self.setStyleSheet("QWidget#aliasTagsEditor { background-color: transparent; }")
        self.aliases = list(aliases or [])
        self.tags = QWidget(self)
        self.tags.setObjectName("aliasTags")
        self.tags.setStyleSheet("QWidget#aliasTags { background-color: transparent; }")
        self.flow = FlowLayout(self.tags, isTight=True)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setHorizontalSpacing(5)
        self.flow.setVerticalSpacing(4)
        self.edit_button = ToolButton(FluentIcon.PENCIL_INK, self)
        self.edit_button.setToolTip(t("desktop.edit_aliases"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 4, 3)
        layout.setSpacing(6)
        layout.addWidget(self.tags, 1)
        layout.addWidget(self.edit_button)
        self.edit_button.clicked.connect(self.edit)
        self._rebuild()

    def _rebuild(self):
        self.flow.removeAllWidgets()
        foreground = "#F8FAFC"
        for index, alias in enumerate(self.aliases[:5]):
            tag = QLabel(alias, self.tags)
            tag.setStyleSheet(
                f"QLabel {{ color: {foreground}; background-color: {self.colors[index % len(self.colors)]}; "
                "border-radius: 9px; padding: 2px 8px; }"
            )
            self.flow.addWidget(tag)
        if len(self.aliases) > 5:
            more = QLabel(f"+{len(self.aliases) - 5}", self.tags)
            background = "#475569" if isDarkTheme() else "#E2E8F0"
            color = "#F8FAFC" if isDarkTheme() else "#334155"
            more.setStyleSheet(f"QLabel {{ color: {color}; background-color: {background}; border-radius: 9px; padding: 2px 8px; }}")
            self.flow.addWidget(more)
        self.setToolTip("\n".join(self.aliases))

    def edit(self):
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(t("desktop.edit_aliases"))
        dialog.resize(480, 360)
        layout = QVBoxLayout(dialog)
        hint = BodyLabel(t("desktop.alias_editor_hint"), dialog)
        editor = AppPlainTextEdit(dialog)
        editor.setPlainText("\n".join(self.aliases))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save, parent=dialog)
        localize_dialog_buttons(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(hint)
        layout.addWidget(editor, 1)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = [part.strip() for line in editor.toPlainText().splitlines() for part in line.split(",") if part.strip()]
        self.aliases = list(dict.fromkeys(values))
        self._rebuild()
        self.changed.emit()


class SourceEditor(QWidget):
    ALL_GROUPS = "__all_groups__"
    UNGROUPED = "__ungrouped__"

    def __init__(self, kind: str, path_provider, parent=None):
        super().__init__(parent)
        self.setObjectName("sourceEditor")
        self.kind = kind
        self.path_provider = path_provider
        self.rows = []
        self.comments = defaultdict(list)
        self.group_order = []
        self._syncing = False
        self.loaded = False
        self._active_group = self.ALL_GROUPS
        self._category_items = {}
        self._template_view_mode = str(QSettings().value("appearance/source_template_view", "category"))
        if self._template_view_mode not in {"category", "list"}:
            self._template_view_mode = "category"
        self.raw_editor = AppPlainTextEdit(self)
        self.raw_editor.setLineWrapMode(AppPlainTextEdit.LineWrapMode.NoWrap)
        self.table = QTableWidget(self)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.check_header = TableCheckBoxHeader(self.table)
        self.table.setHorizontalHeader(self.check_header)
        self.table.setItemDelegateForColumn(0, TableCheckBoxDelegate(self.table))
        self.check_header.sortIndicatorChanged.connect(self._schedule_table_order_sync)
        self.search = AppSearchLineEdit(self)
        self.search.setPlaceholderText(t("desktop.search_source_data"))
        self.search.setMaximumWidth(320)
        self.view_switch = SegmentedWidget(self)
        self.view_switch.addItem("category", t("desktop.channel_view_category"), icon=FluentIcon.FOLDER)
        self.view_switch.addItem("list", t("desktop.channel_view_list"), icon=FluentIcon.MENU)
        self.view_switch.setMinimumWidth(210)
        self.view_switch.setCurrentItem(self._template_view_mode)
        self.view_switch.setVisible(self.kind == "template")
        self.selection_label = BodyLabel("", self)
        self.selection_label.hide()
        self.move_button = PushButton(FluentIcon.MOVE, t("desktop.move_to_category"), self)
        self.move_button.setVisible(self.kind == "template")
        self.import_button = AccentPushButton(FluentIcon.FOLDER, t("desktop.import_files"), self)
        self.add_button = AccentPushButton(FluentIcon.ADD, t("desktop.add_item"), self)
        self.delete_button = DangerPushButton(FluentIcon.DELETE, t("desktop.delete_item"), self)
        self.mode_button = ToolButton(FluentIcon.PENCIL_INK, self)
        self.mode_button.setToolTip(t("desktop.open_raw_editor"))

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(2, 2, 2, 2)
        toolbar.setSpacing(8)
        toolbar.addWidget(self.view_switch)
        toolbar.addWidget(self.search)
        toolbar.addStretch(1)
        toolbar.addWidget(self.selection_label)
        toolbar.addWidget(self.move_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.mode_button)
        visual = QWidget(self)
        visual_layout = QVBoxLayout(visual)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        if self.kind == "template":
            self.template_splitter = QSplitter(Qt.Orientation.Horizontal, visual)
            self.category_sidebar = self._create_category_sidebar(self.template_splitter)
            self.template_splitter.addWidget(self.category_sidebar)
            self.template_splitter.addWidget(self.table)
            self.template_splitter.setCollapsible(0, False)
            self.template_splitter.setStretchFactor(0, 0)
            self.template_splitter.setStretchFactor(1, 1)
            width = max(190, min(320, int(QSettings().value("appearance/source_category_width", 228))))
            self.template_splitter.setSizes([width, 900])
            self.template_splitter.splitterMoved.connect(self._save_category_width)
            visual_layout.addWidget(self.template_splitter)
        else:
            self.category_sidebar = None
            visual_layout.addWidget(self.table)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(visual)
        self.stack.addWidget(self.raw_editor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self.stack, 1)
        self.table.itemChanged.connect(self._visual_changed)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.check_header.toggled.connect(self._toggle_visible_rows)
        self.view_switch.currentItemChanged.connect(self._set_template_view_mode)
        self.move_button.clicked.connect(self.move_selected_to_category)
        self.import_button.clicked.connect(self.import_files)
        self.add_button.clicked.connect(self.add_item)
        self.delete_button.clicked.connect(self.delete_items)
        self.mode_button.clicked.connect(self.toggle_mode)
        self.search.textChanged.connect(self._filter_rows)
        qconfig.themeChangedFinished.connect(self._schedule_theme_refresh)
        self._apply_theme()

    def _create_category_sidebar(self, parent):
        sidebar = CardWidget(parent)
        sidebar.setMinimumWidth(190)
        sidebar.setMaximumWidth(320)
        sidebar.setBorderRadius(8)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QVBoxLayout()
        self.category_heading = StrongBodyLabel(t("desktop.channel_categories"), sidebar)
        self.add_category_button = ToolButton(FluentIcon.FOLDER_ADD, sidebar)
        self.rename_category_button = ToolButton(FluentIcon.EDIT, sidebar)
        self.category_up_button = ToolButton(FluentIcon.UP, sidebar)
        self.category_down_button = ToolButton(FluentIcon.DOWN, sidebar)
        self.delete_category_button = ToolButton(FluentIcon.DELETE, sidebar)
        self.add_category_button.setToolTip(t("desktop.add_category"))
        self.rename_category_button.setToolTip(t("desktop.rename_category"))
        self.category_up_button.setToolTip(t("desktop.move_category_up"))
        self.category_down_button.setToolTip(t("desktop.move_category_down"))
        self.delete_category_button.setToolTip(t("desktop.delete_category"))
        header.addWidget(self.category_heading)
        category_actions = QHBoxLayout()
        category_actions.addStretch(1)
        for button in (
            self.add_category_button,
            self.rename_category_button,
            self.category_up_button,
            self.category_down_button,
            self.delete_category_button,
        ):
            category_actions.addWidget(button)
        header.addLayout(category_actions)
        layout.addLayout(header)

        self.category_tree = TreeWidget(sidebar)
        self.category_tree.setColumnCount(2)
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setRootIsDecorated(False)
        self.category_tree.setIndentation(0)
        self.category_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.category_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.category_tree.setBorderVisible(False)
        self._apply_category_tree_surface(self.category_tree)
        self.category_tree.setItemDelegate(ContinuousTreeItemDelegate(self.category_tree))
        self.category_tree.header().setStretchLastSection(False)
        self.category_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.category_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.category_tree.itemClicked.connect(self._category_clicked)
        layout.addWidget(self.category_tree, 1)

        self.add_category_button.clicked.connect(self.add_category)
        self.rename_category_button.clicked.connect(self.rename_category)
        self.category_up_button.clicked.connect(lambda: self.move_category(-1))
        self.category_down_button.clicked.connect(lambda: self.move_category(1))
        self.delete_category_button.clicked.connect(self.delete_category)
        self._update_category_actions()
        return sidebar

    @staticmethod
    def _apply_category_tree_surface(tree):
        """Keep the category list inside its rounded card rather than a white viewport."""
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

    def _save_category_width(self, *_):
        sizes = self.template_splitter.sizes()
        if sizes and sizes[0] > 0:
            QSettings().setValue("appearance/source_category_width", sizes[0])

    def _set_template_view_mode(self, mode):
        if self.kind != "template" or mode not in {"category", "list"}:
            return
        self._template_view_mode = mode
        QSettings().setValue("appearance/source_template_view", mode)
        self._apply_template_view_mode()
        self._filter_rows()

    def _apply_template_view_mode(self):
        if self.kind != "template":
            return
        categorized = self._template_view_mode == "category"
        self.category_sidebar.setVisible(categorized)
        if self.table.columnCount() >= 3:
            self.table.setColumnHidden(2, categorized)
        adaptive = getattr(self.table.horizontalHeader(), "_adaptive_columns", None)
        if adaptive:
            adaptive.fit()

    @staticmethod
    def _category_item(tree, label, count, route, icon):
        item = QTreeWidgetItem([label, str(count)])
        item.setData(0, Qt.ItemDataRole.UserRole, route)
        item.setIcon(0, icon.icon() if hasattr(icon, "icon") else icon)
        item.setSizeHint(0, QSize(0, 36))
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tree.addTopLevelItem(item)
        return item

    @staticmethod
    def _category_display_label(value):
        label = re.sub(r"^[^A-Za-z0-9\u4e00-\u9fff]+", "", str(value or "")).strip()
        return label or str(value or "")

    def _ordered_groups(self):
        return list(dict.fromkeys(
            self.group_order
            + [row.get("group", "") for row in self.rows if row.get("group")]
        ))

    def _row_matches_search(self, row):
        term = self.search.text().strip().lower()
        if not term:
            return True
        values = []
        for key, value in row.items():
            if key.startswith("_"):
                continue
            values.extend(value if isinstance(value, list) else [value])
        return term in " ".join(str(value) for value in values).lower()

    def _rebuild_category_tree(self):
        if self.kind != "template":
            return
        blocker = QSignalBlocker(self.category_tree)
        self.category_tree.clear()
        self._category_items = {}
        matched_rows = [row for row in self.rows if self._row_matches_search(row)]
        self._category_items[self.ALL_GROUPS] = self._category_item(
            self.category_tree,
            t("desktop.all_channels"),
            len(matched_rows),
            self.ALL_GROUPS,
            FluentIcon.LIBRARY,
        )
        for group in self._ordered_groups():
            self._category_items[group] = self._category_item(
                self.category_tree,
                self._category_display_label(group),
                sum(row.get("group") == group for row in matched_rows),
                group,
                FluentIcon.FOLDER,
            )
        if any(not row.get("group") for row in self.rows):
            self._category_items[self.UNGROUPED] = self._category_item(
                self.category_tree,
                t("desktop.uncategorized"),
                sum(not row.get("group") for row in matched_rows),
                self.UNGROUPED,
                FluentIcon.QUESTION,
            )
        if self._active_group not in self._category_items:
            self._active_group = self.ALL_GROUPS
        self.category_tree.setCurrentItem(self._category_items.get(self._active_group))
        del blocker
        self._update_category_actions()

    def _category_clicked(self, item, _column):
        self._active_group = item.data(0, Qt.ItemDataRole.UserRole)
        self._update_category_actions()
        self._filter_rows()

    def _update_category_actions(self):
        if self.kind != "template":
            return
        group_selected = self._active_group not in {self.ALL_GROUPS, self.UNGROUPED}
        self.rename_category_button.setEnabled(group_selected)
        self.delete_category_button.setEnabled(group_selected)
        if group_selected and self._active_group in self.group_order:
            index = self.group_order.index(self._active_group)
            self.category_up_button.setEnabled(index > 0)
            self.category_down_button.setEnabled(index < len(self.group_order) - 1)
        else:
            self.category_up_button.setEnabled(False)
            self.category_down_button.setEnabled(False)

    def _category_name_dialog(self, title, initial=""):
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(title)
        form = QFormLayout(dialog)
        name = AppLineEdit(dialog)
        name.setText(initial)
        name.selectAll()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        localize_dialog_buttons(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(t("desktop.category_name"), name)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return name.text().strip()

    def _category_target_dialog(self, title, groups):
        dialog = QDialog(self)
        apply_dialog_theme(dialog)
        dialog.setWindowTitle(title)
        form = QFormLayout(dialog)
        target = ComboBox(dialog)
        for label, value in groups:
            target.addItem(label, userData=value)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        localize_dialog_buttons(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(t("desktop.target_category"), target)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return target.currentData()

    def _category_targets(self, exclude=None):
        targets = [
            (group, group)
            for group in self._ordered_groups()
            if group != exclude
        ]
        targets.append((t("desktop.uncategorized"), self.UNGROUPED))
        return targets

    def _refresh_template_rows(self):
        self.raw_editor.setPlainText(self._serialize())
        self._rebuild_table()

    def add_category(self):
        name = self._category_name_dialog(t("desktop.add_category"))
        if not name:
            return
        if name in self._ordered_groups():
            InfoBar.warning(
                t("desktop.category_exists"),
                name,
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        self.group_order.append(name)
        self._active_group = name
        self._refresh_template_rows()

    def rename_category(self):
        old = self._active_group
        if old not in self._ordered_groups():
            return
        name = self._category_name_dialog(t("desktop.rename_category"), old)
        if not name or name == old:
            return
        if name in self._ordered_groups():
            InfoBar.warning(
                t("desktop.category_exists"),
                name,
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        self.group_order = [name if group == old else group for group in self.group_order]
        for row in self.rows:
            if row.get("group") == old:
                row["group"] = name
        if old in self.comments:
            self.comments[name].extend(self.comments.pop(old))
        self._active_group = name
        self._refresh_template_rows()

    def move_category(self, offset):
        group = self._active_group
        if group not in self.group_order:
            return
        index = self.group_order.index(group)
        target = index + offset
        if target < 0 or target >= len(self.group_order):
            return
        self.group_order[index], self.group_order[target] = (
            self.group_order[target],
            self.group_order[index],
        )
        self._refresh_template_rows()

    def delete_category(self):
        group = self._active_group
        if group not in self._ordered_groups():
            return
        affected = [row for row in self.rows if row.get("group") == group]
        if affected:
            target = self._category_target_dialog(
                t("desktop.delete_category"),
                self._category_targets(exclude=group),
            )
            if target is None:
                return
            box = warning_message_box(
                t("desktop.delete_category"),
                t("desktop.delete_category_with_items_confirm").format(name=group),
                self,
            )
            if not box.exec():
                return
            destination = "" if target == self.UNGROUPED else target
            for row in affected:
                row["group"] = destination
        else:
            box = warning_message_box(
                t("desktop.delete_category"),
                t("desktop.delete_category_confirm").format(name=group),
                self,
            )
            if not box.exec():
                return
            destination = ""
        self.group_order = [value for value in self.group_order if value != group]
        if group in self.comments:
            self.comments[destination].extend(self.comments.pop(group))
        self._active_group = destination or self.UNGROUPED
        self._refresh_template_rows()

    def _move_selected_to_group(self, target):
        selected = self._selected_row_indices()
        if not selected:
            return
        destination = "" if target == self.UNGROUPED else target
        for index in selected:
            self.rows[index]["group"] = destination
        self._active_group = target
        self._refresh_template_rows()

    def move_selected_to_category(self):
        if self.kind != "template" or not self._selected_row_indices():
            return
        target = self._category_target_dialog(
            t("desktop.move_to_category"),
            self._category_targets(),
        )
        if target is not None:
            self._move_selected_to_group(target)

    def _schedule_theme_refresh(self):
        QTimer.singleShot(0, self._apply_theme)

    def _apply_theme(self, *_):
        dark = isDarkTheme()
        background = "#202020" if dark else "#FFFFFF"
        alternate = "#262626" if dark else "#F8FAFC"
        foreground = "#E2E8F0" if dark else "#1F2937"
        border = "#3F3F46" if dark else "#E2E8F0"
        header = "#27272A" if dark else "#F8FAFC"
        header_text = "#CBD5E1" if dark else "#475569"
        selected = "#1E3A5F" if dark else "#DBEAFE"
        selected_text = "#F8FAFC" if dark else "#0F172A"

        palette = self.table.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(background))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(alternate))
        palette.setColor(QPalette.ColorRole.Text, QColor(foreground))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(selected))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(selected_text))
        self.table.setPalette(palette)
        self.table.viewport().setPalette(palette)
        self.table.viewport().setAutoFillBackground(True)
        self.table.setStyleSheet(
            f"""
            QTableWidget {{
                color: {foreground};
                background-color: {background};
                alternate-background-color: {alternate};
                border: 1px solid {border};
                gridline-color: {border};
            }}
            QTableWidget::item {{
                color: {foreground};
                background-color: {background};
            }}
            QTableWidget::item:selected {{
                color: {selected_text};
                background-color: {selected};
            }}
            QHeaderView::section, QTableCornerButton::section {{
                color: {header_text};
                background-color: {header};
                border: none;
                border-right: 1px solid {border};
                border-bottom: 1px solid {border};
                padding: 6px;
            }}
            """
        )
        self.setStyleSheet("QWidget#sourceEditor { background-color: transparent; }")
        self.stack.setStyleSheet("QStackedWidget { background-color: transparent; border: none; }")
        if self.category_sidebar:
            self._apply_category_tree_surface(self.category_tree)

    def path(self):
        return resource_path(self.path_provider(), persistent=True)

    def load(self):
        try:
            with open(self.path(), "r", encoding="utf-8") as file:
                content = file.read()
        except FileNotFoundError:
            content = ""
        self.raw_editor.setPlainText(content)
        self.raw_editor.document().setModified(False)
        self._parse(content)
        self._rebuild_table()
        self.loaded = True

    def save(self):
        if self.stack.currentIndex() == 0:
            self._visual_changed()
        path = self.path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        target = QSaveFile(path)
        if target.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Text):
            target.write(self.raw_editor.toPlainText().encode("utf-8"))
            if target.commit():
                self.raw_editor.document().setModified(False)
                return path, ""
        return "", target.errorString()

    def toggle_mode(self):
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.add_button.hide()
            self.delete_button.hide()
            self.move_button.hide()
            self.view_switch.hide()
            self.selection_label.hide()
            self.mode_button.setIcon(FluentIcon.TILES)
            self.mode_button.setToolTip(t("desktop.open_visual_editor"))
        else:
            self._parse(self.raw_editor.toPlainText())
            self._rebuild_table()
            self.stack.setCurrentIndex(0)
            self.add_button.show()
            self.delete_button.show()
            self.move_button.setVisible(self.kind == "template")
            self.view_switch.setVisible(self.kind == "template")
            self.mode_button.setIcon(FluentIcon.PENCIL_INK)
            self.mode_button.setToolTip(t("desktop.open_raw_editor"))
        self._filter_rows()

    def _parse(self, content: str):
        self.rows = []
        self.comments = defaultdict(list)
        self.group_order = []
        section = ""
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                self.comments[section].append(raw)
                continue
            if self.kind == "template" and line.endswith(",#genre#"):
                section = line.rsplit(",#genre#", 1)[0]
                if section not in self.group_order:
                    self.group_order.append(section)
                continue
            if self.kind == "subscribe" and line.upper() == "[WHITELIST]":
                section = "WHITELIST"
                continue
            if self.kind == "whitelist" and line.upper() == "[KEYWORDS]":
                section = "KEYWORDS"
                continue
            if self.kind == "alias" and "," not in line:
                self.comments[section].append(raw)
                continue
            self._parse_data_line(section, line)
        for row in self.rows:
            row["_checked"] = False

    def _parse_data_line(self, section: str, line: str):
        if self.kind == "template":
            self.rows.append({"group": section, "name": line})
        elif self.kind == "local":
            name, separator, value = line.partition(",")
            self.rows.append({"channel": name if separator else "", "url": value if separator else name})
        elif self.kind == "subscribe":
            url, separator, options = line.partition(" ")
            self.rows.append({"whitelist": section == "WHITELIST", "url": url, "options": options.strip() if separator else ""})
        elif self.kind == "epg":
            url, separator, options = line.partition(" ")
            self.rows.append({"url": url, "options": options.strip() if separator else ""})
        elif self.kind == "whitelist":
            channel, separator, value = line.partition(",")
            self.rows.append({
                "rule_type": "keyword" if section == "KEYWORDS" else "exact",
                "channel": channel if separator else "",
                "value": value if separator else channel,
            })
        elif self.kind == "blacklist":
            self.rows.append({"keyword": line})
        elif self.kind == "alias":
            parts = [part.strip() for part in line.split(",")]
            canonical = parts[0]
            aliases = parts[1:] or [""]
            existing = next((row for row in self.rows if row["canonical"] == canonical), None)
            if existing:
                existing["aliases"] = list(dict.fromkeys(existing["aliases"] + aliases))
            else:
                self.rows.append({"canonical": canonical, "aliases": list(dict.fromkeys(aliases))})

    def _headers(self):
        return [""] + {
            "template": [t("name.channel"), t("desktop.column_category")],
            "local": [t("name.channel"), t("desktop.source_url")],
            "subscribe": [t("desktop.source_url"), t("desktop.column_whitelist"), t("desktop.source_options")],
            "epg": [t("desktop.source_url"), t("desktop.source_options")],
            "whitelist": [t("name.channel"), t("desktop.column_match"), t("desktop.column_rule")],
            "blacklist": [t("desktop.keyword")],
            "alias": [t("desktop.column_canonical"), t("desktop.column_aliases")],
        }[self.kind]

    def _column_widths(self):
        return [42] + {
            "template": [260, 160],
            "local": [180, 520],
            "subscribe": [520, 85, 260],
            "epg": [520, 260],
            "whitelist": [180, 420, 110],
            "blacklist": [520],
            "alias": [180, 520],
        }[self.kind]

    def _rebuild_table(self):
        self._syncing = True
        sorting_enabled = self.table.isSortingEnabled()
        self.table.setUpdatesEnabled(False)
        blocker = QSignalBlocker(self.table)
        try:
            self.table.setSortingEnabled(False)
            headers = self._headers()
            self.table.clear()
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.setRowCount(len(self.rows))
            for row_index, row in enumerate(self.rows):
                self._populate_row(row_index, row)
            configure_table_columns(
                self.table,
                self._column_widths(),
                f"sources.{self.kind}.selectable",
                fixed_widths={0: 42},
            )
            if self.kind == "alias":
                self.table.verticalHeader().setDefaultSectionSize(46)
            self.table.setSortingEnabled(sorting_enabled)
        finally:
            del blocker
            self.table.setUpdatesEnabled(True)
            self._syncing = False
        self._rebuild_category_tree()
        self._apply_template_view_mode()
        self._filter_rows()

    def _populate_row(self, row_index: int, row: dict):
        checkbox = QTableWidgetItem()
        checkbox.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        checkbox.setCheckState(
            Qt.CheckState.Checked if row.get("_checked") else Qt.CheckState.Unchecked
        )
        self.table.setItem(row_index, 0, checkbox)
        if self.kind == "subscribe":
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row.get("whitelist") else Qt.CheckState.Unchecked)
            self._set_text_item(row_index, 1, row.get("url", ""))
            self.table.setItem(row_index, 2, item)
            self._set_text_item(row_index, 3, row.get("options", ""))
            return
        if self.kind == "whitelist":
            self._set_text_item(row_index, 1, row.get("channel", ""))
            self._set_text_item(row_index, 2, row.get("value", ""))
            self._set_combo(row_index, 3, [(t("desktop.rule_exact"), "exact"), (t("desktop.rule_keyword"), "keyword")], row.get("rule_type"))
            return
        if self.kind == "alias":
            self._set_text_item(row_index, 1, row.get("canonical", ""))
            aliases = AliasTagsEditor(row.get("aliases", []), self.table)
            aliases.changed.connect(self._visual_changed)
            self.table.setCellWidget(row_index, 2, aliases)
            return
        keys = {
            "template": ("name", "group"),
            "local": ("channel", "url"),
            "epg": ("url", "options"),
            "blacklist": ("keyword",),
        }[self.kind]
        for column, key in enumerate(keys):
            self._set_text_item(row_index, column + 1, row.get(key, ""))

    def _set_text_item(self, row: int, column: int, value: str):
        self.table.setItem(row, column, QTableWidgetItem(value))

    def _set_combo(self, row: int, column: int, options, value):
        combo = ComboBox(self.table)
        for label, data in options:
            combo.addItem(label, userData=data)
        target = next((index for index in range(combo.count()) if combo.itemData(index) == value), 0)
        combo.setCurrentIndex(target)
        combo.currentIndexChanged.connect(self._visual_changed)
        self.table.setCellWidget(row, column, combo)

    def _visual_changed(self, *_):
        if self._syncing:
            return
        self._sync_rows_from_table()

    def _schedule_table_order_sync(self, *_):
        if not self._syncing:
            QTimer.singleShot(0, self._sync_rows_from_table)

    def _sync_rows_from_table(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.rows = [self._read_row(row) for row in range(self.table.rowCount())]
            self.raw_editor.setPlainText(self._serialize())
        finally:
            self._syncing = False
        if self.kind == "template":
            self._filter_rows()

    def _read_row(self, row: int):
        text = lambda column: self.table.item(row, column + 1).text().strip() if self.table.item(row, column + 1) else ""
        checked = self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        if self.kind == "template":
            result = {"group": text(1), "name": text(0)}
        elif self.kind == "local":
            result = {"channel": text(0), "url": text(1)}
        elif self.kind == "subscribe":
            result = {
                "whitelist": self.table.item(row, 2).checkState() == Qt.CheckState.Checked,
                "url": text(0),
                "options": text(2),
            }
        elif self.kind == "epg":
            result = {"url": text(0), "options": text(1)}
        elif self.kind == "whitelist":
            result = {
                "rule_type": self.table.cellWidget(row, 3).currentData(),
                "channel": text(0),
                "value": text(1),
            }
        elif self.kind == "blacklist":
            result = {"keyword": text(0)}
        else:
            aliases = self.table.cellWidget(row, 2)
            result = {"canonical": text(0), "aliases": list(aliases.aliases)}
        result["_checked"] = checked
        return result

    def _serialize(self):
        lines = []
        if self.kind == "template":
            groups = list(dict.fromkeys(self.group_order + [row["group"] for row in self.rows if row["group"]]))
            lines.extend(self.comments.get("", []))
            ungrouped = [
                row["name"]
                for row in self.rows
                if not row.get("group") and row.get("name")
            ]
            lines.extend(ungrouped)
            if ungrouped and groups:
                lines.append("")
            for group in groups:
                lines.append(f"{group},#genre#")
                lines.extend(self.comments.get(group, []))
                lines.extend(row["name"] for row in self.rows if row["group"] == group and row["name"])
                lines.append("")
        elif self.kind == "subscribe":
            lines.extend(self.comments.get("", []))
            lines.extend(self._subscription_line(row) for row in self.rows if not row["whitelist"] and row["url"])
            lines.extend(["", "[WHITELIST]"])
            lines.extend(self.comments.get("WHITELIST", []))
            lines.extend(self._subscription_line(row) for row in self.rows if row["whitelist"] and row["url"])
        elif self.kind == "whitelist":
            lines.extend(self.comments.get("", []))
            lines.extend(self._whitelist_line(row) for row in self.rows if row["rule_type"] == "exact" and row["value"])
            lines.extend(["", "[KEYWORDS]"])
            lines.extend(self.comments.get("KEYWORDS", []))
            lines.extend(self._whitelist_line(row) for row in self.rows if row["rule_type"] == "keyword" and row["value"])
        elif self.kind == "alias":
            lines.extend(self.comments.get("", []))
            grouped = defaultdict(list)
            for row in self.rows:
                for value in row["aliases"]:
                    if row["canonical"] and value:
                        grouped[row["canonical"]].append(value)
            lines.extend(f"{name},{','.join(aliases)}" for name, aliases in grouped.items())
        else:
            lines.extend(self.comments.get("", []))
            for row in self.rows:
                value = self._simple_line(row)
                if value:
                    lines.append(value)
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _subscription_line(row):
        return f"{row['url']} {row['options']}".rstrip()

    @staticmethod
    def _whitelist_line(row):
        return f"{row['channel']},{row['value']}" if row["channel"] else row["value"]

    def _simple_line(self, row):
        if self.kind == "local":
            return f"{row['channel']},{row['url']}" if row["channel"] else row["url"]
        if self.kind == "epg":
            return f"{row['url']} {row['options']}".rstrip()
        return row.get("keyword", "")

    def _visible_row_indices(self):
        return [
            row
            for row in range(self.table.rowCount())
            if not self.table.isRowHidden(row)
        ]

    def _selected_row_indices(self):
        return {
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, 0)
            and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        }

    def _toggle_visible_rows(self, checked):
        visible_rows = self._visible_row_indices()
        self._syncing = True
        self.table.setUpdatesEnabled(False)
        blocker = QSignalBlocker(self.table)
        try:
            for row in visible_rows:
                item = self.table.item(row, 0)
                if item:
                    item.setCheckState(
                        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                    )
        finally:
            del blocker
            self.table.setUpdatesEnabled(True)
            self._syncing = False
        self._visual_changed()
        self._update_selection_state()

    def _selection_changed(self, *_):
        if self._syncing:
            return
        self._update_selection_state()

    def _update_selection_state(self):
        selected_count = 0
        visible_count = 0
        visible_checked_count = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if not item:
                continue
            checked = item.checkState() == Qt.CheckState.Checked
            selected_count += checked
            if not self.table.isRowHidden(row):
                visible_count += 1
                visible_checked_count += checked
        self.selection_label.setText(
            t("desktop.source_items_selected").format(count=selected_count)
        )
        self.selection_label.setVisible(
            selected_count > 0 and self.stack.currentIndex() == 0
        )
        self.delete_button.setEnabled(selected_count > 0)
        self.move_button.setEnabled(self.kind == "template" and selected_count > 0)

        if not visible_count or visible_checked_count == 0:
            state = Qt.CheckState.Unchecked
        elif visible_checked_count == visible_count:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.check_header.set_check_state(state)

    def add_item(self):
        self.search.clear()
        defaults = {
            "template": {"group": "", "name": ""},
            "local": {"channel": "", "url": ""},
            "subscribe": {"whitelist": False, "url": "", "options": ""},
            "epg": {"url": "", "options": ""},
            "whitelist": {"rule_type": "exact", "channel": "", "value": ""},
            "blacklist": {"keyword": ""},
            "alias": {"canonical": "", "aliases": []},
        }
        if self.kind == "template":
            if self._template_view_mode == "category" and self._active_group not in {
                self.ALL_GROUPS,
                self.UNGROUPED,
            }:
                defaults["template"]["group"] = self._active_group
            elif self.rows:
                defaults["template"]["group"] = self.rows[-1].get("group", "")
        row_data = defaults[self.kind]
        row_data["_checked"] = False
        self.rows.append(row_data)
        self._rebuild_table()
        row = self.table.rowCount() - 1
        self.table.selectRow(row)
        edit_column = 2 if self.kind == "local" else 1
        self.table.editItem(self.table.item(row, edit_column))

    def import_files(self):
        if self.kind != "local":
            self._import_source_files()
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("desktop.import_local_sources"),
            "",
            t("desktop.import_file_filter"),
        )
        if not paths:
            return

        records = []
        errors = []
        for path in paths:
            parsed, invalid = parse_local_source_file(path)
            records.extend(parsed)
            errors.extend(invalid)
        merge_records(self.rows, records)

        dialog = LocalSourceImportDialog(records, errors, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_records()
        if not selected:
            InfoBar.warning(
                t("desktop.import_local_sources"),
                t("desktop.import_no_selection"),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        self.search.clear()
        self.rows.extend(
            {"channel": record.channel, "url": record.url, "_checked": False}
            for record in selected
        )
        self._rebuild_table()
        self.raw_editor.setPlainText(self._serialize())
        InfoBar.success(
            t("desktop.import_completed"),
            t("desktop.import_unsaved").format(count=len(selected)),
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _import_source_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("desktop.import_sources").format(source=self._source_label()),
            "",
            t("desktop.import_source_file_filter"),
        )
        if not paths:
            return

        records = []
        for path in paths:
            records.extend(self._parse_import_file(path))
        self._mark_import_duplicates(records)

        dialog = SourceImportDialog(
            t("desktop.import_sources").format(source=self._source_label()),
            records,
            self._import_columns(),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_records()
        if not selected:
            InfoBar.warning(
                t("desktop.import_sources").format(source=self._source_label()),
                t("desktop.import_no_selection"),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        self.search.clear()
        for record in selected:
            self._append_imported_row(record["row"])
        self._rebuild_table()
        self.raw_editor.setPlainText(self._serialize())
        InfoBar.success(
            t("desktop.import_completed"),
            t("desktop.import_unsaved").format(count=len(selected)),
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _source_label(self):
        return t({
            "template": "desktop.template",
            "subscribe": "name.subscribe",
            "epg": "name.epg",
            "whitelist": "name.whitelist",
            "blacklist": "desktop.blacklist",
            "alias": "desktop.alias",
        }[self.kind])

    def _import_columns(self):
        return {
            "template": [(t("name.channel"), "name"), (t("desktop.column_category"), "group")],
            "subscribe": [(t("desktop.source_url"), "url"), (t("desktop.column_whitelist"), "whitelist_text"), (t("desktop.source_options"), "options")],
            "epg": [(t("desktop.source_url"), "url"), (t("desktop.source_options"), "options")],
            "whitelist": [(t("name.channel"), "channel"), (t("desktop.column_match"), "value"), (t("desktop.column_rule"), "rule_text")],
            "blacklist": [(t("desktop.keyword"), "keyword")],
            "alias": [(t("desktop.column_canonical"), "canonical"), (t("desktop.column_aliases"), "aliases_text")],
        }[self.kind]

    @staticmethod
    def _read_import_text(path):
        with open(path, "rb") as source:
            data = source.read()
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("unknown", data, 0, len(data), "unsupported text encoding")

    def _parse_import_file(self, path):
        file_name = os.path.basename(path)
        try:
            content = self._read_import_text(path)
        except (OSError, UnicodeDecodeError) as error:
            return [{
                "file_name": file_name,
                "line_number": 0,
                "row": {},
                "status": "invalid",
                "reason": str(error),
                "selected": False,
            }]

        records = []
        section = ""
        for line_number, raw in enumerate(content.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if self.kind == "template" and line.endswith(",#genre#"):
                section = line.rsplit(",#genre#", 1)[0]
                continue
            if self.kind == "subscribe" and line.upper() == "[WHITELIST]":
                section = "WHITELIST"
                continue
            if self.kind == "whitelist" and line.upper() == "[KEYWORDS]":
                section = "KEYWORDS"
                continue
            row = self._parse_import_line(section, line)
            if row is None:
                continue
            records.append({
                "file_name": file_name,
                "line_number": line_number,
                "row": row,
                "status": "new",
                "reason": "",
                "selected": True,
            })
        return records

    def _parse_import_line(self, section, line):
        if self.kind == "template":
            return {"group": section, "name": line}
        if self.kind in {"subscribe", "epg"}:
            url, separator, options = line.partition(" ")
            row = {"url": url, "options": options.strip() if separator else ""}
            if self.kind == "subscribe":
                row["whitelist"] = section == "WHITELIST"
                row["whitelist_text"] = t("desktop.yes") if row["whitelist"] else t("desktop.no")
            return row
        if self.kind == "whitelist":
            channel, separator, value = line.partition(",")
            row = {
                "rule_type": "keyword" if section == "KEYWORDS" else "exact",
                "channel": channel if separator else "",
                "value": value if separator else channel,
            }
            row["rule_text"] = t("desktop.rule_keyword" if row["rule_type"] == "keyword" else "desktop.rule_exact")
            return row
        if self.kind == "blacklist":
            return {"keyword": line}
        if "," not in line:
            return None
        parts = [part.strip() for part in line.split(",")]
        canonical, aliases = parts[0], parts[1:] or [""]
        row = {"canonical": canonical, "aliases": list(dict.fromkeys(aliases))}
        row["aliases_text"] = ", ".join(row["aliases"])
        return row

    @staticmethod
    def _row_key(row):
        return tuple(
            (key, tuple(value) if isinstance(value, list) else value)
            for key, value in sorted(row.items())
            if not key.endswith("_text") and not key.startswith("_")
        )

    def _mark_import_duplicates(self, records):
        if self.kind == "alias":
            known_aliases = {
                row["canonical"]: set(row["aliases"])
                for row in self.rows
            }
            for record in records:
                if record["status"] != "new":
                    continue
                row = record["row"]
                aliases = known_aliases.setdefault(row["canonical"], set())
                new_aliases = [alias for alias in row["aliases"] if alias not in aliases]
                if not new_aliases:
                    record["status"] = "duplicate"
                    record["reason"] = "duplicate"
                    record["selected"] = False
                    continue
                aliases.update(new_aliases)
                row["aliases"] = new_aliases
                row["aliases_text"] = ", ".join(new_aliases)
            return
        known = {self._row_key(row) for row in self.rows}
        seen = set(known)
        for record in records:
            if record["status"] != "new":
                continue
            key = self._row_key(record["row"])
            if key in seen:
                record["status"] = "duplicate"
                record["reason"] = "duplicate"
                record["selected"] = False
            else:
                seen.add(key)

    def _append_imported_row(self, row):
        row = {
            key: list(value) if isinstance(value, list) else value
            for key, value in row.items()
            if not key.endswith("_text")
        }
        row["_checked"] = False
        if self.kind == "alias":
            existing = next((item for item in self.rows if item["canonical"] == row["canonical"]), None)
            if existing:
                existing["aliases"] = list(dict.fromkeys(existing["aliases"] + row["aliases"]))
                return
        self.rows.append(row)

    def delete_items(self):
        selected = sorted(self._selected_row_indices(), reverse=True)
        if not selected:
            return
        box = warning_message_box(
            t("desktop.delete_item"),
            t("desktop.delete_items_confirm").format(count=len(selected)),
            self,
        )
        if not box.exec():
            return
        selected_set = set(selected)
        self.rows = [row for index, row in enumerate(self.rows) if index not in selected_set]
        self._rebuild_table()
        self.raw_editor.setPlainText(self._serialize())

    def _filter_rows(self, *_):
        term = self.search.text().strip().lower()
        if self.stack.currentIndex() == 1:
            cursor = self.raw_editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.raw_editor.setTextCursor(cursor)
            if term:
                self.raw_editor.find(term)
            return
        self.table.setUpdatesEnabled(False)
        try:
            for index, row in enumerate(self.rows):
                values = []
                for key, value in row.items():
                    if key.startswith("_"):
                        continue
                    values.extend(value if isinstance(value, list) else [value])
                search_matches = not term or term in " ".join(str(value) for value in values).lower()
                category_matches = (
                    self.kind != "template"
                    or self._template_view_mode != "category"
                    or self._active_group == self.ALL_GROUPS
                    or self._active_group == self.UNGROUPED and not row.get("group")
                    or row.get("group") == self._active_group
                )
                matches = search_matches and category_matches
                self.table.setRowHidden(index, not matches)
        finally:
            self.table.setUpdatesEnabled(True)
        self._rebuild_category_tree()
        self._update_selection_state()

    def retranslate(self):
        self.view_switch.items["category"].setText(t("desktop.channel_view_category"))
        self.view_switch.items["list"].setText(t("desktop.channel_view_list"))
        self.move_button.setText(t("desktop.move_to_category"))
        self.import_button.setText(t("desktop.import_files"))
        self.add_button.setText(t("desktop.add_item"))
        self.delete_button.setText(t("desktop.delete_item"))
        self.search.setPlaceholderText(t("desktop.search_source_data"))
        self.mode_button.setToolTip(t("desktop.open_visual_editor" if self.stack.currentIndex() else "desktop.open_raw_editor"))
        if self.kind == "template":
            self.category_heading.setText(t("desktop.channel_categories"))
            self.add_category_button.setToolTip(t("desktop.add_category"))
            self.rename_category_button.setToolTip(t("desktop.rename_category"))
            self.category_up_button.setToolTip(t("desktop.move_category_up"))
            self.category_down_button.setToolTip(t("desktop.move_category_down"))
            self.delete_category_button.setToolTip(t("desktop.delete_category"))
        self._rebuild_table()
        self._rebuild_category_tree()


class SourcesPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sourcesPage")
        self.path_specs = [
            ("desktop.template", "template", lambda: config.source_file, FluentIcon.LAYOUT),
            ("name.local", "local", lambda: constants.local_path, FluentIcon.FOLDER),
            ("name.subscribe", "subscribe", lambda: constants.subscribe_path, FluentIcon.CLOUD_DOWNLOAD),
            ("name.epg", "epg", lambda: constants.epg_path, FluentIcon.CALENDAR),
            ("name.whitelist", "whitelist", lambda: constants.whitelist_path, FluentIcon.ACCEPT),
            ("desktop.blacklist", "blacklist", lambda: constants.blacklist_path, FluentIcon.HIDE),
            ("desktop.alias", "alias", lambda: constants.alias_path, FluentIcon.TAG),
        ]
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("sourcesTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setObjectName("sourcesTabBar")
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setDrawBase(False)
        self.editors = []
        for key, kind, provider, icon in self.path_specs:
            editor = SourceEditor(kind, provider, self)
            self.editors.append(editor)
            self.tabs.addTab(editor, icon.icon(), t(key))
        self.save_button = AccentPushButton(FluentIcon.SAVE, t("desktop.save"), self)
        self.export_button = PushButton(FluentIcon.DOCUMENT, t("desktop.export"), self)
        self.reload_button = PushButton(FluentIcon.SYNC, t("desktop.reload"), self)
        tab_actions = QWidget(self.tabs)
        tab_actions_layout = QHBoxLayout(tab_actions)
        tab_actions_layout.setContentsMargins(0, 0, 0, 0)
        tab_actions_layout.setSpacing(8)
        tab_actions_layout.addWidget(self.reload_button)
        tab_actions_layout.addWidget(self.export_button)
        tab_actions_layout.addWidget(self.save_button)
        self.tabs.setCornerWidget(tab_actions, Qt.Corner.TopRightCorner)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self.tabs, 1)
        self.reload_button.clicked.connect(self.load)
        self.export_button.clicked.connect(self.export)
        self.save_button.clicked.connect(self.save)
        self.tabs.currentChanged.connect(self._tab_changed)
        qconfig.themeChangedFinished.connect(self._schedule_theme_refresh)
        self._apply_theme()
        self.load()

    def _schedule_theme_refresh(self):
        QTimer.singleShot(0, self._apply_theme)

    def _apply_theme(self, *_):
        dark = isDarkTheme()
        background = "#202020" if dark else "#F3F4F6"
        tab_background = "#27272A" if dark else "#F1F5F9"
        tab_selected = "#323232" if dark else "#FFFFFF"
        foreground = "#CBD5E1" if dark else "#475569"
        selected_text = "#F8FAFC" if dark else "#0F172A"
        border = "#3F3F46" if dark else "#E2E8F0"

        self.tabs.setStyleSheet(
            f"""
            QTabWidget#sourcesTabs {{
                background-color: {background};
            }}
            QTabWidget#sourcesTabs::pane {{
                background-color: transparent;
                border: 1px solid {border};
            }}
            """
        )
        palette = self.tabs.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(background))
        palette.setColor(QPalette.ColorRole.Base, QColor(background))
        palette.setColor(QPalette.ColorRole.Button, QColor(tab_background))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(foreground))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(foreground))
        self.tabs.setPalette(palette)
        self.tabs.setAutoFillBackground(True)
        tab_bar = self.tabs.tabBar()
        tab_palette = tab_bar.palette()
        tab_palette.setColor(QPalette.ColorRole.Window, QColor(background))
        tab_palette.setColor(QPalette.ColorRole.Base, QColor(background))
        tab_bar.setPalette(tab_palette)
        tab_bar.setAutoFillBackground(True)
        tab_bar.setStyleSheet(
            f"""
            QTabBar#sourcesTabBar {{
                background-color: {background};
            }}
            QTabBar#sourcesTabBar::tab {{
                color: {foreground};
                background-color: {tab_background};
                border: 1px solid {border};
                padding: 7px 14px;
            }}
            QTabBar#sourcesTabBar::tab:selected {{
                color: {selected_text};
                background-color: {tab_selected};
                border-bottom-color: {tab_selected};
            }}
            """
        )
        page_palette = self.palette()
        page_palette.setColor(QPalette.ColorRole.Window, QColor(background))
        self.setPalette(page_palette)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"QWidget#sourcesPage {{ background-color: {background}; }}")
        for index, (_, _, _, icon) in enumerate(self.path_specs):
            self.tabs.setTabIcon(index, icon.icon())

    def current_editor(self):
        return self.editors[max(0, self.tabs.currentIndex())]

    def load(self):
        self.current_editor().load()

    def _tab_changed(self, _index):
        if not self.current_editor().loaded:
            self.current_editor().load()

    def save(self):
        path, error = self.current_editor().save()
        if path:
            InfoBar.success(t("desktop.saved"), path, parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.error(t("name.error"), error, parent=self, position=InfoBarPosition.TOP)

    def export(self):
        editor = self.current_editor()
        if editor.stack.currentIndex() == 0:
            editor._visual_changed()
        source_path = editor.path()
        path, _ = QFileDialog.getSaveFileName(
            self,
            t("desktop.export_sources"),
            os.path.basename(source_path),
            t("desktop.export_source_file_filter"),
        )
        if not path:
            return
        target = QSaveFile(path)
        if target.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Text):
            target.write(editor.raw_editor.toPlainText().encode("utf-8"))
            if target.commit():
                InfoBar.success(t("desktop.export_completed"), path, parent=self, position=InfoBarPosition.TOP)
                return
        InfoBar.error(t("name.error"), target.errorString(), parent=self, position=InfoBarPosition.TOP)

    def retranslate(self):
        self.save_button.setText(t("desktop.save"))
        self.export_button.setText(t("desktop.export"))
        self.reload_button.setText(t("desktop.reload"))
        for index, ((key, _, _, _), editor) in enumerate(zip(self.path_specs, self.editors)):
            self.tabs.setTabText(index, t(key))
            editor.retranslate()
