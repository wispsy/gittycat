"""Scoped personal tokens and a fresh authentication boundary per operation."""

from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import uuid

from .catalog import CredentialMetadata, timestamp
from .domain import Action, Principal
from .permissions import PermissionDenied


_TOKEN = re.compile(r"gtc_([0-9a-f]{32})_[A-Za-z0-9_-]{43}")
_DUMMY_DIGEST = bytes(32)


class AuthenticationError(PermissionDenied):
    """Malformed, unknown, expired and revoked tokens share one safe error."""


class IssuedToken:
    """An explicit, one-time secret delivery; default representations are safe."""

    __slots__ = ("metadata", "scopes", "_secret")

    def __init__(self, metadata, scopes, secret):
        self.metadata, self.scopes, self._secret = metadata, scopes, secret

    def __repr__(self):
        return f"IssuedToken(id={self.metadata.id!r}, secret=<redacted>)"

    def reveal(self) -> str:
        if self._secret is None:
            raise ValueError("token secret has already been delivered")
        secret, self._secret = self._secret, None
        return secret

    def __reduce_ex__(self, protocol):
        raise TypeError("issued token secrets cannot be serialized")


class TokenAuth:
    """Use request() for one transaction spanning authentication and access.

    A returned Principal is a request snapshot, never a reusable session.
    Bootstrap/token issuance requires a trusted local account principal.
    """

    def __init__(self, catalog):
        self.catalog = catalog

    def issue(self, actor: Principal, *, scopes, expires_at: int, now: int) -> IssuedToken:
        timestamp(now)
        timestamp(expires_at)
        if expires_at <= now:
            raise ValueError("token expiry must follow issuance")
        if isinstance(scopes, str):
            raise ValueError("token scopes must be a nonempty collection of actions")
        scopes = frozenset(Action(scope) for scope in scopes)
        if not scopes:
            raise ValueError("tokens require at least one scope")
        if actor.user_id is None or actor.scopes is not None or actor.credential_id is not None:
            raise PermissionDenied("token issuance requires a trusted local account principal")
        metadata = CredentialMetadata(uuid.uuid4().hex, actor.user_id, now, expires_at)
        secret = f"gtc_{metadata.id}_{secrets.token_urlsafe(32)}"
        digest = hashlib.sha256(secret.encode("ascii")).digest()
        with self.catalog.transaction():
            self.catalog.store_credential(actor, metadata, digest)
            self.catalog._connection.executemany("INSERT INTO credential_scopes VALUES(?,?)",
                                                ((metadata.id, scope.value) for scope in sorted(scopes)))
        return IssuedToken(metadata, scopes, secret)

    def authenticate(self, token: str, *, now: int) -> Principal:
        timestamp(now)
        match = _TOKEN.fullmatch(token) if isinstance(token, str) and len(token) == 80 else None
        candidate = hashlib.sha256(token.encode("ascii") if match else b"").digest()
        with self.catalog._snapshot():
            data = self.catalog._credential_auth_data(match[1]) if match else None
            # Compare fixed-size digests even for unknown/malformed credentials.
            matches = hmac.compare_digest(candidate, data[1] if data else _DUMMY_DIGEST)
            if data is not None:
                metadata, _, scopes = data
                if matches and scopes and metadata.revoked_at is None and metadata.expires_at is not None and metadata.created_at <= now < metadata.expires_at:
                    return Principal(metadata.user_id, scopes, metadata.id)
        raise AuthenticationError("invalid or inactive credential")

    @contextmanager
    def request(self, token: str, *, now: int):
        """Serialize revocation with a single bounded authorized operation."""
        with self.catalog.transaction():
            yield self.authenticate(token, now=now)

    def repository(self, token: str, repository_id: str, *, action=Action.READ, now: int):
        with self.request(token, now=now) as actor:
            return self.catalog.get_repository(actor, repository_id, action=action)

    def repositories(self, token: str, *, limit=50, after="", now: int):
        with self.request(token, now=now) as actor:
            return self.catalog.list_repositories(actor, limit=limit, after=after)

    def revoke(self, token: str, credential_id: str, *, now: int):
        with self.request(token, now=now) as actor:
            self.catalog.revoke_credential(actor, credential_id, now=now)
