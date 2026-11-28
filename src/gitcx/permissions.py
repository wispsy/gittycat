"""One deny-by-default policy for discovery and repository operations."""

from collections.abc import Iterable

from .domain import AccessGrant, Action, Membership, OrganizationRole, OwnerKind, Permission, Principal, RepositoryRecord, Visibility


class PermissionDenied(Exception):
    """Use a concealed result when a caller cannot access a repository."""


def scope_allows(principal: Principal, action: Action) -> bool:
    action = Action(action)
    levels = {Action.READ: 1, Action.WRITE: 2, Action.ADMIN: 3}
    return principal.scopes is None or any(levels[scope] >= levels[action] for scope in principal.scopes)


def require_scope(principal: Principal, action: Action) -> None:
    if not scope_allows(principal, action):
        raise PermissionDenied("repository not found or permission denied")


def allowed(principal: Principal, repository: RepositoryRecord, action: Action, *,
            grants: Iterable[AccessGrant] = (), memberships: Iterable[Membership] = ()) -> bool:
    action = Action(action)
    if not scope_allows(principal, action):
        return False
    if action is Action.READ and repository.visibility is Visibility.PUBLIC:
        return True
    user = principal.user_id
    if user is None:
        return False
    if repository.owner.kind is OwnerKind.USER and repository.owner.id == user:
        return True
    if repository.owner.kind is OwnerKind.ORGANIZATION:
        if any(m.organization_id == repository.owner.id and m.user_id == user
               and m.role is OrganizationRole.OWNER for m in memberships):
            return True
    required = {Action.READ: Permission.READ, Action.WRITE: Permission.WRITE, Action.ADMIN: Permission.ADMIN}[action]
    return any(g.repository_id == repository.id and g.user_id == user and g.permission >= required for g in grants)


def require_permission(principal: Principal, repository: RepositoryRecord, action: Action, *,
                       grants: Iterable[AccessGrant] = (), memberships: Iterable[Membership] = ()) -> None:
    if not allowed(principal, repository, action, grants=grants, memberships=memberships):
        raise PermissionDenied("repository not found or permission denied")


def visible_repositories(principal: Principal, repositories: Iterable[RepositoryRecord], *,
                         grants: Iterable[AccessGrant] = (), memberships: Iterable[Membership] = ()) -> tuple[RepositoryRecord, ...]:
    grants, memberships = tuple(grants), tuple(memberships)
    return tuple(r for r in repositories if allowed(principal, r, Action.READ, grants=grants, memberships=memberships))
