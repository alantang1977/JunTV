import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_ui.pages.sources import SourceEditor


class SourceEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings = QSettings()
        self.previous_mode = self.settings.value("appearance/source_template_view")
        self.settings.setValue("appearance/source_template_view", "category")
        self.addCleanup(self._restore_settings)

    def _restore_settings(self):
        if self.previous_mode is None:
            self.settings.remove("appearance/source_template_view")
        else:
            self.settings.setValue(
                "appearance/source_template_view",
                self.previous_mode,
            )

    def _write(self, name, content):
        path = os.path.join(self.temp_dir.name, name)
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)
        return path

    def _editor(self, kind, path):
        editor = SourceEditor(kind, lambda: path)
        editor.load()
        self.addCleanup(editor.deleteLater)
        return editor

    def test_template_category_view_filters_and_moves_checked_channels(self):
        path = self._write(
            "demo.txt",
            "Loose\n\nGroup B,#genre#\nB1\nB2\n\nGroup A,#genre#\nA1\n",
        )
        editor = self._editor("template", path)

        self.assertEqual(editor.group_order, ["Group B", "Group A"])
        self.assertEqual(editor.view_switch.currentRouteKey(), "category")
        self.assertTrue(editor.table.isColumnHidden(2))
        self.assertEqual(
            [
                editor.category_tree.topLevelItem(index).data(
                    0,
                    Qt.ItemDataRole.UserRole,
                )
                for index in range(editor.category_tree.topLevelItemCount())
            ],
            [
                editor.ALL_GROUPS,
                "Group B",
                "Group A",
                editor.UNGROUPED,
            ],
        )

        editor._category_clicked(editor._category_items["Group B"], 0)
        self.assertEqual(editor._visible_row_indices(), [1, 2])
        editor._toggle_visible_rows(True)
        self.assertEqual(editor._selected_row_indices(), {1, 2})

        editor._move_selected_to_group("Group A")

        self.assertEqual(
            [row["group"] for row in editor.rows if row["name"] in {"B1", "B2"}],
            ["Group A", "Group A"],
        )
        self.assertNotIn("_checked", editor._serialize())

    def test_template_view_preference_and_category_order_are_persisted(self):
        path = self._write(
            "demo.txt",
            "Group B,#genre#\nB1\n\nGroup A,#genre#\nA1\n",
        )
        editor = self._editor("template", path)
        editor._active_group = "Group A"

        editor.move_category(-1)
        editor._set_template_view_mode("list")

        self.assertEqual(editor.group_order, ["Group A", "Group B"])
        self.assertLess(
            editor._serialize().index("Group A,#genre#"),
            editor._serialize().index("Group B,#genre#"),
        )
        self.assertEqual(
            self.settings.value("appearance/source_template_view"),
            "list",
        )
        self.assertFalse(editor.table.isColumnHidden(2))

        restored = self._editor("template", path)
        self.assertEqual(restored.view_switch.currentRouteKey(), "list")

    def test_category_rename_and_delete_migrate_channels(self):
        path = self._write(
            "demo.txt",
            "Group B,#genre#\nB1\n\nGroup A,#genre#\nA1\n",
        )
        editor = self._editor("template", path)
        editor._active_group = "Group B"
        editor._category_name_dialog = lambda *_: "Renamed"

        editor.rename_category()

        self.assertEqual(editor.group_order, ["Renamed", "Group A"])
        self.assertEqual(editor.rows[0]["group"], "Renamed")

        editor._active_group = "Renamed"
        editor._category_target_dialog = lambda *_: "Group A"
        with patch("desktop_ui.pages.sources.warning_message_box") as message_box:
            message_box.return_value.exec.return_value = True
            editor.delete_category()

        self.assertEqual(editor.group_order, ["Group A"])
        self.assertEqual(editor.rows[0]["group"], "Group A")

    def test_all_visual_source_lists_support_checkbox_batch_delete(self):
        fixtures = {
            "template": (
                "demo.txt",
                "Group,#genre#\nChannel 1\nChannel 2\n",
                3,
            ),
            "local": (
                "local.txt",
                "Channel 1,http://example.com/1\nChannel 2,http://example.com/2\n",
                3,
            ),
            "subscribe": (
                "subscribe.txt",
                "http://example.com/1\nhttp://example.com/2\n",
                4,
            ),
            "epg": (
                "epg.txt",
                "http://example.com/1\nhttp://example.com/2\n",
                3,
            ),
            "whitelist": (
                "whitelist.txt",
                "Channel 1,Match 1\nChannel 2,Match 2\n",
                4,
            ),
            "blacklist": (
                "blacklist.txt",
                "Keyword 1\nKeyword 2\n",
                2,
            ),
            "alias": (
                "alias.txt",
                "Channel 1,Alias 1\nChannel 2,Alias 2\n",
                3,
            ),
        }

        for kind, (name, content, columns) in fixtures.items():
            with self.subTest(kind=kind):
                path = self._write(name, content)
                editor = self._editor(kind, path)

                self.assertEqual(editor.table.columnCount(), columns)
                self.assertEqual(editor.table.horizontalHeaderItem(0).text(), "")
                self.assertEqual(editor.table.horizontalHeader().sectionSize(0), 42)

                editor.table.selectRow(0)
                self.assertEqual(editor._selected_row_indices(), set())

                editor._toggle_visible_rows(True)
                self.assertEqual(
                    editor.check_header._state,
                    Qt.CheckState.Checked,
                )
                self.assertEqual(editor._selected_row_indices(), {0, 1})

                editor.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
                self.assertEqual(editor._selected_row_indices(), {1})

                with patch("desktop_ui.pages.sources.warning_message_box") as message_box:
                    message_box.return_value.exec.return_value = True
                    editor.delete_items()

                self.assertEqual(len(editor.rows), 1)
                self.assertEqual(
                    editor.check_header._state,
                    Qt.CheckState.Unchecked,
                )

    def test_checked_source_row_can_be_unchecked_by_clicking_its_checkbox(self):
        path = self._write("blacklist.txt", "Keyword 1\nKeyword 2\n")
        editor = self._editor("blacklist", path)
        editor.show()
        self.app.processEvents()

        editor._toggle_visible_rows(True)
        item = editor.table.item(0, 0)
        QTest.mouseClick(
            editor.table.viewport(),
            Qt.MouseButton.LeftButton,
            pos=editor.table.visualItemRect(item).center(),
        )
        self.app.processEvents()

        self.assertEqual(item.checkState(), Qt.CheckState.Unchecked)
        self.assertEqual(editor._selected_row_indices(), {1})
        self.assertEqual(editor.check_header._state, Qt.CheckState.PartiallyChecked)

    def test_sorting_keeps_source_row_actions_aligned_with_visual_order(self):
        path = self._write(
            "local.txt",
            "Channel 1,http://example.com/1\nChannel 2,http://example.com/2\n",
        )
        editor = self._editor("local", path)
        editor.show()
        self.app.processEvents()

        editor.check_header.setSortIndicator(1, Qt.SortOrder.DescendingOrder)
        self.app.processEvents()
        self.assertEqual(
            [row["channel"] for row in editor.rows],
            ["Channel 2", "Channel 1"],
        )

        editor.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        with patch("desktop_ui.pages.sources.warning_message_box") as message_box:
            message_box.return_value.exec.return_value = True
            editor.delete_items()

        self.assertEqual([row["channel"] for row in editor.rows], ["Channel 1"])

    def test_local_source_import_adds_selected_records_without_writing_file(self):
        path = self._write("local.txt", "Existing,http://example.com/existing\n")
        imported_path = self._write(
            "import.txt",
            "Imported,http://example.com/imported\n",
        )
        editor = self._editor("local", path)

        class AcceptedImportDialog:
            def __init__(self, records, errors, parent):
                self.records = records

            def exec(self):
                return 1

            def selected_records(self):
                return self.records

        with patch(
            "desktop_ui.pages.sources.QFileDialog.getOpenFileNames",
            return_value=([imported_path], ""),
        ), patch(
            "desktop_ui.pages.sources.LocalSourceImportDialog",
            AcceptedImportDialog,
        ):
            editor.import_files()

        self.assertEqual(
            [(row["channel"], row["url"]) for row in editor.rows],
            [
                ("Existing", "http://example.com/existing"),
                ("Imported", "http://example.com/imported"),
            ],
        )
        with open(path, encoding="utf-8") as file:
            self.assertNotIn("Imported", file.read())

    def test_other_source_tabs_import_selected_records_without_writing_files(self):
        fixtures = {
            "template": ("template.txt", "Sports,#genre#\nSports TV\n", {"group": "Sports", "name": "Sports TV"}),
            "subscribe": ("subscribe.txt", "[WHITELIST]\nhttps://example.com/list proxy=on\n", {"whitelist": True, "url": "https://example.com/list", "options": "proxy=on"}),
            "epg": ("epg.txt", "https://example.com/epg.xml offset=8\n", {"url": "https://example.com/epg.xml", "options": "offset=8"}),
            "whitelist": ("whitelist.txt", "[KEYWORDS]\nCCTV,CCTV-1\n", {"rule_type": "keyword", "channel": "CCTV", "value": "CCTV-1"}),
            "blacklist": ("blacklist.txt", "广告\n", {"keyword": "广告"}),
            "alias": ("alias.txt", "CCTV-1,央视一套\n", {"canonical": "CCTV-1", "aliases": ["央视一套"]}),
        }

        class AcceptedImportDialog:
            def __init__(self, title, records, columns, parent):
                self.records = records

            def exec(self):
                return 1

            def selected_records(self):
                return self.records

        for kind, (name, imported_content, expected) in fixtures.items():
            with self.subTest(kind=kind):
                path = self._write(f"existing-{name}", "")
                imported_path = self._write(f"import-{name}", imported_content)
                editor = self._editor(kind, path)
                with patch(
                    "desktop_ui.pages.sources.QFileDialog.getOpenFileNames",
                    return_value=([imported_path], ""),
                ), patch(
                    "desktop_ui.pages.sources.SourceImportDialog",
                    AcceptedImportDialog,
                ):
                    editor.import_files()

                self.assertFalse(editor.import_button.isHidden())
                self.assertIn(expected, [
                    {key: value for key, value in row.items() if key != "_checked"}
                    for row in editor.rows
                ])
                with open(path, encoding="utf-8") as file:
                    self.assertEqual(file.read(), "")


if __name__ == "__main__":
    unittest.main()
