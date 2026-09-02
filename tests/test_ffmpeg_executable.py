import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils.ffmpeg.ffmpeg import check_ffmpeg_installed_status
from utils.ffmpeg.executable import (
    resolve_ffmpeg_executable,
    resolve_ffprobe_executable,
)


class FfmpegExecutableTests(unittest.TestCase):
    def _make_executable(self, directory: str, name: str) -> str:
        path = Path(directory, name)
        path.touch()
        path.chmod(0o755)
        return str(path)

    def test_explicit_ffmpeg_path_takes_precedence_over_path(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self._make_executable(directory, "custom-ffmpeg")
            with mock.patch.dict(
                os.environ,
                {"IPTV_API_FFMPEG_PATH": configured},
                clear=True,
            ), mock.patch(
                "utils.ffmpeg.executable.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ):
                self.assertEqual(resolve_ffmpeg_executable(), configured)

    def test_ffprobe_is_found_next_to_configured_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = self._make_executable(directory, "ffmpeg")
            ffprobe = self._make_executable(directory, "ffprobe")
            with mock.patch.dict(
                os.environ,
                {"IPTV_API_FFMPEG_PATH": ffmpeg},
                clear=True,
            ), mock.patch(
                "utils.ffmpeg.executable.shutil.which",
                side_effect=lambda name, path=None: (
                    ffprobe if name == "ffprobe" and path == directory else None
                ),
            ):
                self.assertEqual(resolve_ffprobe_executable(), ffprobe)

    def test_installation_check_runs_resolved_executable(self):
        result = mock.Mock(returncode=0)
        with mock.patch(
            "utils.ffmpeg.ffmpeg.resolve_ffmpeg_executable",
            return_value="/opt/homebrew/bin/ffmpeg",
        ), mock.patch(
            "utils.ffmpeg.ffmpeg.subprocess.run",
            return_value=result,
        ) as run:
            self.assertTrue(check_ffmpeg_installed_status())

        self.assertEqual(
            run.call_args.args[0],
            ["/opt/homebrew/bin/ffmpeg", "-version"],
        )

    def test_macos_homebrew_path_is_checked_without_shell_path(self):
        expected = "/opt/homebrew/bin/ffmpeg"

        def usable(path):
            return path == expected

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "utils.ffmpeg.executable.shutil.which",
            return_value=None,
        ), mock.patch(
            "utils.ffmpeg.executable.sys.platform",
            "darwin",
        ), mock.patch(
            "utils.ffmpeg.executable.os.path.isfile",
            side_effect=usable,
        ), mock.patch(
            "utils.ffmpeg.executable.os.access",
            side_effect=lambda path, mode: usable(path),
        ):
            self.assertEqual(resolve_ffmpeg_executable(), expected)
