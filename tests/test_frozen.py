import unittest
from unittest.mock import patch

import utils.frozen as frozen


class FrozenUrlTests(unittest.TestCase):
    def test_transient_failures_do_not_freeze_until_threshold(self):
        url = "https://example.com/live.m3u8"
        with patch.dict(frozen._frozen, {}, clear=True):
            frozen.mark_url_bad(url)
            self.assertFalse(frozen.is_url_frozen(url))

            frozen.mark_url_bad(url)
            self.assertFalse(frozen.is_url_frozen(url))

            frozen.mark_url_bad(url)
            self.assertTrue(frozen.is_url_frozen(url))

    def test_success_resets_failure_streak(self):
        url = "https://example.com/live.m3u8"
        with patch.dict(frozen._frozen, {}, clear=True):
            frozen.mark_url_bad(url)
            frozen.mark_url_good(url)
            frozen.mark_url_bad(url)
            self.assertFalse(frozen.is_url_frozen(url))


if __name__ == "__main__":
    unittest.main()
