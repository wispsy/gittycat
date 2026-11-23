"""Git hosting domain and explicit permission contracts."""

from .backend import RepositoryBackend, RepositoryInfo
from .domain import AccessGrant, Action, Membership, OrganizationRole, Owner, OwnerKind, Permission, Principal, RepositoryRecord, Slug, StorageId, Visibility
from .permissions import PermissionDenied, allowed, require_permission, require_scope, scope_allows, visible_repositories
from .catalog import AuditEvent, Catalog, CatalogConflict, CatalogError, CredentialMetadata, EventKind, OutboxJob
from .provisioning import GitdxBackend, HostedRepository, ProvisioningError, RepositoryService
from .authentication import AuthenticationError, IssuedToken, TokenAuth

__all__ = ["AccessGrant", "Action", "Membership", "OrganizationRole", "Owner", "OwnerKind",
           "Permission", "Principal", "RepositoryRecord", "Slug", "StorageId", "Visibility",
           "RepositoryBackend", "RepositoryInfo", "PermissionDenied", "allowed",
           "require_permission", "visible_repositories"]
__all__ += ["AuditEvent", "Catalog", "CatalogConflict", "CatalogError", "CredentialMetadata", "EventKind", "OutboxJob"]
__all__ += ["GitdxBackend", "HostedRepository", "ProvisioningError", "RepositoryService"]
__all__ += ["AuthenticationError", "IssuedToken", "TokenAuth", "require_scope", "scope_allows"]


def main() -> None:
    from .cli import main as run
    raise SystemExit(run())
