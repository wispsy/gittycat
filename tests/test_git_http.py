import asyncio
import base64
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from gitdx import Blob, Commit, ProtocolLimits, Tag, Tree, TreeEntry, UploadPack, UploadPackLimits
from gitcx import (AccessGrant, Action, Catalog, GitdxBackend, Owner, Permission, Principal,
                     RepositoryRecord, RepositoryService, Slug, StorageId, TokenAuth, Visibility)
from test_permissions import OWNER, OUTSIDER

try:
    from expose_api import HTTPRequest, serve
    from gitcx import create_app
except ImportError:
    if os.environ.get('GITCX_REQUIRE_HTTP')=='1':raise
    serve=None

IDENTITY=b'Fixture <fixture@example.invalid> 1700000000 +0000'


def commit(repo,data,parents=()):
    blob=repo.objects.write(Blob(data).to_raw())
    tree=repo.objects.write(Tree((TreeEntry(0o100644,b'file',blob),),repo.object_format).to_raw())
    return repo.objects.write(Commit.create(tree,parents=parents,author=IDENTITY,committer=IDENTITY,message=b'fixture\n').to_raw())


def native(path,*args,version=2,authorization=None):
    environment={k:v for k,v in os.environ.items() if not k.startswith('GIT_') and k.lower() not in ('http_proxy','https_proxy','all_proxy')}
    settings=[('protocol.version',str(version)),('credential.helper',''),('credential.interactive','false')]
    if authorization is not None:settings.append(('http.extraHeader','Authorization: '+authorization))
    environment.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',GIT_CONFIG_COUNT=str(len(settings)))
    for i,(key,value) in enumerate(settings):environment['GIT_CONFIG_KEY_'+str(i)]=key;environment['GIT_CONFIG_VALUE_'+str(i)]=value
    return subprocess.run(['git','-C',str(path),*args],capture_output=True,env=environment,check=True,timeout=30).stdout


