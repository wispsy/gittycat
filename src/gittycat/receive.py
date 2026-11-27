"""Authenticated SHA-256 hosted receive with explicit cross-store outcomes."""

from contextlib import contextmanager
from dataclasses import dataclass
import time

from gitdx import (Advertisement, AdvertisedRef, Capability, CommandStatus, DirectRef, FLUSH,
                   IngestionProfile, Packet, ProtocolLimits, ReceiveInterrupted, ReceiveLimits,
                   ReceivePolicy, ReceiveRejected, ReceiveState, ReportStatus, TransferLimits,
                   UnsupportedFormat, encode_advertisement, encode_packet, encode_report_status,
                   parse_push_request)
from gitdx.confined_fs import ConfinedStorage, supported

from .authentication import TokenAuth
from .catalog import EventKind
from .domain import Action
from .permissions import PermissionDenied
from .ref_policy import RefPolicyService


def default_receive_limits():
    return ReceiveLimits(transfer=TransferLimits(max_refs=128, max_wire_bytes=16*1024*1024))


def require_write_profile(record):
    if record.object_format != 'sha256':
        raise UnsupportedFormat('hosted receive requires native SHA-256 storage')
    if not supported():
        raise UnsupportedFormat('hosted receive requires the qualified Linux descriptor profile')


def preflight_receive(record, repo, limits):
    require_write_profile(record)
    if repo.object_format.value != record.object_format or not repo.bare:
        raise UnsupportedFormat('hosted receive storage format mismatch')
    # This creates no quarantine and runs before the host reads request bytes.
    with ConfinedStorage(repo.git_dir, repo.storage_identity, limits=limits.filesystem): pass


@dataclass(frozen=True, slots=True)
class HostedReceiveResult:
    operation_id: str
    result: object
    wire: bytes


