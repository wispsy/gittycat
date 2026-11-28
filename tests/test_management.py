import asyncio
from pathlib import Path
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from gitcx import Action, Catalog, GitdxBackend, Principal, Slug, TokenAuth
from test_permissions import OWNER, READER, WRITER, OUTSIDER

try:
    from expose_api import HTTPRequest, serve
    from gitcx.management import create_app
except ImportError:
    if os.environ.get("GITCX_REQUIRE_HTTP") == "1":raise
    serve = None


@unittest.skipIf(serve is None, "install the http extra for management API tests")
class ManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.now = 10
        with Catalog(self.root/"catalog.sqlite3") as catalog:
            auth = TokenAuth(catalog);self.tokens = {}
            for user, slug, scope in ((OWNER,"owner",Action.ADMIN),(READER,"reader",Action.READ),(WRITER,"writer",Action.WRITE),(OUTSIDER,"outsider",Action.ADMIN)):
                catalog.create_account(user, Slug(slug), now=1)
                self.tokens[user] = auth.issue(Principal(user), scopes={scope}, expires_at=100, now=2).reveal()
            self.read_only = auth.issue(Principal(OWNER), scopes={Action.READ}, expires_at=100, now=2).reveal()
        self.app = create_app(self.root, clock=lambda:self.now, max_body_size=1024)

    async def request(self, server, method, path, *, user=OWNER, token=None, value=None, raw=None, headers=()):
        if token is None and user is not None:token = self.tokens[user]
        body = json.dumps(value).encode() if value is not None else raw or b""
        fields = [("Host","localhost"),("Content-Length",str(len(body))),*headers]
        if value is not None or raw is not None:fields.append(("Content-Type","application/json"))
        if token is not None:fields.append(("Authorization","Bearer "+token))
        raw_request = (f"{method} {path} HTTP/1.1\r\n"+"".join(k+": "+v+"\r\n" for k,v in fields)+"\r\n").encode()+body
        host,port = server.sockets[0].getsockname()[:2]
        reader,writer = await asyncio.open_connection(host,port)
        try:
            writer.write(raw_request);await writer.drain()
            response = await asyncio.wait_for(reader.read(), 5)
        finally:
            writer.close();await writer.wait_closed()
        head, body = response.split(b"\r\n\r\n",1);lines = head.split(b"\r\n")
        status = int(lines[0].split()[1]);fields = dict(line.decode().split(": ",1) for line in lines[1:])
        return status,fields,json.loads(body) if body else None

    async def create(self, server, **overrides):
        record = {"id":uuid.uuid4().hex,"slug":"repo-"+uuid.uuid4().hex[:8],**overrides}
        status,headers,value = await self.request(server,"POST","/api/repositories",value=record)
        self.assertEqual(status,201,value);self.assertNotIn("storage_id",value)
        return value

    async def test_two_collaborators_outsider_and_both_formats_over_real_http(self):
        async with serve(self.app,port=0) as server:
            for format in ("sha1","sha256"):
                record = await self.create(server,object_format=format);path = "/api/repositories/"+record["id"]
                status,headers,value = await self.request(server,"GET",path)
                self.assertEqual(status,200,value);self.assertEqual(value["object_format"],format)
                self.assertEqual(headers["cache-control"],"no-store")
                for user,permission in ((READER,"read"),(WRITER,"write")):
                    status,_,value = await self.request(server,"PUT",path+"/grants/"+user,value={"permission":permission})
                    self.assertEqual(status,200,value)
                    self.assertEqual((await self.request(server,"GET",path,user=user))[0],200)
                with patch.object(GitdxBackend,"inspect",side_effect=AssertionError("storage lookup before denial")):
                    denied = await self.request(server,"GET",path,user=OUTSIDER)
                    missing = await self.request(server,"GET","/api/repositories/"+uuid.uuid4().hex,user=OUTSIDER)
                self.assertEqual(denied,missing);self.assertEqual(denied[0],404)
                self.assertEqual((await self.request(server,"PUT",path+"/grants/"+OUTSIDER,user=WRITER,value={"permission":"admin"}))[0],404)
                self.assertEqual((await self.request(server,"DELETE",path+"/grants/"+READER))[0],200)
                self.assertEqual((await self.request(server,"GET",path,user=READER))[0],404)
            self.assertEqual((await self.request(server,"GET","/api/repositories",user=OUTSIDER))[2]["items"],[])
            self.assertEqual((await self.request(server,"GET","/api/me",user=WRITER))[2]["id"],WRITER)

    async def test_authentication_scope_expiry_revocation_and_redacted_errors(self):
        async with serve(self.app,port=0) as server:
            for kwargs in ({"user":None},{"token":"do-not-echo-this"},{"headers":(("Authorization","Bearer duplicate"),)}):
                status,headers,value = await self.request(server,"GET","/api/me",**kwargs)
                self.assertEqual(status,401,value);self.assertEqual(headers["www-authenticate"],"Bearer")
                self.assertNotIn("do-not-echo-this",json.dumps(value))
            self.assertEqual((await self.request(server,"POST","/api/repositories",token=self.read_only,raw=b"not-json"))[0],404)
            self.assertEqual((await self.request(server,"POST","/api/repositories",raw=b"not-json"))[0],400)
            self.now = 100
            self.assertEqual((await self.request(server,"GET","/api/me"))[0],401)
            self.now = 10
            with Catalog(self.root/"catalog.sqlite3") as catalog:
                credential = TokenAuth(catalog).authenticate(self.tokens[OWNER],now=10).credential_id
            self.assertEqual((await self.request(server,"POST","/api/tokens/"+credential+"/revoke"))[0],200)
            self.assertEqual((await self.request(server,"GET","/api/me"))[0],401)

    async def test_pending_provisioning_survives_failure_and_recovers_by_known_id(self):
        repository_id = uuid.uuid4().hex
        async with serve(self.app,port=0) as server:
            with patch.object(Catalog,"finish_provisioning",side_effect=RuntimeError("private path and secret must not escape")):
                status,_,value = await self.request(server,"POST","/api/repositories",value={"id":repository_id,"slug":"recover"})
            self.assertEqual(status,500);self.assertNotIn("private path",json.dumps(value))
            self.assertEqual((await self.request(server,"GET","/api/repositories"))[2]["items"],[])
            with Catalog(self.root/"catalog.sqlite3") as catalog:self.assertEqual(catalog.provisioning_state(repository_id),"pending")
            self.assertEqual((await self.request(server,"POST","/api/repositories/"+repository_id+"/recover",user=OUTSIDER))[0],404)
            self.assertEqual((await self.request(server,"POST","/api/repositories/"+repository_id+"/recover"))[0],200)
            self.assertEqual((await self.request(server,"GET","/api/repositories/"+repository_id))[0],200)

    async def test_expiry_after_body_read_cannot_mutate_the_catalog(self):
        original = HTTPRequest.json
        async def expires_during_body(request):
            value = await original(request)
            self.now = 100
            return value
        async with serve(self.app,port=0) as server:
            with patch.object(HTTPRequest,"json",expires_during_body):
                status,_,value = await self.request(server,"POST","/api/repositories",value={"id":uuid.uuid4().hex,"slug":"expired"})
            self.assertEqual(status,401,value)
        with Catalog(self.root/"catalog.sqlite3") as catalog:
            self.assertEqual(catalog.list_repositories(Principal(OWNER)),())

    async def test_bounded_queries_bodies_and_binary_git_namespaces(self):
        async with serve(self.app,port=0) as server:
            first = await self.create(server);second = await self.create(server)
            page = (await self.request(server,"GET","/api/repositories?limit=1"))[2]
            next_page = (await self.request(server,"GET","/api/repositories?limit=1&after="+page["next_after"]))[2]
            self.assertEqual({page["items"][0]["id"],next_page["items"][0]["id"]},{first["id"],second["id"]})
            for query in ("limit=101","limit=0","limit=1&limit=2","extra=x","after=bad"):
                self.assertEqual((await self.request(server,"GET","/api/repositories?"+query))[0],400)
            self.assertEqual((await self.request(server,"POST","/api/repositories",raw=b"x"*1025))[0],413)
            self.assertEqual((await self.request(server,"POST","/api/repositories",value={"id":uuid.uuid4().hex,"slug":"bad","storage_id":"../escape"}))[0],400)
            self.assertEqual((await self.request(server,"GET","/repo.git/info/refs?service=git-upload-pack"))[0],404)
            self.assertEqual((await self.request(server,"POST","/repo.git/git-receive-pack",raw=b"PACK"))[0],404)

    async def test_lifecycle_restart_and_organization_identity_routes(self):
        organization = uuid.uuid4().hex
        self.assertFalse(self.app.state["ready"])
        async with serve(self.app,port=0) as server:
            self.assertTrue(self.app.state["ready"])
            self.assertEqual((await self.request(server,"GET","/healthz",user=None))[2],{"ready":True})
            self.assertEqual((await self.request(server,"POST","/api/organizations",value={"id":organization,"slug":"example-group"}))[0],201)
            self.assertEqual((await self.request(server,"PUT",f"/api/organizations/{organization}/members/{READER}",value={"role":"member"}))[0],200)
            record = await self.create(server,owner_id=organization,owner_kind="organization")
            self.assertEqual((await self.request(server,"GET","/api/repositories/"+record["id"],user=READER))[0],404)
        self.assertFalse(self.app.state["ready"])
        restarted = create_app(self.root,clock=lambda:self.now)
        async with serve(restarted,port=0) as server:
            self.assertEqual((await self.request(server,"GET","/api/repositories/"+record["id"]))[0],200)
