"""Git hosting domain and explicit permission contracts."""

from .backend import BackendMismatch, RepositoryBackend, RepositoryInfo, REQUIRED_CAPABILITIES
from .domain import AccessGrant, Action, Membership, OrganizationRole, Owner, OwnerKind, Permission, Principal, RepositoryRecord, Slug, StorageId, Visibility
from .permissions import PermissionDenied, allowed, require_permission, require_scope, scope_allows, visible_repositories
from .catalog import AuditEvent, Catalog, CatalogConflict, CatalogError, CredentialMetadata, EventKind, OutboxJob
from .provisioning import GitdxBackend, HostedRepository, ProvisioningError, RepositoryService
from .authentication import AuthenticationError, IssuedToken, TokenAuth
from .branch_policy import BranchPolicy, BranchRule
from .ref_policy import RefDecision, RefPolicyResult, RefPolicyService, RefReason

__all__ = ["AccessGrant", "Action", "Membership", "OrganizationRole", "Owner", "OwnerKind",
           "Permission", "Principal", "RepositoryRecord", "Slug", "StorageId", "Visibility",
           "RepositoryBackend", "RepositoryInfo", "PermissionDenied", "allowed",
           "require_permission", "visible_repositories"]
__all__ += ["AuditEvent", "Catalog", "CatalogConflict", "CatalogError", "CredentialMetadata", "EventKind", "OutboxJob"]
__all__ += ["GitdxBackend", "HostedRepository", "ProvisioningError", "RepositoryService"]
__all__ += ["AuthenticationError", "IssuedToken", "TokenAuth", "require_scope", "scope_allows"]
__all__ += ["BackendMismatch", "REQUIRED_CAPABILITIES"]
__all__ += ["BranchPolicy", "BranchRule", "RefDecision", "RefPolicyResult", "RefPolicyService", "RefReason"]
__all__ += ["create_app"]


def create_app(*args, **kwargs):
    """Load the optional HTTP adapter only when explicitly selected."""
    from .management import create_app as factory
    return factory(*args, **kwargs)


def main() -> None:
    from .cli import main as run
    raise SystemExit(run())
