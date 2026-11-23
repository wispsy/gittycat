"""Hosting-facing backend boundary. Gitdx supplies the concrete implementation."""

from dataclasses import dataclass
from typing import Protocol

from .domain import StorageId


REQUIRED_CAPABILITIES = frozenset({"objects.read", "objects.write", "refs.read", "refs.update"})


class BackendMismatch(Exception):
    """A selected provider cannot satisfy the qualified hosting profile."""


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    object_format: str
    bare: bool
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


class RepositoryBackend(Protocol):
    """No branch/object shadow state; operations delegate to the engine."""

    def create(self, storage_id: StorageId, *, object_format: str) -> RepositoryInfo: ...

    def inspect(self, storage_id: StorageId) -> RepositoryInfo: ...


def require_backend_info(info, object_format):
    if (not isinstance(info, RepositoryInfo) or info.bare is not True
            or info.object_format != object_format or not REQUIRED_CAPABILITIES <= info.capabilities):
        raise BackendMismatch("backend does not satisfy the managed repository profile")
    return info
