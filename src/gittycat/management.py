"""Optional Expose API JSON management routes over the application services."""

from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import re
import sqlite3
import time

from expose_api import App, HTTPException, JSONResponse, Principal as HTTPPrincipal
from gitdx import GitdxError

from .authentication import AuthenticationError, TokenAuth
from .backend import BackendMismatch
from .catalog import Catalog, CatalogConflict
from .domain import AccessGrant, Action, Membership, OrganizationRole, Owner, Permission, RepositoryRecord, Slug, StorageId, Visibility, identifier
from .permissions import PermissionDenied, require_scope
from .provisioning import GitdxBackend, ProvisioningError, RepositoryService


def _record(record):
    return {"id": record.id, "owner_id": record.owner.id, "owner_kind": record.owner.kind.value,
            "slug": record.slug.value, "visibility": record.visibility.value, "object_format": record.object_format}


def _id(request, name="repository_id"):
    try:return identifier(request.path_params[name])
    except ValueError:raise PermissionDenied("repository not found or permission denied") from None


async def _json(request, required, optional=()):
    value = await request.json()
    if type(value) is not dict or not set(required) <= value.keys() or value.keys()-set(required)-set(optional):
        raise ValueError("invalid request fields")
    return value


def create_app(data_root, *, clock=time.time, max_body_size=16*1024):
    """Create an ASGI app; its lifespan owns startup, never token bootstrap."""
    if type(max_body_size) is not int or not 1 <= max_body_size <= 1024*1024:
        raise ValueError("body limit must be between 1 byte and 1 MiB")
    if not callable(clock):raise TypeError("clock must be callable")
    root = Path(data_root).absolute();database = root/"catalog.sqlite3"

    @asynccontextmanager
    async def lifespan(app):
        if root.is_symlink():raise ProvisioningError("data root cannot be a symlink")
        with Catalog(database):pass
        app.state["backend"] = GitdxBackend(root/"git")
        app.state["ready"] = True
        try:yield
        finally:
            app.state["ready"] = False;app.state.pop("backend", None)

    def token(request):
        header = request.header("authorization")
        match = re.fullmatch(r"(?i:Bearer) (gtc_[A-Za-z0-9_-]{76})", header) if isinstance(header, str) and len(header) <= 128 else None
        if match is None:raise AuthenticationError("invalid or inactive credential")
        return match[1]

    def now():
        value = clock()
        if type(value) not in (int, float) or value < 0:raise ValueError("invalid server clock")
        return int(value)

    @contextmanager
    def authorized(request):
        if not app.state.get("ready"):raise HTTPException(503, "service unavailable")
        with Catalog(database) as catalog:
            with TokenAuth(catalog).request(token(request), now=now()) as actor:
                yield catalog, actor

    def authenticate(request):
        try:
            with authorized(request) as (_, actor):
                return HTTPPrincipal(actor.user_id, scopes=frozenset(a.value for a in actor.scopes))
        except AuthenticationError:return None

    app = App(title="Gittycat management", max_body_size=max_body_size, lifespan=lifespan,
              authenticate=authenticate, auth_challenge="Bearer")
    app.state["ready"] = False
    protected = {"authenticate": True, "authorize": lambda request: True}

    def service(catalog):return RepositoryService(catalog, app.state["backend"])

    def error(status, code, message):
        return JSONResponse({"error": {"code": code, "message": message}}, status=status,
                            headers={"Cache-Control": "no-store"})

    @app.exception_handler(AuthenticationError)
    def inactive(request, exception):
        response = error(401, "unauthorized", "invalid or inactive credential")
        response.headers.append((b"www-authenticate", b"Bearer"))
        return response

    @app.exception_handler(PermissionDenied)
    def denied(request, exception):return error(404, "not_found", "resource not found or permission denied")

    @app.exception_handler(CatalogConflict)
    def conflict(request, exception):return error(409, "conflict", "catalog change conflicts with existing state")

    def unavailable(request, exception):return error(503, "unavailable", "service unavailable")
    for kind in (BackendMismatch, ProvisioningError, GitdxError, OSError, sqlite3.Error):
        app.exception_handler(kind)(unavailable)
    def invalid(request, exception):return error(400, "invalid_request", "invalid request")
    for kind in (ValueError, TypeError, KeyError):app.exception_handler(kind)(invalid)

    @app.route("/healthz", public=True)
    def health(request):
        ready = app.state.get("ready", False)
        return JSONResponse({"ready": ready}, status=200 if ready else 503)

    @app.route("/api/me", **protected)
    def me(request):
        with authorized(request) as (catalog, actor):
            require_scope(actor, Action.READ)
            return JSONResponse({"id": actor.user_id, "scopes": sorted(a.value for a in actor.scopes), "credential_id": actor.credential_id})

    @app.route("/api/repositories", **protected)
    def repositories(request):
        query = request.query_params
        if set(query)-{"limit", "after"} or any(len(query.getlist(k)) != 1 for k in query):raise ValueError("invalid query")
        limit = query.get("limit", "50")
        if not re.fullmatch(r"[0-9]{1,3}", limit):raise ValueError("invalid limit")
        with authorized(request) as (catalog, actor):
            records = service(catalog).list(actor, limit=int(limit), after=query.get("after", ""))
            return JSONResponse({"items": [_record(r) for r in records], "next_after": records[-1].id if records else None})

    @app.route("/api/repositories/{repository_id}", name="repository", **protected)
    def repository(request):
        with authorized(request) as (catalog, actor):
            hosted = service(catalog).open(actor, _id(request))
            return JSONResponse({**_record(hosted.record), "bare": hosted.info.bare, "capabilities": sorted(hosted.info.capabilities)})

    @app.route("/api/repositories", methods=("POST",), **protected)
    async def create_repository(request):
        with authorized(request) as (_, actor):require_scope(actor, Action.ADMIN)
        body = await _json(request, ("id", "slug"), ("owner_id", "owner_kind", "visibility", "object_format"))
        with authorized(request) as (catalog, actor):
            record = RepositoryRecord(identifier(body["id"]), Owner(body.get("owner_id", actor.user_id), body.get("owner_kind", "user")),
                                      Slug(body["slug"]), StorageId.new(), Visibility(body.get("visibility", "private")), body.get("object_format", "sha1"))
            catalog.reserve_repository(actor, record, now=now())
        # Pending metadata commits before filesystem work. A later failure or
        # revocation retains the caller's known ID for authorized recovery.
        with authorized(request) as (catalog, actor):hosted = service(catalog).recover(actor, record.id, now=now())
        return JSONResponse(_record(hosted.record), status=201,
                            headers={"Location": request.url_for("repository", repository_id=record.id)})

    @app.route("/api/repositories/{repository_id}/recover", methods=("POST",), **protected)
    def recover(request):
        with authorized(request) as (catalog, actor):
            return JSONResponse(_record(service(catalog).recover(actor, _id(request), now=now()).record))

    @app.route("/api/repositories/{repository_id}/grants/{user_id}", methods=("PUT",), **protected)
    async def grant(request):
        repository_id = _id(request);user_id = _id(request, "user_id")
        with authorized(request) as (catalog, actor):catalog.get_repository(actor, repository_id, action=Action.ADMIN)
        body = await _json(request, ("permission",))
        permission = {"read": Permission.READ, "write": Permission.WRITE, "admin": Permission.ADMIN}[body["permission"]]
        with authorized(request) as (catalog, actor):catalog.grant(actor, AccessGrant(repository_id, user_id, permission), now=now())
        return JSONResponse({"user_id": user_id, "permission": body["permission"]})

    @app.route("/api/repositories/{repository_id}/grants/{user_id}", methods=("DELETE",), **protected)
    def revoke_grant(request):
        with authorized(request) as (catalog, actor):catalog.revoke_grant(actor, _id(request), _id(request, "user_id"), now=now())
        return JSONResponse(None, status=200)

    @app.route("/api/organizations", methods=("POST",), **protected)
    async def organization(request):
        with authorized(request) as (_, actor):require_scope(actor, Action.ADMIN)
        body = await _json(request, ("id", "slug"))
        with authorized(request) as (catalog, actor):catalog.create_organization(actor, identifier(body["id"]), Slug(body["slug"]), now=now())
        return JSONResponse({"id": body["id"], "slug": body["slug"]}, status=201)

    @app.route("/api/organizations/{organization_id}/members/{user_id}", methods=("PUT",), **protected)
    async def membership(request):
        with authorized(request) as (_, actor):require_scope(actor, Action.ADMIN)
        body = await _json(request, ("role",))
        with authorized(request) as (catalog, actor):catalog.set_membership(actor, Membership(_id(request, "organization_id"), _id(request, "user_id"), OrganizationRole(body["role"])), now=now())
        return JSONResponse({"role": body["role"]})

    @app.route("/api/tokens/{credential_id}/revoke", methods=("POST",), **protected)
    def revoke_token(request):
        with authorized(request) as (catalog, actor):catalog.revoke_credential(actor, _id(request, "credential_id"), now=now())
        return JSONResponse(None, status=200)

    return app
