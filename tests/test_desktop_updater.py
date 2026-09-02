import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from desktop_ui.update_installer import application_root
from desktop_ui.update_manager import (
    _asset_selection,
    _checksum_for_asset,
    _manifest_from_bytes,
    _sha256_from_digest,
)
from desktop_ui.updater import (
    UpdateError,
    _preserve_runtime_data,
    _safe_extract,
    extracted_application_root,
    install_update,
    sha256_file,
)


class DesktopUpdaterTests(unittest.TestCase):
    @staticmethod
    def _release_asset(name, digest=""):
        return {
            "name": name,
            "browser_download_url": f"https://example.com/{name}",
            "digest": digest,
        }

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

    def test_manifest_selects_the_latest_complete_active_revision(self):
        assets = [
            self._release_asset("IPTV-API-GUI-Windows-X64-v3.0.0.zip"),
            self._release_asset("IPTV-API-GUI-macOS-ARM64-v3.0.0.zip"),
            self._release_asset("IPTV-API-GUI-Windows-X64-v3.0.0-r20260810120000.zip"),
            self._release_asset("IPTV-API-GUI-macOS-ARM64-v3.0.0-r20260810120000.zip"),
            self._release_asset("IPTV-API-GUI-Windows-X64-v3.0.0-r20260811120000.zip"),
            self._release_asset("IPTV-API-GUI-macOS-ARM64-v3.0.0-r20260811120000.zip"),
        ]
        manifest = {
            "schema_version": 1,
            "version": "3.0.0",
            "builds": [
                {
                    "revision": 20260810120000,
                    "status": "active",
                    "assets": {
                        "windows-x64": assets[2]["name"],
                        "macos-arm64": assets[3]["name"],
                    },
                },
                {
                    "revision": 20260811120000,
                    "status": "active",
                    "assets": {
                        "windows-x64": assets[4]["name"],
                        "macos-arm64": assets[5]["name"],
                    },
                },
            ],
        }

        asset, revision = _asset_selection(
            assets,
            "3.0.0",
            manifest,
            platform_name="win32",
            machine_name="AMD64",
        )

        self.assertEqual(asset["name"], assets[4]["name"])
        self.assertEqual(revision, 20260811120000)

    def test_deleted_or_withdrawn_revision_falls_back_to_legacy_asset(self):
        windows = self._release_asset("IPTV-API-GUI-Windows-X64-v3.0.0.zip")
        macos = self._release_asset("IPTV-API-GUI-macOS-ARM64-v3.0.0.zip")
        deleted_windows = "IPTV-API-GUI-Windows-X64-v3.0.0-r20260811120000.zip"
        hotfix_macos = self._release_asset("IPTV-API-GUI-macOS-ARM64-v3.0.0-r20260811120000.zip")
        manifest = {
            "schema_version": 1,
            "version": "3.0.0",
            "builds": [{
                "revision": 20260811120000,
                "status": "active",
                "assets": {
                    "windows-x64": deleted_windows,
                    "macos-arm64": hotfix_macos["name"],
                },
            }],
        }

        asset, revision = _asset_selection(
            [windows, macos, hotfix_macos],
            "3.0.0",
            manifest,
            platform_name="darwin",
            machine_name="arm64",
        )

        self.assertEqual(asset, macos)
        self.assertEqual(revision, 0)

        manifest["builds"][0]["status"] = "withdrawn"
        asset, revision = _asset_selection(
            [windows, macos, self._release_asset(deleted_windows), hotfix_macos],
            "3.0.0",
            manifest,
            platform_name="win32",
            machine_name="AMD64",
        )
        self.assertEqual(asset, windows)
        self.assertEqual(revision, 0)

    def test_manifest_parser_validates_schema(self):
        content = b'{"schema_version": 1, "version": "3.0.0", "builds": []}'
        self.assertEqual(_manifest_from_bytes(content)["version"], "3.0.0")
        with self.assertRaises(ValueError):
            _manifest_from_bytes(b'{"schema_version": 2, "builds": []}')

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

    def test_runtime_data_is_preserved_without_replacing_new_default_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            previous = directory / "previous"
            replacement = directory / "replacement"
            (previous / "config" / "logo").mkdir(parents=True)
            (previous / "output" / "data").mkdir(parents=True)
            (replacement / "config").mkdir(parents=True)
            (previous / "config" / "config.ini").write_text("old default", encoding="utf-8")
            (replacement / "config" / "config.ini").write_text("new default", encoding="utf-8")
            (previous / "config" / "user_config.ini").write_text("user setting", encoding="utf-8")
            (previous / "config" / "logo" / "CCTV.png").write_text("logo", encoding="utf-8")
            (previous / "output" / "data" / "history.db").write_text("history", encoding="utf-8")

            _preserve_runtime_data(previous, replacement)

            self.assertEqual((replacement / "config" / "config.ini").read_text(encoding="utf-8"), "new default")
            self.assertEqual((replacement / "config" / "user_config.ini").read_text(encoding="utf-8"), "user setting")
            self.assertEqual((replacement / "config" / "logo" / "CCTV.png").read_text(encoding="utf-8"), "logo")
            self.assertEqual((replacement / "output" / "data" / "history.db").read_text(encoding="utf-8"), "history")

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
