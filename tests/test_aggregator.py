import unittest
from unittest.mock import patch

from utils.aggregator import _preserve_unmeasured_history
from utils.identity import stable_result_id


def result(url, speed=5.0):
    return {
        "id": stable_result_id(url),
        "url": url,
        "origin": "subscribe",
        "ipv_type": "ipv4",
        "speed": speed,
        "delay": 10,
        "resolution": "1920x1080",
    }


class AggregatorHistoryTests(unittest.TestCase):
    def test_preserves_previous_result_not_retested_this_run(self):
        tested_url = "https://example.com/tested.m3u8"
        pending_url = "https://example.com/pending.m3u8"
        base_data = {"News": {"Demo": [result(tested_url), result(pending_url)]}}
        test_copy = {"News": {"Demo": [result(tested_url, speed=0)]}}
        previous = {"News": {"Demo": [result(tested_url), result(pending_url)]}}

        with patch("utils.aggregator.is_url_frozen", return_value=False):
            preserved = _preserve_unmeasured_history(test_copy, previous, base_data)

        urls = [item["url"] for item in preserved["News"]["Demo"]]
        self.assertEqual(urls, [tested_url, pending_url])

    def test_does_not_preserve_frozen_previous_result(self):
        pending_url = "https://example.com/pending.m3u8"
        base_data = {"News": {"Demo": [result(pending_url)]}}
        previous = {"News": {"Demo": [result(pending_url)]}}

        with patch("utils.aggregator.is_url_frozen", return_value=True):
            preserved = _preserve_unmeasured_history({}, previous, base_data)

        self.assertEqual(preserved["News"]["Demo"], [])


if __name__ == "__main__":
    unittest.main()
