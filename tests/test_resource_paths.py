import os
import tempfile
import unittest
from pathlib import Path

from utils.config import resource_path as config_resource_path
from utils.tools import get_version_info, resource_path as tools_resource_path


class ResourcePathTests(unittest.TestCase):
    def test_static_resources_resolve_after_working_directory_changes(self):
        resource_paths = (
            "version.json",
            "CHANGELOG.md",
            "favicon.ico",
            "locales/zh_CN.json",
            "service/nginx.conf.template",
        )
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            os.chdir(directory)
            try:
                for resolve in (config_resource_path, tools_resource_path):
                    for relative_path in resource_paths:
                        self.assertTrue(Path(resolve(relative_path)).is_file(), relative_path)
                self.assertIn("version", get_version_info())
            finally:
                os.chdir(previous_directory)

    def test_persistent_paths_stay_in_the_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            os.chdir(directory)
            try:
                expected = Path(os.getcwd()) / "output" / "data.json"
                self.assertEqual(Path(config_resource_path("output/data.json", persistent=True)), expected)
                self.assertEqual(Path(tools_resource_path("output/data.json", persistent=True)), expected)
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
