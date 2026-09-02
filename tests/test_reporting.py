import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import utils.constants as constants
from desktop_ui.logging_bridge import SignalLogStream
from utils.artifacts import ArtifactWriter
from utils.reporting import Reporter, redact_url


class ReportingTests(unittest.TestCase):
    def test_redact_url_hides_credentials_and_sensitive_query_values(self):
        redacted = redact_url(
            "https://user:password@example.com/live.m3u8?txSecret=abc&key=123&quality=hd"
        )

        parts = urlsplit(redacted)
        query = parse_qs(parts.query)
        self.assertEqual(parts.netloc, "example.com")
        self.assertEqual(query["txSecret"], ["***"])
        self.assertEqual(query["key"], ["***"])
        self.assertEqual(query["quality"], ["hd"])

    def test_reporter_emits_plain_events_and_progress_without_control_codes(self):
        stream = io.StringIO()
        events = []
        reporter = Reporter(
            event_callback=events.append,
            stream=stream,
            enable_console=True,
            enable_runtime_file=False,
        )

        reporter.info(
            "source.loaded",
            "地址 https://example.com/live.m3u8?token=secret",
            url="https://example.com/live.m3u8?token=secret",
        )
        reporter.warning("source.disabled", "⚠️ 已停用")
        reporter.start_progress("fetch", "获取订阅源", 2, phase="fetch")
        reporter.update_progress("fetch", completed=1)
        reporter.finish_progress("fetch", status="完成")
        reporter.close()

        output = stream.getvalue()
        self.assertNotIn("\r", output)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("⚠️", output)
        self.assertEqual(events[0]["data"]["url"], "https://example.com/live.m3u8?token=%2A%2A%2A")

    def test_runtime_and_artifact_files_are_structured_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = os.path.join(temp_dir, "runtime.log")
            runtime_jsonl = os.path.join(temp_dir, "runtime.jsonl")
            artifact_path = os.path.join(temp_dir, "speed.log")
            artifact_jsonl = os.path.join(temp_dir, "speed.jsonl")
            with (
                patch.object(constants, "log_path", runtime_path),
                patch.object(constants, "runtime_jsonl_path", runtime_jsonl),
            ):
                reporter = Reporter(enable_console=False)
                reporter.bind_run("run-1")
                reporter.warning(
                    "request.failed",
                    "请求失败 https://example.com/live?auth=private",
                    phase="fetch",
                )
                reporter.close()

            writer = ArtifactWriter(
                artifact_path,
                artifact_jsonl,
                lambda record: f"{record['name']},{record['url']}",
                limit=1,
            )
            writer.write({
                "event": "speed_test.completed",
                "name": "CCTV-1",
                "url": "https://example.com/live?signature=private",
            })
            writer.write({"event": "speed_test.completed", "name": "CCTV-2", "url": "url"})
            writer.close()

            with open(runtime_path, encoding="utf-8") as file:
                runtime_text = file.read()
            with open(runtime_jsonl, encoding="utf-8") as file:
                runtime_payload = json.loads(file.readline())
            with open(artifact_path, encoding="utf-8") as file:
                artifact_text = file.read()
            with open(artifact_jsonl, encoding="utf-8") as file:
                artifact_payloads = [json.loads(line) for line in file]

            self.assertNotIn("private", runtime_text)
            self.assertEqual(runtime_payload["run_id"], "run-1")
            self.assertNotIn("private", artifact_text)
            self.assertEqual(artifact_payloads[0]["event"], "speed_test.completed")
            self.assertEqual(artifact_payloads[-1]["event"], "artifact.truncated")

    def test_desktop_log_stream_strips_terminal_refresh_and_ansi_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "runtime.log")
            emitted = []
            stream = SignalLogStream(path, emitted.append)
            stream.write("\r\x1b[32m测速 1/2\x1b[0m")
            stream.write("\r测速 2/2\n")
            stream.close()

            with open(path, encoding="utf-8") as file:
                content = file.read()
            self.assertNotIn("\r", content)
            self.assertNotIn("\x1b", content)
            self.assertIn("测速 1/2", content)
            self.assertIn("测速 2/2", content)


if __name__ == "__main__":
    unittest.main()
