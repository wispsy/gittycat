# gittycat

A Git-compatible hosting and collaboration platform powered by [gitdx](https://github.com/wispsy/gitdx).

Python 3.14+ · MIT

Development starts with the [implementation plan](IMPLEMENTATION_PLAN.md) and
[shared Project](https://github.com/orgs/wispsy/projects/30). The roadmap records
qualification targets; remote exchange and public hosting are later milestones.

Source development does not imply a published package or deployed service.

The first implementation provides immutable platform records, validated slugs
and opaque storage IDs, an explicit Git backend protocol and deny-by-default
repository permissions. [Permission contracts](docs/permissions.md) describe
anonymous, owner, organization and collaborator behavior. Discovery uses the
same read policy as repository access.

This milestone has no third-party runtime dependencies. The concrete Gitdx
authentication, HTTP hosting and collaboration are later tasks.

[SQLite catalog storage](docs/catalog.md) now persists identities, ownership,
repository metadata, permissions, credential metadata and a transactional audit
outbox. Authentication remains a separate service.

[Managed provisioning](docs/provisioning.md) now connects catalog permissions to
real Gitdx bare repositories with pending/ready recovery and an immutable backend
source. Authentication and HTTP/protocol hosting remain separate tasks.

Run `uv sync --locked` and `uv run python -m unittest discover -s tests -v`.
`uv run python scripts/check_distribution.py` checks wheel/sdist installations
outside the checkout and startup with Git absent from PATH.