@unittest.skipIf(serve is None,'install the http extra for Git HTTP tests')
class GitHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.now=10
        self.backend=GitdxBackend(self.root/'git');self.tokens={}
        with Catalog(self.root/'catalog.sqlite3') as catalog:
            for user,slug in ((OWNER,'owner'),(OUTSIDER,'outsider')):
                catalog.create_account(user,Slug(slug),now=1)
                self.tokens[user]=TokenAuth(catalog).issue(Principal(user),scopes={Action.ADMIN},expires_at=100,now=2).reveal()
        self.app=create_app(self.root,clock=lambda:self.now,max_body_size=1024,max_git_transfers=1,
                            git_limits=UploadPackLimits(protocol=ProtocolLimits(max_bytes=512)))

    def repository(self,format='sha1',visibility=Visibility.PRIVATE,empty=False):
        record=RepositoryRecord(uuid.uuid4().hex,Owner(OWNER,'user'),Slug('repo-'+uuid.uuid4().hex[:8]),StorageId.new(),visibility,format)
        with Catalog(self.root/'catalog.sqlite3') as catalog:RepositoryService(catalog,self.backend).create(Principal(OWNER),record,now=3)
        repo=self.backend.open_repository(record.storage_id)
        tip=None
        if not empty:
            tip=commit(repo,b'data');repo.refs.update('refs/heads/main',tip,expected=None)
        return record,repo,tip

    async def raw(self,server,method,path,*,body=b'',authorization=None,headers=()):
        host,port=server.sockets[0].getsockname()[:2];reader,writer=await asyncio.open_connection(host,port)
        fields=[('Host','localhost'),('Content-Length',str(len(body))),*headers]
        if authorization is not None:fields.append(('Authorization',authorization))
        writer.write((method+' '+path+' HTTP/1.1\r\n'+''.join(k+': '+v+'\r\n' for k,v in fields)+'\r\n').encode()+body)
        await writer.drain()
        return reader,writer

    async def request(self,server,method,path,**options):
        reader,writer=await self.raw(server,method,path,**options)
        try:response=await asyncio.wait_for(reader.read(),10)
        finally:writer.close();await writer.wait_closed()
        head,body=response.split(b'\r\n\r\n',1);lines=head.split(b'\r\n')
        return int(lines[0].split()[1]),dict(line.decode().split(': ',1) for line in lines[1:]),body

    async def test_public_anonymous_private_concealment_basic_bearer_and_no_storage_before_access(self):
        private,repo,tip=self.repository();public,_,_=self.repository(visibility=Visibility.PUBLIC)
        path=lambda id:'/git/'+id+'.git/info/refs?service=git-upload-pack'
        bearer='Bearer '+self.tokens[OWNER]
        basic='Basic '+base64.b64encode(('label:'+self.tokens[OWNER]).encode()).decode()
        async with serve(self.app,port=0) as server:
            with patch.object(GitdxBackend,'open_repository',side_effect=AssertionError('storage read before authorization')):
                denied=await self.request(server,'GET',path(private.id));missing=await self.request(server,'GET',path(uuid.uuid4().hex))
                self.assertEqual(denied,missing);self.assertEqual(denied[0],401)
                self.assertTrue(denied[1]['www-authenticate'].startswith('Basic realm="Gitcx"'))
                unauthorized=await self.request(server,'GET',path(private.id),authorization='Bearer '+self.tokens[OUTSIDER])
                absent=await self.request(server,'GET',path(uuid.uuid4().hex),authorization='Bearer '+self.tokens[OUTSIDER])
                self.assertEqual(unauthorized,absent);self.assertEqual(absent[0],404)
            for auth in (bearer,basic):
                status,headers,body=await self.request(server,'GET',path(private.id),authorization=auth)
                self.assertEqual(status,200);self.assertIn(str(tip).encode(),body)
                self.assertEqual(headers['content-type'],'application/x-git-upload-pack-advertisement')
                self.assertIn('no-store',headers['cache-control']);self.assertEqual(headers['vary'],'Authorization, Git-Protocol')
                self.assertNotIn(self.tokens[OWNER].encode(),body)
            self.assertEqual((await self.request(server,'GET',path(public.id)))[0],200)
            for auth,headers in (('Bearer malformed',()),(bearer,(('Authorization',bearer),))):
                status,_,body=await self.request(server,'GET',path(public.id),authorization=auth,headers=headers)
                self.assertEqual(status,401);self.assertNotIn(self.tokens[OWNER].encode(),body)
            with patch.object(GitdxBackend,'open_repository',side_effect=AssertionError('raw object route')):
                self.assertEqual((await self.request(server,'GET','/git/'+private.id+'.git/objects/00/file',authorization=bearer))[0],404)

    async def test_request_bounds_content_types_framing_and_expiry_after_body_read(self):
        record,_,_=self.repository();base='/git/'+record.id+'.git/';bearer='Bearer '+self.tokens[OWNER]
        media=(('Content-Type','application/x-git-upload-pack-request'),)
        async with serve(self.app,port=0) as server:
            for headers in ((),(('Content-Type','application/json'),),(*media,('Content-Encoding','gzip'))):
                self.assertEqual((await self.request(server,'POST',base+'git-upload-pack',authorization=bearer,headers=headers))[0],415)
            self.assertEqual((await self.request(server,'POST',base+'git-upload-pack',body=b'x'*513,authorization=bearer,headers=media))[0],413)
            status,headers,body=await self.request(server,'POST',base+'git-upload-pack',body=b'invalid',authorization=bearer,headers=media)
            self.assertEqual(status,200);self.assertIn(b'ERR upload-pack request failed',body);self.assertNotIn(b'invalid',body)
            self.assertEqual(headers['content-type'],'application/x-git-upload-pack-result')
            self.assertEqual((await self.request(server,'GET',base+'info/refs?service=git-receive-pack',authorization=bearer))[0],403)
            self.assertEqual((await self.request(server,'GET',base+'info/refs?service=git-upload-pack&extra=x',authorization=bearer))[0],403)
            original=HTTPRequest.body
            async def expires(request,**options):
                data=await original(request,**options);self.now=100;return data
            with patch.object(HTTPRequest,'body',expires),patch.object(GitdxBackend,'open_repository',side_effect=AssertionError('storage after expiry')):
                self.assertEqual((await self.request(server,'POST',base+'git-upload-pack',body=b'0000',authorization=bearer,headers=media))[0],401)

    async def test_revocation_during_preparation_refuses_all_disclosure(self):
        record,_,tip=self.repository();original=UploadPack.advertise;token=self.tokens[OWNER]
        def revoked(service,*args,**options):
            data=original(service,*args,**options)
            with Catalog(self.root/'catalog.sqlite3') as catalog:
                actor=TokenAuth(catalog).authenticate(token,now=10);catalog.revoke_credential(Principal(OWNER),actor.credential_id,now=10)
            return data
        async with serve(self.app,port=0) as server:
            with patch.object(UploadPack,'advertise',revoked):
                status,_,body=await self.request(server,'GET','/git/'+record.id+'.git/info/refs?service=git-upload-pack',authorization='Bearer '+token)
            self.assertEqual(status,200);self.assertIn(b'ERR ',body);self.assertNotIn(str(tip).encode(),body);self.assertNotIn(token.encode(),body)

    async def test_disconnect_cancels_preparation_and_holds_admission_until_cleanup(self):
        loop=asyncio.get_running_loop();errors=[];previous=loop.get_exception_handler()
        loop.set_exception_handler(lambda loop,context:errors.append(context['message']))
        self.addCleanup(loop.set_exception_handler,previous)
        record,_,_=self.repository();started=threading.Event();finished=threading.Event()
        def blocked(service,*args,**options):
            started.set()
            try:
                while not service.budget.cancelled():
                    service.budget.check();time.sleep(.01)
                service.budget.check()
            finally:finished.set()
        path='/git/'+record.id+'.git/info/refs?service=git-upload-pack';auth='Bearer '+self.tokens[OWNER]
        async with serve(self.app,port=0) as server:
            with patch.object(UploadPack,'advertise',blocked):
                reader,writer=await self.raw(server,'GET',path,authorization=auth)
                await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3)
                self.assertTrue(await asyncio.to_thread(started.wait,3))
                self.assertEqual((await self.request(server,'GET',path,authorization=auth))[0],503)
                writer.close();await writer.wait_closed();self.assertTrue(await asyncio.to_thread(finished.wait,3))
            for _ in range(30):
                status,_,_=await self.request(server,'GET',path,authorization=auth)
                if status==200:break
                await asyncio.sleep(.01)
            self.assertEqual(status,200)
        self.assertEqual(errors,[])

    async def test_authorized_read_has_no_git_or_shell_runtime(self):
        record,_,tip=self.repository();auth='Bearer '+self.tokens[OWNER]
        async with serve(self.app,port=0) as server:
            with patch.dict(os.environ,PATH=''),patch('subprocess.Popen',side_effect=AssertionError('runtime subprocess')),patch('os.system',side_effect=AssertionError('runtime shell')):
                status,_,body=await self.request(server,'GET','/git/'+record.id+'.git/info/refs?service=git-upload-pack',authorization=auth)
                self.assertEqual(status,200);self.assertIn(str(tip).encode(),body)

    async def test_native_clone_fetch_both_formats_all_versions_tags_and_empty(self):
        if shutil.which('git') is None:
            if os.environ.get('GITCX_REQUIRE_GIT')=='1':self.fail('native Git oracle is required')
            self.skipTest('native Git oracle is unavailable')
        # Use normal budgets for real negotiation rather than the tiny negative
        # fixture profile selected in setUp.
        self.app=create_app(self.root,clock=lambda:self.now)
        async with serve(self.app,port=0,request_timeout=30) as server:
            host,port=server.sockets[0].getsockname()[:2]
            for format in ('sha1','sha256'):
                record,repo,tip=self.repository(format)
                repo.refs.update('refs/heads/side',tip,expected=None)
                tag=repo.objects.write(Tag.create(tip,target_type='commit',name=b'tag',tagger=IDENTITY,message=b'tag\n').to_raw())
                repo.refs.update('refs/tags/tag',tag,expected=None)
                url='http://'+host+':'+str(port)+'/git/'+record.id+'.git'
                for version in (0,1,2):
                    client=self.root/(format+'-'+str(version));auth='Basic '+base64.b64encode(('git:'+self.tokens[OWNER]).encode()).decode()
                    await asyncio.to_thread(native,self.root,'clone','--bare',url,str(client),version=version,authorization=auth)
                    self.assertEqual((await asyncio.to_thread(native,client,'rev-parse','HEAD')).strip().decode(),str(tip))
                    self.assertEqual((await asyncio.to_thread(native,client,'rev-parse','refs/tags/tag^{}')).strip().decode(),str(tip))
                    new=commit(repo,b'new '+str(version).encode(),(tip,));repo.refs.update('refs/heads/main',new,expected=tip)
                    await asyncio.to_thread(native,client,'fetch','--tags','origin','refs/heads/*:refs/heads/*',version=version,authorization=auth)
                    self.assertEqual((await asyncio.to_thread(native,client,'rev-parse','refs/heads/main')).strip().decode(),str(new))
                    await asyncio.to_thread(native,client,'fsck','--strict','--no-dangling')
                    self.assertNotIn(self.tokens[OWNER],(client/'config').read_text());repo.refs.update('refs/heads/main',tip,expected=new)
                empty,_,_=self.repository(format,Visibility.PUBLIC,empty=True)
                url='http://'+host+':'+str(port)+'/git/'+empty.id+'.git'
                for version in (0,1,2):
                    client=self.root/('empty-'+format+'-'+str(version))
                    await asyncio.to_thread(native,self.root,'clone','--bare',url,str(client),version=version)
                    self.assertEqual((await asyncio.to_thread(native,client,'rev-parse','--show-object-format')).strip().decode(),format)
