import copy
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, PropertyMock, patch

import utils.constants as constants
from updates.subscribe.request import get_channels_by_subscribe_urls
from utils.channel import (
    _TOTAL_URLS_CACHE,
    _WRITTEN_CONTENT_DIGESTS,
    append_data_to_info_data,
    process_write_content,
    sort_channel_result,
)
from utils.config import config
from utils.identity import stable_result_id
from utils.reporting import Reporter
from utils.tools import process_nested_dict


STREAM_URL = "https://example.com/live.m3u8"


def make_channel(headers, *, catchup=None):
    return {
        "id": stable_result_id(STREAM_URL, headers),
        "url": STREAM_URL,
        "host": "example.com",
        "date": None,
        "delay": 10,
        "speed": 5,
        "resolution": "1920x1080",
        "origin": "subscribe",
        "ipv_type": "ipv4",
        "location": None,
        "isp": None,
        "headers": headers,
        "catchup": catchup,
        "tvg_logo": "https://example.com/logo.png",
        "extra_info": "",
        "supply": False,
    }


class PlaylistMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _TOTAL_URLS_CACHE.clear()
        _WRITTEN_CONTENT_DIGESTS.clear()

    def tearDown(self):
        _TOTAL_URLS_CACHE.clear()
        _WRITTEN_CONTENT_DIGESTS.clear()

    def output_config(self):
        stack = ExitStack()
        values = {
            "open_epg": False,
            "open_update_time": False,
            "update_time_position": "top",
            "open_url_info": False,
            "open_subscribe_logo": True,
            "open_subscribe_epg": False,
            "open_headers": True,
            "open_unmatch_category": False,
            "open_auto_disable_source": False,
            "user_agent": "",
            "logo_url": "https://example.com/logos",
            "logo_type": "png",
            "cdn_url": "",
            "urls_limit": 10,
        }
        for name, value in values.items():
            stack.enter_context(
                patch.object(type(config), name, new=PropertyMock(return_value=value))
            )
        stack.enter_context(
            patch("utils.channel.get_public_url", return_value="http://localhost")
        )
        stack.enter_context(
            patch("utils.tools.get_logo_url", return_value="https://example.com/logos")
        )
        return stack

    def test_same_url_with_different_headers_is_not_deduplicated(self):
        info_data = {}

        append_data_to_info_data(
            info_data,
            "News",
            "Demo",
            [
                {
                    "url": STREAM_URL,
                    "headers": {"User-Agent": "UA-A"},
                    "ipv_type": "ipv4",
                },
                {
                    "url": STREAM_URL,
                    "headers": {"User-Agent": "UA-B"},
                    "ipv_type": "ipv4",
                },
            ],
            origin="subscribe",
            skip_validation=True,
        )

        channels = info_data["News"]["Demo"]
        self.assertEqual(len(channels), 2)
        self.assertNotEqual(channels[0]["id"], channels[1]["id"])

        test_queue = copy.deepcopy(info_data)
        process_nested_dict(test_queue, seen=set())
        self.assertEqual(len(test_queue["News"]["Demo"]), 2)

        with patch("utils.channel.get_sort_result", side_effect=lambda items, **_: list(items)):
            sorted_data = sort_channel_result(info_data, result=info_data)
        self.assertEqual(len(sorted_data["News"]["Demo"]), 2)

    def test_metadata_change_regenerates_m3u_and_preserves_variant_order(self):
        channels = [
            make_channel(
                {"User-Agent": "UA-A"},
                catchup={"catchup": "append", "catchup-source": "archive-a"},
            ),
            make_channel(
                {"User-Agent": "UA-B"},
                catchup={"catchup": "append", "catchup-source": "archive-b"},
            ),
        ]
        data = {"News": {"Demo": channels}}

        with tempfile.TemporaryDirectory() as temp_dir, self.output_config():
            result_path = os.path.join(temp_dir, "result.txt")
            first_changed = process_write_content(
                result_path,
                data,
                ipv_type_prefer=[],
                origin_type_prefer=[],
                is_last=True,
            )
            with open(os.path.splitext(result_path)[0] + ".m3u", encoding="utf-8") as file:
                first_m3u = file.read()

            self.assertTrue(first_changed)
            self.assertLess(first_m3u.index("UA-A"), first_m3u.index("UA-B"))
            self.assertIn('catchup-source="archive-a"', first_m3u)
            self.assertIn('catchup-source="archive-b"', first_m3u)

            unchanged = process_write_content(
                result_path,
                data,
                ipv_type_prefer=[],
                origin_type_prefer=[],
                is_last=True,
            )
            self.assertFalse(unchanged)

            channels[1]["headers"] = {"User-Agent": "UA-C"}
            channels[1]["id"] = stable_result_id(STREAM_URL, channels[1]["headers"])
            second_changed = process_write_content(
                result_path,
                data,
                ipv_type_prefer=[],
                origin_type_prefer=[],
                is_last=True,
            )
            with open(os.path.splitext(result_path)[0] + ".m3u", encoding="utf-8") as file:
                second_m3u = file.read()

        self.assertTrue(second_changed)
        self.assertIn("UA-A", second_m3u)
        self.assertIn("UA-C", second_m3u)
        self.assertNotIn("UA-B", second_m3u)
        self.assertLess(second_m3u.index("UA-A"), second_m3u.index("UA-C"))

    async def test_subscribe_catchup_variants_are_retained(self):
        content = """#EXTM3U
#EXTINF:-1 catchup="append" catchup-source="archive-a",Demo
https://example.com/live.m3u8
#EXTINF:-1 catchup="append" catchup-source="archive-b",Demo
https://example.com/live.m3u8
"""
        reporter = Reporter(enable_console=False, enable_runtime_file=False)

        with tempfile.TemporaryDirectory() as temp_dir, self.output_config(), (
            patch.object(constants, "unmatch_log_path", os.path.join(temp_dir, "unmatch.log"))
        ), patch.object(
            constants,
            "unmatch_jsonl_path",
            os.path.join(temp_dir, "unmatch.jsonl"),
        ), patch(
            "updates.subscribe.request.fetch_first",
            new=AsyncMock(return_value=content),
        ), patch(
            "updates.subscribe.request.save_url_content"
        ):
            result = await get_channels_by_subscribe_urls(
                ["https://example.com/list.m3u"],
                names=["Demo"],
                reporter=reporter,
            )
        reporter.close()

        channels = result["Demo"]
        self.assertEqual(len(channels), 2)
        self.assertEqual(
            [item["catchup"]["catchup-source"] for item in channels],
            ["archive-a", "archive-b"],
        )


if __name__ == "__main__":
    unittest.main()
