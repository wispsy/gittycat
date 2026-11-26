"""Read-only ref-change decisions for freshly authorized repository writers."""

from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import islice

from gitdx import ObjectType, PushCommand, RevisionLimits, parse_object
from gitdx.errors import GitdxError
from gitdx.names import validate_refname
from gitdx.refs import DirectRef

from .authentication import TokenAuth
from .domain import Action, Permission, Principal
from .permissions import PermissionDenied


class RefReason(StrEnum):
    ALLOWED = "allowed"
    UNSUPPORTED_REF = "unsupported-ref"
    STALE_TIP = "stale-tip"
    INVALID_TARGET = "invalid-target"
    CREATE_DENIED = "creation-protected"
    DELETE_DENIED = "deletion-protected"
    FORCE_DENIED = "force-protected"
    ADMIN_REQUIRED = "administrator-required"
    REVIEW_REQUIRED = "review-required"
    POLICY_CHANGED = "policy-changed"


@dataclass(frozen=True, slots=True)
class RefDecision:
    command: PushCommand
    reason: RefReason
    fast_forward: bool | None = None
    rules: tuple[str, ...] = ()

    @property
    def allowed(self):return self.reason is RefReason.ALLOWED


@dataclass(frozen=True, slots=True)
class RefPolicyResult:
    repository_id: str
    actor: Principal
    policy_version: int
    decisions: tuple[RefDecision, ...]

    @property
    def allowed(self):return all(item.allowed for item in self.decisions)


class RefPolicyService:
    """Decisions are snapshots, never authority to write refs later.

    This trusted Python boundary accepts principals, not user-supplied identity
    records. HTTP callers must use TokenAuth.request() first. Evaluation holds a
    catalog transaction, refreshes credentials/grants and reads managed storage;
    native writers can still change refs, so publication must validate again.
    """

    def __init__(self, catalog, backend, *, max_commands=256, ancestry_limits=None):
        if type(max_commands) is not int or not 1 <= max_commands <= 256:
            raise ValueError("policy batches contain at most 256 ref commands")
        self.catalog, self.backend = catalog, backend
        self.max_commands = max_commands
        self.ancestry_limits = ancestry_limits or RevisionLimits()

    def evaluate(self, actor: Principal, repository_id: str, commands, *, now: int) -> RefPolicyResult:
        commands = tuple(islice(commands, self.max_commands + 1))
        if not commands or len(commands) > self.max_commands:raise ValueError("empty or excessive ref command batch")
        if any(not isinstance(c, PushCommand) for c in commands):raise TypeError("select gitdx PushCommand records")
        if len({c.name for c in commands}) != len(commands):raise ValueError("duplicate ref commands")
        with self.catalog.transaction():
            actor = TokenAuth(self.catalog).revalidate(actor, now=now)
            record = self.catalog.get_repository(actor, repository_id, action=Action.WRITE)
            if self.catalog.provisioning_state(repository_id) != "ready":raise PermissionDenied("repository not found or permission denied")
            policy = self.catalog.branch_policy(actor, repository_id)
            try:self.catalog.get_repository(actor, repository_id, action=Action.ADMIN)
            except PermissionDenied:administrator = False
            else:administrator = True
            repo = self.backend.open_repository(record.storage_id)
            if repo.object_format.value != record.object_format:raise ValueError("managed storage format mismatch")
            decisions = tuple(self._decide(repo, c, policy.rules, administrator) for c in commands)
            return RefPolicyResult(repository_id, actor, policy.version, decisions)

    def recheck(self, result: RefPolicyResult, *, now: int) -> RefPolicyResult:
        if not isinstance(result, RefPolicyResult):raise TypeError("select a ref policy result")
        current = self.evaluate(result.actor, result.repository_id, (d.command for d in result.decisions), now=now)
        if current.policy_version != result.policy_version:
            return replace(current, decisions=tuple(replace(d, reason=RefReason.POLICY_CHANGED) for d in current.decisions))
        return current

    def _decide(self, repo, command, rules, administrator):
        def decision(reason, ff=None):return RefDecision(command, reason, ff, tuple(r.pattern for r in selected))
        selected = ()
        try:
            name = command.name.decode("ascii")
            validate_refname(name)
            if not name.startswith(("refs/heads/", "refs/tags/")):return decision(RefReason.UNSUPPORTED_REF)
            if len(command.name) > 1024:return decision(RefReason.UNSUPPORTED_REF)
        except (ValueError, UnicodeError):return decision(RefReason.UNSUPPORTED_REF)
        selected = tuple(rule for rule in rules if rule.matches(name))
        try:
            actual = repo.refs.read(name)
            if actual is not None and not isinstance(actual, DirectRef):return decision(RefReason.UNSUPPORTED_REF)
            if (actual.oid if actual else None) != command.old:return decision(RefReason.STALE_TIP)
            if any(oid is not None and oid.format != repo.object_format for oid in (command.old, command.new)):
                return decision(RefReason.INVALID_TARGET)
            ff = None
            if command.new is not None:
                raw = repo.objects.read(command.new);parse_object(raw, repo.object_format)
                if name.startswith("refs/heads/"):
                    if raw.kind is not ObjectType.COMMIT:return decision(RefReason.INVALID_TARGET)
                    # Graph errors and exhausted limits are refusals, not proof
                    # of a non-fast-forward update. All parents are validated.
                    ff = repo.is_ancestor(command.old or command.new, command.new, limits=self.ancestry_limits)
                elif command.old is not None:ff = command.old == command.new
        except (GitdxError, OSError):return decision(RefReason.INVALID_TARGET)
        if command.old == command.new:return decision(RefReason.ALLOWED, ff)
        if any(r.minimum_permission is Permission.ADMIN for r in selected) and not administrator:
            return decision(RefReason.ADMIN_REQUIRED, ff)
        if command.old is None and any(not r.allow_create for r in selected):return decision(RefReason.CREATE_DENIED, ff)
        if command.new is None and any(not r.allow_delete for r in selected):return decision(RefReason.DELETE_DENIED, ff)
        if command.old is not None and command.new is not None and not ff and any(not r.allow_force for r in selected):
            return decision(RefReason.FORCE_DENIED, ff)
        if any(r.require_review for r in selected):return decision(RefReason.REVIEW_REQUIRED, ff)
        return decision(RefReason.ALLOWED, ff)
