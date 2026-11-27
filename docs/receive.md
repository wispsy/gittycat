# Hosted receive-pack

Current source supports `GET /git/<repository-id>.git/info/refs?service=git-receive-pack`
and `POST /git/<repository-id>.git/git-receive-pack` through the optional Expose
API app. Every hosted receive selects `IngestionProfile.UNTRUSTED`. Only native
SHA-256 repositories on the qualified Linux descriptor/allocation profile can
advertise or accept writes. SHA-1, unqualified platforms and unsafe storage fail
before body intake, quarantine creation or object-policy callbacks. Provisioning
keeps the explicitly selected native object format; no repository is converted.
Trusted-local SHA-1 operations and read serving remain available.

Use the same Basic token password or Bearer credential as [smart HTTP](smart-http.md).
Fresh write permission precedes storage reads; public visibility grants no write
access. Access is checked again after bounded body intake and at preparation.
Receive discovery uses the legacy wire protocol even when fetch clients select
protocol v2. Advertised capabilities include report-status/v2, ofs-delta,
delete-refs and no-thin. No atomic, push options, signed push, hooks or ref
rewriting is advertised. Native clients must send self-contained delta packs.

Gitdx validates all supplied objects and complete typed graph connectivity in
owned quarantine. Branch/tag policy uses that overlay, including new commits
not yet in live storage. One denied command refuses the coordinated batch before
promotion. Verified objects publish before refs; rejected late updates can leave
valid unreachable objects. Configured server hooks/helpers never execute.

After promotion, the receiver enters a catalog transaction and then acquires
the engine's deterministic ref locks. Token activity/expiry, current grants,
the selected policy revision and expected-old tips are checked again under both
guards. Catalog mutations therefore commit before or after publication. Ref CAS
still handles native writers. Owners have no policy bypass. This is coordinated
files-ref publication, **not atomic multi-ref visibility**, and it does not form
a distributed transaction with SQLite.

Schema 6 adds secret-free receive receipts. A prepared row records only operation
ID, repository/actor IDs, creation time and proposed ref names/old/new IDs. These
proposals are historical inputs, never current ref authority. After receive,
per-ref outcomes, unpack/cleanup state and any ref transaction receipt commit
with an audit event and durable outbox envelope. Accepted pushes enqueue indexing.
`catalog.receive_operation(administrator, operation_id)` returns the sanitized
receipt. It contains no token, verifier, submitted packet text or exception text.

Accepted refs get `ok`; known refusals get `ng`. Unknown publication outcomes
produce a fixed Git `ERR` telling the client to reread refs, rather than a false
rejection or blind retry. A database failure/process exit after Git publication
can leave a prepared receipt; that remains unknown across restart. Observing a
later ref tip is insufficient to infer that an earlier request committed.

`ReceiveService(catalog, backend).recover(administrator, repository_id)` explicitly
selects confined engine recovery. A prepared Git journal rolls back its exact
known before-state; a committed journal finalizes its known after-state. The
returned engine receipt is authoritative for that ref transaction. Untied
prepared request receipts stay unknown. Later edits, corrupt/foreign journals,
unknown locks and unowned incoming directories are refused. Operators must
prove a lock owner's exit before reconciling its exact locks; recovery never
steals them or recursively adopts unknown quarantine. Pending cleanup and retired
paths stay in the receipt for bounded owner inspection.

`create_app(receive_limits=ReceiveLimits(...), max_git_transfers=...)` selects
independent receive budgets. Defaults allow 128 commands, a 16 MiB request and a
30-second cooperative engine deadline, plus explicit pack expansion, graph,
quarantine, descriptor work/allocation and separate owned-cleanup bounds. Receive
POST admission precedes body allocation. A cancelled/disconnected preparation
settles and cleans its owned storage before returning its shared transfer slot.
Management JSON and read negotiation retain their separate limits. These are
per-operation budgets; global repository quotas and destructive GC/retention
remain separately selected operations.

Tests use real stock Git initial/fast-forward/feature/tag/delete pushes and
native fsck, early refusal, private/missing concealment, media/body bounds,
policy/credential changes, catalog exclusion, invalid packs, cancellation,
unknown outcomes, database failure and explicit prepared/committed recovery.
Linux selects the actual hosted profile; other POSIX mechanism tests cannot
enable it in application code. This local source profile authorizes neither a
package upload nor public TLS/proxy deployment.
