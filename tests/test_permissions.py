from dataclasses import FrozenInstanceError, replace
import unittest

from gittycat import AccessGrant, Action, Membership, OrganizationRole, Owner, OwnerKind, Permission, PermissionDenied, Principal, RepositoryRecord, Slug, StorageId, Visibility, allowed, require_permission, visible_repositories


OWNER = "1" * 32
READER = "2" * 32
WRITER = "3" * 32
OUTSIDER = "4" * 32
ORG = "5" * 32
REPO = "6" * 32


def repository(**changes):
    record = RepositoryRecord(REPO, Owner(OWNER, OwnerKind.USER), Slug("example"), StorageId("a" * 32))
    return replace(record, **changes)


class PermissionTests(unittest.TestCase):
    def test_anonymous_only_reads_public(self):
        for visibility in Visibility:
            repo = repository(visibility=visibility)
            for action in Action:
                with self.subTest(visibility=visibility, action=action):
                    self.assertEqual(allowed(Principal(), repo, action), visibility is Visibility.PUBLIC and action is Action.READ)

    def test_user_owner_admin_and_outsider_denied(self):
        for visibility in Visibility:
            repo = repository(visibility=visibility)
            for action in Action:
                self.assertTrue(allowed(Principal(OWNER), repo, action))
                self.assertEqual(allowed(Principal(OUTSIDER), repo, action), visibility is Visibility.PUBLIC and action is Action.READ)

    def test_grant_hierarchy_for_two_authorized_users(self):
        repo = repository()
        grants = (AccessGrant(REPO, READER, Permission.READ), AccessGrant(REPO, WRITER, Permission.WRITE))
        for principal, outcomes in ((Principal(READER), (True, False, False)),
                                    (Principal(WRITER), (True, True, False)),
                                    (Principal(OUTSIDER), (False, False, False)),
                                    (Principal(), (False, False, False))):
            for action, expected in zip(Action, outcomes):
                self.assertEqual(allowed(principal, repo, action, grants=grants), expected)
        admin = (AccessGrant(REPO, WRITER, Permission.ADMIN),)
        self.assertTrue(all(allowed(Principal(WRITER), repo, a, grants=admin) for a in Action))

    def test_other_repository_grants_do_not_apply(self):
        grant = AccessGrant("9" * 32, OUTSIDER, Permission.ADMIN)
        self.assertFalse(allowed(Principal(OUTSIDER), repository(), Action.READ, grants=[grant]))

    def test_organization_owners_and_explicit_membership_policy(self):
        repo = repository(owner=Owner(ORG, OwnerKind.ORGANIZATION))
        memberships = (Membership(ORG, OWNER, OrganizationRole.OWNER), Membership(ORG, READER))
        for action in Action:
            self.assertTrue(allowed(Principal(OWNER), repo, action, memberships=memberships))
            self.assertFalse(allowed(Principal(READER), repo, action, memberships=memberships))
        grants = (AccessGrant(REPO, READER, Permission.READ),)
        self.assertTrue(allowed(Principal(READER), repo, Action.READ, memberships=memberships, grants=grants))
        self.assertFalse(allowed(Principal(OWNER), repo, Action.ADMIN, memberships=()))

    def test_memberships_cannot_cross_owner_namespace(self):
        memberships = (Membership(ORG, OUTSIDER, OrganizationRole.OWNER),)
        self.assertFalse(allowed(Principal(OUTSIDER), repository(owner=Owner(ORG, OwnerKind.USER)), Action.ADMIN, memberships=memberships))
        self.assertFalse(allowed(Principal(OUTSIDER), repository(owner=Owner("8" * 32, OwnerKind.ORGANIZATION)), Action.ADMIN, memberships=memberships))

    def test_discovery_and_read_use_identical_policy_even_with_generators(self):
        private = repository()
        public = repository(id="7" * 32, visibility=Visibility.PUBLIC)
        another = repository(id="8" * 32, slug=Slug("another"))
        records = (private, public, another)
        grants = (AccessGrant(REPO, READER, Permission.READ), AccessGrant(another.id, READER, Permission.READ))
        self.assertEqual(visible_repositories(Principal(OUTSIDER), records), (public,))
        self.assertEqual(visible_repositories(Principal(READER), iter(records), grants=iter(grants)), records)
        for record in records:
            self.assertEqual(record in visible_repositories(Principal(OUTSIDER), records),
                             allowed(Principal(OUTSIDER), record, Action.READ))

    def test_revocation_and_concealed_denial(self):
        repo = repository()
        grant = AccessGrant(REPO, WRITER, Permission.WRITE)
        require_permission(Principal(WRITER), repo, Action.WRITE, grants=[grant])
        with self.assertRaisesRegex(PermissionDenied, "repository not found or permission denied") as error:
            require_permission(Principal(WRITER), repo, Action.WRITE)
        self.assertNotIn(repo.id, str(error.exception))
        self.assertNotIn(repo.slug.value, str(error.exception))

    def test_unknown_actions_never_default_to_access(self):
        with self.assertRaises(ValueError):
            allowed(Principal(OWNER), repository(), "unknown")

    def test_records_are_immutable(self):
        repo = repository()
        with self.assertRaises(FrozenInstanceError):
            repo.visibility = Visibility.PUBLIC
