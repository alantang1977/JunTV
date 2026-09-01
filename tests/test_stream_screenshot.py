import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop_ui.screenshot_dialog import StreamScreenshotDialog
import utils.constants as constants
from utils.channel_operations import ChannelOperations
from utils.channel_repository import (
    ensure_channel_repository,
    list_channel_results,
    list_operations,
    prune_stream_screenshots,
    upsert_stream_screenshot,
)
from utils.ffmpeg.screenshot import capture_stream_screenshot


class StreamScreenshotCaptureTests(unittest.IsolatedAsyncioTestCase):
    def _executable(self, directory: str, body: str) -> str:
        path = Path(directory, "fake-ffmpeg")
        path.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    async def test_capture_writes_atomic_image_and_returns_stream_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = self._executable(
                directory,
                """
import sys
with open(sys.argv[-1], "wb") as image:
    image.write(b"x" * 2048)
print("Stream #0:0: Video: h264 (High), yuv420p, 1920x1080, 25 fps", file=sys.stderr)
print("Stream #0:1: Audio: aac, 48000 Hz, stereo", file=sys.stderr)
""",
            )
            with patch(
                "utils.ffmpeg.screenshot.resolve_ffmpeg_executable",
                return_value=executable,
            ):
                result = await capture_stream_screenshot(
                    "https://example.invalid/live.m3u8",
                    "result-key",
                    directory,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["resolution"], "1920x1080")
            self.assertEqual(result["fps"], 25.0)
            self.assertEqual(result["video_codec"], "h264")
            self.assertEqual(result["audio_codec"], "aac")
            self.assertTrue(Path(directory, "result-key.jpg").is_file())
            self.assertFalse(any(path.name.startswith(".result-key.") for path in Path(directory).iterdir()))

    async def test_failed_capture_removes_previous_image(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory, "result-key.jpg")
            previous.write_bytes(b"old-image")
            executable = self._executable(directory, "raise SystemExit(1)")
            with patch(
                "utils.ffmpeg.screenshot.resolve_ffmpeg_executable",
                return_value=executable,
            ):
                result = await capture_stream_screenshot(
                    "https://example.invalid/live.m3u8",
                    "result-key",
                    directory,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], "decode_failed")
            self.assertFalse(previous.exists())


class StreamScreenshotRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "channels.db")
        self.screenshot_dir = os.path.join(self.temp_dir.name, "screenshots")
        ensure_channel_repository(self.db_path)
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO channels(
                channel_key, category, name, health, total_results,
                valid_results, selected_results, updated_at
            ) VALUES ('channel', 'News', 'World News', 'healthy', 1, 1, 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO channel_results(
                channel_key, result_key, url, speed, delay, valid,
                selected_rank, last_seen_at
            ) VALUES ('channel', 'active', 'https://example.invalid/live', 1, 20, 1, 1, 1)
            """
        )
        connection.commit()
        connection.close()

    def test_metadata_is_joined_and_orphan_files_are_pruned(self):
        os.makedirs(self.screenshot_dir)
        Path(self.screenshot_dir, "active.jpg").write_bytes(b"active")
        Path(self.screenshot_dir, "orphan.jpg").write_bytes(b"orphan")
        Path(self.screenshot_dir, ".active.tmp.jpg").write_bytes(b"temporary")
        upsert_stream_screenshot(
            self.db_path,
            {
                "result_key": "active",
                "filename": "active.jpg",
                "status": "success",
                "captured_at": 10,
                "attempted_at": 10,
                "width": 1920,
                "height": 1080,
            },
        )
        upsert_stream_screenshot(
            self.db_path,
            {
                "result_key": "orphan",
                "filename": "orphan.jpg",
                "status": "success",
                "captured_at": 9,
                "attempted_at": 9,
            },
        )

        row = list_channel_results(self.db_path, "channel")[0]
        self.assertEqual(row["screenshot_status"], "success")
        self.assertEqual(row["screenshot_width"], 1920)

        summary = prune_stream_screenshots(self.db_path, self.screenshot_dir)

        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["files"], 2)
        self.assertTrue(Path(self.screenshot_dir, "active.jpg").is_file())
        self.assertFalse(Path(self.screenshot_dir, "orphan.jpg").exists())
        self.assertFalse(Path(self.screenshot_dir, ".active.tmp.jpg").exists())


class StreamScreenshotBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_deduplicates_results_and_reports_partial_success(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "channels.db")
            ensure_channel_repository(db_path)
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                INSERT INTO channels(
                    channel_key, category, name, health, total_results,
                    valid_results, selected_results, updated_at
                ) VALUES ('channel', 'News', 'World News', 'healthy', 2, 2, 1, 1)
                """
            )
            connection.executemany(
                """
                INSERT INTO channel_results(
                    channel_key, result_key, url, valid, selected_rank, last_seen_at
                ) VALUES ('channel', ?, ?, 1, ?, 1)
                """,
                [
                    ("success", "https://example.invalid/success", 1),
                    ("failed", "https://example.invalid/failed", None),
                ],
            )
            connection.commit()
            connection.close()
            captured = []

            async def capture(_url, result_key, *_args, **_kwargs):
                captured.append(result_key)
                return {
                    "result_key": result_key,
                    "filename": f"{result_key}.jpg",
                    "status": "success" if result_key == "success" else "failed",
                    "captured_at": 10 if result_key == "success" else None,
                    "attempted_at": 10,
                    "width": 1280 if result_key == "success" else None,
                    "height": 720 if result_key == "success" else None,
                    "error": None if result_key == "success" else "decode_failed",
                }

            operations = ChannelOperations(db_path)
            with (
                patch(
                    "utils.channel_operations.capture_stream_screenshot",
                    side_effect=capture,
                ),
                patch.object(operations, "_resort_and_publish") as publish,
            ):
                result = await operations.capture_result_screenshots(
                    "channel",
                    ["success", "failed", "success"],
                )

            self.assertCountEqual(captured, ["success", "failed"])
            self.assertEqual(result["success"], 1)
            self.assertEqual(result["failed"], 1)
            publish.assert_called_once_with("channel")
            rows = {
                row["result_key"]: row
                for row in list_channel_results(db_path, "channel")
            }
            self.assertEqual(rows["success"]["screenshot_status"], "success")
            self.assertEqual(rows["failed"]["screenshot_status"], "failed")
            self.assertEqual(list_operations(db_path)[0]["status"], "partial")


class StreamScreenshotDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_stays_open_and_switches_to_loading(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            constants,
            "screenshot_dir",
            directory,
        ):
            result = {
                "result_key": "result-key",
                "url": "https://example.invalid/live",
                "screenshot_status": "not_captured",
            }
            dialog = StreamScreenshotDialog(result)
            self.addCleanup(dialog.deleteLater)
            captured = []
            dialog.capture_requested.connect(captured.append)
            dialog.show()
            self.app.processEvents()

            dialog.capture_button.click()
            self.app.processEvents()

            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.is_loading)
            self.assertFalse(dialog.capture_button.isEnabled())
            self.assertEqual(captured[0]["result_key"], "result-key")

            dialog.set_result({
                **result,
                "screenshot_status": "failed",
                "screenshot_error": "decode_failed",
            })
            self.assertTrue(dialog.isVisible())
            self.assertFalse(dialog.is_loading)
            self.assertTrue(dialog.capture_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
