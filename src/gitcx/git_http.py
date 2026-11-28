"""Expose API read routes with catalog authorization and bounded Gitdx sessions."""

import asyncio
import base64
from contextlib import contextmanager, nullcontext
import re
import threading

from expose_api import HTTPException, Response, StreamingResponse
from gitdx import Packet, UnsupportedFormat, UploadPack, UploadPackLimits, encode_packet

from .authentication import AuthenticationError, TokenAuth
from .catalog import Catalog
from .domain import Action, Principal, identifier
from .permissions import PermissionDenied
from .receive import ReceiveService, default_receive_limits, preflight_receive, require_write_profile


class GitAuthenticationRequired(AuthenticationError):pass


def _credential(request):
    headers=request.headers.getlist('authorization')
    if len(headers)>1:raise GitAuthenticationRequired('ambiguous credential')
    header=headers[0] if headers else None
    if header is None:return None
    if not isinstance(header,str) or len(header)>512:raise GitAuthenticationRequired('invalid credential')
    kind,space,value=header.partition(' ')
    if space and kind.lower()=='basic':
        try:
            decoded=base64.b64decode(value,validate=True).decode('ascii');user,colon,value=decoded.partition(':')
            if not colon or not user or len(user)>128 or any(ord(c)<33 or ord(c)>126 for c in user):raise ValueError
        except (ValueError,UnicodeError):raise GitAuthenticationRequired('invalid credential') from None
    elif not space or kind.lower()!='bearer':raise GitAuthenticationRequired('invalid credential')
    if not re.fullmatch(r'gtc_[A-Za-z0-9_-]{76}',value):raise GitAuthenticationRequired('invalid credential')
    return value


def _version(request):
    if len(request.headers.getlist('git-protocol'))>1:raise ValueError('ambiguous Git protocol header')
    header=request.header('git-protocol')
    if header is None:return 0
    if len(header)>256 or any(ord(c)<32 or ord(c)>126 for c in header):raise ValueError('invalid Git protocol header')
    versions=[p[8:] for p in header.split(':') if p.startswith('version=')]
    if len(versions)>1 or versions and versions[0] not in ('0','1','2'):raise ValueError('unsupported Git protocol version')
    return int(versions[0]) if versions else 0


class _TransferStream:
    """Retain admission until a cancelled preparation thread has really stopped."""

    def __init__(self,prepare,slots,*,error=b'ERR upload-pack request failed\n'):
        self.prepare=prepare;self.slots=slots;self.cancel=threading.Event()
        self.error=error
        self.worker=None;self.iterator=None;self.closed=False

    def __aiter__(self):return self

    async def __anext__(self):
        if self.closed:raise StopAsyncIteration
        if self.worker is None:
            self.worker=asyncio.create_task(asyncio.to_thread(self.prepare,self.cancel))
            try:
                await asyncio.wait((self.worker,))
                service,data=self.worker.result();self.iterator=iter(service.chunks(data))
            except asyncio.CancelledError:
                await self.aclose();raise
            except Exception:
                # Authentication already ran before response headers. Later
                # failures use a fixed Git error packet, never exception text.
                self.iterator=iter((encode_packet(Packet(data=self.error)),))
        try:return next(self.iterator)
        except StopIteration:
            await self.aclose();raise StopAsyncIteration from None

    async def aclose(self):
        if self.closed:return
        self.closed=True;self.cancel.set();interrupted=False
        try:
            if self.worker is not None:
                while not self.worker.done():
                    try:await asyncio.wait((self.worker,))
                    except asyncio.CancelledError:interrupted=True
                if not self.worker.cancelled():self.worker.exception()
        finally:
            if self.iterator is not None:
                close=getattr(self.iterator,'close',None)
                if close is not None:close()
            self.slots.release()
        if interrupted:raise asyncio.CancelledError


