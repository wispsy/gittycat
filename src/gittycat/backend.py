"""Hosting-facing backend boundary. Gitdx supplies the concrete implementation."""

from dataclasses import dataclass
from typing import Protocol

from .domain import StorageId


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