class ReceiveService:
    def __init__(self, catalog, backend, *, limits=None, clock=time.time):
        self.catalog = catalog;self.backend = backend
        self.limits = limits or default_receive_limits();self.clock = clock
        if not isinstance(self.limits, ReceiveLimits): raise TypeError('select ReceiveLimits')
        self.protocol = ProtocolLimits(max_bytes=self.limits.transfer.max_wire_bytes,
            max_refs=min(256, self.limits.transfer.max_refs), max_name_bytes=1024,
            timeout=self.limits.transfer.max_seconds)

    def _authorized(self, actor, repository_id, *, action=Action.WRITE):
        actor = TokenAuth(self.catalog).revalidate(actor, now=int(self.clock()))
        record = self.catalog.get_repository(actor, repository_id, action=action)
        if self.catalog.provisioning_state(record.id) != 'ready':
            raise PermissionDenied('repository not found or permission denied')
        require_write_profile(record)
        return actor, record

    def _advertisement(self, repo):
        caps = (Capability(b'object-format', b'sha256'), Capability(b'report-status'),
                Capability(b'report-status-v2'), Capability(b'ofs-delta'), Capability(b'no-thin'), Capability(b'delete-refs'))
        refs = []
        for name, value in repo.refs.snapshot(max_refs=self.protocol.max_refs):
            if not name.startswith(('refs/heads/', 'refs/tags/')): continue
            if not isinstance(value, DirectRef): raise UnsupportedFormat('hosted receive refuses symbolic destinations')
            refs.append(AdvertisedRef(name.encode('ascii'), value.oid))
        return Advertisement(0, repo.object_format, caps, tuple(refs))

    def advertise(self, actor, repository_id):
        with self.catalog.transaction():
            actor, record = self._authorized(actor, repository_id)
            repo = self.backend.open_repository(record.storage_id)
            preflight_receive(record, repo, self.limits)
            data = encode_advertisement(self._advertisement(repo), limits=self.protocol)
            self._authorized(actor, repository_id)
        return encode_packet(Packet(data=b'# service=git-receive-pack\n'))+encode_packet(FLUSH)+data

    def receive(self, actor, repository_id, body, *, cancel=None):
        with self.catalog.transaction():
            actor, record = self._authorized(actor, repository_id)
            repo = self.backend.open_repository(record.storage_id)
            preflight_receive(record, repo, self.limits)
            request = parse_push_request(body, self._advertisement(repo), repo.object_format, limits=self.protocol)
            version = self.catalog.branch_policy(actor, record.id).version
            operation = self.catalog._prepare_receive(actor, record, request.commands, now=int(self.clock()))
        policy_service = RefPolicyService(self.catalog, self.backend, max_commands=self.protocol.max_refs)

        def ref_check(view, command):
            result = policy_service.evaluate_receive(actor, record, repo, (command,), view,
                expected_version=version, now=int(self.clock()))
            decision = result.decisions[0]
            return None if decision.allowed else decision.reason.value.encode('ascii')

        @contextmanager
        def publication_guard(view, commands):
            # Catalog changes commit wholly before or after this publication.
            # Engine lock order is catalog, then deterministic Git ref locks.
            with self.catalog.transaction():
                current_actor, current = self._authorized(actor, record.id)
                if current != record or self.catalog.branch_policy(current_actor, record.id).version != version:
                    raise PermissionDenied('repository policy changed before publication')
                yield

        policy = ReceivePolicy(allow_deletes=True, allow_non_fast_forwards=True, allow_tag_replacement=True,
            ref_check=ref_check, ingestion=IngestionProfile.UNTRUSTED, publication_guard=publication_guard)
        try:
            result = repo.receive(request.commands, request.pack, policy=policy, limits=self.limits,
                                  cancel=cancel, coordinated=True)
        except (ReceiveRejected, ReceiveInterrupted) as error:
            result = error.result
        summary = {'unpack_ok': result.unpack_ok, 'cleanup_pending': result.cleanup_pending,
                   'cleanup_error': result.cleanup_error, 'atomic': result.atomic,
                   'ref_transaction': None if result.ref_transaction is None else {
                       'id': result.ref_transaction.transaction_id, 'state': result.ref_transaction.state.value,
                       'journal_pending': result.ref_transaction.journal_pending,
                       'retired_path': str(result.ref_transaction.retired_path) if result.ref_transaction.retired_path else None},
                   'outcomes': [{'name': o.command.name.decode('ascii'), 'state': o.state.value,
                                 'reason': o.reason.decode('ascii') if o.reason else None} for o in result.outcomes]}
        # A crash/database failure here leaves a prepared receipt: explicitly
        # unknown, never inferred accepted merely by observing later ref tips.
        self.catalog._finish_receive(operation, summary, now=int(self.clock()))
        if any(o.state is ReceiveState.UNKNOWN for o in result.outcomes):
            wire = encode_packet(Packet(data=b'ERR receive outcome unknown; re-read refs\n'))
        else:
            statuses = tuple(CommandStatus(o.command.name, o.state is ReceiveState.ACCEPTED,
                b'' if o.state is ReceiveState.ACCEPTED else o.reason or b'not accepted') for o in result.outcomes)
            report = ReportStatus(result.unpack_ok, statuses, b'' if result.unpack_ok else b'pack rejected')
            version = 2 if any(c.name == b'report-status-v2' for c in request.negotiated.capabilities) else 1
            wire = encode_report_status(report, request.commands, repo.object_format, version=version, limits=self.protocol)
        return HostedReceiveResult(operation, result, wire)

    def recover(self, actor, repository_id, *, cancel=None):
        """Explicit administrator recovery; never adopt lockfiles or orphans."""
        with self.catalog.transaction():
            actor, record = self._authorized(actor, repository_id, action=Action.ADMIN)
            repo = self.backend.open_repository(record.storage_id)
            preflight_receive(record, repo, self.limits)
            result = repo.recover_receive(ingestion=IngestionProfile.UNTRUSTED, limits=self.limits, cancel=cancel)
            if result is not None:
                self.catalog._event(EventKind.RECEIVE_RECOVERED, actor.user_id, record.id, int(self.clock()))
            # A recovered Git journal is authoritative about that transaction.
            # Untied prepared request receipts remain unknown across restarts.
            return result

    def chunks(self, data):
        for start in range(0, len(data), 64*1024): yield data[start:start+64*1024]
