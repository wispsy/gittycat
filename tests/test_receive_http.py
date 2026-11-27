import asyncio
import base64
import threading
import time
import unittest
import subprocess
from unittest.mock import patch

from gitdx import ReceiveLimits, Repository, TransferLimits
from gittycat import Action, BranchRule, Catalog, GitdxBackend, Principal, ReceiveService, TokenAuth, create_app
import test_git_http as http_tests
from test_git_http import OWNER, OUTSIDER, commit, native, serve
from test_receive import descriptor_mechanism


@unittest.skipIf(serve is None, 'install the http extra for Git HTTP tests')
class ReceiveHTTPTests(unittest.IsolatedAsyncioTestCase):
    setUp = http_tests.GitHTTPTests.setUp
    repository = http_tests.GitHTTPTests.repository
    raw = http_tests.GitHTTPTests.raw
    request = http_tests.GitHTTPTests.request

    async def test_unauthorized_sha1_and_unqualified_profiles_refuse_before_body_and_storage(self):
        old, _, _ = self.repository('sha1', empty=True)
        record, _, _ = self.repository('sha256', empty=True)
        media = (('Content-Type', 'application/x-git-receive-pack-request'),)
        auth = 'Bearer '+self.tokens[OWNER]
        async with serve(self.app, port=0) as server:
            with (patch('expose_api.HTTPRequest.body', side_effect=AssertionError('body before authorization/profile')),
                  patch.object(GitdxBackend, 'open_repository', side_effect=AssertionError('storage before authorization/profile'))):
                for record_id, authorization, expected in ((record.id, None, 401), (record.id, 'Bearer '+self.tokens[OUTSIDER], 404), (old.id, auth, 403)):
                    status, _, body = await self.request(server, 'POST', '/git/'+record_id+'.git/git-receive-pack',
                        authorization=authorization, body=b'invalid', headers=media)
                    self.assertEqual(status, expected);self.assertNotIn(self.tokens[OWNER].encode(), body)
                with patch('gittycat.receive.supported', return_value=False):
                    for path, method in (('/git/'+record.id+'.git/git-receive-pack', 'POST'),
                                         ('/git/'+record.id+'.git/info/refs?service=git-receive-pack', 'GET')):
                        self.assertEqual((await self.request(server, method, path, authorization=auth, headers=media))[0], 503)

    async def test_bounded_bodies_media_types_and_revocation_after_body(self):
        from expose_api import HTTPRequest
        record, _, _ = self.repository('sha256', empty=True)
        self.app = create_app(self.root, clock=lambda: self.now,
            receive_limits=ReceiveLimits(transfer=TransferLimits(max_refs=16, max_wire_bytes=512)))
        path = '/git/'+record.id+'.git/git-receive-pack';auth = 'Bearer '+self.tokens[OWNER]
        media = (('Content-Type', 'application/x-git-receive-pack-request'),)
        with descriptor_mechanism():
            async with serve(self.app, port=0) as server:
                for headers in ((), (('Content-Type', 'application/json'),), (*media, ('Content-Encoding', 'gzip'))):
                    self.assertEqual((await self.request(server, 'POST', path, authorization=auth, headers=headers))[0], 415)
                self.assertEqual((await self.request(server, 'POST', path, authorization=auth, headers=media, body=b'x'*513))[0], 413)
                original = HTTPRequest.body
                async def revoke(request, **options):
                    data = await original(request, **options)
                    with Catalog(self.root/'catalog.sqlite3') as catalog:
                        actor = TokenAuth(catalog).authenticate(self.tokens[OWNER], now=10)
                        catalog.revoke_credential(Principal(OWNER), actor.credential_id, now=10)
                    return data
                with patch.object(HTTPRequest, 'body', revoke), patch.object(ReceiveService, 'receive', side_effect=AssertionError('intake after revocation')):
                    self.assertEqual((await self.request(server, 'POST', path, authorization=auth, headers=media, body=b'0000'))[0], 401)

    async def test_stock_git_pushes_initial_fast_forward_feature_tag_delete_and_policy_refusal(self):
        self.app = create_app(self.root, clock=lambda: self.now)
        record, repo, _ = self.repository('sha256', empty=True)
        client = Repository.init(self.root/'client.git', bare=True, object_format='sha256')
        first = commit(client, b'shared contents\n'*4000+b'first\n')
        new = commit(client, b'shared contents\n'*4000+b'second\n', (first,))
        auth = 'Basic '+base64.b64encode(('git:'+self.tokens[OWNER]).encode()).decode()
        with descriptor_mechanism():
            async with serve(self.app, port=0, request_timeout=30) as server:
                host, port = server.sockets[0].getsockname()[:2]
                url = 'http://'+host+':'+str(port)+'/git/'+record.id+'.git'
                for tip in (first, new):
                    try: await asyncio.to_thread(native, client.git_dir, 'push', url, tip.hex+':refs/heads/main', authorization=auth)
                    except subprocess.CalledProcessError as error: self.fail(error.stderr.decode('utf-8', 'replace'))
                    self.assertEqual(repo.refs.resolve().oid, tip)
                await asyncio.to_thread(native, client.git_dir, 'push', url, new.hex+':refs/heads/feature', new.hex+':refs/tags/tag', authorization=auth)
                await asyncio.to_thread(native, client.git_dir, 'push', url, ':refs/tags/tag', authorization=auth)
                self.assertIsNone(repo.refs.read('refs/tags/tag'))
                await asyncio.to_thread(native, repo.git_dir, 'fsck', '--strict', '--no-dangling')
                status, _, advertisement = await self.request(server, 'GET', '/git/'+record.id+'.git/info/refs?service=git-receive-pack', authorization=auth)
                self.assertEqual(status, 200);self.assertIn(b'no-thin', advertisement);self.assertNotIn(b' atomic', advertisement)
                with Catalog(self.root/'catalog.sqlite3') as catalog:
                    catalog.set_branch_rule(Principal(OWNER), BranchRule(record.id, 'refs/heads/main', require_review=True), expected_version=0, now=10)
                third = commit(client, b'third', (new,))
                with self.assertRaises(subprocess.CalledProcessError):
                    await asyncio.to_thread(native, client.git_dir, 'push', url, third.hex+':refs/heads/main', authorization=auth)
                self.assertEqual(repo.refs.resolve().oid, new)

    async def test_cancelled_receive_worker_releases_admission_only_after_settling(self):
        record, _, _ = self.repository('sha256', empty=True)
        path = '/git/'+record.id+'.git/git-receive-pack';discovery = '/git/'+record.id+'.git/info/refs?service=git-receive-pack'
        auth = 'Bearer '+self.tokens[OWNER];media = (('Content-Type', 'application/x-git-receive-pack-request'),)
        started = threading.Event();finished = threading.Event()
        def blocked(service, *args, cancel=None, **options):
            started.set()
            try:
                while not cancel.is_set(): time.sleep(.01)
                raise RuntimeError('cancelled fixture')
            finally: finished.set()
        with descriptor_mechanism():
            async with serve(self.app, port=0) as server:
                with patch.object(ReceiveService, 'receive', blocked):
                    reader, writer = await self.raw(server, 'POST', path, authorization=auth, headers=media, body=b'0000')
                    await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 3)
                    self.assertTrue(await asyncio.to_thread(started.wait, 3))
                    self.assertEqual((await self.request(server, 'GET', discovery, authorization=auth))[0], 503)
                    writer.close();await writer.wait_closed()
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))
                for _ in range(30):
                    status, _, _ = await self.request(server, 'GET', discovery, authorization=auth)
                    if status == 200: break
                    await asyncio.sleep(.01)
                self.assertEqual(status, 200)
