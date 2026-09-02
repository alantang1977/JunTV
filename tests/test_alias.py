import re
import unittest
from pathlib import Path

from utils.alias import Alias


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.aliases = Alias()

    def test_cctv_regional_variants_do_not_match_general_channels(self):
        self.assertEqual(
            self.aliases.get_primary("CCTV5＋体育赛事"),
            "CCTV-5+",
        )
        self.assertEqual(
            self.aliases.get_primary("CCTV4中文國際歐洲IPV6"),
            "CCTV-4欧洲",
        )
        self.assertEqual(
            self.aliases.get_primary("CCTV-1綜合"),
            "CCTV-1",
        )

    def test_distinct_channels_are_not_aliases(self):
        first_theatre_aliases = self.aliases.get("第一剧场")
        military_documentary_aliases = self.aliases.get("NewTV军事纪实")

        self.assertNotIn("风云剧场", first_theatre_aliases)
        self.assertNotIn("中国电影", first_theatre_aliases)
        self.assertNotIn("魅力潇湘", military_documentary_aliases)

    def test_discussion_aliases_are_available_without_cross_channel_matches(self):
        self.assertEqual(self.aliases.get_primary("4kfitness"), "4K健身")
        self.assertEqual(self.aliases.get_primary("TVB J1频道"), "TVB J1")
        self.assertEqual(self.aliases.get_primary("CCTV5P"), "CCTV-5+")
        self.assertEqual(self.aliases.get_primary("cctv1风云剧场"), "风云剧场")
        self.assertEqual(self.aliases.get_primary("CCTV-1未知频道"), "CCTV-1未知频道")

    def test_alias_catalog_has_no_cross_channel_collisions(self):
        definitions = self._definitions()
        self.assertEqual(len(definitions), 2769)
        self.assertEqual(len(self.aliases.primary_to_aliases), 2769)

        exact_aliases = 0
        regex_aliases = 0
        owners = {}
        for primary, aliases in definitions:
            owners.setdefault(primary, set()).add(primary)
            for alias in aliases:
                self.assertFalse(any("\x80" <= char <= "\x9f" for char in alias))
                if alias.startswith("re:"):
                    regex_aliases += 1
                else:
                    owners.setdefault(alias, set()).add(primary)
                    exact_aliases += alias != primary

        self.assertEqual(exact_aliases, 7206)
        self.assertEqual(regex_aliases, 48)
        self.assertTrue(all(len(channel_owners) == 1 for channel_owners in owners.values()))

        for primary, aliases in definitions:
            for alias in aliases:
                if not alias.startswith("re:"):
                    continue
                pattern = re.compile(alias[3:])
                for name, channel_owners in owners.items():
                    if primary not in channel_owners:
                        self.assertIsNone(pattern.match(name), f"{alias} matches {name}")

    @staticmethod
    def _definitions():
        alias_path = Path(__file__).parents[1] / "config" / "alias.txt"
        definitions = []
        for line in alias_path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "," not in line:
                continue
            values = [value.strip() for value in line.split(",") if value.strip()]
            if len(values) >= 2:
                definitions.append((values[0], values[1:]))
        return definitions


if __name__ == "__main__":
    unittest.main()
