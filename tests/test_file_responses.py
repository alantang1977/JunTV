import gzip
import os
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from flask import Flask

from utils.config import config
from utils.tools import get_result_file_content


class ResultFileResponseTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def response_data(self, response):
        response.direct_passthrough = False
        payload = response.get_data()
        response.close()
        return payload

    def test_binary_gzip_is_downloaded_when_m3u_default_is_disabled(self):
        path = os.path.join(self.temp_dir.name, "epg.gz")
        with gzip.open(path, "wb") as file:
            file.write(b"<tv />")

        with self.app.test_request_context(), patch.object(
            type(config),
            "open_m3u_result",
            new=PropertyMock(return_value=False),
        ):
            response = get_result_file_content(path=path, file_type="gz")
            payload = self.response_data(response)

        self.assertTrue(payload.startswith(b"\x1f\x8b"))
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_explicit_m3u_and_xml_are_downloaded_independently_of_default(self):
        txt_path = os.path.join(self.temp_dir.name, "result.txt")
        m3u_path = os.path.join(self.temp_dir.name, "result.m3u")
        xml_path = os.path.join(self.temp_dir.name, "epg.xml")
        with open(txt_path, "w", encoding="utf-8") as file:
            file.write("Demo,https://example.com/live.m3u8")
        with open(m3u_path, "w", encoding="utf-8") as file:
            file.write("#EXTM3U\n")
        with open(xml_path, "w", encoding="utf-8") as file:
            file.write("<tv />")

        with self.app.test_request_context(), patch.object(
            type(config),
            "open_m3u_result",
            new=PropertyMock(return_value=False),
        ):
            m3u_response = get_result_file_content(path=txt_path, file_type="m3u")
            m3u_payload = self.response_data(m3u_response)
            xml_response = get_result_file_content(path=xml_path, file_type="xml")
            xml_payload = self.response_data(xml_response)

        self.assertEqual(m3u_payload, b"#EXTM3U\n")
        self.assertEqual(xml_payload, b"<tv />")
        self.assertIn("attachment", m3u_response.headers["Content-Disposition"])
        self.assertIn("attachment", xml_response.headers["Content-Disposition"])

    def test_text_and_content_views_remain_inline(self):
        txt_path = os.path.join(self.temp_dir.name, "result.txt")
        m3u_path = os.path.join(self.temp_dir.name, "result.m3u")
        with open(txt_path, "w", encoding="utf-8") as file:
            file.write("TXT content")
        with open(m3u_path, "w", encoding="utf-8") as file:
            file.write("#EXTM3U\n")

        with self.app.test_request_context(), patch.object(
            type(config),
            "open_m3u_result",
            new=PropertyMock(return_value=True),
        ):
            txt_response = get_result_file_content(path=txt_path, file_type="txt")
            m3u_response = get_result_file_content(
                path=txt_path,
                file_type="m3u",
                show_content=True,
            )

        self.assertEqual(txt_response.get_data(as_text=True), "TXT content")
        self.assertEqual(m3u_response.get_data(as_text=True), "#EXTM3U\n")
        self.assertEqual(txt_response.mimetype, "text/plain")
        self.assertEqual(m3u_response.mimetype, "text/plain")
        self.assertNotIn("Content-Disposition", txt_response.headers)
        self.assertNotIn("Content-Disposition", m3u_response.headers)

    def test_missing_result_returns_stateful_http_response(self):
        missing_path = os.path.join(self.temp_dir.name, "missing.txt")
        with self.app.test_request_context(), patch(
            "utils.tools.read_run_state", return_value={"status": "never_run"}
        ):
            response = get_result_file_content(path=missing_path, file_type="txt")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.mimetype, "application/json")
        self.assertEqual(response.get_json()["status"], "never_run")


if __name__ == "__main__":
    unittest.main()
