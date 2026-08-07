import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from desktop_ui.controller import OperationWorker
from desktop_ui.pages.channels import ChannelCenterPage
import utils.constants as constants
from utils.channel_repository import delete_channel_results, ensure_channel_repository, list_categories, list_channel_results, list_channels
from utils.i18n import t


class ChannelRepositoryFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "channels.db")
        ensure_channel_repository(self.db_path)
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO channels(
                channel_key, category, name, health, total_results,
                valid_results, selected_results, updated_at
            ) VALUES (?, ?, ?, ?, 0, 0, 0, 1)
            """,
            [
                ("news-world", "News", "World News", "healthy"),
                ("news-local", "News", "Local News", "warning"),
                ("sports-one", "Sports", "Sports One", "offline"),
                ("sports-two", "Sports", "Sports Two", "unknown"),
            ],
        )
        connection.commit()
        connection.close()

    def test_category_counts_can_be_scoped_by_search(self):
        categories = list_categories(self.db_path, search="World")

        self.assertEqual(categories, [{
            "category": "News",
            "channel_count": 1,
            "healthy_count": 1,
            "warning_count": 0,
            "offline_count": 0,
            "valid_results": 0,
        }])

    def test_channels_can_be_filtered_by_category_search_and_health(self):
        rows = list_channels(
            self.db_path,
            category="Sports",
            search="Two",
            health="unknown",
        )

        self.assertEqual([row["channel_key"] for row in rows], ["sports-two"])

    def test_delete_results_refreshes_channel_summary_and_runtime_records(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, resolution,
                valid, selected_rank, last_seen_at, extra_data
            ) VALUES ('news-world', ?, ?, ?, ?, ?, 1, ?, 1, '{}')
            """,
            [
                ("keep", "https://example.invalid/keep", 2, 20, "1280x720", 1),
                ("drop", "https://example.invalid/drop", 1, 30, "640x360", None),
            ],
        )
        connection.execute(
            """
            INSERT INTO stream_screenshots(
                result_key, filename, status, attempted_at
            ) VALUES ('drop', 'drop.png', 'success', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO stream_samples(
                sampled_at, result_key, clients, bw_in, bw_out,
                bytes_in, bytes_out, active
            ) VALUES (1, 'drop', 1, 0, 0, 0, 0, 1)
            """
        )
        connection.commit()
        connection.close()

        deleted = delete_channel_results(self.db_path, "news-world", ["drop"])

        self.assertEqual(deleted, ["drop"])
        self.assertEqual(
            [row["result_key"] for row in list_channel_results(self.db_path, "news-world")],
            ["keep"],
        )
        connection = sqlite3.connect(self.db_path)
        summary = connection.execute(
            """
            SELECT total_results, valid_results, selected_results,
                   best_speed, min_delay, max_resolution, health
            FROM channels WHERE channel_key='news-world'
            """
        ).fetchone()
        self.assertEqual(summary, (1, 1, 1, 2.0, 20.0, "1280x720", "warning"))
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM stream_screenshots WHERE result_key='drop'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM stream_samples WHERE result_key='drop'"
            ).fetchone()[0],
            0,
        )
        connection.close()


class OperationProgressTests(unittest.TestCase):
    def test_operation_worker_initializes_and_clamps_progress(self):
        worker = OperationWorker("retest_channel", {})
        progress = []
        worker.progress.connect(lambda _name, value: progress.append(value))

        worker._progress(5, 10, "Channel")
        worker._progress(2, 10, "Channel")

        self.assertEqual(progress, [50])


class ChannelCenterViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "channels.db")
        self.whitelist_path = os.path.join(self.temp_dir.name, "whitelist.txt")
        self.blacklist_path = os.path.join(self.temp_dir.name, "blacklist.txt")
        self.template_path = os.path.join(self.temp_dir.name, "demo.txt")
        with open(self.template_path, "w", encoding="utf-8") as file:
            file.write("Sports,#genre#\nSports One\n\nNews,#genre#\nWorld News\n")
        ensure_channel_repository(self.db_path)
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO channels(
                channel_key, category, name, health, total_results,
                valid_results, selected_results, updated_at
            ) VALUES (?, ?, ?, ?, 0, 0, 0, 1)
            """,
            [
                ("news-world", "News", "World News", "warning"),
                ("sports-one", "Sports", "Sports One", "offline"),
            ],
        )
        connection.commit()
        connection.close()
        self.settings = QSettings()
        self.previous_view = self.settings.value("appearance/channel_center_view")
        self.previous_drawer_height = self.settings.value(
            "appearance/channel_result_drawer_height"
        )
        self.previous_channel_header_state = self.settings.value(
            "appearance/table_headers/channel_center.channels.v2"
        )
        self.previous_channel_column_weights = self.settings.value(
            "appearance/table_column_weights/channel_center.channels.v2"
        )
        self.settings.setValue("appearance/channel_center_view", "category")
        self.settings.setValue("appearance/channel_result_drawer_height", 360)
        self.settings.remove("appearance/table_headers/channel_center.channels.v2")
        self.settings.remove("appearance/table_column_weights/channel_center.channels.v2")
        self.addCleanup(self._restore_settings)

    def _restore_settings(self):
        if self.previous_view is None:
            self.settings.remove("appearance/channel_center_view")
        else:
            self.settings.setValue("appearance/channel_center_view", self.previous_view)
        if self.previous_drawer_height is None:
            self.settings.remove("appearance/channel_result_drawer_height")
        else:
            self.settings.setValue(
                "appearance/channel_result_drawer_height",
                self.previous_drawer_height,
            )
        for key, value in (
            (
                "appearance/table_headers/channel_center.channels.v2",
                self.previous_channel_header_state,
            ),
            (
                "appearance/table_column_weights/channel_center.channels.v2",
                self.previous_channel_column_weights,
            ),
        ):
            if value is None:
                self.settings.remove(key)
            else:
                self.settings.setValue(key, value)

    def test_category_view_filters_rows_and_list_view_restores_category_column(self):
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)

            self.assertEqual(list(page.view_switch.items), ["category", "list"])
            self.assertEqual(page.view_switch.currentRouteKey(), "category")
            self.assertFalse(page.category_sidebar.isHidden())
            self.assertTrue(page.channel_table.isColumnHidden(5))
            self.assertEqual(
                [column[0] for column in page.channel_model.columns],
                [
                    "batch_selected",
                    "name",
                    "health",
                    "valid_results",
                    "total_results",
                    "category",
                    "whitelist_count",
                    "blacklist_count",
                    "updated_at",
                ],
            )
            self.assertEqual(page.category_tree.topLevelItemCount(), 3)
            self.assertEqual(page.smart_tree.topLevelItemCount(), 5)
            self.assertEqual(
                [
                    page.category_tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
                    for index in range(1, page.category_tree.topLevelItemCount())
                ],
                [("category", "Sports"), ("category", "News")],
            )
            self.assertEqual(
                [row["category"] for row in page.channel_model.rows],
                ["Sports", "News"],
            )
            self.assertEqual(
                [
                    page.category_selector.itemData(index)
                    for index in range(1, page.category_selector.count())
                ],
                ["Sports", "News"],
            )

            page._category_item_clicked(page._category_items[("category", "News")], 0)
            self.assertEqual([row["name"] for row in page.channel_model.rows], ["World News"])

            page.channel_model.setData(
                page.channel_model.index(0, 0),
                Qt.CheckState.Checked,
                Qt.ItemDataRole.CheckStateRole,
            )
            page._category_item_clicked(page._category_items[("category", "Sports")], 0)
            self.assertEqual(page._checked_channel_keys, {"news-world"})

            page._set_view_mode("list")
            self.assertTrue(page.category_sidebar.isHidden())
            self.assertFalse(page.channel_table.isColumnHidden(5))

    def test_smart_collections_include_healthy_channels(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO channels(
                channel_key, category, name, health, total_results,
                valid_results, selected_results, updated_at
            ) VALUES ('news-healthy', 'News', 'Healthy News', 'healthy', 0, 0, 0, 1)
            """
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)

            self.assertEqual(
                [
                    page.smart_tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
                    for index in range(page.smart_tree.topLevelItemCount())
                ],
                [
                    ("health", "healthy"),
                    ("health", "warning"),
                    ("health", "offline"),
                    ("health", "unknown"),
                    ("streaming", True),
                ],
            )
            page._smart_item_clicked(page._smart_items[("health", "healthy")], 0)
            self.assertEqual([row["name"] for row in page.channel_model.rows], ["Healthy News"])

    def test_channel_center_imports_channels_and_playback_sources(self):
        channel_import = os.path.join(self.temp_dir.name, "channels-import.txt")
        playback_import = os.path.join(self.temp_dir.name, "playback-import.txt")
        with open(channel_import, "w", encoding="utf-8") as file:
            file.write("Movies,#genre#\nMovie One\n")
        with open(playback_import, "w", encoding="utf-8") as file:
            file.write(
                "Sports One,https://example.com/sports.m3u8\n"
                "Unknown,https://example.com/unknown.m3u8\n"
            )

        class AcceptedImportDialog:
            def __init__(self, title, records, columns, parent):
                self.records = records

            def exec(self):
                return 1

            def selected_records(self):
                return [record for record in self.records if record["status"] == "new"]

        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "local_path", os.path.join(self.temp_dir.name, "local.txt")),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
            patch("desktop_ui.pages.channels.SourceImportDialog", AcceptedImportDialog),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            with patch(
                "desktop_ui.pages.channels.QFileDialog.getOpenFileNames",
                return_value=([channel_import], ""),
            ), patch("desktop_ui.pages.channels.add_channel", return_value=True) as add, patch(
                "desktop_ui.pages.channels.upsert_manual_channel"
            ) as upsert:
                page._import_channels()

            add.assert_called_once_with("Movies", "Movie One")
            upsert.assert_called_once_with(self.db_path, "Movies", "Movie One")

            with patch(
                "desktop_ui.pages.channels.QFileDialog.getOpenFileNames",
                return_value=([playback_import], ""),
            ), patch("desktop_ui.pages.channels.add_manual_channel_result") as add_source, patch(
                "desktop_ui.pages.channels.add_manual_result"
            ) as add_result:
                page._import_playback_sources()

            add_source.assert_called_once_with("Sports One", "https://example.com/sports.m3u8")
            add_result.assert_called_once_with(self.db_path, "sports-one", "https://example.com/sports.m3u8")

    def test_channel_center_exports_template_and_playback_sources(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, valid, last_seen_at, extra_data
            ) VALUES ('sports-one', 'sports-stream', 'https://example.com/sports.m3u8', 1, 1, '{}')
            """
        )
        connection.commit()
        connection.close()
        template_export = os.path.join(self.temp_dir.name, "channels-export.txt")
        playback_export = os.path.join(self.temp_dir.name, "playback-export.m3u")

        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            with patch(
                "desktop_ui.pages.channels.QFileDialog.getSaveFileName",
                side_effect=[(template_export, ""), (playback_export, "")],
            ):
                page._export_channel_template()
                page._export_playback_sources()

        with open(template_export, encoding="utf-8") as file:
            self.assertIn("Sports,#genre#\nSports One", file.read())
        with open(playback_export, encoding="utf-8") as file:
            self.assertIn("#EXTINF:-1,Sports One\nhttps://example.com/sports.m3u8", file.read())

    def test_stream_snapshot_updates_channel_result_status_and_streaming_filter(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES ('sports-one', 'stream-result', 'https://example.invalid/stream',
                      1, 20, 1, 1, 1)
            """
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            page.set_stream_snapshot(
                {
                    "streams": [
                        {
                            "channel_key": "sports-one",
                            "result_key": "stream-result",
                            "state": "active",
                            "clients": 2,
                            "bw_out": 1000,
                        }
                    ]
                }
            )

            channel_index = next(
                page.channel_model.index(index, 1)
                for index, row in enumerate(page.channel_model.rows)
                if row["channel_key"] == "sports-one"
            )
            self.assertIn(
                t("desktop.stream_running_badge"),
                page.channel_model.data(channel_index, Qt.ItemDataRole.DisplayRole),
            )
            page._drawer_channel_key = "sports-one"
            page._load_results("sports-one")
            self.assertEqual(page.result_model.rows[0]["stream_state"], "active")
            self.assertEqual(
                page.result_model.data(
                    page.result_model.index(0, 2),
                    Qt.ItemDataRole.DisplayRole,
                ),
                t("desktop.stream_running_badge"),
            )
            stream_requests = []
            page.stream_control_many_requested.connect(
                lambda action, keys: stream_requests.append((action, keys))
            )
            with patch("desktop_ui.pages.channels.warning_message_box") as message_box:
                message_box.return_value.exec.return_value = True
                page._stop_selected_result_streams()
            self.assertEqual(stream_requests, [("stop", ["stream-result"])])

            streaming_item = page._smart_items[("streaming", True)]
            self.assertEqual(streaming_item.text(1), "1")
            page._smart_item_clicked(streaming_item, 0)
            self.assertEqual(
                [row["channel_key"] for row in page.channel_model.rows],
                ["sports-one"],
            )
            page.channel_table.selectRow(0)
            page._update_selection_label()
            self.assertTrue(page.stop_stream_button.isHidden())
            self.assertTrue(page.channel_stop_stream_action.isEnabled())
            self.assertFalse(page.channel_delete_action.isEnabled())
            self.assertFalse(page.delete_result_action.isEnabled())

    def test_stream_status_menu_does_not_open_channel_drawer(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES ('sports-one', 'stream-result', 'https://example.invalid/stream',
                      1, 20, 1, 1, 1)
            """
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
            patch("desktop_ui.pages.channels.RoundMenu.exec"),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            page.set_stream_snapshot({
                "streams": [{
                    "channel_key": "sports-one",
                    "result_key": "stream-result",
                    "state": "active",
                }],
            })
            row = next(row for row in page.channel_model.rows if row["channel_key"] == "sports-one")
            page._show_stream_menu(row, QPoint(0, 0))
            index = next(
                page.channel_model.index(index, 1)
                for index, row in enumerate(page.channel_model.rows)
                if row["channel_key"] == "sports-one"
            )
            with patch.object(page, "show_result_drawer") as show_drawer:
                page._channel_clicked(index)
                show_drawer.assert_not_called()

    def test_view_mode_is_restored_and_updated(self):
        self.settings.setValue("appearance/channel_center_view", "list")
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)

            self.assertEqual(page.view_switch.currentRouteKey(), "list")
            self.assertTrue(page.category_sidebar.isHidden())

            page._set_view_mode("category")

            self.assertEqual(
                self.settings.value("appearance/channel_center_view"),
                "category",
            )

    def test_playback_defaults_to_best_valid_result_and_groups_actions(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES ('sports-one', ?, ?, ?, 20, ?, ?, 1)
            """,
            [
                ("invalid-top", "https://example.invalid/invalid", 99, 0, 1),
                ("valid-ranked", "https://example.invalid/ranked", 1, 1, 2),
                ("valid-fast", "https://example.invalid/fast", 100, 1, None),
            ],
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
            patch("desktop_ui.pages.channels.play_url") as play_url,
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)

            self.assertEqual(page.add_result_button.text(), t("desktop.add_result"))
            self.assertEqual(page.retest_channel_button.text(), t("desktop.retest_channel"))
            self.assertTrue(page.retest_channel_button.isHidden())
            self.assertEqual(page.stream_selected_button.text(), t("desktop.open_selected_streams"))
            self.assertFalse(page.screenshot_button.isHidden())
            self.assertTrue(page.stream_button.isHidden())
            self.assertTrue(page.stop_result_stream_button.isHidden())
            self.assertIn(page.start_result_stream_action, page.result_more_menu.actions())
            self.assertIn(page.stop_result_stream_action, page.result_more_menu.actions())
            self.assertEqual(page.stop_result_stream_action.text(), t("desktop.stop_stream"))
            self.assertFalse(page.copy_button.isHidden())
            self.assertIn(page.copy_action, page.copy_menu.actions())
            self.assertIn(page.copy_stream_action, page.copy_menu.actions())
            self.assertEqual(page.copy_action.text(), t("desktop.copy_source_url"))
            self.assertFalse(page.channel_more_button.isHidden())
            self.assertIn(page.channel_add_action, page.channel_more_menu.actions())
            self.assertIn(page.channel_add_result_action, page.channel_more_menu.actions())
            self.assertIn(page.channel_stream_action, page.channel_more_menu.actions())
            self.assertIn(page.channel_delete_action, page.channel_more_menu.actions())
            self.assertIn(page.channel_play_action, page.channel_menu.actions())
            self.assertIn(page.channel_stream_action, page.channel_menu.actions())
            self.assertIn(page.channel_stop_stream_action, page.channel_menu.actions())
            self.assertIs(page.channel_more_menu.actions()[0], page.channel_retest_action)
            self.assertIs(page.channel_more_menu.actions()[-1], page.channel_delete_action)
            self.assertNotIn(page.preview_screenshot_action, page.result_more_menu.actions())
            self.assertNotIn(page.copy_action, page.result_more_menu.actions())
            self.assertNotIn(page.copy_stream_action, page.result_more_menu.actions())

            channel_index = next(
                page.channel_model.index(index, 1)
                for index, row in enumerate(page.channel_model.rows)
                if row["channel_key"] == "sports-one"
            )
            page.channel_table.selectRow(channel_index.row())
            page._update_selection_label()
            self.assertTrue(page.play_selected_button.isEnabled())
            page._play_selected_channels()
            self.assertEqual(play_url.call_count, 1)
            self.assertEqual(play_url.call_args.args[0], "https://example.invalid/ranked")

            page._drawer_channel_key = "sports-one"
            page._load_results("sports-one")
            self.assertEqual(
                [row["result_key"] for row in page.selected_results()],
                ["valid-ranked"],
            )
            page._open_result()
            self.assertEqual(play_url.call_count, 2)
            self.assertEqual(play_url.call_args.args[0], "https://example.invalid/ranked")

    def test_table_sorting_persists_after_channel_refresh(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES ('sports-one', ?, ?, ?, 20, 1, ?, 1)
            """,
            [
                ("result-slow", "https://example.invalid/slow", 1, 1),
                ("result-fast", "https://example.invalid/fast", 3, 2),
            ],
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            page.resize(1100, 700)
            page.show()
            self.app.processEvents()

            header = page.channel_header
            click_position = header.viewport().rect().center()
            click_position.setX(header.sectionViewportPosition(1) + 20)
            QTest.mouseClick(
                header.viewport(),
                Qt.MouseButton.LeftButton,
                pos=click_position,
            )
            self.app.processEvents()
            self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
            sorted_names = [row["name"] for row in page.channel_model.rows]
            self.assertEqual(sorted_names, ["World News", "Sports One"])

            page.reload()
            self.assertEqual(page.channel_header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
            self.assertEqual(
                [row["name"] for row in page.channel_model.rows],
                sorted_names,
            )

            page._drawer_channel_key = "sports-one"
            page._load_results("sports-one")
            page.result_drawer.show()
            page.result_drawer.setGeometry(page._drawer_geometry())
            self.app.processEvents()
            result_header = page.result_header
            result_position = result_header.viewport().rect().center()
            result_position.setX(result_header.sectionViewportPosition(3) + 20)
            QTest.mouseClick(
                result_header.viewport(),
                Qt.MouseButton.LeftButton,
                pos=result_position,
            )
            self.app.processEvents()
            self.assertEqual(
                [row["result_key"] for row in page.result_model.rows],
                ["result-slow", "result-fast"],
            )
            QTest.mouseClick(
                result_header.viewport(),
                Qt.MouseButton.LeftButton,
                pos=result_position,
            )
            self.app.processEvents()
            self.assertEqual(
                [row["result_key"] for row in page.result_model.rows],
                ["result-fast", "result-slow"],
            )

    def test_result_drawer_stays_open_for_buttons_menus_and_dialogs(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES (
                'sports-one', 'result-one', 'https://example.invalid/one',
                1, 20, 1, 1, 1
            )
            """
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            dialog = QDialog(page)
            dialog_button = QPushButton(dialog)
            self.addCleanup(dialog.deleteLater)
            mouse_press = QEvent(QEvent.Type.MouseButtonPress)

            page.show()
            page._drawer_channel_key = "sports-one"
            page._load_results("sports-one")
            page.result_drawer.show()
            page.result_drawer.setGeometry(page._drawer_geometry())
            self.app.processEvents()
            for watched in (
                page.more_button,
                page.result_table.viewport(),
                page.result_menu,
                dialog_button,
            ):
                with self.subTest(widget=type(watched).__name__), patch.object(
                    page,
                    "hide_result_drawer",
                ) as hide:
                    page.eventFilter(watched, mouse_press)
                    hide.assert_not_called()

            with patch.object(page, "hide_result_drawer") as hide:
                page.eventFilter(page.search, mouse_press)
                hide.assert_called_once_with()

            result_index = page.result_model.index(0, 1)
            result_position = page.result_table.visualRect(result_index).center()
            with patch.object(page, "hide_result_drawer") as hide:
                QTest.mouseClick(
                    page.result_table.viewport(),
                    Qt.MouseButton.RightButton,
                    pos=result_position,
                )
                self.app.processEvents()
                hide.assert_not_called()

            with patch.object(page.result_menu, "exec") as execute_menu:
                page._show_result_menu(result_position)
                execute_menu.assert_called_once()

            drawer_center = page.result_drawer.rect().center()
            drawer_global = page.result_drawer.mapToGlobal(drawer_center)
            attributed_elsewhere = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(0, 0),
                QPointF(drawer_global),
                Qt.MouseButton.RightButton,
                Qt.MouseButton.RightButton,
                Qt.KeyboardModifier.NoModifier,
            )
            with patch.object(page, "hide_result_drawer") as hide:
                page.eventFilter(page.search, attributed_elsewhere)
                hide.assert_not_called()

    def test_result_drawer_margins_resize_and_fullscreen(self):
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            page.resize(1000, 700)

            geometry = page._drawer_geometry()
            self.assertEqual(geometry.left(), 12)
            self.assertEqual(page.width() - geometry.right() - 1, 12)
            self.assertEqual(page.height() - geometry.bottom() - 1, 8)

            page.result_drawer.setGeometry(geometry)
            page._resize_result_drawer(80)
            self.assertEqual(page._drawer_geometry().height(), 440)
            page._save_result_drawer_height()
            self.assertEqual(
                self.settings.value(
                    "appearance/channel_result_drawer_height",
                    type=int,
                ),
                440,
            )

            page._toggle_result_drawer_fullscreen()
            self.assertTrue(page._drawer_fullscreen)
            self.assertEqual(page.result_drawer.geometry(), page.rect())
            self.assertEqual(page.result_drawer.getBorderRadius(), 0)

            page._toggle_result_drawer_fullscreen()
            self.assertFalse(page._drawer_fullscreen)
            self.assertEqual(page.result_drawer.getBorderRadius(), 12)

    def test_screenshot_preview_auto_capture_only_when_missing(self):
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            dialog = Mock()

            dialog.has_screenshot.return_value = True
            page._auto_capture_missing_screenshot(
                dialog,
                {"screenshot_status": "success"},
            )
            dialog.request_capture.assert_not_called()

            page._auto_capture_missing_screenshot(
                dialog,
                {"screenshot_status": "failed"},
            )
            dialog.request_capture.assert_not_called()

            page._auto_capture_missing_screenshot(
                dialog,
                {"screenshot_status": "not_captured"},
            )
            dialog.request_capture.assert_called_once_with()

            dialog.request_capture.reset_mock()
            dialog.has_screenshot.return_value = False
            page._auto_capture_missing_screenshot(
                dialog,
                {"screenshot_status": "success"},
            )
            dialog.request_capture.assert_called_once_with()

    def test_result_actions_follow_single_and_multiple_selection(self):
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, origin, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES (?, ?, ?, ?, 1, 20, 1, ?, 1)
            """,
            [
                ("sports-one", "result-one", "https://example.invalid/one", "subscribe", 1),
                ("sports-one", "result-two", "https://example.invalid/two", "whitelist", None),
                ("news-world", "news-result", "https://example.invalid/news", "local", 1),
            ],
        )
        connection.commit()
        connection.close()
        with (
            patch.object(constants, "channel_results_path", self.db_path),
            patch.object(constants, "whitelist_path", self.whitelist_path),
            patch.object(constants, "blacklist_path", self.blacklist_path),
            patch("desktop_ui.pages.channels.resource_path", return_value=self.template_path),
        ):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)
            page._drawer_channel_key = "sports-one"
            page._load_results("sports-one")

            self.assertEqual(
                [row["result_key"] for row in page.selected_results()],
                ["result-one"],
            )
            origin_column = next(
                index
                for index, column in enumerate(page.result_model.columns)
                if column[0] == "origin"
            )
            self.assertEqual(
                page.result_model.data(
                    page.result_model.index(0, origin_column),
                    Qt.ItemDataRole.DisplayRole,
                ),
                t("name.subscribe"),
            )
            self.assertFalse(page.result_model.rows[0]["batch_selected"])
            self.assertTrue(page.play_button.isEnabled())
            self.assertTrue(page.retest_result_button.isEnabled())
            self.assertTrue(page.screenshot_button.isEnabled())
            self.assertTrue(page.stream_button.isEnabled())
            self.assertTrue(page.start_result_stream_action.isEnabled())
            self.assertTrue(page.more_button.isEnabled())
            self.assertIn(page.play_result_action, page.result_menu.actions())
            self.assertIn(page.retest_result_action, page.result_menu.actions())

            retested = []
            page.retest_result_requested.connect(
                lambda row: retested.append(row["result_key"])
            )
            page.retest_result_action.trigger()
            self.assertEqual(retested, ["result-one"])

            automatic_captures = []
            page.capture_screenshot_requested.connect(
                lambda row: automatic_captures.append(row["result_key"])
            )
            page._preview_result_screenshot()
            self.app.processEvents()
            dialog = page._screenshot_dialog
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.is_loading)
            self.assertEqual(automatic_captures, ["result-one"])
            with patch("desktop_ui.pages.channels.InfoBar.success") as success:
                page.show_result_screenshot("result-one", notify=True)
                self.assertIs(success.call_args.kwargs["parent"], dialog)
            dialog.reject()
            self.app.processEvents()

            captured = []
            page.capture_screenshot_requested.connect(
                lambda row: captured.append(row["result_key"])
            )
            page._request_result_screenshot()
            self.assertEqual(captured, ["result-one"])

            selection = page.result_table.selectionModel()
            selection.select(
                page.result_model.index(1, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.app.processEvents()
            self.assertFalse(page.play_button.isEnabled())
            self.assertTrue(page.retest_result_button.isEnabled())
            self.assertFalse(page.screenshot_button.isEnabled())
            self.assertTrue(page.stream_button.isEnabled())
            self.assertTrue(page.start_result_stream_action.isEnabled())
            self.assertTrue(page.capture_screenshot_action.isEnabled())

            captured_batches = []
            page.capture_screenshots_requested.connect(
                lambda rows: captured_batches.append(
                    [row["result_key"] for row in rows]
                )
            )
            page._request_result_screenshot()
            self.assertEqual(
                set(captured_batches[-1]),
                {"result-one", "result-two"},
            )

            selection.clearSelection()
            for row in range(2):
                page.result_model.setData(
                    page.result_model.index(row, 0),
                    Qt.CheckState.Checked,
                    Qt.ItemDataRole.CheckStateRole,
                )
            self.app.processEvents()
            self.assertFalse(page.screenshot_button.isEnabled())
            self.assertTrue(page.capture_screenshot_action.isEnabled())
            page._request_result_screenshot()
            self.assertEqual(
                set(captured_batches[-1]),
                {"result-one", "result-two"},
            )

            news_index = next(
                page.channel_model.index(index, 1)
                for index, row in enumerate(page.channel_model.rows)
                if row["channel_key"] == "news-world"
            )
            page._current_channel_changed(news_index, news_index)
            self.app.processEvents()
            self.assertEqual(page._drawer_channel_key, "news-world")
            self.assertEqual(page._checked_result_keys, set())
            self.assertEqual(
                [row["result_key"] for row in page.selected_results()],
                ["news-result"],
            )
            self.assertFalse(page.result_model.rows[0]["batch_selected"])
            self.assertTrue(page.play_button.isEnabled())
            self.assertTrue(page.screenshot_button.isEnabled())
            self.assertTrue(page.more_button.isEnabled())

            page._drawer_channel_key = "missing-channel"
            page._load_results("missing-channel")
            self.app.processEvents()
            self.assertEqual(page.selected_results(), [])
            self.assertFalse(page.play_button.isEnabled())
            self.assertFalse(page.screenshot_button.isEnabled())
            self.assertFalse(page.more_button.isEnabled())

    def test_task_progress_stays_with_its_originating_surface(self):
        with patch.object(constants, "channel_results_path", self.db_path):
            page = ChannelCenterPage()
            self.addCleanup(page.deleteLater)

            page.show_result_drawer()
            page.set_task_started("retest_result")
            self.assertTrue(page.task_row.isHidden())
            self.assertFalse(page.drawer_task_row.isHidden())
            page.set_task_progress("Sports One", 42)
            self.assertEqual(page.drawer_task_progress.value(), 42)
            self.assertIn("Sports One", page.drawer_task_label.text())
            page.set_task_finished()
            self.assertTrue(page.drawer_task_row.isHidden())

            page.set_task_started("retest_channel")
            self.assertFalse(page.task_row.isHidden())
            self.assertFalse(page.task_label.isHidden())
            self.assertFalse(page.task_progress.isHidden())
            self.assertFalse(page.task_percent_label.isHidden())
            self.assertTrue(page.drawer_task_row.isHidden())
            page.set_task_finished()


if __name__ == "__main__":
    unittest.main()
