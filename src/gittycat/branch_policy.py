"""Immutable, bounded protection rules; the catalog never stores branch tips."""

from dataclasses import dataclass

from gitdx.names import validate_refname

from .domain import Permission, identifier


MAX_BRANCH_RULES = 128


def ref_pattern(value):
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("ref patterns must be strings of at most 1024 characters")
    candidate = value[:-1] + "example" if value.endswith("/*") else value
    validate_refname(candidate)
    if not candidate.startswith(("refs/heads/", "refs/tags/")):
        raise ValueError("protection rules select branches or tags")
    return value


@dataclass(frozen=True, slots=True)
class BranchRule:
    repository_id: str
    pattern: str
    allow_create: bool = True
    allow_delete: bool = False
    allow_force: bool = False
    require_review: bool = False
    minimum_permission: Permission = Permission.WRITE

    def __post_init__(self):
        identifier(self.repository_id); ref_pattern(self.pattern)
        for value in (self.allow_create, self.allow_delete, self.allow_force, self.require_review):
            if type(value) is not bool:raise TypeError("rule switches must be booleans")
        object.__setattr__(self, "minimum_permission", Permission(self.minimum_permission))
        if self.minimum_permission not in (Permission.WRITE, Permission.ADMIN):
            raise ValueError("rules can require write or administrator permission")

    def matches(self, name):
        return name.startswith(self.pattern[:-1]) if self.pattern.endswith("/*") else name == self.pattern


@dataclass(frozen=True, slots=True)
class BranchPolicy:
    repository_id: str
    version: int
    rules: tuple[BranchRule, ...]
