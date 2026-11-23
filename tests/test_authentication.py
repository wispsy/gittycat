from contextlib import closing
import hashlib
import hmac
from pathlib import Path
import pickle
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gittycat import (AccessGrant, Action, AuthenticationError, Catalog, CatalogConflict, CredentialMetadata,
                     GitdxBackend, Membership, OrganizationRole, Owner, OwnerKind,
                     Permission, PermissionDenied, Principal, RepositoryService, Slug,
                     StorageId, TokenAuth, Visibility, allowed, visible_repositories)
from test_permissions import OWNER, READER, WRITER, OUTSIDER, ORG, REPO, repository


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / "catalog.sqlite3")
        self.addCleanup(self.catalog.close)
        self.auth = TokenAuth(self.catalog)
        self.owner = Principal(OWNER)
        for i, user in enumerate((OWNER, READER, WRITER, OUTSIDER)):
            self.catalog.create_account(user, Slug(f"user-{i}"), now=1)
        self.catalog.create_repository(self.owner, repository(), now=2)
        self.public = repository(id="7" * 32, slug=Slug("public-repo"), storage_id=StorageId.new(), visibility=Visibility.PUBLIC)
        self.catalog.create_repository(self.owner, self.public, now=2)
        self.catalog.grant(self.owner, AccessGrant(REPO, READER, Permission.READ), now=2)
        self.catalog.grant(self.owner, AccessGrant(REPO, WRITER, Permission.WRITE), now=2)

    def token(self, user=OWNER, scope=Action.READ, *, expires_at=20):
        return self.auth.issue(Principal(user), scopes={scope}, expires_at=expires_at, now=3).reveal()

    def test_scope_hierarchy_caps_owner_and_never_grants_acl_access(self):
        for user, granted in ((OWNER, 3), (READER, 1), (WRITER, 2), (OUTSIDER, 0)):
            for scope, cap in ((Action.READ, 1), (Action.WRITE, 2), (Action.ADMIN, 3)):
                token = self.token(user, scope)
                actor = self.auth.authenticate(token, now=4)
                for action, level in ((Action.READ, 1), (Action.WRITE, 2), (Action.ADMIN, 3)):
                    self.assertEqual(allowed(actor, repository(), action, grants=(
                        AccessGrant(REPO, READER, Permission.READ), AccessGrant(REPO, WRITER, Permission.WRITE))),
                        level <= min(granted, cap))
                    if level <= min(granted, cap):
                        self.assertEqual(self.auth.repository(token, REPO, action=action, now=4).id, REPO)
                    else:
                        with self.assertRaisesRegex(PermissionDenied, "repository not found or permission denied"):
                            self.auth.repository(token, REPO, action=action, now=4)
                expected = {self.public.id, REPO} if granted else {self.public.id}
                self.assertEqual({r.id for r in self.auth.repositories(token, now=4)}, expected)
        capped = Principal(OWNER, frozenset())
        self.assertEqual(self.catalog.list_repositories(capped), ())
        self.assertEqual(visible_repositories(capped, (repository(), self.public)), ())
        self.assertFalse(allowed(capped, self.public, Action.READ))

    def test_expiry_revocation_and_acl_changes_are_fresh_per_request(self):
        token = self.token(WRITER, Action.WRITE, expires_at=8)
        self.auth.repository(token, REPO, action=Action.WRITE, now=7)
        for now in (2, 8, 9):
            with self.assertRaises(AuthenticationError):self.auth.authenticate(token, now=now)
        self.catalog.revoke_grant(self.owner, REPO, WRITER, now=5)
        with self.assertRaises(PermissionDenied):self.auth.repository(token, REPO, now=5)
        metadata = self.auth.authenticate(token, now=5)
        self.catalog.revoke_credential(Principal(WRITER), metadata.credential_id, now=6)
        with self.assertRaises(AuthenticationError):self.auth.repositories(token, now=6)
        with Catalog(self.catalog.path) as reopened:
            with self.assertRaises(AuthenticationError):TokenAuth(reopened).authenticate(token, now=7)

    def test_malformed_unknown_wrong_revoked_and_expired_use_safe_errors_and_digest_comparison(self):
        token = self.token()
        wrong = token[:-1] + ("B" if token[-1] == "A" else "A")
        unknown = "gtc_" + "f" * 32 + "_" + "A" * 43
        rejected = (None, b"secret", "", "\u00e9" * 80, "sensitive" * 1000, unknown, wrong)
        for value in rejected:
            with patch("gittycat.authentication.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
                with self.assertRaises(AuthenticationError) as error:
                    self.auth.authenticate(value, now=4)
                self.assertEqual(str(error.exception), "invalid or inactive credential")
                self.assertEqual(compare.call_count, 1)
                self.assertEqual(tuple(map(len, compare.call_args.args)), (32, 32))

    def test_secret_delivery_storage_and_audit_are_redacted(self):
        issued = self.auth.issue(self.owner, scopes={Action.READ, Action.WRITE}, expires_at=20, now=3)
        text = repr(issued) + str(issued) + repr(issued.metadata)
        with self.assertRaises(TypeError):pickle.dumps(issued)
        token = issued.reveal()
        with self.assertRaises(ValueError):issued.reveal()
        actor = self.auth.authenticate(token, now=4)
        text += repr(actor) + repr(self.catalog.pending_jobs()) + repr(self.catalog.repository_events(self.owner, REPO))
        self.assertNotIn(token, text)
        self.assertNotIn(token[37:], text)
        self.assertNotIn(token.encode(), self.catalog.path.read_bytes())
        with closing(sqlite3.connect(self.catalog.path)) as connection:
            verifier, = connection.execute("SELECT verifier FROM credentials WHERE id=?", (actor.credential_id,)).fetchone()
        self.assertEqual(verifier, hashlib.sha256(token.encode("ascii")).digest())
        self.assertEqual(actor.scopes, frozenset({Action.READ, Action.WRITE}))

    def test_issuance_validation_and_failure_rollback(self):
        for kwargs in ({"scopes": ()}, {"scopes": "read"}, {"scopes": ("unknown",)},
                       {"expires_at": 3}, {"expires_at": None}, {"expires_at": True}):
            with self.assertRaises(ValueError):
                self.auth.issue(self.owner, **({"scopes": {Action.READ}, "expires_at": 20, "now": 3} | kwargs))
        for actor in (Principal(), Principal("9" * 32)):
            with self.assertRaises(PermissionDenied):self.auth.issue(actor, scopes={Action.READ}, expires_at=20, now=3)
        token = self.token(scope=Action.ADMIN)
        with self.auth.request(token, now=4) as actor:
            with self.assertRaises(PermissionDenied):self.auth.issue(actor, scopes={Action.ADMIN}, expires_at=20, now=4)
        before = self.catalog.pending_jobs()
        with patch("gittycat.authentication.secrets.token_urlsafe", return_value="A" * 43):
            with patch.object(self.catalog, "_event", side_effect=RuntimeError("injected audit failure")):
                with self.assertRaises(RuntimeError):self.auth.issue(self.owner, scopes={Action.READ}, expires_at=20, now=4)
        self.assertEqual(self.catalog.pending_jobs(), before)
        with closing(sqlite3.connect(self.catalog.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM credentials").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM credential_scopes").fetchone()[0], 1)
        self.catalog._connection.execute("CREATE TRIGGER reject_scope BEFORE INSERT ON credential_scopes BEGIN SELECT RAISE(ABORT,'injected scope failure'); END")
        try:
            with self.assertRaises(CatalogConflict):self.auth.issue(self.owner, scopes={Action.READ}, expires_at=20, now=4)
        finally:
            self.catalog._connection.execute("DROP TRIGGER reject_scope")
        self.assertEqual(self.catalog.pending_jobs(), before)

    def test_scope_denials_cover_catalog_mutations_and_provisioning_before_storage(self):
        backend = GitdxBackend(self.root / "git")
        service = RepositoryService(self.catalog, backend)
        record = repository(id="8" * 32, slug=Slug("managed"), storage_id=StorageId.new())
        issued = self.auth.issue(self.owner, scopes={Action.ADMIN}, expires_at=20, now=3)
        read_token = self.token()
        with self.auth.request(read_token, now=4) as actor:
            before = self.catalog.pending_jobs()
            operations = (
                lambda: self.catalog.create_organization(actor, ORG, Slug("group"), now=4),
                lambda: self.catalog.set_membership(actor, Membership(ORG, READER), now=4),
                lambda: self.catalog.create_repository(actor, record, now=4),
                lambda: self.catalog.grant(actor, AccessGrant(REPO, OUTSIDER, Permission.ADMIN), now=4),
                lambda: self.catalog.revoke_grant(actor, REPO, READER, now=4),
                lambda: self.catalog.revoke_credential(actor, issued.metadata.id, now=4),
                lambda: service.create(actor, record, now=4),
            )
            with patch.object(backend, "create", side_effect=AssertionError("storage before authorization")):
                for operation in operations:
                    with self.assertRaises(PermissionDenied):operation()
            self.assertEqual(self.catalog.pending_jobs(), before)
        admin_token = issued.reveal()
        with self.auth.request(admin_token, now=4) as actor:service.create(actor, record, now=4)
        with self.auth.request(read_token, now=5) as actor:
            self.assertEqual(service.open(actor, record.id).record, record)
            with patch.object(backend, "inspect", side_effect=AssertionError("storage before authorization")):
                with self.assertRaises(PermissionDenied):service.open(actor, record.id, action=Action.WRITE)
                with self.assertRaises(PermissionDenied):service.recover(actor, record.id, now=5)
        self.auth.revoke(admin_token, issued.metadata.id, now=6)
        with self.assertRaises(AuthenticationError):self.auth.repository(admin_token, record.id, now=6)

    def test_organization_roles_and_scope_caps_share_current_policy(self):
        self.catalog.create_organization(self.owner, ORG, Slug("group"), now=2)
        self.catalog.set_membership(self.owner, Membership(ORG, READER), now=2)
        org_repo = repository(id="8" * 32, owner=Owner(ORG, OwnerKind.ORGANIZATION), storage_id=StorageId.new())
        self.catalog.create_repository(self.owner, org_repo, now=2)
        owner_token, member_token = self.token(scope=Action.ADMIN), self.token(READER, Action.ADMIN)
        self.auth.repository(owner_token, org_repo.id, action=Action.ADMIN, now=4)
        with self.assertRaises(PermissionDenied):self.auth.repository(member_token, org_repo.id, now=4)
        self.catalog.set_membership(self.owner, Membership(ORG, READER, OrganizationRole.OWNER), now=4)
        self.catalog.set_membership(self.owner, Membership(ORG, OWNER), now=4)
        self.auth.repository(member_token, org_repo.id, action=Action.ADMIN, now=5)
        with self.assertRaises(PermissionDenied):self.auth.repository(owner_token, org_repo.id, now=5)

    def test_legacy_verifiers_without_scopes_are_not_bearer_tokens(self):
        credential_id = "c" * 32
        token = "gtc_" + credential_id + "_" + "A" * 43
        metadata = CredentialMetadata(credential_id, OWNER, 3, 20)
        self.catalog.store_credential(self.owner, metadata, hashlib.sha256(token.encode("ascii")).digest())
        with self.assertRaises(AuthenticationError):self.auth.authenticate(token, now=4)
        with self.assertRaises(ValueError):Principal(OWNER, credential_id=credential_id)

    def test_only_the_credential_owner_with_admin_scope_can_revoke_and_revocation_is_idempotent(self):
        token = self.token(scope=Action.ADMIN)
        credential_id = self.auth.authenticate(token, now=4).credential_id
        other = self.token(READER, Action.ADMIN)
        with self.assertRaises(PermissionDenied):self.auth.revoke(other, credential_id, now=4)
        self.auth.repository(token, REPO, now=4)
        before = len(self.catalog.pending_jobs())
        self.catalog.revoke_credential(self.owner, credential_id, now=5)
        self.catalog.revoke_credential(self.owner, credential_id, now=6)
        self.assertEqual(len(self.catalog.pending_jobs()), before + 1)
        self.assertEqual(self.catalog.credential_metadata(self.owner, credential_id).revoked_at, 5)
        with self.assertRaises(AuthenticationError):self.auth.authenticate(token, now=5)
