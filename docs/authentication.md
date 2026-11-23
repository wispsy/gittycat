# Personal token authentication

`TokenAuth(catalog)` authenticates high-entropy personal tokens. Bootstrap an
account and issue tokens only through a trusted local catalog handle; no
unauthenticated account-creation, password-login or token-issuance endpoint is
provided. Existing account/organization IDs, memberships and grants remain the
authority for repository access.

```python
from gittycat import Action, Catalog, Principal, Slug, TokenAuth

with Catalog("data/gittycat.sqlite3") as catalog:
    account_id = "1" * 32
    catalog.create_account(account_id, Slug("example-user"), now=1700000000)
    auth = TokenAuth(catalog)
    issued = auth.issue(Principal(account_id), scopes={Action.READ},
                        expires_at=1700086400, now=1700000000)
    token = issued.reveal()  # Deliver securely once; never log this value.
    assert auth.repositories(token, now=1700000010) == ()
    catalog.revoke_credential(Principal(account_id), issued.metadata.id,
                              now=1700000020)
```

Issuance uses 32 explicitly selected random bytes from Python's `secrets`
module, with a non-secret UUID selector in `gtc_<id>_<secret>` format. SQLite
stores only the SHA-256 digest of the complete token, metadata and scope rows.
This fast verifier is for generated 256-bit secrets, not human passwords.
Authentication compares fixed-size digests with `hmac.compare_digest`, including
a dummy comparison for malformed/unknown credentials. The complete request is
not claimed to have uniform timing.

`IssuedToken` redacts its default representations, refuses serialization and
allows one explicit `reveal()` call. Metadata, principals, audit and outbox
records contain no token or verifier. There is no secret retrieval API. Callers
must protect the returned string, exclude it from URLs/logs/exceptions and use a
protected transport when future network routes are added. Losing the token
requires revocation and issuance of another; Python strings cannot be reliably
zeroed in memory.

Expiry is mandatory: the token is valid only for `created_at <= now < expires_at`
and while `revoked_at` is absent. Callers supply a trusted current UTC Unix
timestamp per operation; request data must never control the clock. Revocation
is permanent and idempotent, becomes effective for the next request, and requires
the same account with admin scope or a trusted local account principal.
Tokens cannot issue child tokens, including with admin scope.

Read/write/admin scopes form a hierarchy. They cap current owner, organization
owner and collaborator permissions; an admin token for an unrelated account
still cannot discover a private repository. Ordinary organization members have
no implicit private access. Demoting an organization owner removes implicit
rights; explicit grants are separately revoked. Each operation rereads current
membership/grant state. A read token cannot create repositories, change grants
or memberships, recover provisioning, or revoke tokens.

`repository()`, `repositories()` and `revoke()` authenticate freshly and apply
the shared policy within one SQLite transaction. For a composed service call:

```python
with auth.request(token, now=current_unix_timestamp) as principal:
    hosted = repository_service.open(principal, repository_id)
```

The boundary serializes authorization with local revocation/grant changes and
keeps denials ahead of filesystem lookup. Use one connection per thread and
keep requests bounded; do not stream long network responses while holding the
transaction. `authenticate()` alone returns a snapshot for immediate use, not
a session. Never cache or reuse that principal across requests. Services taking
raw principals are trusted composition APIs, not an authentication substitute.

Malformed, unknown, expired, future-created and revoked credentials all raise
`AuthenticationError("invalid or inactive credential")`, a `PermissionDenied`
subclass. Invalid presented credentials never fall back to anonymous access.
Valid credentials without access receive the same concealed repository denial
as a missing repository. Anonymous public reads remain explicit via
`Principal()`; credentials are never inferred from URL slugs.

The [management API](management-api.md) now uses this boundary for Bearer
credential extraction and JSON control routes. Git protocol routes, rate limits,
interactive login and public-hosting qualification remain separate tasks. Python's
[secrets](https://docs.python.org/3/library/secrets.html) and
[digest-comparison](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)
documentation describe the standard-library primitives used here.
