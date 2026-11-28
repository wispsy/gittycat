import os
import subprocess
import sys
import unittest

from gitcx import AccessGrant, Owner, Permission, Principal, RepositoryInfo, RepositoryRecord, Slug, StorageId
from test_permissions import OWNER, REPO, repository


class DomainTests(unittest.TestCase):
    def test_canonical_slug_and_reserved_routes(self):
        for name in ("example", "r", "repo_2", "my-project", "a" * 63):
            self.assertEqual(str(Slug(name)), name)
        for name in ("", "../escape", ".git", "repo.git", "A", "a/b", "a\\b", "a%2fb", "a b", "é", "api", "admin", "-repo", "repo-", "a" * 64):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Slug(name)

    def test_opaque_storage_ids_are_separate_from_slugs(self):
        storage = StorageId.new()
        self.assertEqual(len(str(storage)), 32)
        record = repository(storage_id=storage)
        self.assertEqual(repository(slug=Slug("renamed"), storage_id=storage).storage_id, record.storage_id)
        for value in ("../escape", "A" * 32, "0" * 31, "g" * 32, "/tmp/repo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                StorageId(value)

    def test_invalid_identity_role_and_metadata_fail(self):
        with self.assertRaises(ValueError):
            Principal("username")
        with self.assertRaises(ValueError):
            Owner(OWNER, "unknown")
        with self.assertRaises(ValueError):
            AccessGrant(REPO, OWNER, 4)
        for value in (True, 1.0, "1"):
            with self.assertRaises(ValueError):
                AccessGrant(REPO, OWNER, value)
        with self.assertRaises(ValueError):
            repository(object_format="unknown")
        with self.assertRaises(TypeError):
            RepositoryRecord(REPO, repository().owner, "raw-slug", StorageId.new())

    def test_backend_description_has_immutable_capabilities(self):
        selected = {"loose-objects.read"}
        info = RepositoryInfo("sha256", True, selected)
        selected.add("receive")
        self.assertEqual(info.capabilities, frozenset({"loose-objects.read"}))

    def test_package_cli_without_git_runtime(self):
        result = subprocess.run([sys.executable, "-I", "-m", "gitcx", "--help"],
                                capture_output=True, env=dict(os.environ, PATH=""))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"Git-compatible", result.stdout)
