import unittest
from unittest import mock

from utils.rtmp_runtime import find_homebrew_executable


class RtmpRuntimeTests(unittest.TestCase):
    def test_finds_apple_silicon_homebrew_without_shell_path(self):
        expected = "/opt/homebrew/bin/brew"

        def usable(path):
            return path == expected

        with mock.patch(
            "utils.rtmp_runtime.shutil.which",
            return_value=None,
        ), mock.patch(
            "utils.rtmp_runtime.os.path.isfile",
            side_effect=usable,
        ), mock.patch(
            "utils.rtmp_runtime.os.access",
            side_effect=lambda path, mode: usable(path),
        ):
            self.assertEqual(find_homebrew_executable(), expected)
