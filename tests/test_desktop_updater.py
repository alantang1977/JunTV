import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from desktop_ui.update_installer import application_root
from desktop_ui.update_manager import _checksum_for_asset, _sha256_from_digest
from desktop_ui.updater import UpdateError, _safe_extract, extracted_application_root, install_update, sha256_file


class DesktopUpdaterTests(unittest.TestCase):
    def test_release_checksum_parsing_requires_the_matching_asset(self):
        content = (
            b"a" * 64 + b"  IPTV-API-GUI-Windows-X64-v2.0.0.zip\n"
            + b"b" * 64 + b"  IPTV-API-GUI-macOS-ARM64-v2.0.0.zip\n"
        )
        self.assertEqual(
            _checksum_for_asset(content, "IPTV-API-GUI-macOS-ARM64-v2.0.0.zip"),
            "b" * 64,
        )
        self.assertEqual(_checksum_for_asset(content, "missing.zip"), "")
        self.assertEqual(_sha256_from_digest("sha256:" + "c" * 64), "c" * 64)

    def test_application_root_uses_the_app_bundle_on_macos(self):
        executable = "/Applications/IPTV API.app/Contents/MacOS/IPTV API"
        self.assertEqual(application_root(executable, "darwin"), Path("/Applications/IPTV API.app"))

    def test_safe_extract_rejects_paths_outside_the_staging_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside", "no")
            with self.assertRaises(UpdateError):
                _safe_extract(archive, directory / "staging")

    def test_safe_extract_preserves_a_single_application_root_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "update.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("IPTV-API-GUI-v2.0.0/IPTV-API-GUI-v2.0.0.exe", "program")
            staging = directory / "staging"
            staging.mkdir()
            _safe_extract(archive, staging)
            root = extracted_application_root(staging, "win32")
            program = root / "IPTV-API-GUI-v2.0.0.exe"
            self.assertEqual(sha256_file(program), hashlib.sha256(b"program").hexdigest())

    def test_install_replaces_the_bundle_only_after_the_helper_starts_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "IPTV API.app"
            old_binary = target / "Contents" / "MacOS" / "IPTV API"
            old_binary.parent.mkdir(parents=True)
            old_binary.write_text("old", encoding="utf-8")
            old_binary.chmod(0o755)
            archive = directory / "update.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                item = zipfile.ZipInfo("IPTV API.app/Contents/MacOS/IPTV API")
                item.external_attr = 0o755 << 16
                bundle.writestr(item, "new")

            class StartedProcess:
                def poll(self):
                    return None

            def start(command, cwd, env):
                Path(env["IPTV_API_UPDATE_HEALTH_FILE"]).write_text("ok", encoding="utf-8")
                return StartedProcess()

            with patch("desktop_ui.updater.sys.platform", "darwin"), patch(
                "desktop_ui.updater.wait_for_process"
            ), patch("desktop_ui.updater.subprocess.Popen", side_effect=start):
                install_update(123, archive, target, sha256_file(archive))

            self.assertEqual(old_binary.read_text(encoding="utf-8"), "new")
            self.assertFalse((directory / ".IPTV API.app.previous").exists())


if __name__ == "__main__":
    unittest.main()
