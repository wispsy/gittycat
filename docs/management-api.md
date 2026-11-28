# Local management API

Install the explicit HTTP extra with `uv sync --locked --extra http` or install
`gitcx[http]`. It selects published `expose-api==0.2.0`; wheel hashes are locked.
Core imports and backend operations do not import or require Expose API.

`gitcx.create_app(data_root)` returns an Expose API ASGI application. Its
lifespan migrates the catalog, opens the managed storage root and marks readiness;
shutdown clears readiness. It never creates accounts, tokens or a listening
socket implicitly. Use a trusted local `Catalog(data_root / 'catalog.sqlite3')`
and `TokenAuth` to bootstrap accounts and issue tokens as described in
[authentication](authentication.md). Existing repositories use `data_root/git`.

`gitcx serve --data-root ./data --port 8000` explicitly starts the framework's
loopback-only demo server. It is for local acceptance, with the framework's
10-second request timeout and 100-connection defaults. Production server/proxy,
TLS, deployment and public-hosting hardening remain separate choices. An ASGI
host must run lifespan and provide trusted scope/header data. No server remains
running after the acceptance tests.

| Method and route | Behavior |
| --- | --- |
| GET /healthz | Public readiness only |
| GET /api/me | Authenticated account ID, scopes and non-secret credential ID |
| GET /api/repositories?limit=50&after=ID | Visible ready repositories; maximum 100, exclusive ID cursor |
| POST /api/repositories | Reserve and provision a managed bare repository |
| GET /api/repositories/{id} | Authorized open and capability/format summary |
| POST /api/repositories/{id}/recover | Authorized pending/ready recovery |
| PUT /api/repositories/{id}/grants/{user_id} | Set read/write/admin collaborator permission |
| DELETE /api/repositories/{id}/grants/{user_id} | Revoke a collaborator grant |
| POST /api/organizations | Create an organization and its owner membership |
| PUT /api/organizations/{id}/members/{user_id} | Set member/owner role; preserve a final owner |
| POST /api/tokens/{id}/revoke | Revoke an owned credential with admin scope |

All API routes require one `Authorization: Bearer TOKEN` header. No cookies,
query tokens or anonymous fallback are accepted. Missing/invalid/expired/revoked
credentials return 401 with the Bearer challenge. Valid principals without scope
or resource access receive a concealed 404, matching missing resources. Protected
responses and errors are no-store; unexpected errors omit payloads, credentials
and storage paths. Repository views never return internal storage IDs/paths.
HEAD and OPTIONS follow the framework's route contracts; OPTIONS exposes only
route method metadata, never resource existence or data.

Creation JSON requires `id` (a caller-generated lowercase UUID hex ID) and `slug`.
Optional fields are `owner_id` (defaults to the token's account), `owner_kind`
(`user` or `organization`), `visibility` (`private` default or `public`) and
`object_format` (`sha1` default or `sha256`). Internal storage IDs are generated
by the service and cannot be selected over HTTP. Success returns 201 and a local
Location header. The caller retains its repository ID even if provisioning fails.
After an interrupted create, use the recovery endpoint with that ID; reposting
creation is not an idempotency protocol. Conflicting catalog changes return 409.

Grant JSON is `{"permission":"read"}` (or write/admin), organization JSON is
`{"id":"...","slug":"..."}`, and membership JSON is `{"role":"member"}`
(or owner). Unknown fields, invalid IDs/enums and malformed pagination are refused
with 400. Bodies default to 16 KiB and JSON parsing/media types follow Expose API's
bounded request rules; oversized input returns 413. Queries reject duplicates and
unknown fields. The next cursor is the last returned ID; an empty page ends iteration.

Each request uses its own SQLite connection and the shared scope/permission
policy. Authentication runs before body reads and repeats after awaited body
input, so expiry/revocation during upload cannot authorize a mutation. Operations
use one transaction per service phase. Creation commits pending metadata before
filesystem publication, then authenticates again for recovery/ready publication;
failures remain hidden and recoverable. Synchronous local catalog/storage work
runs inline and is bounded; long/background jobs and public concurrency
qualification remain later work. Storage errors return redacted 503 responses.

Only JSON control routes exist. `/repo.git/info/refs`, upload-pack/receive-pack
and Git packet/binary serializers are not registered. Account login/token issuance
remain local bootstrap operations. Real loopback HTTP tests qualify both hashes,
two collaborators and an outsider, scope/expiry/revocation, limits, recovery,
organization roles, concealed discovery and lifespan restart. Installed wheel
and sdist pairs run the same HTTP suite outside source trees on all CI platforms.
