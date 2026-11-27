# Read-only smart HTTP

Current source serves Git discovery and fetch through the existing optional
Expose API app. The published Gittycat/Gitdx 0.1.0a1 previews predate these
handlers. Source installs select an immutable Gitdx revision recorded in
[backend-sources.json](backend-sources.json); [installation.md](installation.md)
separates that development profile from the released packages.

The repository URL is `/git/<repository-id>.git`, using the public catalog ID,
never a storage path or user-supplied filesystem name:

```
git clone http://127.0.0.1:8000/git/<repository-id>.git
```

`GET .../info/refs?service=git-upload-pack` and
`POST .../git-upload-pack` delegate all Git negotiation/pack generation to
Gitdx. Protocols 0, 1 and 2 support same-format SHA-1/SHA-256 clone/fetch,
branches, peeled annotated tags and empty repositories. Responses are complete
non-thin packs; incremental exclusion and shallow/filter/deepen requests remain
outside the selected engine profile. Receive-pack, dumb/raw object routes and
hosted writes are disabled. Read-serving trusted SHA-1 storage does not enable
untrusted SHA-1 ingestion.

## Authentication and disclosure

Anonymous clients can read ready public repositories. Private/missing resources
both return the same 401 Basic challenge to an anonymous client, allowing normal
Git credential helpers to retry. Authenticated callers without read permission
receive the same concealed 404 for private/missing resources. Pending storage is
hidden. Invalid, expired, revoked, duplicate or insufficiently scoped credentials
cannot fall back to anonymous access.

Use an explicitly selected personal token as the Basic password; the username
is a transport label, while the token identifies the account. Bearer tokens are
also accepted. Keep tokens in a credential helper or explicitly managed header
source, never repository URLs or clone configuration. The server does not run
Git, credential helpers, repository hooks or other processes, and does not load
ambient outbound credentials or proxies.

Catalog authorization precedes storage receipts, refs and object reads. It is
rechecked after request-body consumption, at preparation, and before the first
response-body byte. Revocation during preparation discards the prepared data.
An authorized response already in transmission uses its request snapshot;
subsequent requests revalidate credentials and grants. No token, submitted packet
text or internal exception message is included in a protocol error.

## Limits, streaming and transport

`create_app(..., git_limits=UploadPackLimits(...), max_git_transfers=4)` selects
engine budgets and at most four concurrent preparations/streams by default.
Exhausted admission returns 503 without starting work. Default negotiation bodies
are at most 1 MiB; Gitdx independently limits packets, refs, wants/haves, graph
work, object/pack/response bytes and its cooperative 30-second deadline.
Management JSON retains its separate `max_body_size` limit (16 KiB by default).
Upload requests require the Git request media type and identity content encoding.
Discovery permits exactly the requested upload-pack service parameter.

Preparation runs in a worker thread. Expose API owns raw body limits, binary
media types, asynchronous byte streaming and disconnect detection. Disconnect or
server cancellation signals the Gitdx session and settles the worker before
returning its admission slot. A blocking kernel I/O/compression call is not
forcibly interrupted; its finite operation/graph bounds still apply. This is
bounded in-memory pack preparation followed by streamed chunks, without an
unbounded pack writer or additional web framework.

Content types are `application/x-git-upload-pack-advertisement` and
`application/x-git-upload-pack-result`. Responses disable caching and vary on
authorization/protocol selection. Authentication, method, media-type and body-size
failures use HTTP status errors. Failures during preparation after headers have
started send a fixed Git `ERR` packet; failure during output can close the stream.
Clients must treat an incomplete response as a failed read.

`gittycat serve` remains an explicit loopback development listener. Its transport
deadline can be shorter than the engine budget. These fixtures do not qualify
public TLS/proxy deployment, production connection management or arbitrary local
filesystem writers. Storage retains the owner-controlled provisioning contract.

Source/wheel/sdist suites use stock Git over real loopback HTTP for both hashes,
all three protocol versions, branches/tags, subsequent fetches and empty clones.
They cover private/missing concealment before any storage lookup, Basic/Bearer
tokens, expiry/revocation, malformed/bounded bodies, absent raw-object routes,
admission exhaustion and disconnect cleanup without background exception leaks.
An empty-PATH fixture forbids subprocess/shell creation in the HTTP implementation.
