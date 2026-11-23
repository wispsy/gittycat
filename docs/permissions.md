# Domain and permission contracts

```python
from gittycat import (
    Action, Owner, OwnerKind, Principal, RepositoryRecord, Slug, StorageId,
    Visibility, allowed,
)

owner = Owner("1" * 32, OwnerKind.USER)
repo = RepositoryRecord("2" * 32, owner, Slug("example"), StorageId.new(),
                        visibility=Visibility.PUBLIC)
assert allowed(Principal(), repo, Action.READ)
assert not allowed(Principal(), repo, Action.WRITE)
```

All records are immutable. Account, organization and repository IDs use
canonical lowercase UUID hex strings. `Principal()` explicitly means anonymous.
Repository visibility defaults to private. Native object-format metadata is
`sha1` or `sha256`; Git algorithms and actual compatibility checks belong to
the Gitdx backend. `GitdxBackend` implements `RepositoryBackend` for managed
bare repositories; see [provisioning](provisioning.md).

`StorageId` is independent of the URL slug, so a rename need not move Git data.
It contains no path components. Slugs are a conservative 1–63 character
lowercase ASCII subset with digits/dashes/underscores; they start and end with
letters/digits. Dots, escapes and platform endpoint names are refused. These
values do not themselves authorize a filesystem operation or route.

| Caller | Public read | Private read | Write/admin |
| --- | --- | --- | --- |
| Anonymous | Yes | No | No |
| Unrelated authenticated user | Yes | No | No |
| Personal repository owner | Yes | Yes | Both |
| Organization owner | Yes | Yes | Both for that organization |
| Organization member | Yes | Only with an explicit repository grant | Explicit grant required |
| Explicit repository collaborator | Yes | READ, WRITE or ADMIN grant | WRITE permits read/write; ADMIN permits all |

Membership alone grants no private access to ordinary organization members.
An explicit repository grant can also designate an external collaborator.
Removing membership removes implicit organization-owner rights; remove explicit
grants separately when revoking collaborator access. These functions consume an
already established principal and authoritative grant/membership snapshot.
[Token authentication](authentication.md) produces a scoped principal. A read
scope permits read; write permits read/write; admin permits all three actions.
Scopes cap the existing permissions, including owner/public access; they never
add a grant. An empty scope set permits no action. `scopes=None` is reserved for
trusted local identities and anonymous public reads.

`allowed` evaluates read/write/admin. `require_permission` raises a generic
concealed denial. `visible_repositories` uses the exact same read policy, including
when grants/memberships are one-shot iterators. Public visibility never permits
mutation. Branch protection later constrains authorized writes further.

Catalog and repository services apply these checks before storage access.
Future management and Git routes must enter the same per-request authentication
boundary; network endpoints and server operation remain later milestones.
