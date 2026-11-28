from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gitcx import AccessGrant, Action, Catalog, CatalogConflict, CatalogError, CredentialMetadata, Membership, OrganizationRole, Owner, OwnerKind, Permission, PermissionDenied, Principal, Slug, StorageId, Visibility
from gitcx.catalog import APPLICATION_ID, MIGRATIONS
from test_permissions import OWNER, READER, WRITER, OUTSIDER, ORG, REPO, repository


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "catalog.sqlite3"
        self.catalog = Catalog(self.path)
        self.addCleanup(self.catalog.close)
        self.owner = Principal(OWNER)
        for i, user in enumerate((OWNER, READER, WRITER, OUTSIDER)):
            self.catalog.create_account(user, Slug(f"user-{i}"), now=1)

    def test_restart_repository_grants_memberships_and_query_policy(self):
        c = self.catalog
        c.create_repository(self.owner, repository(), now=2)
        public = repository(id="7" * 32, slug=Slug("public-repo"), storage_id=StorageId.new(), visibility=Visibility.PUBLIC)
        c.create_repository(self.owner, public, now=2)
        c.grant(self.owner, AccessGrant(REPO, READER, Permission.READ), now=3)
        c.grant(self.owner, AccessGrant(REPO, WRITER, Permission.WRITE), now=3)
        c.create_organization(self.owner, ORG, Slug("example-org"), now=3)
        c.set_membership(self.owner, Membership(ORG, READER), now=4)
        organization_repo = repository(id="8" * 32, owner=Owner(ORG, OwnerKind.ORGANIZATION), storage_id=StorageId.new())
        c.create_repository(self.owner, organization_repo, now=4)
        with Catalog(self.path) as reopened:
            for actor, ids in ((Principal(), {public.id}), (Principal(OUTSIDER), {public.id}),
                               (Principal(READER), {REPO, public.id}), (self.owner, {REPO, public.id, organization_repo.id})):
                self.assertEqual({r.id for r in reopened.list_repositories(actor)}, ids)
            self.assertEqual(reopened.get_repository(Principal(WRITER), REPO, action=Action.WRITE), repository())
            with self.assertRaises(PermissionDenied):reopened.get_repository(Principal(READER), REPO, action=Action.WRITE)
            with self.assertRaises(PermissionDenied):reopened.get_repository(Principal(OUTSIDER), REPO)
            with self.assertRaises(PermissionDenied):reopened.repository_events(Principal(READER), REPO)
            reopened.revoke_grant(self.owner, REPO, READER, now=5)
            with self.assertRaises(PermissionDenied):reopened.get_repository(Principal(READER), REPO)

    def test_denied_mutations_leave_catalog_and_outbox_unchanged(self):
        c = self.catalog;c.create_repository(self.owner, repository(), now=2)
        before = c.pending_jobs()
        for action in (lambda: c.grant(Principal(OUTSIDER), AccessGrant(REPO, OUTSIDER, Permission.ADMIN), now=3),
                       lambda: c.create_repository(Principal(OUTSIDER), repository(id="8" * 32, storage_id=StorageId.new()), now=3)):
            with self.assertRaises(PermissionDenied):action()
        self.assertEqual(c.pending_jobs(), before)

    def test_uniqueness_and_foreign_account_checks(self):
        c = self.catalog;c.create_repository(self.owner, repository(), now=2)
        with self.assertRaises(CatalogConflict):c.create_repository(self.owner, repository(id="8" * 32), now=3)
        with self.assertRaises(CatalogConflict):c.create_account("9" * 32, Slug("user-0"), now=2)
        with self.assertRaises(PermissionDenied):c.grant(self.owner, AccessGrant(REPO, "9" * 32, Permission.READ), now=3)
        self.assertEqual(len(c.list_repositories(self.owner)), 1)

    def test_mutation_audit_and_outbox_are_one_transaction(self):
        c = self.catalog;before = c.pending_jobs()
        original = Catalog._event
        def interrupted(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise RuntimeError("injected failure after event and outbox insertion")
        with patch.object(Catalog, "_event", interrupted):
            with self.assertRaises(RuntimeError):c.create_repository(self.owner, repository(), now=2)
        self.assertEqual(c.pending_jobs(), before)
        with self.assertRaises(PermissionDenied):c.get_repository(self.owner, REPO)
        # An inner failure caught by a caller must not commit its partial rows.
        with c.transaction():
            with patch.object(Catalog, "_event", interrupted):
                with self.assertRaises(RuntimeError):c.create_repository(self.owner, repository(), now=2)
            c.create_organization(self.owner, ORG, Slug("survives"), now=3)
        with self.assertRaises(PermissionDenied):c.get_repository(self.owner, REPO)

    def test_explicit_outer_rollback_preserves_prior_state(self):
        before = self.catalog.pending_jobs()
        with self.assertRaises(RuntimeError):
            with self.catalog.transaction():
                self.catalog.create_repository(self.owner, repository(), now=2)
                raise RuntimeError("abort")
        self.assertEqual(self.catalog.pending_jobs(), before)

    def test_outbox_restart_bounded_pages_and_idempotent_ack(self):
        before = self.catalog.pending_jobs()
        with Catalog(self.path) as reopened:
            self.assertEqual(reopened.pending_jobs(), before)
            page = reopened.pending_jobs(limit=2)
            self.assertEqual(page, before[:2])
            self.assertEqual(reopened.pending_jobs(after=page[-1].sequence), before[2:])
            self.assertTrue(reopened.acknowledge_job(before[0].id))
            self.assertFalse(reopened.acknowledge_job(before[0].id))
        self.assertEqual(self.catalog.pending_jobs(), before[1:])
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):self.catalog.pending_jobs(limit=limit)

    def test_audit_records_and_credential_reads_exclude_verifiers(self):
        c = self.catalog;c.create_repository(self.owner, repository(), now=2)
        raw_token = b"token-value-not-for-storage"
        digest = hashlib.sha256(raw_token).digest()
        metadata = CredentialMetadata("c" * 32, OWNER, 3, 10)
        c.store_credential(self.owner, metadata, digest)
        self.assertEqual(c.credential_metadata(self.owner, metadata.id), metadata)
        with self.assertRaises(PermissionDenied):c.credential_metadata(Principal(OUTSIDER), metadata.id)
        c.revoke_credential(self.owner, metadata.id, now=5)
        self.assertEqual(c.credential_metadata(self.owner, metadata.id).revoked_at, 5)
        with self.assertRaises(ValueError):c.store_credential(self.owner, replace(metadata, id="d" * 32), raw_token.decode())
        self.assertNotIn(raw_token, self.path.read_bytes())
        text = repr(c.pending_jobs()) + repr(c.repository_events(self.owner, REPO))
        self.assertNotIn(digest.hex(), text)
        self.assertNotIn(raw_token.decode(), text)

    def test_last_organization_owner_is_preserved(self):
        self.catalog.create_organization(self.owner, ORG, Slug("organization"), now=2)
        with self.assertRaises(CatalogConflict):self.catalog.set_membership(self.owner, Membership(ORG, OWNER), now=3)
        self.catalog.set_membership(self.owner, Membership(ORG, READER, OrganizationRole.OWNER), now=3)
        self.catalog.set_membership(self.owner, Membership(ORG, OWNER), now=4)

    def test_separate_connections_serialize_unique_writers(self):
        def create(_):
            try:
                with Catalog(self.path) as c:c.create_repository(self.owner, repository(), now=2)
                return "created"
            except CatalogConflict:return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(create, range(2))), ["created", "conflict"])
        self.assertEqual(len(self.catalog.repository_events(self.owner, REPO)), 1)

    def test_failed_upgrade_rolls_back_ddl_version_and_data(self):
        path = self.path.with_name("old.sqlite3")
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("BEGIN")
            for statement in MIGRATIONS[1]:connection.execute(statement)
            connection.execute("INSERT INTO owners VALUES(?,'user','original')", (OWNER,))
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1");connection.execute("COMMIT")
        broken = {**MIGRATIONS, 2: (*MIGRATIONS[2], "INVALID SQL")}
        with patch("gitcx.catalog.MIGRATIONS", broken):
            with self.assertRaises(sqlite3.OperationalError):Catalog(path)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT slug FROM owners").fetchone()[0], "original")
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='audit_events'").fetchone())
        with Catalog(path) as c:
            self.assertEqual(c.list_repositories(self.owner), ())

    def test_foreign_and_future_schema_are_refused_without_changes(self):
        for future in (False, True):
            path = self.path.with_name(f"foreign-{future}.sqlite3")
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE keep(value TEXT)")
                connection.execute("INSERT INTO keep VALUES('original')")
                if future:
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}");connection.execute("PRAGMA user_version=99")
            before = path.read_bytes()
            with self.assertRaises(CatalogError):Catalog(path)
            self.assertEqual(path.read_bytes(), before)

    def test_no_git_object_or_ref_authority_in_schema(self):
        with closing(sqlite3.connect(self.path)) as connection:
            columns = {r[1] for r in connection.execute("PRAGMA table_info(repositories)")}
        self.assertEqual(columns, {"id", "owner_id", "slug", "storage_id", "visibility", "object_format"})
