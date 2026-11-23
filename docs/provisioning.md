# Managed bare repositories

`RepositoryService(catalog, GitdxBackend(data_root))` authorizes before storage
creation/lookup and uses opaque `StorageId` directories. URL slugs never choose
filesystem paths. `create(principal, record, now=...)` reserves pending catalog
metadata, creates a bare repository and marks it ready. `open` returns verified
record/backend information; `list` exposes only ready, authorized repositories.

Pending rows and indexing envelopes stay hidden until storage and catalog agree.
`recover(principal, repository_id, now=...)` is an explicit admin operation. It
can resume a process interruption before publication or adopt the same already
published, ownership-marked result after a failed catalog commit. Existing ready
storage is inspected, never reinitialized. Unmanaged repositories/staging areas
and live/stale locks are refused for owner reconciliation, never deleted.

Provisioning uses an exclusive per-storage lock and a private staging container
with an ownership receipt. Only an unpublished container with the matching
receipt is replaced during recovery. Published repositories and unrelated files
are preserved. This assumes an owner-controlled local data root; it does not
provide hostile filesystem isolation or power-loss durability across SQLite and
filesystem boundaries. Missing ready storage is an error, not permission to
create a new empty repository.

The mandatory Gitdx dependency selects the immutable qualified source in
[backend-sources.json](backend-sources.json), avoiding unpublished PyPI version
ambiguity and absolute sibling paths. Source installation may use Git; repository
operations never call it. Both native hashes are qualified against stock Git.
Smart HTTP and protocol services remain later tasks.