def install_git_routes(app,database,*,clock,limits=None,receive_limits=None,max_transfers=4):
    limits=limits or UploadPackLimits()
    if not isinstance(limits,UploadPackLimits):raise TypeError('select UploadPackLimits')
    if type(max_transfers) is not int or not 1<=max_transfers<=64:raise ValueError('select 1–64 concurrent Git transfers')
    receive_limits=receive_limits or default_receive_limits()
    slots=threading.BoundedSemaphore(max_transfers)

    @contextmanager
    def authorized(request,*,open_storage=False,action=Action.READ,preflight=False):
        if not app.state.get('ready'):raise HTTPException(503,'service unavailable')
        token=_credential(request)
        try:
            selected=request.path_params['repository']
            if not selected.endswith('.git'):raise ValueError('invalid repository route')
            repository_id=identifier(selected[:-4])
            with Catalog(database) as catalog:
                context=TokenAuth(catalog).request(token,now=int(clock())) if token is not None else nullcontext(Principal(scopes=frozenset({Action.READ})))
                with context as actor:
                    record=catalog.get_repository(actor,repository_id,action=action)
                    if catalog.provisioning_state(repository_id)!='ready':raise PermissionDenied('repository not found or permission denied')
                    if action is Action.WRITE:
                        if record.object_format!='sha256':raise HTTPException(403,'hosted writes require native SHA-256')
                        require_write_profile(record)
                    # No storage receipt, ref or object lookup precedes access.
                    repo=app.state['backend'].open_repository(record.storage_id) if open_storage else None
                    if preflight:preflight_receive(record,repo,receive_limits)
                    yield record,repo
        except AuthenticationError:raise GitAuthenticationRequired('invalid or inactive credential') from None
        except UnsupportedFormat:raise
        except (PermissionDenied,ValueError):
            if token is None:raise GitAuthenticationRequired('repository not found or permission denied') from None
            raise PermissionDenied('repository not found or permission denied') from None

    @app.exception_handler(GitAuthenticationRequired)
    def credentials(request,exception):
        return Response(b'authentication required\n',status=401,media_type='text/plain',headers={
            'WWW-Authenticate':'Basic realm="Gitcx", charset="UTF-8"','Cache-Control':'no-store'})

    def response(request,body,version,discovery):
        if not slots.acquire(blocking=False):raise HTTPException(503,'Git transfer capacity reached')
        def prepare(cancel):
            with authorized(request,open_storage=True) as (record,repo):pass
            service=UploadPack(repo,limits=limits,cancel=cancel)
            data=service.advertise(version,http=True) if discovery else service.respond(body,version)
            with authorized(request) as (current,_):
                if current!=record:raise PermissionDenied('repository changed during transfer preparation')
            return service,data
        stream=_TransferStream(prepare,slots)
        try:
            return StreamingResponse(stream,media_type='application/x-git-upload-pack-'+('advertisement' if discovery else 'result'),
                headers={'Cache-Control':'no-cache, no-store, must-revalidate','Pragma':'no-cache',
                         'Vary':'Authorization, Git-Protocol','X-Content-Type-Options':'nosniff'})
        except BaseException:
            slots.release();raise

    def receive_response(request,body=b'',*,discovery=False,admitted=False):
        if not admitted and not slots.acquire(blocking=False):raise HTTPException(503,'Git transfer capacity reached')
        def prepare(cancel):
            token=_credential(request)
            if token is None:raise GitAuthenticationRequired('authentication required')
            with Catalog(database) as catalog:
                actor=TokenAuth(catalog).authenticate(token,now=int(clock()))
                selected=request.path_params['repository'];repository_id=identifier(selected[:-4])
                service=ReceiveService(catalog,app.state['backend'],limits=receive_limits,clock=clock)
                data=service.advertise(actor,repository_id) if discovery else service.receive(actor,repository_id,body,cancel=cancel).wire
                return service,data
        stream=_TransferStream(prepare,slots,error=b'ERR receive outcome unavailable; re-read refs\n')
        try:
            return StreamingResponse(stream,media_type='application/x-git-receive-pack-'+('advertisement' if discovery else 'result'),
                headers={'Cache-Control':'no-cache, no-store, must-revalidate','Pragma':'no-cache',
                         'Vary':'Authorization, Git-Protocol','X-Content-Type-Options':'nosniff'})
        except BaseException:
            slots.release();raise

    @app.route('/git/{repository}/info/refs',methods=('GET',),public=True)
    async def discover(request):
        with authorized(request):pass
        if request.method!='GET':raise HTTPException(405,'method not allowed')
        query=request.query_params
        services=query.getlist('service')
        if set(query)!={'service'} or len(services)!=1 or services[0] not in ('git-upload-pack','git-receive-pack'):raise HTTPException(403,'Git service disabled')
        if services[0]=='git-receive-pack':
            with authorized(request,open_storage=True,action=Action.WRITE,preflight=True):pass
            _version(request)  # Receive uses legacy discovery even for v2 fetch clients.
            if await request.body(max_size=limits.protocol.max_bytes):raise ValueError('discovery has no request body')
            with authorized(request,action=Action.WRITE):pass
            return receive_response(request,discovery=True)
        if await request.body(max_size=limits.protocol.max_bytes):raise ValueError('discovery has no request body')
        with authorized(request):pass
        return response(request,b'',_version(request),True)

    @app.route('/git/{repository}/git-upload-pack',methods=('POST',),public=True)
    async def upload(request):
        with authorized(request):pass
        if request.query_params:raise ValueError('upload-pack does not accept query parameters')
        if len(request.headers.getlist('content-type'))!=1 or (request.header('content-type') or '').strip().lower()!='application/x-git-upload-pack-request':raise HTTPException(415,'unsupported media type')
        if len(request.headers.getlist('content-encoding'))>1 or (request.header('content-encoding') or 'identity').lower()!='identity':raise HTTPException(415,'unsupported content encoding')
        body=await request.body(max_size=limits.protocol.max_bytes)
        with authorized(request):pass
        return response(request,body,_version(request),False)

    @app.route('/git/{repository}/git-receive-pack',methods=('POST',),public=True)
    async def receive_pack(request):
        with authorized(request,open_storage=True,action=Action.WRITE,preflight=True):pass
        _version(request)
        if request.query_params:raise ValueError('receive-pack does not accept query parameters')
        if len(request.headers.getlist('content-type'))!=1 or (request.header('content-type') or '').strip().lower()!='application/x-git-receive-pack-request':raise HTTPException(415,'unsupported media type')
        if len(request.headers.getlist('content-encoding'))>1 or (request.header('content-encoding') or 'identity').lower()!='identity':raise HTTPException(415,'unsupported content encoding')
        # Admission precedes body allocation and is transferred to the streaming
        # cleanup owner only after the bounded body and fresh access checks.
        if not slots.acquire(blocking=False):raise HTTPException(503,'Git transfer capacity reached')
        owned=True
        try:
            body=await request.body(max_size=receive_limits.transfer.max_wire_bytes)
            with authorized(request,action=Action.WRITE):pass
            owned=False
            return receive_response(request,body,admitted=True)
        finally:
            if owned:slots.release()
