"""SQLite platform metadata and transactional, secret-free audit/outbox records."""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
import os
import json
from pathlib import Path
import sqlite3
import stat
import uuid

from .domain import AccessGrant, Action, Membership, OrganizationRole, Owner, OwnerKind, Permission, Principal, RepositoryRecord, Slug, StorageId, Visibility, identifier
from .permissions import PermissionDenied, require_permission, require_scope, scope_allows
from .branch_policy import BranchPolicy, BranchRule, MAX_BRANCH_RULES, ref_pattern


APPLICATION_ID = 0x47434154
SCHEMA_VERSION = 6
MIGRATIONS = {
    1: (
        "CREATE TABLE owners (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('user','organization')), slug TEXT NOT NULL UNIQUE)",
        "CREATE TABLE memberships (organization_id TEXT NOT NULL REFERENCES owners(id), user_id TEXT NOT NULL REFERENCES owners(id), role TEXT NOT NULL CHECK(role IN ('owner','member')), PRIMARY KEY(organization_id,user_id))",
        "CREATE TABLE repositories (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id), slug TEXT NOT NULL, storage_id TEXT NOT NULL UNIQUE, visibility TEXT NOT NULL CHECK(visibility IN ('private','public')), object_format TEXT NOT NULL CHECK(object_format IN ('sha1','sha256')), UNIQUE(owner_id,slug))",
        "CREATE TABLE grants (repository_id TEXT NOT NULL REFERENCES repositories(id), user_id TEXT NOT NULL REFERENCES owners(id), permission INTEGER NOT NULL CHECK(permission BETWEEN 1 AND 3), PRIMARY KEY(repository_id,user_id))",
        "CREATE TABLE credentials (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES owners(id), verifier BLOB NOT NULL CHECK(length(verifier)=32), created_at INTEGER NOT NULL, expires_at INTEGER, revoked_at INTEGER)",
    ),
    2: (
        "CREATE TABLE audit_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, actor_id TEXT, resource_id TEXT NOT NULL, timestamp INTEGER NOT NULL)",
        "CREATE INDEX audit_resource ON audit_events(resource_id,sequence)",
        "CREATE TABLE outbox (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(id), kind TEXT NOT NULL CHECK(kind IN ('catalog.changed','repository.index')), resource_id TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)))",
        "CREATE INDEX outbox_pending ON outbox(done,sequence)",
    ),
    3: ("CREATE TABLE provisioning (repository_id TEXT PRIMARY KEY REFERENCES repositories(id), state TEXT NOT NULL CHECK(state IN ('pending','ready')))",),
    4: ("CREATE TABLE credential_scopes (credential_id TEXT NOT NULL REFERENCES credentials(id), action TEXT NOT NULL CHECK(action IN ('read','write','admin')), PRIMARY KEY(credential_id,action))",),
    5: (
        "CREATE TABLE ref_policy_versions (repository_id TEXT PRIMARY KEY REFERENCES repositories(id), version INTEGER NOT NULL CHECK(version>=0))",
        "CREATE TABLE branch_rules (repository_id TEXT NOT NULL REFERENCES repositories(id), pattern TEXT NOT NULL, allow_create INTEGER NOT NULL CHECK(allow_create IN (0,1)), allow_delete INTEGER NOT NULL CHECK(allow_delete IN (0,1)), allow_force INTEGER NOT NULL CHECK(allow_force IN (0,1)), require_review INTEGER NOT NULL CHECK(require_review IN (0,1)), minimum_permission INTEGER NOT NULL CHECK(minimum_permission IN (2,3)), PRIMARY KEY(repository_id,pattern))",
    ),
    6: (
        "CREATE TABLE receive_operations (id TEXT PRIMARY KEY, repository_id TEXT NOT NULL REFERENCES repositories(id), actor_id TEXT NOT NULL REFERENCES owners(id), created_at INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('prepared','completed','rejected','unknown')), commands TEXT NOT NULL, result TEXT)",
        "CREATE INDEX receive_repository ON receive_operations(repository_id,created_at,id)",
    ),
}


