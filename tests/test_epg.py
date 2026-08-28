import gzip
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import PropertyMock, patch

from updates.epg.request import (
    EpgResourceLimitError,
    EpgStreamParser,
    _consume_epg_response,
    dedupe_epg_entries,
    parse_epg,
)
from updates.epg.tools import write_to_xml
from utils.config import config


class EpgTimezoneTests(unittest.TestCase):
    def test_programme_offsets_are_preserved(self):
        content = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="utc"><display-name>UTC</display-name></channel>
  <channel id="india"><display-name>India</display-name></channel>
  <programme channel="utc" start="20990101120000 +0000" stop="20990101130000 +0000">
    <title>UTC programme</title>
  </programme>
  <programme channel="india" start="20990101173000 +0530" stop="20990101183000 +0530">
    <title>India programme</title>
  </programme>
</tv>
"""

        _, programmes = parse_epg(content)

        utc_programme = programmes["utc"][0]
        india_programme = programmes["india"][0]
        self.assertEqual(utc_programme.get("start"), "20990101120000 +0000")
        self.assertEqual(utc_programme.get("stop"), "20990101130000 +0000")
        self.assertEqual(india_programme.get("start"), "20990101173000 +0530")
        self.assertEqual(india_programme.get("stop"), "20990101183000 +0530")

    def test_xml_generation_uses_configured_timezone_for_document_date(self):
        programme = ET.Element(
            "programme",
            attrib={
                "channel": "Demo",
                "start": "20990101120000 +0000",
                "stop": "20990101130000 +0000",
            },
        )
        ET.SubElement(programme, "title").text = "Demo"

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            type(config),
            "time_zone",
            new=PropertyMock(return_value="UTC"),
        ):
            path = os.path.join(temp_dir, "epg.xml")
            write_to_xml({"Demo": [programme]}, path)
            root = ET.parse(path).getroot()

        self.assertTrue(root.get("date").endswith(" +0000"))
        self.assertEqual(
            root.find("programme").get("start"),
            "20990101120000 +0000",
        )


class EpgResourceTests(unittest.TestCase):
    def test_parser_only_retains_requested_channels(self):
        content = """<tv>
<channel id="wanted"><display-name>Wanted</display-name></channel>
<channel id="other"><display-name>Other</display-name></channel>
<programme channel="wanted" start="20990101120000 +0000" stop="20990101130000 +0000"><title>Keep</title></programme>
<programme channel="other" start="20990101120000 +0000" stop="20990101130000 +0000"><title>Drop</title></programme>
</tv>"""

        channels, programmes = parse_epg(
            content,
            normalized_names={"Wanted"},
            include_unmatched=False,
        )

        self.assertEqual(channels, {"wanted": "Wanted"})
        self.assertEqual(len(programmes["wanted"]), 1)
        self.assertNotIn("other", programmes)

    def test_parser_stops_at_programme_limit(self):
        parser = EpgStreamParser(max_programmes=1)
        with self.assertRaises(EpgResourceLimitError):
            parser.feed("""<tv>
<channel id="demo"><display-name>Demo</display-name></channel>
<programme channel="demo" start="20990101120000 +0000" stop="20990101130000 +0000"><title>One</title></programme>
<programme channel="demo" start="20990101130000 +0000" stop="20990101140000 +0000"><title>Two</title></programme>
</tv>""")

    def test_sources_are_deduplicated_before_download_and_discovery_is_limited(self):
        entries, duplicates, limited, discovered = dedupe_epg_entries(
            [{"url": "https://EXAMPLE.com:443/epg.xml#fragment"}],
            [{"url": "https://example.com/epg.xml"}],
            [
                "https://example.com/epg.xml",
                "https://example.com/second.xml",
                "https://example.com/third.xml",
                "https://example.com/fourth.xml",
            ],
            2,
        )

        self.assertEqual([entry["_origin"] for entry in entries], [
            "whitelist", "discovered", "discovered"
        ])
        self.assertEqual(duplicates, 2)
        self.assertEqual(limited, 1)
        self.assertEqual(discovered, 2)


class _ChunkContent:
    def __init__(self, payload, chunk_size=17):
        self.payload = payload
        self.chunk_size = chunk_size

    async def iter_chunked(self, _):
        for index in range(0, len(self.payload), self.chunk_size):
            yield self.payload[index:index + self.chunk_size]


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self.content = _ChunkContent(payload)
        self.headers = headers or {}


class EpgStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_gzip_epg_is_decompressed_and_parsed_incrementally(self):
        payload = gzip.compress(b"""<tv>
<channel id="demo"><display-name>Demo</display-name></channel>
<programme channel="demo" start="20990101120000 +0000" stop="20990101130000 +0000"><title>Demo</title></programme>
</tv>""")
        with (
            patch("updates.epg.request.EPG_MAX_PROGRAMMES", 10),
            patch("updates.epg.request.EPG_DAYS_BACK", 0),
            patch("updates.epg.request.EPG_DAYS_AHEAD", 36500),
            patch("updates.epg.request.EPG_MAX_DOWNLOAD_BYTES", 1024 * 1024),
            patch("updates.epg.request.EPG_MAX_DECOMPRESSED_BYTES", 1024 * 1024),
        ):
            channels, programmes, digest, stats = await _consume_epg_response(
                _FakeResponse(payload),
                {"Demo"},
                False,
            )

        self.assertEqual(channels, {"demo": "Demo"})
        self.assertEqual(len(programmes["demo"]), 1)
        self.assertEqual(len(digest), 64)
        self.assertEqual(stats["retained_programmes"], 1)

    async def test_decompressed_size_limit_aborts_gzip_source(self):
        payload = gzip.compress(b"<tv>" + b" " * 1024 + b"</tv>")
        with (
            patch("updates.epg.request.EPG_MAX_PROGRAMMES", 10),
            patch("updates.epg.request.EPG_DAYS_BACK", 1),
            patch("updates.epg.request.EPG_DAYS_AHEAD", 14),
            patch("updates.epg.request.EPG_MAX_DOWNLOAD_BYTES", 1024 * 1024),
            patch("updates.epg.request.EPG_MAX_DECOMPRESSED_BYTES", 100),
        ):
            with self.assertRaises(EpgResourceLimitError):
                await _consume_epg_response(_FakeResponse(payload), set(), True)


if __name__ == "__main__":
    unittest.main()
