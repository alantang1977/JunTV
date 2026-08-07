import contextlib
import io
import unittest

from utils.version_check import log_new_version_if_available, parse_release, version_tuple


class VersionCheckTests(unittest.TestCase):
    def test_version_comparison_handles_prefixed_versions(self):
        self.assertGreater(version_tuple("v2.1.0"), version_tuple("2.0.9"))

    def test_release_payload_reports_newer_version(self):
        result = parse_release({
            "tag_name": "v2.1.0",
            "html_url": "https://example.com/release",
            "assets": [],
        }, "2.0.8")

        self.assertTrue(result["newer"])
        self.assertEqual(result["latest"], "2.1.0")

    def test_non_gui_check_logs_only_when_update_is_available(self):
        output = io.StringIO()
        checker = lambda _: {
            "current": "2.0.8",
            "latest": "2.1.0",
            "newer": True,
            "release_url": "https://example.com/release",
        }

        with contextlib.redirect_stdout(output):
            log_new_version_if_available("2.0.8", checker=checker)

        self.assertIn("2.1.0", output.getvalue())
        self.assertIn("https://example.com/release", output.getvalue())

    def test_non_gui_check_silently_ignores_failures(self):
        def failed_checker(_):
            raise RuntimeError("offline")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = log_new_version_if_available("2.0.8", checker=failed_checker)

        self.assertIsNone(result)
        self.assertEqual(output.getvalue(), "")