class CatalogError(Exception):
    """A catalog cannot be opened or changed as requested."""


class CatalogConflict(CatalogError):
    """A uniqueness or lifecycle invariant rejected a change."""


class EventKind(StrEnum):
    ACCOUNT_CREATED = "account.created"
    ORGANIZATION_CREATED = "organization.created"
    MEMBERSHIP_CHANGED = "membership.changed"
    REPOSITORY_CREATED = "repository.created"
    REPOSITORY_READY = "repository.ready"
    GRANT_CHANGED = "grant.changed"
    CREDENTIAL_CREATED = "credential.created"
    CREDENTIAL_REVOKED = "credential.revoked"
    REF_POLICY_CHANGED = "ref-policy.changed"
    RECEIVE_PREPARED = "receive.prepared"
    RECEIVE_ACCEPTED = "receive.accepted"
    RECEIVE_REJECTED = "receive.rejected"
    RECEIVE_INTERRUPTED = "receive.interrupted"
    RECEIVE_RECOVERED = "receive.recovered"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    id: str
    kind: EventKind
    actor_id: str | None
    resource_id: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class OutboxJob:
    sequence: int
    id: str
    event_id: str
    kind: str
    resource_id: str


@dataclass(frozen=True, slots=True)
class CredentialMetadata:
    id: str
    user_id: str
    created_at: int
    expires_at: int | None = None
    revoked_at: int | None = None

    def __post_init__(self):
        identifier(self.id); identifier(self.user_id)
        for value in (self.created_at, self.expires_at, self.revoked_at):
            if value is not None:
                timestamp(value)
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("credential expiry must follow its creation")
        if self.revoked_at is not None and self.revoked_at < self.created_at:
            raise ValueError("credential revocation cannot precede its creation")


def timestamp(value):
    if type(value) is not int or not 0 <= value <= 0x7fffffffffffffff:
        raise ValueError("timestamps must be nonnegative SQLite integers")


def page(limit, after):
    if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or not 0 <= after <= 0x7fffffffffffffff:
        raise ValueError("pages require a limit of 1–100 and a nonnegative cursor")


