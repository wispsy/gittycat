from contextlib import contextmanager, closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gitdx import (ObjectQuarantine, PushCommand, RefLocked, Repository, TransferCancelled,
                   encode_push_request, negotiate_push)
from gitdx.confined_fs import supported
from gittycat import (Action, BranchRule, Catalog, EventKind, GitdxBackend, PermissionDenied, Principal,
                     ReceiveService, RepositoryService, Slug, TokenAuth)
from gittycat.receive import default_receive_limits
from test_git_http import commit
from test_permissions import OWNER, OUTSIDER, repository


@contextmanager
def descriptor_mechanism():
    """Linux uses the actual profile; other POSIX hosts test mechanics only."""
    if supported():
        yield
    elif os.name == 'posix':
        with patch('gitdx.confined_fs.supported', return_value=True), patch('gittycat.receive.supported', return_value=True):
            yield
    else:
        raise unittest.SkipTest('descriptor mechanisms require POSIX; public refusal is qualified separately')


class ReceiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.database = self.root/'catalog.sqlite3';self.now = 10
        self.catalog = Catalog(self.database);self.addCleanup(self.catalog.close)
        self.backend = GitdxBackend(self.root/'git')
        for user, slug in ((OWNER, 'owner'), (OUTSIDER, 'outsider')): self.catalog.create_account(user, Slug(slug), now=1)
        self.token = TokenAuth(self.catalog).issue(Principal(OWNER), scopes={Action.ADMIN}, expires_at=100, now=2).reveal()
        self.actor = TokenAuth(self.catalog).authenticate(self.token, now=10)
        self.record = repository(object_format='sha256')
        RepositoryService(self.catalog, self.backend).create(Principal(OWNER), self.record, now=3)
        self.repo = self.backend.open_repository(self.record.storage_id)
        self.source = Repository.init(self.root/'source', bare=True, object_format='sha256')
        self.service = ReceiveService(self.catalog, self.backend, clock=lambda: self.now)

    def wire(self, commands, *, pack=None):
        ad = self.service._advertisement(self.repo)
        negotiated = negotiate_push(ad, self.repo.object_format)
        roots = tuple(c.new for c in commands if c.new is not None)
        if pack is None: pack = self.source.pack(roots).pack if roots else b''
        return encode_push_request(negotiated, commands, pack)

    def test_overlay_initial_fast_forward_deletion_and_secret_free_audit_restart(self):
        with descriptor_mechanism(), patch('subprocess.Popen', side_effect=AssertionError('runtime helper')):
            first = commit(self.source, b'old');new = commit(self.source, b'new', (first,))
            for old, tip in ((None, first), (first, new)):
                command = PushCommand(b'refs/heads/main', old, tip)
                result = self.service.receive(self.actor, self.record.id, self.wire((command,)))
                self.assertTrue(result.result.complete);self.assertIn(b'ok refs/heads/main', result.wire)
                self.assertFalse(result.result.atomic)
                receipt = self.catalog.receive_operation(Principal(OWNER), result.operation_id)
                self.assertEqual(receipt['status'], 'completed')
                self.assertNotIn(self.token, repr(receipt))
            command = PushCommand(b'refs/heads/main', new, None)
            result = self.service.receive(self.actor, self.record.id, self.wire((command,)))
            self.assertTrue(result.result.complete);self.assertTrue(self.repo.refs.resolve().unborn)
            with Catalog(self.database) as reopened:
                self.assertEqual(reopened.receive_operation(Principal(OWNER), result.operation_id)['status'], 'completed')
                events = reopened.repository_events(Principal(OWNER), self.record.id)
                self.assertEqual(sum(e.kind is EventKind.RECEIVE_ACCEPTED for e in events), 3)
                self.assertTrue(any(j.kind == 'repository.index' for j in reopened.pending_jobs()))
            self.assertNotIn(self.token.encode(), self.database.read_bytes())

    def test_rule_refusals_coordinate_the_whole_batch_before_promotion(self):
        with descriptor_mechanism():
            tip = commit(self.source, b'data')
            self.catalog.set_branch_rule(Principal(OWNER), BranchRule(self.record.id, 'refs/heads/main', require_review=True), expected_version=0, now=5)
            commands = (PushCommand(b'refs/heads/main', None, tip), PushCommand(b'refs/heads/feature', None, tip))
            result = self.service.receive(self.actor, self.record.id, self.wire(commands))
            self.assertFalse(result.result.complete);self.assertEqual(result.result.promoted_objects, 0)
            self.assertIn(b'review-required', result.wire);self.assertEqual(self.repo.refs.snapshot(), ())
            self.assertEqual(self.catalog.receive_operation(Principal(OWNER), result.operation_id)['status'], 'rejected')
            with self.assertRaises(PermissionDenied): self.catalog.receive_operation(Principal(OUTSIDER), result.operation_id)

    def test_revocation_rule_change_and_expiry_are_fresh_at_publication(self):
        for mode in ('revoke', 'rule', 'expiry'):
            with self.subTest(mode=mode), descriptor_mechanism():
                # Use a fresh credential per attempt; all attempted refs stay absent.
                token = TokenAuth(self.catalog).issue(Principal(OWNER), scopes={Action.ADMIN}, expires_at=100, now=2).reveal()
                actor = TokenAuth(self.catalog).authenticate(token, now=10)
                self.now = 10;tip = commit(self.source, mode.encode())
                command = PushCommand(('refs/heads/'+mode).encode(), None, tip)
                original = ObjectQuarantine.promote
                def changed(incoming):
                    result = original(incoming)
                    with Catalog(self.database) as catalog:
                        if mode == 'revoke': catalog.revoke_credential(Principal(OWNER), actor.credential_id, now=10)
                        elif mode == 'rule':
                            version = catalog.branch_policy(Principal(OWNER), self.record.id).version
                            catalog.set_branch_rule(Principal(OWNER), BranchRule(self.record.id, command.name.decode(), require_review=True), expected_version=version, now=10)
                        else: self.now = 100
                    return result
                with patch.object(ObjectQuarantine, 'promote', changed):
                    result = self.service.receive(actor, self.record.id, self.wire((command,)))
                self.assertNotIn(b'ok refs/heads/', result.wire)
                self.assertIsNone(self.repo.refs.read(command.name.decode()))

    def test_under_ref_lock_policy_checks_retain_catalog_exclusion(self):
        from gittycat.ref_policy import RefPolicyService
        with descriptor_mechanism():
            tip = commit(self.source, b'data');command = PushCommand(b'refs/heads/main', None, tip)
            original = RefPolicyService._decide;observed = []
            def decide(service, *args, **options):
                if (self.repo.git_dir/'packed-refs.lock').exists():
                    self.assertTrue(service.catalog._connection.in_transaction)
                    with closing(sqlite3.connect(self.database, timeout=0)) as other:
                        with self.assertRaises(sqlite3.OperationalError): other.execute('BEGIN IMMEDIATE')
                    observed.append(True)
                return original(service, *args, **options)
            with patch.object(RefPolicyService, '_decide', decide):
                result = self.service.receive(self.actor, self.record.id, self.wire((command,)))
            self.assertTrue(result.result.complete);self.assertTrue(observed)

    def test_malformed_pack_quotas_and_cancellation_never_publish_refs(self):
        import threading
        with descriptor_mechanism():
            tip = commit(self.source, b'data');command = PushCommand(b'refs/heads/main', None, tip)
            pack = self.source.pack((tip,)).pack
            result = self.service.receive(self.actor, self.record.id, self.wire((command,), pack=pack[:-1]+bytes((pack[-1]^1,))))
            self.assertFalse(result.result.unpack_ok);self.assertIn(b'unpack pack rejected', result.wire)
            cancelled = threading.Event();cancelled.set()
            # The operation was authenticated/prepared, but cancellation occurs
            # before any ref publication and is retained in its receipt.
            with self.assertRaises(TransferCancelled): self.service.receive(self.actor, self.record.id, self.wire((command,)), cancel=cancelled)
            limited = ReceiveService(self.catalog, self.backend, clock=lambda: self.now,
                limits=replace(default_receive_limits(), graph=replace(default_receive_limits().graph, max_objects=1)))
            result = limited.receive(self.actor, self.record.id, self.wire((command,)))
            self.assertFalse(result.result.complete);self.assertEqual(self.repo.refs.snapshot(), ())

    def test_prepared_and_committed_git_recovery_preserve_unknown_request_receipts(self):
        import gitdx.ref_transaction as transaction
        with descriptor_mechanism():
            tip = commit(self.source, b'data');command = PushCommand(b'refs/heads/main', None, tip)
            original = transaction._put
            def fail(store, name, data):
                original(store, name, data)
                if name == 'refs/heads/main': raise OSError('interrupted publication')
            with patch.object(transaction, '_put', fail):
                result = self.service.receive(self.actor, self.record.id, self.wire((command,)))
            self.assertIn(b'ERR receive outcome unknown', result.wire)
            receipt = self.catalog.receive_operation(Principal(OWNER), result.operation_id)
            self.assertEqual(receipt['status'], 'unknown')
            lock = self.repo.git_dir/'packed-refs.lock';lock.write_bytes(b'unknown owner')
            with self.assertRaises(RefLocked): self.service.recover(Principal(OWNER), self.record.id)
            self.assertEqual(lock.read_bytes(), b'unknown owner');lock.unlink()
            recovery = self.service.recover(Principal(OWNER), self.record.id)
            self.assertEqual(recovery.state.value, 'rolled-back');self.assertTrue(self.repo.refs.resolve().unborn)
            self.assertEqual(self.catalog.receive_operation(Principal(OWNER), result.operation_id)['status'], 'unknown')
            with patch.object(transaction, '_retire', side_effect=OSError('interrupted retirement')):
                committed = self.service.receive(self.actor, self.record.id, self.wire((command,)))
            self.assertTrue(committed.result.complete);self.assertTrue(committed.result.ref_transaction.journal_pending)
            self.assertIn(b'ok refs/heads/main', committed.wire)
            self.assertTrue(self.service.recover(Principal(OWNER), self.record.id).committed)

    def test_outcome_database_failure_leaves_a_prepared_receipt_after_accepted_git_write(self):
        with descriptor_mechanism():
            tip = commit(self.source, b'data');command = PushCommand(b'refs/heads/main', None, tip)
            with patch.object(Catalog, '_finish_receive', side_effect=OSError('outcome unavailable')), self.assertRaises(OSError):
                self.service.receive(self.actor, self.record.id, self.wire((command,)))
            self.assertEqual(self.repo.refs.resolve().oid, tip)
            with Catalog(self.database) as reopened:
                row = reopened._connection.execute('SELECT id FROM receive_operations').fetchone()
                receipt = reopened.receive_operation(Principal(OWNER), row[0])
                self.assertEqual(receipt['status'], 'prepared');self.assertIsNone(receipt['result'])
