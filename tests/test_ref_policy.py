from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gitdx import Blob, Commit, ObjectId, PushCommand, RevisionLimits, Tree
from gittycat import (AccessGrant, Action, AuthenticationError, BranchRule, Catalog,
                     CatalogConflict, GitdxBackend, Membership, OrganizationRole,
                     Owner, OwnerKind, Permission, PermissionDenied, Principal,
                     RefPolicyService, RefReason, RepositoryService, Slug, StorageId,
                     TokenAuth, Visibility)
from gittycat.catalog import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION
from test_permissions import OWNER, READER, WRITER, OUTSIDER, ORG, REPO, repository


IDENTITY = b"Policy Test <policy@example.invalid> 1700000000 +0000"


def commit(repo, label, parents=()):
    tree = repo.objects.write(Tree((), repo.object_format).to_raw())
    return repo.objects.write(Commit.create(tree, author=IDENTITY, committer=IDENTITY,
                                            message=label, parents=parents).to_raw())


class RefPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / "catalog.sqlite3");self.addCleanup(self.catalog.close)
        self.owner = Principal(OWNER)
        for i, user in enumerate((OWNER, READER, WRITER, OUTSIDER)):
            self.catalog.create_account(user, Slug(f"user-{i}"), now=1)
        self.backend = GitdxBackend(self.root / "git")
        self.record = repository(object_format="sha256")
        RepositoryService(self.catalog, self.backend).create(self.owner, self.record, now=2)
        for user, permission in ((READER, Permission.READ), (WRITER, Permission.WRITE)):
            self.catalog.grant(self.owner, AccessGrant(REPO, user, permission), now=2)
        self.service = RefPolicyService(self.catalog, self.backend)
        self.repo = self.backend.open_repository(self.record.storage_id)
        self.base = commit(self.repo, b"base\n")
        self.child = commit(self.repo, b"child\n", (self.base,))
        self.other = commit(self.repo, b"other\n", (self.base,))
        self.repo.refs.update("refs/heads/main", self.base, expected=None)

    def rule(self, pattern="refs/heads/main", **kwargs):
        version = self.catalog.branch_policy(self.owner, REPO).version
        return self.catalog.set_branch_rule(self.owner, BranchRule(REPO, pattern, **kwargs), expected_version=version, now=3)

    def decide(self, old=None, new=None, *, name=b"refs/heads/main", actor=None):
        return self.service.evaluate(actor or self.owner, REPO, (PushCommand(name, old, new),), now=4).decisions[0]

    def test_role_scope_and_concealment_before_backend_reads(self):
        command = PushCommand(b"refs/heads/main", self.base, self.child)
        for actor in (Principal(), Principal(OUTSIDER), Principal(READER), Principal(OWNER, frozenset({Action.READ}))):
            with patch.object(self.backend, "open_repository", side_effect=AssertionError("read before authorization")):
                with self.assertRaisesRegex(PermissionDenied, "repository not found or permission denied"):
                    self.service.evaluate(actor, REPO, (command,), now=4)
        self.assertTrue(self.decide(self.base, self.child, actor=Principal(WRITER)).allowed)
        self.rule(minimum_permission=Permission.ADMIN)
        self.assertEqual(self.decide(self.base, self.child, actor=Principal(WRITER)).reason, RefReason.ADMIN_REQUIRED)
        self.assertTrue(self.decide(self.base, self.child).allowed)
        for operation in (lambda:self.catalog.set_branch_rule(Principal(WRITER), BranchRule(REPO, "refs/heads/new"), expected_version=1, now=4),
                          lambda:self.catalog.delete_branch_rule(Principal(READER), REPO, "refs/heads/main", expected_version=1, now=4)):
            with self.assertRaises(PermissionDenied):operation()

    def test_rules_intersect_no_owner_bypass_and_each_operation_has_a_reason(self):
        self.rule("refs/heads/*", allow_create=False)
        self.rule(allow_force=True, allow_delete=True)
        self.assertEqual(self.decide(None, self.child, name=b"refs/heads/new").reason, RefReason.CREATE_DENIED)
        self.assertEqual(self.decide(self.base, None).reason, RefReason.DELETE_DENIED)
        self.repo.refs.update("refs/heads/main", self.child, expected=self.base)
        decision = self.decide(self.child, self.other)
        self.assertEqual(decision.reason, RefReason.FORCE_DENIED)
        self.assertFalse(decision.fast_forward)
        self.assertEqual(decision.rules, ("refs/heads/*", "refs/heads/main"))
        self.rule(require_review=True, allow_delete=True, allow_force=True)
        self.assertTrue(self.decide(self.child, self.child).allowed)
        self.assertEqual(self.decide(self.child, self.other).reason, RefReason.FORCE_DENIED)
        self.catalog.delete_branch_rule(self.owner, REPO, "refs/heads/*", expected_version=3, now=4)
        self.assertEqual(self.decide(self.child, self.other).reason, RefReason.REVIEW_REQUIRED)

    def test_tags_and_unprotected_changes(self):
        blob = self.repo.objects.write(Blob(b"tag target").to_raw());name = b"refs/tags/v1"
        self.assertTrue(self.decide(None, blob, name=name).allowed)
        self.repo.refs.update(name.decode(), blob, expected=None)
        self.rule("refs/tags/*")
        self.assertEqual(self.decide(blob, self.child, name=name).reason, RefReason.FORCE_DENIED)
        self.rule("refs/tags/*", allow_force=True, allow_delete=True)
        self.assertTrue(self.decide(blob, self.child, name=name).allowed)
        self.assertTrue(self.decide(blob, None, name=name).allowed)
        self.assertTrue(self.decide(self.base, self.other).allowed)

    def test_expected_old_policy_revision_and_grants_are_rechecked(self):
        command = PushCommand(b"refs/heads/main", self.base, self.child)
        result = self.service.evaluate(Principal(WRITER), REPO, (command,), now=4)
        self.repo.refs.update("refs/heads/main", self.other, expected=self.base)
        self.assertEqual(self.service.recheck(result, now=5).decisions[0].reason, RefReason.STALE_TIP)
        self.repo.refs.update("refs/heads/main", self.base, expected=self.other)
        self.rule()
        self.assertEqual(self.service.recheck(result, now=5).decisions[0].reason, RefReason.POLICY_CHANGED)
        self.catalog.revoke_grant(self.owner, REPO, WRITER, now=5)
        with patch.object(self.backend, "open_repository", side_effect=AssertionError("revoked writer read")):
            with self.assertRaises(PermissionDenied):self.service.recheck(result, now=6)

    def test_credential_revocation_expiry_and_scope_caps_are_fresh(self):
        auth = TokenAuth(self.catalog)
        token = auth.issue(self.owner, scopes={Action.WRITE}, expires_at=8, now=3).reveal()
        actor = auth.authenticate(token, now=4)
        command = PushCommand(b"refs/heads/main", self.base, self.child)
        result = self.service.evaluate(actor, REPO, (command,), now=4)
        self.rule(minimum_permission=Permission.ADMIN)
        self.assertEqual(self.service.evaluate(actor, REPO, (command,), now=5).decisions[0].reason, RefReason.ADMIN_REQUIRED)
        for now in (2, 8):
            with self.assertRaises(AuthenticationError):self.service.recheck(result, now=now)
        self.catalog.revoke_credential(self.owner, actor.credential_id, now=6)
        with patch.object(self.backend, "open_repository", side_effect=AssertionError("revoked credential read")):
            with self.assertRaises(AuthenticationError):self.service.recheck(result, now=7)
        self.assertNotIn(token, repr(result))

    def test_batch_reasons_formats_invalid_objects_and_graph_limits(self):
        missing = ObjectId.from_bytes(b"\xff" * self.repo.object_format.digest_size, self.repo.object_format)
        blob = self.repo.objects.write(Blob(b"not a commit").to_raw())
        broken = commit(self.repo, b"missing parent\n", (missing,))
        commands = (PushCommand(b"refs/heads/main", self.child, self.other),
                    PushCommand(b"refs/heads/blob", None, blob),
                    PushCommand(b"refs/heads/missing", None, missing),
                    PushCommand(b"refs/heads/broken", None, broken),
                    PushCommand(b"refs/other/hidden", None, self.child),
                    PushCommand(b"refs/heads/new", None, self.child))
        result = self.service.evaluate(self.owner, REPO, commands, now=4)
        self.assertEqual([d.reason for d in result.decisions], [RefReason.STALE_TIP, RefReason.INVALID_TARGET,
                         RefReason.INVALID_TARGET, RefReason.INVALID_TARGET, RefReason.UNSUPPORTED_REF, RefReason.ALLOWED])
        self.assertFalse(result.allowed)
        limited = RefPolicyService(self.catalog, self.backend, ancestry_limits=RevisionLimits(max_steps=1))
        self.assertEqual(limited.evaluate(self.owner, REPO, (commands[-1],), now=4).decisions[0].reason, RefReason.INVALID_TARGET)
        sha1 = ObjectId.from_bytes(b"\x01" * 20)
        self.assertEqual(self.decide(None, sha1, name=b"refs/heads/wrong-format").reason, RefReason.INVALID_TARGET)
        for values in ((), commands * 50, (commands[-1], commands[-1])):
            with self.assertRaises(ValueError):self.service.evaluate(self.owner, REPO, values, now=4)
        self.assertEqual(self.repo.refs.resolve("refs/heads/main").oid, self.base)
        self.assertIsNone(self.repo.refs.read("refs/heads/new"))

    def test_organization_roles_and_public_visibility_do_not_bypass_write_policy(self):
        c = self.catalog;c.create_organization(self.owner, ORG, Slug("team"), now=3)
        record = repository(id="7" * 32, owner=Owner(ORG, OwnerKind.ORGANIZATION), storage_id=StorageId.new(), visibility=Visibility.PUBLIC)
        RepositoryService(c, self.backend).create(self.owner, record, now=3)
        repo = self.backend.open_repository(record.storage_id);target = commit(repo, b"org\n")
        command = PushCommand(b"refs/heads/main", None, target)
        c.set_membership(self.owner, Membership(ORG, READER), now=3)
        for actor in (Principal(), Principal(READER)):
            with self.assertRaises(PermissionDenied):self.service.evaluate(actor, record.id, (command,), now=4)
        c.set_membership(self.owner, Membership(ORG, READER, OrganizationRole.OWNER), now=4)
        self.assertTrue(self.service.evaluate(Principal(READER), record.id, (command,), now=4).allowed)

    def test_policy_restart_cas_delete_and_audit_rollback(self):
        policy = self.rule();before = self.catalog.pending_jobs()
        with patch.object(self.catalog, "_event", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):self.rule("refs/heads/*", require_review=True)
        self.assertEqual(self.catalog.pending_jobs(), before)
        self.assertEqual(self.catalog.branch_policy(self.owner, REPO), policy)
        with Catalog(self.catalog.path) as reopened:
            self.assertEqual(reopened.branch_policy(self.owner, REPO), policy)
            with self.assertRaises(CatalogConflict):reopened.set_branch_rule(self.owner, policy.rules[0], expected_version=0, now=4)
            deleted = reopened.delete_branch_rule(self.owner, REPO, policy.rules[0].pattern, expected_version=1, now=4)
            self.assertEqual((deleted.version, deleted.rules), (2, ()))
            self.assertEqual(reopened.repository_events(self.owner, REPO)[-1].kind.value, "ref-policy.changed")

    def test_rule_and_count_bounds(self):
        for pattern in ("HEAD", "refs/remotes/*", "refs/heads/a*", "refs/heads/../x", "refs/heads/*/x", "refs/heads/" + "a" * 1024):
            with self.assertRaises(ValueError):BranchRule(REPO, pattern)
        with self.assertRaises(TypeError):BranchRule(REPO, "refs/heads/main", allow_force=1)
        with self.assertRaises(ValueError):BranchRule(REPO, "refs/heads/main", minimum_permission=Permission.READ)
        with self.catalog.transaction():
            for i in range(128):self.rule(f"refs/heads/b{i}")
        with self.assertRaises(CatalogConflict):self.rule("refs/heads/overflow")
        self.rule("refs/heads/b0", allow_force=True)

    def test_upgrade_schema_four_keeps_data_and_rollback_is_atomic(self):
        path = self.root / "v4.sqlite3"
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("BEGIN")
            for version in range(1, 5):
                for statement in MIGRATIONS[version]:connection.execute(statement)
            connection.execute("INSERT INTO owners VALUES(?,'user','original')", (OWNER,))
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=4");connection.execute("COMMIT")
        broken = {**MIGRATIONS, 5: (*MIGRATIONS[5], "INVALID SQL")}
        with patch("gittycat.catalog.MIGRATIONS", broken):
            with self.assertRaises(sqlite3.OperationalError):Catalog(path)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='branch_rules'").fetchone())
        with Catalog(path) as upgraded:
            self.assertEqual(upgraded._connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(upgraded._connection.execute("SELECT slug FROM owners").fetchone()[0], "original")

    def test_no_executable_and_both_hash_formats(self):
        for format in ("sha1", "sha256"):
            with self.subTest(format=format):
                record = repository(id=("8" if format == "sha1" else "9") * 32, slug=Slug(format), storage_id=StorageId.new(), object_format=format)
                with patch("subprocess.Popen", side_effect=AssertionError("runtime executable")):
                    RepositoryService(self.catalog, self.backend).create(self.owner, record, now=3)
                    repo = self.backend.open_repository(record.storage_id)
                    base = commit(repo, b"base\n");child = commit(repo, b"child\n", (base,))
                    repo.refs.update("refs/heads/main", base, expected=None)
                    result = self.service.evaluate(self.owner, record.id, (PushCommand(b"refs/heads/main", base, child),), now=4)
                self.assertTrue(result.allowed);self.assertTrue(result.decisions[0].fast_forward)


class RefPolicyNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("git"):
            if os.environ.get("GITTYCAT_REQUIRE_GIT") == "1":raise RuntimeError("native Git oracle is required")
            raise unittest.SkipTest("native Git is unavailable")

    def test_ancestry_decisions_match_native_git_for_both_formats(self):
        for format in ("sha1", "sha256"):
            with tempfile.TemporaryDirectory() as tmp, Catalog(Path(tmp) / "catalog.sqlite3") as catalog:
                owner = Principal(OWNER);catalog.create_account(OWNER, Slug("owner"), now=1)
                backend = GitdxBackend(Path(tmp) / "git");record = repository(object_format=format)
                RepositoryService(catalog, backend).create(owner, record, now=2)
                repo = backend.open_repository(record.storage_id)
                base = commit(repo, b"base\n");child = commit(repo, b"child\n", (base,));other = commit(repo, b"other\n")
                service = RefPolicyService(catalog, backend)
                catalog.set_branch_rule(owner, BranchRule(REPO, "refs/heads/*"), expected_version=0, now=3)
                env = {k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
                env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
                for old, new in ((base, child), (child, base), (base, other)):
                    actual = repo.refs.read("refs/heads/main")
                    repo.refs.update("refs/heads/main", old, expected=actual.oid if actual else None)
                    result = service.evaluate(owner, REPO, (PushCommand(b"refs/heads/main", old, new),), now=4).decisions[0]
                    native = subprocess.run(["git", "-C", str(repo.git_dir), "merge-base", "--is-ancestor", str(old), str(new)], env=env, capture_output=True)
                    self.assertIn(native.returncode, (0, 1), native.stderr)
                    self.assertEqual(result.fast_forward, native.returncode == 0)
                    self.assertEqual(result.allowed, native.returncode == 0)
