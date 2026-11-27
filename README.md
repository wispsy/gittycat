# gittycat

Initial PyPI preview: **0.1.0a1**, paired with `gitdx==0.1.0a1`.
The current scope includes catalog storage, repository provisioning,
authentication and the management API. Current source adds bounded read-only
smart HTTP and SHA-256-only hosted receive on qualified Linux storage.
Production deployment remains later work.
[Wispsy Releases](https://github.com/orgs/wispsy/projects/31)
tracks publication separately from current development.

A Git-compatible hosting and collaboration platform powered by [gitdx](https://github.com/wispsy/gitdx).

Python 3.14+ · MIT

Development starts with the [implementation plan](IMPLEMENTATION_PLAN.md) and
[shared Project](https://github.com/orgs/wispsy/projects/30). The roadmap records
qualification targets; public deployment remains a later milestone.

Source development does not imply a published package or deployed service.

The first implementation provides immutable platform records, validated slugs
and opaque storage IDs, an explicit Git backend protocol and deny-by-default
repository permissions. [Permission contracts](docs/permissions.md) describe
anonymous, owner, organization and collaborator behavior. Discovery uses the
same read policy as repository access.

[SQLite catalog storage](docs/catalog.md) now persists identities, ownership,
repository metadata, permissions, credential metadata and a transactional audit
outbox.

[Managed provisioning](docs/provisioning.md) now connects catalog permissions to
real Gitdx bare repositories with pending/ready recovery and an immutable backend
source.

[Personal token authentication](docs/authentication.md) provides one-time secret
delivery, expiring/revocable credentials and read/write/admin scope caps shared
by discovery and repository services. Account bootstrap and token issuance use
a trusted local catalog handle. The [optional management API](docs/management-api.md)
adds authorized JSON routes through Expose API and explicit loopback serving.
[Read-only smart HTTP](docs/smart-http.md) adds authorized discovery/upload-pack
through the same Expose API app. [Hosted receive](docs/receive.md) adds scoped,
policy-checked SHA-256 pushes with quarantined intake, audit receipts and explicit
interruption recovery. Interactive account login remains later work.

Current source also provides [branch protection and ref-change policy](docs/ref-policy.md):
versioned administrator rules, fresh token/grant checks and expected-old,
ancestry-based decisions for managed branches and tags. These read-only decisions
are rechecked under ref locks by the hosted receiver before publication.

Run `uv sync --locked` and `uv run python -m unittest discover -s tests -v`.
The [two-user hosting acceptance](docs/hosting-acceptance.md) runner checks real
stock-Git workflows, access refusal and restart, retaining a secret-free receipt.
The [installation profile](docs/installation.md) qualifies independently built
Gitdx/Gittycat artifacts and rejects backend capability mismatches.
`uv run python scripts/check_distribution.py` checks wheel/sdist installations
outside the checkout and startup with Git absent from PATH.
