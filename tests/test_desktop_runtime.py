import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_ui.runtime import (
    DATA_DIRECTORY_ENV,
    RuntimeDirectoryError,
    default_runtime_directory,
    prepare_runtime_directory,
)


class DesktopRuntimeTests(unittest.TestCase):
    def test_explicit_directory_takes_precedence_and_consumes_option(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "custom data"
            argv = ["iptv-api.exe", "--data-dir", str(directory)]
            environment = {}

            with patch("desktop_ui.runtime.os.chdir") as change_directory:
                result = prepare_runtime_directory(
                    argv,
                    environ=environment,
                    frozen=True,
                    executable=Path(temporary) / "iptv-api.exe",
                )

            self.assertEqual(result, directory.resolve())
            self.assertEqual(argv, ["iptv-api.exe"])
            self.assertEqual(environment[DATA_DIRECTORY_ENV], str(directory.resolve()))
            change_directory.assert_called_once_with(directory.resolve())

    def test_frozen_windows_prefers_the_executable_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "application" / "iptv-api.exe"
            executable.parent.mkdir()
            environment = {}

            with patch("desktop_ui.runtime.os.chdir") as change_directory:
                result = prepare_runtime_directory(
                    [str(executable)],
                    environ=environment,
                    frozen=True,
                    executable=executable,
                    fallback_directory=Path(temporary) / "fallback",
                )

            self.assertEqual(result, executable.parent.resolve())
            self.assertEqual(environment[DATA_DIRECTORY_ENV], str(executable.parent.resolve()))
            change_directory.assert_called_once_with(executable.parent.resolve())

    def test_nonportable_frozen_runtime_uses_system_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            fallback = Path(temporary) / "system data"

            with patch("desktop_ui.runtime.os.chdir") as change_directory:
                result = prepare_runtime_directory(
                    ["iptv-api"],
                    environ={},
                    frozen=True,
                    fallback_directory=fallback,
                    prefer_executable_directory=False,
                )

            self.assertEqual(result, fallback.resolve())
            change_directory.assert_called_once_with(fallback.resolve())

    def test_saved_directory_is_used_when_no_explicit_override_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            saved = Path(temporary) / "saved data"
            executable = Path(temporary) / "application" / "iptv-api.exe"

            with patch("desktop_ui.runtime.os.chdir") as change_directory:
                result = prepare_runtime_directory(
                    [str(executable)],
                    environ={},
                    frozen=True,
                    executable=executable,
                    saved_directory=saved,
                )

            self.assertEqual(result, saved.resolve())
            change_directory.assert_called_once_with(saved.resolve())

    def test_locked_executable_directory_falls_back_to_system_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "application" / "iptv-api.exe"
            fallback = Path(temporary) / "system data"

            with patch("desktop_ui.runtime._writable_directory", side_effect=[False, True]), patch(
                "desktop_ui.runtime.os.chdir"
            ) as change_directory:
                result = prepare_runtime_directory(
                    [str(executable)],
                    environ={},
                    frozen=True,
                    executable=executable,
                    fallback_directory=fallback,
                )

            self.assertEqual(result, fallback.resolve())
            change_directory.assert_called_once_with(fallback.resolve())

    def test_default_directory_falls_back_when_executable_directory_is_locked(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "desktop_ui.runtime._writable_directory", side_effect=[False, True]
        ):
            fallback = Path(temporary) / "system data"
            result = default_runtime_directory(
                fallback_directory=fallback,
                frozen=True,
                executable=Path(temporary) / "application" / "iptv-api.exe",
            )

        self.assertEqual(result, fallback.resolve())

    def test_source_runtime_keeps_its_current_directory_when_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            working_directory = Path(temporary) / "working"
            fallback = Path(temporary) / "system data"
            working_directory.mkdir()
            previous_directory = os.getcwd()
            os.chdir(working_directory)
            try:
                result = default_runtime_directory(
                    fallback_directory=fallback,
                    frozen=False,
                )
            finally:
                os.chdir(previous_directory)

        self.assertEqual(result, working_directory.resolve())

    def test_unwritable_explicit_directory_reports_an_error(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "desktop_ui.runtime._writable_directory", return_value=False
        ):
            with self.assertRaises(RuntimeDirectoryError):
                prepare_runtime_directory(
                    ["iptv-api", "--data-dir", os.path.join(temporary, "locked")],
                    environ={},
                    frozen=True,
                )


if __name__ == "__main__":
    unittest.main()
