"""Immutable platform records, separate from Git object and ref authority."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
import re
import uuid


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("identifiers must be canonical lowercase UUID hex strings")
    return value


@dataclass(frozen=True, slots=True)
class StorageId:
    value: str

    def __post_init__(self) -> None:
        identifier(self.value)

    @classmethod
    def new(cls) -> StorageId:
        return cls(uuid.uuid4().hex)

    def __str__(self) -> str:
        return self.value


RESERVED_SLUGS = frozenset(("api", "admin", "login", "logout", "settings", "new", "git",
                            "assets", "static", "health", "info", "git-upload-pack", "git-receive-pack"))


@dataclass(frozen=True, slots=True)
class Slug:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?", self.value):
            raise ValueError("slugs must contain 1–63 lowercase ASCII letters, digits, underscores or dashes and start/end with a letter or digit")
        if self.value in RESERVED_SLUGS:
            raise ValueError("slug is reserved for a platform endpoint")

    def __str__(self) -> str:
        return self.value


class OwnerKind(StrEnum):
    USER = "user"
    ORGANIZATION = "organization"


class Visibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class Permission(IntEnum):
    READ = 1
    WRITE = 2
    ADMIN = 3


class Action(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class OrganizationRole(StrEnum):
    MEMBER = "member"
    OWNER = "owner"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str | None = None

    def __post_init__(self) -> None:
        if self.user_id is not None:
            identifier(self.user_id)


@dataclass(frozen=True, slots=True)
class Owner:
    id: str
    kind: OwnerKind

    def __post_init__(self) -> None:
        identifier(self.id)
        object.__setattr__(self, "kind", OwnerKind(self.kind))


@dataclass(frozen=True, slots=True)
class RepositoryRecord:
    id: str
    owner: Owner
    slug: Slug
    storage_id: StorageId
    visibility: Visibility = Visibility.PRIVATE
    object_format: str = "sha1"

    def __post_init__(self) -> None:
        identifier(self.id)
        if not isinstance(self.owner, Owner) or not isinstance(self.slug, Slug) or not isinstance(self.storage_id, StorageId):
            raise TypeError("repository owner, slug and storage ID must be typed records")
        object.__setattr__(self, "visibility", Visibility(self.visibility))
        if self.object_format not in ("sha1", "sha256"):
            raise ValueError("only native sha1/sha256 metadata is supported")


@dataclass(frozen=True, slots=True)
class AccessGrant:
    repository_id: str
    user_id: str
    permission: Permission

    def __post_init__(self) -> None:
        identifier(self.repository_id)
        identifier(self.user_id)
        if type(self.permission) is not int and not isinstance(self.permission, Permission):
            raise ValueError("permission must be a declared permission level")
        object.__setattr__(self, "permission", Permission(self.permission))


@dataclass(frozen=True, slots=True)
class Membership:
    organization_id: str
    user_id: str
    role: OrganizationRole = OrganizationRole.MEMBER

    def __post_init__(self) -> None:
        identifier(self.organization_id)
        identifier(self.user_id)
        object.__setattr__(self, "role", OrganizationRole(self.role))
