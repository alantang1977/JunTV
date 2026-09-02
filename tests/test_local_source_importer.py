import os
import tempfile
import unittest

from utils.local_source_importer import merge_records, parse_local_source_file


class LocalSourceImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _write(self, name, content, encoding="utf-8"):
        path = os.path.join(self.temp_dir.name, name)
        with open(path, "w", encoding=encoding) as file:
            file.write(content)
        return path

    def test_txt_supports_ascii_and_chinese_commas(self):
        path = self._write(
            "sources.txt",
            "# comment\nCCTV-1,http://example.com/1\nCCTV-5，rtsp://example.com/5\nBad,nope\n",
        )

        records, errors = parse_local_source_file(path)

        self.assertEqual([(item.channel, item.url) for item in records], [
            ("CCTV-1", "http://example.com/1"),
            ("CCTV-5", "rtsp://example.com/5"),
        ])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].reason, "invalid_url")

    def test_m3u_extracts_names_and_urls(self):
        path = self._write(
            "sources.m3u",
            '#EXTM3U\n#EXTINF:-1 tvg-id="one",CCTV-1\nhttps://example.com/1\n'
            "#EXTINF:-1,CCTV-5\n\n#EXTVLCOPT:http-referrer=x\nrtmp://example.com/5\n",
        )

        records, errors = parse_local_source_file(path)

        self.assertEqual([(item.channel, item.url) for item in records], [
            ("CCTV-1", "https://example.com/1"),
            ("CCTV-5", "rtmp://example.com/5"),
        ])
        self.assertEqual(errors, [])

    def test_gb18030_is_supported(self):
        path = self._write("sources.txt", "央视一套,http://example.com/1\n", "gb18030")

        records, errors = parse_local_source_file(path)

        self.assertEqual(records[0].channel, "央视一套")
        self.assertEqual(errors, [])

    def test_merge_marks_existing_and_batch_duplicates(self):
        existing_record = parse_local_source_file(
            self._write("sources.txt", "A,http://example.com/1\n")
        )[0][0]
        batch_records = parse_local_source_file(
            self._write("sources2.txt", "B,http://example.com/2\nB,http://example.com/2\n")
        )[0]
        records = [existing_record, *batch_records]

        merge_records([{"channel": "A", "url": "http://example.com/1"}], records)

        self.assertEqual(records[0].status, "duplicate")
        self.assertEqual(records[1].status, "new")
        self.assertEqual(records[2].status, "duplicate")


if __name__ == "__main__":
    unittest.main()
