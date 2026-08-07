import unittest
from unittest.mock import Mock, call, patch

from utils.rtmp_stats import fetch_rtmp_snapshot


class RtmpSnapshotRequestTests(unittest.TestCase):
    @patch("utils.rtmp_stats.parse_rtmp_stats")
    @patch("utils.rtmp_stats.requests.get")
    def test_loopback_requests_bypass_system_proxy(self, get, parse):
        stat_response = Mock(content=b"<rtmp />")
        stat_response.raise_for_status.return_value = None
        runtime_response = Mock()
        runtime_response.raise_for_status.return_value = None
        runtime_response.json.return_value = {"streams": {}}
        get.side_effect = [stat_response, runtime_response]
        parse.return_value = {
            "available": True,
            "streams": [],
            "active_count": 0,
        }

        snapshot = fetch_rtmp_snapshot(timeout=1.5)

        self.assertTrue(snapshot["available"])
        self.assertEqual(
            get.call_args_list,
            [
                call(
                    "http://127.0.0.1:8080/stat",
                    timeout=1.5,
                    proxies={"http": None, "https": None, "all": None},
                ),
                call(
                    "http://127.0.0.1:5180/api/rtmp/runtime",
                    timeout=0.8,
                    proxies={"http": None, "https": None, "all": None},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
