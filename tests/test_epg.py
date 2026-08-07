import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import PropertyMock, patch

from updates.epg.request import parse_epg
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


if __name__ == "__main__":
    unittest.main()
