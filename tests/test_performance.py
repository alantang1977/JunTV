import unittest
from unittest.mock import patch

from utils.performance import get_performance_settings


class PerformanceProfileTests(unittest.TestCase):
    def tearDown(self):
        get_performance_settings.cache_clear()

    def test_two_core_two_gb_profiles_serialize_epg_parsing(self):
        with patch("utils.performance.detect_resources", return_value=(2.0, 2.0)):
            get_performance_settings.cache_clear()
            automatic = get_performance_settings("auto", 0)
            powersave = get_performance_settings("powersave", 0)

        self.assertEqual(automatic.epg_parse_concurrency, 1)
        self.assertEqual(powersave.epg_parse_concurrency, 1)
        self.assertEqual(automatic.epg_fetch_concurrency, 2)
        self.assertEqual(powersave.epg_fetch_concurrency, 1)

    def test_fast_high_memory_profile_allows_bounded_epg_parallelism(self):
        with patch("utils.performance.detect_resources", return_value=(8.0, 8.0)):
            get_performance_settings.cache_clear()
            settings = get_performance_settings("fast", 0)

        self.assertEqual(settings.epg_fetch_concurrency, 4)
        self.assertEqual(settings.epg_parse_concurrency, 2)


if __name__ == "__main__":
    unittest.main()