class Catalog:
    """Trusted local catalog handle, not an authenticated HTTP/service surface.

    One connection per thread. Repository query/mutation methods enforce the
    shared permission policy; bootstrap and outbox methods are trusted duties.
    """

    def __init__(self, path, *, timeout: float = 5.0):
        self.path = Path(path).absolute()
        if not 0 <= timeout <= 30:
            raise ValueError("catalog lock timeout must be between 0 and 30 seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for target in (self.path.parent, self.path, *(Path(str(self.path) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
            if target.is_symlink():
                raise CatalogError("catalog paths must not be symlinks")
            if target != self.path.parent and target.exists() and not stat.S_ISREG(target.stat().st_mode):
                raise CatalogError("catalog files must be regular files")
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._connection = sqlite3.connect(self.path, isolation_level=None, timeout=timeout)
        self._connection.row_factory = sqlite3.Row
        self._savepoint = 0
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._migrate()
        except BaseException:
            self.close()
            raise

    def __enter__(self):return self

    def __exit__(self, *exc):self.close()

    def close(self):self._connection.close()

    @contextmanager
    def transaction(self):
        nested = self._connection.in_transaction
        self._savepoint += 1
        name = f"catalog_{self._savepoint}"
        self._connection.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
        try:
            yield self
            self._connection.execute(f"RELEASE {name}" if nested else "COMMIT")
        except BaseException as error:
            if self._connection.in_transaction:
                if nested:
                    self._connection.execute(f"ROLLBACK TO {name}")
                    self._connection.execute(f"RELEASE {name}")
                else:
                    self._connection.execute("ROLLBACK")
            if isinstance(error, sqlite3.IntegrityError):
                raise CatalogConflict("catalog constraint rejected the change") from error
            raise

    @contextmanager
    def _snapshot(self):
        if self._connection.in_transaction:
            yield
            return
        self._connection.execute("BEGIN")
        try:
            yield
        finally:
            self._connection.execute("ROLLBACK")

    def _migrate(self):
        with self.transaction():
            app = self._connection.execute("PRAGMA application_id").fetchone()[0]
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            tables = self._connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone()
            if app not in (0, APPLICATION_ID) or (app == 0 and (version or tables)):
                raise CatalogError("database is not an initialized Gittycat catalog")
            if not 0 <= version <= SCHEMA_VERSION:
                raise CatalogError("catalog schema version is newer than this application")
            if version == 0 and tables:
                raise CatalogError("unversioned catalog has existing schema")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[target]:
                    self._connection.execute(statement)
                self._connection.execute(f"PRAGMA user_version={target}")
            self._connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise CatalogError("catalog foreign-key integrity check failed")

    def _event(self, kind, actor_id, resource_id, now, *, job_kind="catalog.changed"):
        timestamp(now)
        event_id = uuid.uuid4().hex
        self._connection.execute("INSERT INTO audit_events(id,kind,actor_id,resource_id,timestamp) VALUES(?,?,?,?,?)",
                                 (event_id, EventKind(kind).value, actor_id, resource_id, now))
        self._connection.execute("INSERT INTO outbox(id,event_id,kind,resource_id) VALUES(?,?,?,?)",
                                 (uuid.uuid4().hex, event_id, job_kind, resource_id))

    def _user(self, user_id):
        if user_id is None or self._connection.execute("SELECT 1 FROM owners WHERE id=? AND kind='user'", (user_id,)).fetchone() is None:
            raise PermissionDenied("authenticated account required")

    def _prepare_receive(self, actor, record, commands, *, now):
        """Trusted outcome bookkeeping; Git refs remain authoritative."""
        with self.transaction():
            self.get_repository(actor, record.id, action=Action.WRITE)
            operation_id = uuid.uuid4().hex
            payload = json.dumps([[c.name.decode('ascii'), c.old.hex if c.old else None, c.new.hex if c.new else None] for c in commands])
            if len(payload) > 512*1024: raise ValueError('receive receipt command limit exceeded')
            self._connection.execute('INSERT INTO receive_operations VALUES(?,?,?,?,?,?,NULL)',
                (operation_id, record.id, actor.user_id, now, 'prepared', payload))
            self._event(EventKind.RECEIVE_PREPARED, actor.user_id, record.id, now)
            return operation_id

    def _finish_receive(self, operation_id, result, *, now):
        with self.transaction():
            row = self._connection.execute('SELECT * FROM receive_operations WHERE id=?', (operation_id,)).fetchone()
            if row is None: raise CatalogConflict('receive receipt is missing')
            encoded = json.dumps(result, sort_keys=True)
            if len(encoded) > 512*1024: raise ValueError('receive outcome limit exceeded')
            states = [item['state'] for item in result['outcomes']]
            status = 'unknown' if 'unknown' in states else 'completed' if states and all(s == 'accepted' for s in states) else 'rejected'
            self._connection.execute('UPDATE receive_operations SET status=?,result=? WHERE id=?', (status, encoded, operation_id))
            kind = EventKind.RECEIVE_ACCEPTED if status == 'completed' else EventKind.RECEIVE_INTERRUPTED if status == 'unknown' else EventKind.RECEIVE_REJECTED
            self._event(kind, row['actor_id'], row['repository_id'], now,
                        job_kind='repository.index' if 'accepted' in states else 'catalog.changed')

    def receive_operation(self, actor, operation_id):
        identifier(operation_id)
        with self._snapshot():
            row = self._connection.execute('SELECT * FROM receive_operations WHERE id=?', (operation_id,)).fetchone()
            if row is None: raise PermissionDenied('receive operation not found or permission denied')
            self.get_repository(actor, row['repository_id'], action=Action.ADMIN)
            return {'id': row['id'], 'repository_id': row['repository_id'], 'actor_id': row['actor_id'],
                    'created_at': row['created_at'], 'status': row['status'], 'commands': json.loads(row['commands']),
                    'result': json.loads(row['result']) if row['result'] else None}

    def create_account(self, account_id: str, slug: Slug, *, now: int):
        identifier(account_id)
        if not isinstance(slug, Slug):raise TypeError("account slug must be a Slug")
        with self.transaction():
            self._connection.execute("INSERT INTO owners VALUES(?, 'user', ?)", (account_id, slug.value))
            self._event(EventKind.ACCOUNT_CREATED, None, account_id, now)

    def create_organization(self, actor: Principal, organization_id: str, slug: Slug, *, now: int):
        require_scope(actor, Action.ADMIN)
        identifier(organization_id)
        if not isinstance(slug, Slug):raise TypeError("organization slug must be a Slug")
        with self.transaction():
            self._user(actor.user_id)
            self._connection.execute("INSERT INTO owners VALUES(?, 'organization', ?)", (organization_id, slug.value))
            self._connection.execute("INSERT INTO memberships VALUES(?,?,'owner')", (organization_id, actor.user_id))
            self._event(EventKind.ORGANIZATION_CREATED, actor.user_id, organization_id, now)

    def _organization_owner(self, actor, organization_id):
        self._user(actor.user_id)
        row = self._connection.execute("SELECT 1 FROM memberships m JOIN owners o ON o.id=m.organization_id WHERE m.organization_id=? AND m.user_id=? AND m.role='owner' AND o.kind='organization'", (organization_id, actor.user_id)).fetchone()
        if row is None:raise PermissionDenied("organization not found or permission denied")

    def set_membership(self, actor: Principal, membership: Membership, *, now: int):
        require_scope(actor, Action.ADMIN)
        with self.transaction():
            self._organization_owner(actor, membership.organization_id)
            self._user(membership.user_id)
            if membership.role is OrganizationRole.MEMBER:
                existing = self._connection.execute("SELECT role FROM memberships WHERE organization_id=? AND user_id=?", (membership.organization_id, membership.user_id)).fetchone()
                count = self._connection.execute("SELECT count(*) FROM memberships WHERE organization_id=? AND role='owner'", (membership.organization_id,)).fetchone()[0]
                if existing and existing[0] == 'owner' and count == 1:raise CatalogConflict("organization must retain an owner")
            self._connection.execute("INSERT INTO memberships VALUES(?,?,?) ON CONFLICT(organization_id,user_id) DO UPDATE SET role=excluded.role", (membership.organization_id, membership.user_id, membership.role.value))
            self._event(EventKind.MEMBERSHIP_CHANGED, actor.user_id, membership.organization_id, now)

    def create_repository(self, actor: Principal, repository: RepositoryRecord, *, now: int):
        require_scope(actor, Action.ADMIN)
        with self.transaction():
            self._user(actor.user_id)
            if repository.owner.kind is OwnerKind.USER:
                if repository.owner.id != actor.user_id:raise PermissionDenied("repository owner permission denied")
            else:
                self._organization_owner(actor, repository.owner.id)
            actual_owner = self._connection.execute("SELECT kind FROM owners WHERE id=?", (repository.owner.id,)).fetchone()
            if actual_owner is None or actual_owner[0] != repository.owner.kind.value:raise CatalogConflict("owner kind mismatch")
            self._connection.execute("INSERT INTO repositories VALUES(?,?,?,?,?,?)", (repository.id, repository.owner.id, repository.slug.value, repository.storage_id.value, repository.visibility.value, repository.object_format))
            self._event(EventKind.REPOSITORY_CREATED, actor.user_id, repository.id, now, job_kind="repository.index")

    def _record(self, repository_id):
        identifier(repository_id)
        row = self._connection.execute("SELECT r.*,o.kind FROM repositories r JOIN owners o ON o.id=r.owner_id WHERE r.id=?", (repository_id,)).fetchone()
        if row is None:raise PermissionDenied("repository not found or permission denied")
        return RepositoryRecord(row['id'], Owner(row['owner_id'], row['kind']), Slug(row['slug']), StorageId(row['storage_id']), Visibility(row['visibility']), row['object_format'])

    def _authorize(self, actor, record, action):
        grants = tuple(AccessGrant(row['repository_id'], row['user_id'], Permission(row['permission'])) for row in self._connection.execute("SELECT * FROM grants WHERE repository_id=? AND user_id=?", (record.id, actor.user_id)))
        memberships = tuple(Membership(row['organization_id'], row['user_id'], OrganizationRole(row['role'])) for row in self._connection.execute("SELECT * FROM memberships WHERE organization_id=? AND user_id=?", (record.owner.id, actor.user_id)))
        require_permission(actor, record, action, grants=grants, memberships=memberships)

    def get_repository(self, actor: Principal, repository_id: str, *, action: Action = Action.READ) -> RepositoryRecord:
        with self._snapshot():
            record = self._record(repository_id)
            self._authorize(actor, record, action)
            if self.provisioning_state(repository_id)=="pending":raise PermissionDenied("repository not found or permission denied")
            return record

    def list_repositories(self, actor: Principal, *, limit: int = 50, after: str = "", ready_only: bool = False) -> tuple[RepositoryRecord, ...]:
        page(limit, 0)
        if not isinstance(after, str):raise ValueError("repository cursor must be an ID string")
        if after:identifier(after)
        if not scope_allows(actor, Action.READ):return ()
        with self._snapshot():
            rows = self._connection.execute("""SELECT r.id FROM repositories r JOIN owners o ON o.id=r.owner_id LEFT JOIN provisioning p ON p.repository_id=r.id
                WHERE r.id>? AND ((?=0 AND p.repository_id IS NULL) OR p.state='ready') AND (r.visibility='public' OR (o.kind='user' AND o.id=?)
                OR (o.kind='organization' AND EXISTS(SELECT 1 FROM memberships m WHERE m.organization_id=o.id AND m.user_id=? AND m.role='owner'))
                OR EXISTS(SELECT 1 FROM grants g WHERE g.repository_id=r.id AND g.user_id=? AND g.permission>=1))
                ORDER BY r.id LIMIT ?""", (after, int(ready_only), actor.user_id, actor.user_id, actor.user_id, limit)).fetchall()
            result = []
            for row in rows:
                record = self._record(row['id']);self._authorize(actor, record, Action.READ);result.append(record)
            return tuple(result)

    def grant(self, actor: Principal, grant: AccessGrant, *, now: int):
        with self.transaction():
            self.get_repository(actor, grant.repository_id, action=Action.ADMIN)
            self._user(grant.user_id)
            self._connection.execute("INSERT INTO grants VALUES(?,?,?) ON CONFLICT(repository_id,user_id) DO UPDATE SET permission=excluded.permission", (grant.repository_id, grant.user_id, int(grant.permission)))
            self._event(EventKind.GRANT_CHANGED, actor.user_id, grant.repository_id, now)

    def revoke_grant(self, actor: Principal, repository_id: str, user_id: str, *, now: int):
        identifier(user_id)
        with self.transaction():
            self.get_repository(actor, repository_id, action=Action.ADMIN)
            deleted = self._connection.execute("DELETE FROM grants WHERE repository_id=? AND user_id=?", (repository_id, user_id)).rowcount
            if deleted:self._event(EventKind.GRANT_CHANGED, actor.user_id, repository_id, now)

    def branch_policy(self, actor: Principal, repository_id: str) -> BranchPolicy:
        with self._snapshot():
            self.get_repository(actor, repository_id, action=Action.WRITE)
            row = self._connection.execute("SELECT version FROM ref_policy_versions WHERE repository_id=?", (repository_id,)).fetchone()
            rows = self._connection.execute("SELECT * FROM branch_rules WHERE repository_id=? ORDER BY pattern LIMIT ?", (repository_id, MAX_BRANCH_RULES + 1)).fetchall()
            if len(rows) > MAX_BRANCH_RULES:raise CatalogError("repository rule count exceeds the supported bound")
            rules = tuple(BranchRule(r['repository_id'], r['pattern'], *(bool(r[k]) for k in ('allow_create','allow_delete','allow_force','require_review')), Permission(r['minimum_permission'])) for r in rows)
            return BranchPolicy(repository_id, row[0] if row else 0, rules)

    def _policy_revision(self, actor, repository_id, expected_version):
        self.get_repository(actor, repository_id, action=Action.ADMIN)
        if type(expected_version) is not int or not 0 <= expected_version < 0x7fffffffffffffff:
            raise ValueError("select a nonnegative policy revision below the SQLite maximum")
        current = self.branch_policy(actor, repository_id)
        if current.version != expected_version:raise CatalogConflict("ref policy revision changed")
        return current

    def _advance_policy(self, actor, repository_id, version, now):
        self._connection.execute("INSERT INTO ref_policy_versions VALUES(?,?) ON CONFLICT(repository_id) DO UPDATE SET version=excluded.version", (repository_id, version + 1))
        self._event(EventKind.REF_POLICY_CHANGED, actor.user_id, repository_id, now)

    def set_branch_rule(self, actor: Principal, rule: BranchRule, *, expected_version: int, now: int) -> BranchPolicy:
        if not isinstance(rule, BranchRule):raise TypeError("select a BranchRule")
        with self.transaction():
            current = self._policy_revision(actor, rule.repository_id, expected_version)
            if len(current.rules) >= MAX_BRANCH_RULES and all(r.pattern != rule.pattern for r in current.rules):
                raise CatalogConflict("repository rule count exceeded")
            self._connection.execute("INSERT INTO branch_rules VALUES(?,?,?,?,?,?,?) ON CONFLICT(repository_id,pattern) DO UPDATE SET allow_create=excluded.allow_create,allow_delete=excluded.allow_delete,allow_force=excluded.allow_force,require_review=excluded.require_review,minimum_permission=excluded.minimum_permission",
                                     (rule.repository_id, rule.pattern, int(rule.allow_create), int(rule.allow_delete), int(rule.allow_force), int(rule.require_review), int(rule.minimum_permission)))
            self._advance_policy(actor, rule.repository_id, current.version, now)
            return self.branch_policy(actor, rule.repository_id)

    def delete_branch_rule(self, actor: Principal, repository_id: str, pattern: str, *, expected_version: int, now: int) -> BranchPolicy:
        ref_pattern(pattern)
        with self.transaction():
            current = self._policy_revision(actor, repository_id, expected_version)
            deleted = self._connection.execute("DELETE FROM branch_rules WHERE repository_id=? AND pattern=?", (repository_id, pattern)).rowcount
            if deleted:self._advance_policy(actor, repository_id, current.version, now)
            return self.branch_policy(actor, repository_id)

    def store_credential(self, actor: Principal, metadata: CredentialMetadata, verifier_digest: bytes):
        if actor.scopes is not None or actor.credential_id is not None:
            raise PermissionDenied("token issuance requires a trusted local account principal")
        if not isinstance(verifier_digest, bytes) or len(verifier_digest) != 32:
            raise ValueError("credential storage requires a 32-byte verifier digest, never a raw token")
        with self.transaction():
            self._user(actor.user_id)
            if actor.user_id != metadata.user_id:raise PermissionDenied("credential permission denied")
            self._connection.execute("INSERT INTO credentials VALUES(?,?,?,?,?,?)", (metadata.id, metadata.user_id, verifier_digest, metadata.created_at, metadata.expires_at, metadata.revoked_at))
            self._event(EventKind.CREDENTIAL_CREATED, actor.user_id, metadata.id, metadata.created_at)

    def credential_metadata(self, actor: Principal, credential_id: str) -> CredentialMetadata:
        identifier(credential_id)
        row = self._connection.execute("SELECT id,user_id,created_at,expires_at,revoked_at FROM credentials WHERE id=? AND user_id=?", (credential_id, actor.user_id)).fetchone()
        if row is None:raise PermissionDenied("credential not found or permission denied")
        return CredentialMetadata(*row)

    def revoke_credential(self, actor: Principal, credential_id: str, *, now: int):
        require_scope(actor, Action.ADMIN)
        timestamp(now)
        with self.transaction():
            metadata = self.credential_metadata(actor, credential_id)
            if now < metadata.created_at:raise ValueError("revocation cannot precede credential creation")
            if metadata.revoked_at is None:
                self._connection.execute("UPDATE credentials SET revoked_at=? WHERE id=?", (now, credential_id))
                self._event(EventKind.CREDENTIAL_REVOKED, actor.user_id, credential_id, now)

    def _credential_auth_data(self, credential_id):
        """Verifier material is private to the token authentication boundary."""
        row = self._connection.execute("SELECT c.* FROM credentials c JOIN owners o ON o.id=c.user_id AND o.kind='user' WHERE c.id=?", (credential_id,)).fetchone()
        if row is None:return None
        scopes = frozenset(Action(r[0]) for r in self._connection.execute("SELECT action FROM credential_scopes WHERE credential_id=?", (credential_id,)))
        return CredentialMetadata(row['id'], row['user_id'], row['created_at'], row['expires_at'], row['revoked_at']), bytes(row['verifier']), scopes

    def repository_events(self, actor: Principal, repository_id: str, *, limit=50, after=0) -> tuple[AuditEvent, ...]:
        page(limit, after)
        with self._snapshot():
            self.get_repository(actor, repository_id, action=Action.ADMIN)
            rows = self._connection.execute("SELECT * FROM audit_events WHERE resource_id=? AND sequence>? ORDER BY sequence LIMIT ?", (repository_id, after, limit)).fetchall()
            return tuple(AuditEvent(r['sequence'], r['id'], EventKind(r['kind']), r['actor_id'], r['resource_id'], r['timestamp']) for r in rows)

    def pending_jobs(self, *, limit=50, after=0) -> tuple[OutboxJob, ...]:
        page(limit, after)
        rows = self._connection.execute("SELECT j.sequence,j.id,j.event_id,j.kind,j.resource_id FROM outbox j LEFT JOIN provisioning p ON p.repository_id=j.resource_id WHERE j.done=0 AND j.sequence>? AND (j.kind!='repository.index' OR p.repository_id IS NULL OR p.state='ready') ORDER BY j.sequence LIMIT ?", (after, limit)).fetchall()
        return tuple(OutboxJob(*r) for r in rows)

    def acknowledge_job(self, job_id: str) -> bool:
        identifier(job_id)
        with self.transaction():
            return self._connection.execute("UPDATE outbox SET done=1 WHERE id=? AND done=0", (job_id,)).rowcount == 1

    def provisioning_state(self, repository_id: str):
        identifier(repository_id)
        row=self._connection.execute("SELECT state FROM provisioning WHERE repository_id=?",(repository_id,)).fetchone()
        return row[0] if row else None

    def reserve_repository(self, actor, record, *, now):
        require_scope(actor, Action.ADMIN)
        with self.transaction():
            existing=self._connection.execute("SELECT id FROM repositories WHERE id=?",(record.id,)).fetchone()
            if existing:
                stored=self._record(record.id);self._authorize(actor,stored,Action.ADMIN)
                state=self.provisioning_state(record.id)
                if stored!=record or state is None:raise CatalogConflict("repository is not the same managed provisioning request")
                return state
            self.create_repository(actor,record,now=now)
            self._connection.execute("INSERT INTO provisioning VALUES(?,'pending')",(record.id,))
            return "pending"

    def provisioning_record(self, actor, repository_id):
        record=self._record(repository_id);self._authorize(actor,record,Action.ADMIN)
        if self.provisioning_state(repository_id) is None:raise CatalogConflict("repository is not managed by provisioning")
        return record

    def finish_provisioning(self, actor, repository_id, *, now):
        with self.transaction():
            self.provisioning_record(actor,repository_id)
            if self._connection.execute("UPDATE provisioning SET state='ready' WHERE repository_id=? AND state='pending'",(repository_id,)).rowcount:
                self._event(EventKind.REPOSITORY_READY,actor.user_id,repository_id,now)
