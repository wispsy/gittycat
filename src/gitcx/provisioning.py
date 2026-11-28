"""Authorized, restart-safe bare repository provisioning through Gitdx."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil

from gitdx import Repository

from .backend import RepositoryInfo, REQUIRED_CAPABILITIES, require_backend_info
from .domain import Action, RepositoryRecord, StorageId
from .permissions import PermissionDenied


class ProvisioningError(Exception):
    """Storage cannot be opened or safely reconciled."""


class GitdxBackend:
    def __init__(self, root):
        self.root=Path(root).absolute()
        if self.root.is_symlink():raise ProvisioningError("repository root cannot be a symlink")
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not self.root.is_dir():raise ProvisioningError("repository root must be a directory")

    def _path(self, storage_id):
        if not isinstance(storage_id,StorageId):raise TypeError("backend requires an opaque StorageId")
        path=self.root/(storage_id.value+".git")
        if self.root.is_symlink() or path.is_symlink():raise ProvisioningError("repository storage cannot be a symlink")
        return path

    def _receipt(self, path, storage_id, object_format=None):
        # Keep the existing on-disk ownership format across the package rename.
        marker=path/"gittycat-owner"
        if marker.is_symlink() or not marker.is_file():raise ProvisioningError("unmanaged repository storage is not adopted")
        with marker.open("rb") as stream:data=stream.read(4097)
        if len(data)>4096:raise ProvisioningError("invalid repository ownership receipt")
        try:record=json.loads(data)
        except (ValueError,UnicodeError) as error:raise ProvisioningError("invalid repository ownership receipt") from error
        if not isinstance(record,dict) or record.get("storage_id")!=storage_id.value or record.get("object_format") not in ("sha1","sha256") or (object_format and record["object_format"]!=object_format):raise ProvisioningError("repository ownership/format mismatch")
        return record["object_format"]

    def open_repository(self, storage_id):
        """Open managed native storage after the service authorizes its caller."""
        path=self._path(storage_id);format=self._receipt(path,storage_id)
        repo=Repository.open(path)
        if (repo.config.values.get('extensions','refstorage','files') or '').lower()!='files':
            raise ProvisioningError('managed mutation capabilities require files reference storage')
        if not repo.bare or repo.object_format.value!=format:raise ProvisioningError("managed storage must be a format-consistent bare repository")
        return repo

    def inspect(self, storage_id):
        repo=self.open_repository(storage_id)
        return RepositoryInfo(repo.object_format.value,True,REQUIRED_CAPABILITIES)

    def create(self, storage_id, *, object_format):
        if object_format not in ("sha1","sha256"):raise ValueError("unsupported native object format")
        path=self._path(storage_id)
        lock=self.root/(storage_id.value+".lock")
        if lock.is_symlink():raise ProvisioningError("provisioning lock cannot be a symlink")
        try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError as error:raise ProvisioningError("provisioning lock exists; its owner must reconcile it") from error
        os.close(fd)
        temporary=self.root/(".provision-"+storage_id.value)
        try:
            if path.exists():
                self._receipt(path,storage_id,object_format);return self.inspect(storage_id)
            if temporary.is_symlink():raise ProvisioningError("provisioning directory cannot be a symlink")
            receipt=json.dumps({"storage_id":storage_id.value,"object_format":object_format},sort_keys=True).encode()
            if temporary.exists():
                self._receipt(temporary,storage_id,object_format)
                # Only an unpublished staging container carrying our receipt is
                # replaced. Published repositories are never reset or removed.
                shutil.rmtree(temporary)
            temporary.mkdir(mode=0o700)
            (temporary/"gittycat-owner").write_bytes(receipt)
            repo=Repository.init(temporary/"repository.git",bare=True,object_format=object_format)
            (repo.git_dir/"gittycat-owner").write_bytes(receipt)
            os.rename(repo.git_dir,path)
            shutil.rmtree(temporary)
            return self.inspect(storage_id)
        finally:lock.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class HostedRepository:
    record: RepositoryRecord
    info: RepositoryInfo


class RepositoryService:
    def __init__(self,catalog,backend):self.catalog,self.backend=catalog,backend

    def create(self,actor,record,*,now):
        state=self.catalog.reserve_repository(actor,record,now=now)
        if state=="ready":return self.open(actor,record.id,action=Action.ADMIN)
        return self.recover(actor,record.id,now=now)

    def recover(self,actor,repository_id,*,now):
        with self.catalog.transaction():
            record=self.catalog.provisioning_record(actor,repository_id)
            state=self.catalog.provisioning_state(repository_id)
            info=self.backend.inspect(record.storage_id) if state=="ready" else self.backend.create(record.storage_id,object_format=record.object_format)
            require_backend_info(info,record.object_format)
            self.catalog.finish_provisioning(actor,repository_id,now=now)
            return HostedRepository(record,info)

    def open(self,actor,repository_id,*,action=Action.READ):
        with self.catalog.transaction():
            record=self.catalog.get_repository(actor,repository_id,action=action)
            if self.catalog.provisioning_state(repository_id)!="ready":raise PermissionDenied("repository not found or permission denied")
            info=self.backend.inspect(record.storage_id)
            require_backend_info(info,record.object_format)
            return HostedRepository(record,info)

    def list(self,actor,*,limit=50,after=""):
        return self.catalog.list_repositories(actor,limit=limit,after=after,ready_only=True)
