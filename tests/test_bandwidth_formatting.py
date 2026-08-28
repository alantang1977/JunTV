import unittest

from desktop_ui.formatting import format_bandwidth


class BandwidthFormattingTests(unittest.TestCase):
    def test_uses_kilobits_below_one_megabit(self):
        self.assertEqual(format_bandwidth(999_900), "999.9 Kbit/s")

    def test_uses_megabits_at_one_megabit(self):
        self.assertEqual(format_bandwidth(1_000_000), "1.0 Mbit/s")

    def test_handles_missing_or_invalid_values(self):
        self.assertEqual(format_bandwidth(None), "0.0 Kbit/s")
        self.assertEqual(format_bandwidth("invalid"), "0.0 Kbit/s")


if __name__ == "__main__":
    unittest.main()
