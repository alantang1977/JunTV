import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "scripts" / "gui_showcase" / "fixture.py"
SPEC = importlib.util.spec_from_file_location("gui_showcase_fixture", FIXTURE_PATH)
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class GuiShowcaseFixtureTests(unittest.TestCase):
    def test_bilingual_fixture_counts_and_stream_references(self):
        for language in ("zh_CN", "en"):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                data = fixture.build_demo_data(language, root / "logos")
                snapshot = fixture.seed_demo_repository(
                    root / "channel_results.db",
                    data,
                    now=1_800_000_000,
                )
                summary = fixture.validate_demo_repository(
                    root / "channel_results.db",
                    snapshot,
                )

                self.assertEqual(summary["channels"], fixture.EXPECTED_CHANNELS)
                self.assertEqual(summary["results"], fixture.EXPECTED_RESULTS)
                self.assertEqual(
                    summary["valid_results"],
                    fixture.EXPECTED_VALID_RESULTS,
                )
                self.assertEqual(
                    summary["selected_results"],
                    fixture.EXPECTED_SELECTED_RESULTS,
                )
                self.assertEqual(
                    summary["active_streams"],
                    fixture.EXPECTED_ACTIVE_STREAMS,
                )
                self.assertEqual(
                    summary["starting_streams"],
                    fixture.EXPECTED_STARTING_STREAMS,
                )

    def test_showcase_tooling_is_excluded_from_build_artifacts(self):
        dockerignore = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("scripts/gui_showcase", dockerignore)
        self.assertIn("tests", dockerignore)
        self.assertIn("output/screenshots", dockerignore)
        for spec_path in (
            ROOT / "desktop_ui" / "desktop_ui.spec",
            ROOT / "tkinter_ui" / "tkinter_ui.spec",
        ):
            self.assertNotIn(
                "gui_showcase",
                spec_path.read_text(encoding="utf-8"),
            )

    def test_production_modules_do_not_import_showcase_tooling(self):
        production_paths = [
            ROOT / "main.py",
            *(ROOT / "desktop_ui").rglob("*.py"),
            *(ROOT / "service").rglob("*.py"),
            *(ROOT / "utils").rglob("*.py"),
        ]
        for path in production_paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn(
                    "gui_showcase",
                    path.read_text(encoding="utf-8"),
                )

if __name__ == "__main__":
    unittest.main()
