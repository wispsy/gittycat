# Gittycat implementation plan

Baseline: 22 November 2026. Python 3.14+, MIT, public source.
The [shared Gitdx → Gittycat plan](https://github.com/wispsy/gitdx/blob/main/IMPLEMENTATION_PLAN.md)
owns the complete engine/hosting roadmap and task register. This document owns
the hosting boundary. The [shared Project](https://github.com/orgs/wispsy/projects/30)
and linked issues are the execution queue.

## Goal and first milestone

Gittycat will be a self-hosted single-node Git forge powered by Gitdx. Stock Git
clients must clone, push and fetch without installing either Python package on
the client. The 0.8 alpha adds identities, organizations, repository roles,
branch protection, a repository browser, pull requests, comments/reviews, audit
records and recoverable administration. This is a qualification target, not a
claim about the current implementation or authorization to publish/deploy.

The first source batch implements CAT-01: immutable domain records, safe
identifiers, explicit permissions and a backend protocol. It deliberately has
no HTTP listener, credentials store or Git implementation. Subsequent tasks
persist metadata and provision bare repositories through qualified Gitdx APIs.

## Application boundaries

Build a modular monolith. Application services coordinate a control plane
(identities, catalog, policy, collaboration, jobs) and a Git data plane (binary
protocol requests delegated to Gitdx). Management JSON and smart-HTTP Git routes
share authentication/application services but retain distinct serialization,
content types, error contracts and request budgets.

Suggested modules grow only as implemented:

```text
domain.py / permissions.py     identities, repository records and access policy
storage/                      SQLite metadata, migrations, audit outbox and jobs
services/                     accounts, repositories, branches and pull requests
git/                          Gitdx adapter, upload/receive orchestration, policy
http/                         management API, smart HTTP and escaped HTML views
integrations/                 separately selected capabilities and clients
cli.py                        configuration and local administrative commands
```

Gitdx owns object parsing, IDs, refs, history, diffs, packs and negotiation.
The backend protocol exposes selected operations/capabilities rather than a
subprocess command runner. Opening a backend checks repository formats and
capabilities before advertising writes. Repository metadata records object
format and storage ID but never becomes a competing authority for branch tips.

## Durable model and filesystem

Use standard-library sqlite3 for versioned platform metadata. Transactions cover
catalog mutations and the durable audit/job outbox. Enforce uniqueness and
bounded queries; define migration backup/rollback and process restart behavior.

| Record | Authority |
| --- | --- |
| Principal / account | Authenticated identity; anonymous callers are explicit |
| Organization / membership | Ownership and selected organization roles |
| RepositoryRecord | Opaque ID, owner, slug, visibility, object format, lifecycle |
| AccessGrant | Explicit read, write or admin permission for an account/repository |
| Credential | Token verifier/scope/expiry/revocation; raw token is never stored |
| BranchRule | Protected ref actions, force-push/delete/approval requirements |
| PullRequest / review / comment | Collaboration and checked state transitions |
| AuditEvent / Job | Durable sanitized outcomes, delivery and derived indexing |
| Git refs/objects | Gitdx store only; database caches can be rebuilt |

```text
data/
  git/<opaque-storage-id>.git/
  db/gittycat.sqlite3
  jobs/
  backups/
```

User slugs and owner names never select arbitrary filesystem paths. Define
canonical names and reserved namespaces; keep external URLs distinct from
internal storage IDs. Repository creation crosses SQLite and filesystem state:
use an explicit provisioning lifecycle, idempotency and reconciliation. Do not
pretend they are a single atomic database transaction.

## Authorization and request handling

Use one deny-by-default decision function for repository discovery, API, Git
routes, browser pages and collaboration. Public visibility permits reading;
it grants no mutation rights. Anonymous users cannot inherit authenticated
grants. Validate principal/owner/grant identity and organization membership.
Private repositories are concealed from unauthorized listing and retrieval.
Ownership/admin/write/read actions have explicit tests; branch policies add
constraints to write permission rather than granting access themselves.

Authenticate before reading private refs/objects or accepting pack bytes. Scope
and revoke personal tokens; keep raw tokens out of logs, exceptions and audit
payloads. No general external command, hook, repository filter or integration
runs in authentication or receive validation. Browser content must be escaped
and active/binary downloads isolated from normal HTML rendering.

## Git hosting transaction

1. Resolve repository and authenticated caller without leaking private state.
2. Enforce repository permission, endpoint budgets and requested capabilities.
3. Quarantine incoming pack data through Gitdx.
4. Validate checksums, object kinds, deltas, graph connectivity and quotas.
5. Apply branch rules, ancestry/fast-forward checks and expected-old ref values.
6. Publish verified objects, then commit permitted ref changes.
7. Persist sanitized outcomes and durable derived-work envelopes.
8. Return accurate per-ref success/rejection; advertise atomic only when proven.

The Git store and catalog/outbox also cross durability boundaries. Make the
Git transaction authoritative; use replayable outcome records/reconciliation
instead of falsely promising a distributed transaction. Interrupted responses
may require clients to re-read refs. Unreachable verified objects after a
rejected update are acceptable maintenance input; invalid accepted refs are not.

## Interfaces and selected dependencies

Smart HTTP precedes SSH. Required discovery/upload/receive routes are
`info/refs?service=git-upload-pack`, `git-upload-pack`,
`info/refs?service=git-receive-pack` and `git-receive-pack`. They carry packet
lines and binary packs, not JSON. Protocol v2 fetch and ordinary-client push
variants must both be qualified against stock Git.

Gitdx is the mandatory backend once provisioning starts. Until a release is
explicitly approved, qualify immutable source commits/built artifacts; do not
publish metadata with absolute sibling paths or accidentally resolve an
unrelated package from an index. CAT-17 owns installed composition evidence.

Expose API supplies the selected application HTTP adapter after inspection of
its current public API. Requ/Apiconn may supply optional outgoing clients.
Carets, Hidden Moves, Moveset, Astree and Gitwriter remain optional profiles
with ordinary Python APIs underneath. Inspect each owning implementation and
split concrete adapters before adding dependencies. No external service is
needed for the initial local milestone.

## Acceptance and operations

The prototype requires two authorized stock Git clients and an outsider:
clone an empty repository, commit/push main, push a feature branch, fetch it
from the second user, reject the outsider, restart and repeat. Both supported
native object formats are tested separately. The alpha adds browser comparison,
pull request, comment/review and merge, rechecking current base/head/policy.

Run focused unittest tests locally and the relevant suite. CI covers Python
3.14 on Linux/macOS/Windows, source/wheel/sdist outside the checkout, real
loopback HTTP and a runtime without the Git executable. Required oracle jobs
fail on skips. Browser qualification uses real rendered pages, authorization
probes and untrusted-content fixtures. Retain exact engine/framework revisions
and unsupported capability reports.

Quotas cover request bytes, expanded objects, graph/delta work, repository disk,
concurrent operations and background jobs. Backups must consistently restore
catalog, Git state and audit/outbox into an isolated target. Qualify disk pressure,
crashes, maintenance with active writers, failed migrations and token revocation.
Ordinary SHA-1 hashing is not collision detection: untrusted SHA-1 hosting stays
gated on DX-26's validated policy. Static path checks alone are insufficient
for attacker-controlled concurrent filesystem changes.

Public deployment requires a selected hardened server/reverse proxy, TLS,
authentication bootstrap, host/domain, quotas, backup/restore and rollback.
A standard-library demonstration listener is loopback-only. Package uploads,
release publication and public deployment each require explicit owner authority
after concrete artifacts exist. SSH, LFS, hosted CI, registries, issue boards,
distributed storage and broad external integrations are independent later work.

## Hosting task register

Detailed scope and acceptance live in each linked issue. Native dependency
links include the engine tasks in the shared Project. For the complete engine
sequence and milestone gates, use the shared plan.

| Task | Scope | Prerequisites |
| --- | --- | --- |
| [CAT-01](https://github.com/wispsy/gittycat/issues/1) | Hosting domain and explicit repository permissions | None |
| [CAT-02](https://github.com/wispsy/gittycat/issues/2) | SQLite catalog, migrations and audit outbox | CAT-01 |
| [CAT-03](https://github.com/wispsy/gittycat/issues/3) | Bare repository provisioning through gitdx | CAT-02, DX-04 |
| [CAT-04](https://github.com/wispsy/gittycat/issues/4) | Accounts, organizations and token authentication | CAT-02 |
| [CAT-05](https://github.com/wispsy/gittycat/issues/5) | Branch protection and ref-change policy | CAT-04, DX-15 |
| [CAT-06](https://github.com/wispsy/gittycat/issues/6) | Management API with Expose API integration | CAT-03, CAT-04 |
| [CAT-07](https://github.com/wispsy/gittycat/issues/7) | Smart-HTTP advertisement and upload-pack | CAT-06, DX-22 |
| [CAT-08](https://github.com/wispsy/gittycat/issues/8) | Receive-pack with quarantine and policy enforcement | CAT-07, CAT-05, DX-26, DX-24 |
| [CAT-09](https://github.com/wispsy/gittycat/issues/9) | Local two-user stock-Git hosting acceptance | CAT-08 |
| [CAT-10](https://github.com/wispsy/gittycat/issues/10) | Repository browser and safe commit/diff views | CAT-09, DX-14 |
| [CAT-11](https://github.com/wispsy/gittycat/issues/11) | Pull requests, comments, reviews and checked merge | CAT-10, CAT-05, DX-16 |
| [CAT-12](https://github.com/wispsy/gittycat/issues/12) | Durable jobs, audit queries and optional webhooks | CAT-08, CAT-02 |
| [CAT-13](https://github.com/wispsy/gittycat/issues/13) | Backup, restore, quotas and single-node operations | CAT-11, CAT-12, DX-27 / policy gate |
| [CAT-14](https://github.com/wispsy/gittycat/issues/14) | Selected typed operations and terminal/workflow adapters | CAT-11, DX-33 / policy gate |
| [CAT-15](https://github.com/wispsy/gittycat/issues/15) | Installed forge alpha and security qualification | CAT-13, DX-33 |
| [CAT-16](https://github.com/wispsy/gittycat/issues/16) | Owner-selected publication and deployment | CAT-15 / policy gate |
| [CAT-17](https://github.com/wispsy/gittycat/issues/17) | Immutable cross-repository installation profile | CAT-03, DX-04 |
