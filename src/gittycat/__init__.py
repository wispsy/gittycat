"""Git hosting domain and explicit permission contracts."""

from .backend import RepositoryBackend, RepositoryInfo
from .domain import AccessGrant, Action, Membership, OrganizationRole, Owner, OwnerKind, Permission, Principal, RepositoryRecord, Slug, StorageId, Visibility
from .permissions import PermissionDenied, allowed, require_permission, visible_repositories

__all__ = ["AccessGrant", "Action", "Membership", "OrganizationRole", "Owner", "OwnerKind",
           "Permission", "Principal", "RepositoryRecord", "Slug", "StorageId", "Visibility",
           "RepositoryBackend", "RepositoryInfo", "PermissionDenied", "allowed",
           "require_permission", "visible_repositories"]


def main() -> None:
    from .cli import main as run
    raise SystemExit(run())
