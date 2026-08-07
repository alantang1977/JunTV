import gzip
import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from utils.ip_checker.ip_checker import (
    COMPRESSED_DATABASE_PATH,
    DATABASE_HASH_SUFFIX,
    DATABASE_PATH,
    prepare_database,
)


class PrepareDatabaseTests(unittest.TestCase):
    def test_uses_uncompressed_database_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "qqwry.ipdb")
            with open(database_path, "wb") as file:
                file.write(b"database")

            with patch(
                "utils.ip_checker.ip_checker.resource_path",
                side_effect=lambda path, persistent=False: (
                    os.path.join(directory, "missing.gz")
                    if path == COMPRESSED_DATABASE_PATH
                    else database_path
                ),
            ) as resource:
                self.assertEqual(prepare_database(), database_path)

            self.assertEqual(
                resource.call_args_list[0].args,
                (COMPRESSED_DATABASE_PATH,),
            )

    def test_extracts_compressed_database_to_persistent_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            packaged_database = os.path.join(directory, "missing.ipdb")
            compressed_path = os.path.join(directory, "qqwry.ipdb.gz")
            persistent_path = os.path.join(directory, "data", "qqwry.ipdb")
            with gzip.open(compressed_path, "wb") as file:
                file.write(b"compressed database")

            def resolve(path, persistent=False):
                if persistent:
                    return persistent_path
                if path == COMPRESSED_DATABASE_PATH:
                    return compressed_path
                return packaged_database

            with patch(
                "utils.ip_checker.ip_checker.resource_path",
                side_effect=resolve,
            ):
                self.assertEqual(prepare_database(), persistent_path)

            with open(persistent_path, "rb") as file:
                self.assertEqual(file.read(), b"compressed database")
            with open(
                f"{persistent_path}{DATABASE_HASH_SUFFIX}",
                encoding="ascii",
            ) as file:
                stored_hash = file.read()
            with open(compressed_path, "rb") as file:
                expected_hash = hashlib.sha256(file.read()).hexdigest()
            self.assertEqual(stored_hash, expected_hash)

    def test_refreshes_persistent_database_when_bundle_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            compressed_path = os.path.join(directory, "qqwry.ipdb.gz")
            persistent_path = os.path.join(directory, "data", "qqwry.ipdb")
            os.makedirs(os.path.dirname(persistent_path))
            with open(persistent_path, "wb") as file:
                file.write(b"old database")
            with open(
                f"{persistent_path}{DATABASE_HASH_SUFFIX}",
                "w",
                encoding="ascii",
            ) as file:
                file.write("outdated")
            with gzip.open(compressed_path, "wb") as file:
                file.write(b"new database")

            def resolve(path, persistent=False):
                if persistent:
                    return persistent_path
                if path == COMPRESSED_DATABASE_PATH:
                    return compressed_path
                return os.path.join(directory, "missing.ipdb")

            with patch(
                "utils.ip_checker.ip_checker.resource_path",
                side_effect=resolve,
            ):
                prepare_database()

            with open(persistent_path, "rb") as file:
                self.assertEqual(file.read(), b"new database")


if __name__ == "__main__":
    unittest.main()
