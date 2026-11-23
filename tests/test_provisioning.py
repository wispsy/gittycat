from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gitdx import Repository
from gittycat import Catalog,GitdxBackend,PermissionDenied,Principal,ProvisioningError,RepositoryService,Slug,StorageId,Visibility
from test_permissions import OWNER,OUTSIDER,repository


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.catalog=Catalog(self.root/"catalog.sqlite3");self.addCleanup(self.catalog.close)
        self.owner=Principal(OWNER);self.catalog.create_account(OWNER,Slug("owner"),now=1)
        self.backend=GitdxBackend(self.root/"git");self.service=RepositoryService(self.catalog,self.backend)

    def test_create_both_formats_idempotence_and_ready_storage_never_reinitialized(self):
        for i,format in enumerate(("sha1","sha256"),2):
            record=repository(id=str(i)*32,slug=Slug("repo-"+format),storage_id=StorageId.new(),object_format=format)
            hosted=self.service.create(self.owner,record,now=2)
            self.assertTrue(hosted.info.bare);self.assertEqual(hosted.info.object_format,format)
            self.assertEqual(self.service.create(self.owner,record,now=3),hosted)
            path=self.backend.root/(record.storage_id.value+".git")
            self.assertEqual(Repository.open(path).object_format.value,format)
            (path/"preserve").write_bytes(b"user data")
            self.service.create(self.owner,record,now=4);self.assertEqual((path/"preserve").read_bytes(),b"user data")
            renamed=path.with_name("removed-"+path.name);path.rename(renamed)
            with self.assertRaises(ProvisioningError):self.service.create(self.owner,record,now=5)
            self.assertFalse(path.exists());renamed.rename(path)

    def test_denied_open_and_creation_do_not_lookup_storage(self):
        record=repository();self.service.create(self.owner,record,now=2)
        with patch.object(self.backend,"inspect",side_effect=AssertionError("backend lookup before authorization")):
            with self.assertRaises(PermissionDenied):self.service.open(Principal(OUTSIDER),record.id)
        with patch.object(self.backend,"create",side_effect=AssertionError("backend creation before authorization")):
            with self.assertRaises(PermissionDenied):self.service.create(Principal(OUTSIDER),record,now=3)
        self.assertEqual(self.service.list(Principal()),())

    def test_catalog_failure_after_publication_stays_hidden_and_recovers(self):
        record=repository(visibility=Visibility.PUBLIC)
        with patch.object(self.catalog,"finish_provisioning",side_effect=RuntimeError("injected DB failure")):
            with self.assertRaises(RuntimeError):self.service.create(self.owner,record,now=2)
        self.assertTrue((self.backend.root/(record.storage_id.value+".git")).exists())
        self.assertEqual(self.catalog.list_repositories(Principal()),())
        self.assertFalse(any(j.resource_id==record.id for j in self.catalog.pending_jobs()))
        with self.assertRaises(PermissionDenied):self.service.open(self.owner,record.id)
        with Catalog(self.catalog.path) as reopened:
            service=RepositoryService(reopened,self.backend)
            self.assertEqual(service.recover(self.owner,record.id,now=3).record,record)
            self.assertEqual(service.list(Principal()),(record,))

    def test_interrupted_staging_recovery_and_unknown_orphans_preserved(self):
        record=repository()
        with patch("gittycat.provisioning.os.rename",side_effect=OSError("injected publish failure")):
            with self.assertRaises(OSError):self.service.create(self.owner,record,now=2)
        self.assertEqual(self.service.recover(self.owner,record.id,now=3).record,record)
        other=replace(record,id="9"*32,slug=Slug("orphan"),storage_id=StorageId.new())
        path=self.backend.root/(other.storage_id.value+".git");path.mkdir();(path/"keep").write_bytes(b"unmanaged")
        with self.assertRaises(ProvisioningError):self.service.create(self.owner,other,now=4)
        self.assertEqual((path/"keep").read_bytes(),b"unmanaged")
        with self.assertRaises(ValueError):StorageId("../escape")

    def test_static_symlink_and_existing_lock_refused(self):
        storage=StorageId.new();lock=self.backend.root/(storage.value+".lock");lock.write_bytes(b"another owner")
        with self.assertRaises(ProvisioningError):self.backend.create(storage,object_format="sha1")
        self.assertEqual(lock.read_bytes(),b"another owner");lock.unlink()
        outside=self.root/"outside";outside.mkdir()
        try:(self.backend.root/(storage.value+".git")).symlink_to(outside,target_is_directory=True)
        except OSError as error:self.skipTest(str(error))
        with self.assertRaises(ProvisioningError):self.backend.create(storage,object_format="sha1")
        self.assertEqual(list(outside.iterdir()),[])


class ProvisioningGitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("git"):
            if os.environ.get("GITTYCAT_REQUIRE_GIT")=="1":raise RuntimeError("native Git oracle is required")
            raise unittest.SkipTest("native Git is unavailable")

    def test_native_git_opens_provisioned_repositories(self):
        for format in ("sha1","sha256"):
            with tempfile.TemporaryDirectory() as tmp:
                backend=GitdxBackend(tmp);storage=StorageId.new();backend.create(storage,object_format=format)
                path=Path(tmp)/(storage.value+".git")
                env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")};env.update(GIT_CONFIG_NOSYSTEM="1",GIT_CONFIG_GLOBAL=os.devnull)
                result=subprocess.run(["git","-C",str(path),"rev-parse","--is-bare-repository","--show-object-format"],env=env,capture_output=True,check=True)
                self.assertEqual(result.stdout.splitlines(),[b"true",format.encode()])
