# SQLite catalog and audit outbox

`Catalog(path)` owns one SQLite connection; use it as a context manager and use
a separate connection per thread. Migrations use an application ID, schema
version and one explicit transaction for all DDL/version changes. Unknown or
foreign schemas are refused. Failed upgrades roll back and preserve old rows.

Account bootstrap and outbox maintenance require a trusted local catalog handle.
They are not unauthenticated server endpoints. Repository reads, lists, grants
and mutations consume an already authenticated `Principal` and enforce the
shared read/write/admin policy. Read snapshots keep records and permission
checks consistent; SQL prefilters apply the same current read policy before
bounded pagination. Ordinary organization members gain no implicit private access.

```python
from gittycat import Catalog, Principal, Slug

with Catalog("data/gittycat.sqlite3") as catalog:
    catalog.create_account("1" * 32, Slug("example-user"), now=1700000000)
    assert catalog.list_repositories(Principal("1" * 32)) == ()
```

Every mutation writes its typed audit event and durable outbox envelope in the
same transaction. Nested calls use savepoints, so a caught inner failure cannot
commit a partial mutation. `with catalog.transaction():` groups multiple changes;
normal exit commits and exceptions roll back. Uniqueness failures raise
`CatalogConflict`; denied queries/mutations use concealed permission errors.

Audits contain only declared event kinds, opaque actor/resource IDs and explicit
timestamps. They accept no arbitrary message, request header or payload that
could copy credentials into logs. Credential storage accepts a caller-computed
32-byte verifier digest, never a token string; callers remain responsible for
correct digest derivation. Credential metadata/query results exclude that digest.
Use [TokenAuth](authentication.md) for generated bearer tokens and their scopes;
legacy metadata/verifiers without scope rows cannot authenticate as tokens.

`pending_jobs(limit, after)` and `acknowledge_job(id)` provide restart-safe bounded
outbox envelopes and idempotent acknowledgement. Delivery can occur at least once;
consumers must be idempotent. Worker leases/retries, execution and external delivery
remain CAT-12 work. No worker or network service starts automatically.

Pages contain at most 100 results. New databases use owner-only file permissions;
static database/sidecar symlinks are refused. SQLite manages database transaction
durability; callers must control the directory/ancestors. This is not protection
against hostile concurrent path replacement or a cross-filesystem transaction.
Repository enrollment records alone do not provision Git storage. Use
`RepositoryService` for [managed provisioning/reconciliation](provisioning.md).
Objects and branch tips stay exclusively in the Gitdx backend.
