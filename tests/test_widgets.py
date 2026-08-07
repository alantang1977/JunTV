import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QWidget
from qfluentwidgets import TableView

from desktop_ui.pages.settings import SettingsPage
from desktop_ui.models import ConfigTableModel, MappingTableModel
from desktop_ui.widgets import TableCheckBoxHeader, apply_dialog_theme, localize_dialog_buttons, paint_table_checkbox, warning_message_box
from utils.i18n import get_language, set_language


class TableCheckBoxPaintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _render(state):
        image = QImage(24, 24, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_table_checkbox(painter, QRect(0, 0, 24, 24), state)
        painter.end()
        return bytes(image.constBits())

    def test_integer_and_enum_checked_states_render_identically(self):
        self.assertEqual(
            self._render(Qt.CheckState.Checked.value),
            self._render(Qt.CheckState.Checked),
        )


class DialogStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_standard_dialog_buttons_are_localized_and_themed(self):
        language = get_language()
        self.addCleanup(set_language, language)
        set_language("en")
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        localize_dialog_buttons(buttons)
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Save).text(), "Save")
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), "Cancel")

        set_language("zh_CN")
        localize_dialog_buttons(buttons)
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Save).text(), "保存")
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), "取消")

        with patch("desktop_ui.widgets.isDarkTheme", return_value=True):
            apply_dialog_theme(dialog)
        self.assertIn("#202020", dialog.styleSheet())


class SettingsEditorLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_persistent_editors_relayout_when_hidden_page_is_shown(self):
        page = SettingsPage()
        self.addCleanup(page.close)
        self.app.processEvents()

        index = next(
            page.model.index(row, 1)
            for row in range(page.model.rowCount())
            if page.model.flags(page.model.index(row, 1)) & Qt.ItemFlag.ItemIsEditable
        )
        editor = page.table.indexWidget(index)
        self.assertIsNotNone(editor)

        page.resize(900, 600)
        page.show()
        for _ in range(3):
            self.app.processEvents()

        cell = page.table.visualRect(index)
        self.assertEqual(editor.geometry().left(), cell.left() + 6)
        self.assertEqual(editor.geometry().right(), cell.right() - 6)
        self.assertGreaterEqual(editor.geometry().top(), cell.top())
        self.assertLessEqual(editor.geometry().bottom(), cell.bottom())

    def test_advanced_port_settings_are_hidden_until_searched(self):
        model = ConfigTableModel()
        visible_keys = {row["key"] for row in model.rows}
        self.assertIn("service_port", visible_keys)
        self.assertIn("public_url", visible_keys)
        self.assertNotIn("app_port", visible_keys)
        self.assertNotIn("nginx_http_port", visible_keys)

        model.filter("app_port")

        self.assertEqual([row["key"] for row in model.rows], ["app_port"])

        model.filter("nginx_http_port")
        legacy_row = next(
            index
            for index, row in enumerate(model.rows)
            if row["key"] == "nginx_http_port"
        )
        legacy_index = model.index(legacy_row, 1)
        self.assertFalse(model.flags(legacy_index) & Qt.ItemFlag.ItemIsEditable)


class TableSortingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mapping_and_config_models_sort_from_header_requests(self):
        model = MappingTableModel([("value", "Value", None)])
        model.set_rows([{"value": 2}, {"value": None}, {"value": 1}])
        model.sort(0, Qt.SortOrder.AscendingOrder)
        self.assertEqual(model.rows, [{"value": 1}, {"value": 2}, {"value": None}])
        model.sort(0, Qt.SortOrder.DescendingOrder)
        model.set_rows([{"value": 4}, {"value": 2}, {"value": 3}])
        self.assertEqual(model.rows, [{"value": 4}, {"value": 3}, {"value": 2}])

        config_model = ConfigTableModel()
        config_model.filter("port")
        config_model.sort(0, Qt.SortOrder.DescendingOrder)
        keys = [row["key"] for row in config_model.rows]
        self.assertEqual(keys, sorted(keys, reverse=True))

    def test_mapping_model_skips_identical_refreshes_and_rejects_stale_indexes(self):
        model = MappingTableModel([("value", "Value", None)])
        model.set_rows([{"value": 1}])
        reset_count = 0

        def count_reset():
            nonlocal reset_count
            reset_count += 1

        model.modelReset.connect(count_reset)
        stale_index = model.index(0, 0)
        model.set_rows([{"value": 1}])
        self.assertEqual(reset_count, 0)

        model.set_rows([])
        self.assertEqual(reset_count, 1)
        self.assertIsNone(model.data(stale_index))
        self.assertFalse(model.setData(stale_index, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole))

    def test_checkable_header_click_toggles_direction_and_sorts_rows(self):
        model = MappingTableModel([
            ("batch_selected", "", None),
            ("value", "Value", None),
        ], checkable_key="batch_selected")
        model.set_rows([
            {"batch_selected": False, "value": 1},
            {"batch_selected": False, "value": 3},
            {"batch_selected": False, "value": 2},
        ])
        table = TableView()
        self.addCleanup(table.deleteLater)
        table.setModel(model)
        table.setSortingEnabled(True)
        table.resize(400, 200)
        header = TableCheckBoxHeader(table)
        table.setHorizontalHeader(header)
        header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        table.show()
        self.app.processEvents()

        self.assertTrue(header.sectionsClickable())
        self.assertTrue(header.isSortIndicatorShown())
        click_position = QPoint(header.sectionViewportPosition(1) + 20, 10)
        QTest.mouseClick(
            header.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            click_position,
        )
        self.app.processEvents()
        self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual([row["value"] for row in model.rows], [3, 2, 1])

        QTest.mouseClick(
            header.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            click_position,
        )
        self.app.processEvents()
        self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)
        self.assertEqual([row["value"] for row in model.rows], [1, 2, 3])

    def test_warning_message_box_has_warning_icon(self):
        parent = QWidget()
        parent.resize(800, 600)
        self.addCleanup(parent.deleteLater)
        box = warning_message_box("Delete", "This cannot be undone.", parent)
        self.addCleanup(box.deleteLater)

        icon = box.findChild(QWidget, "warningIcon")
        self.assertIsNotNone(icon)
        self.assertFalse(box.windowIcon().isNull())


if __name__ == "__main__":
    unittest.main()
