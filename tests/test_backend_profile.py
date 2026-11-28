from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gitdx import Blob, Commit, Repository, Tree
from gitcx import (BackendMismatch, Catalog, GitdxBackend, Principal, REQUIRED_CAPABILITIES,
                     ProvisioningError, RepositoryInfo, RepositoryService, Slug, StorageId)
from test_permissions import OWNER, repository


class BackendProfileTests(unittest.TestCase):
    def test_reftable_storage_does_not_claim_managed_mutation_capabilities(self):
        if shutil.which('git') is None:
            if os.environ.get('GITCX_REQUIRE_GIT')=='1':self.fail('native Git oracle is required')
            self.skipTest('native Git oracle is unavailable')
        environment={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        environment.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
        with tempfile.TemporaryDirectory() as temporary:
            backend=GitdxBackend(Path(temporary)/'git')
            for format in ('sha1','sha256'):
                storage=StorageId.new();backend.create(storage,object_format=format)
                path=backend.root/(storage.value+'.git')
                subprocess.run(['git','-C',str(path),'refs','migrate','--ref-format=reftable'],
                               capture_output=True,env=environment,check=True,timeout=30)
                with self.assertRaises(ProvisioningError):backend.inspect(storage)

    def test_both_formats_execute_capabilities_without_external_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            backend = GitdxBackend(Path(temporary)/"git")
            for format in ("sha1", "sha256"):
                storage = StorageId.new()
                with patch.dict(os.environ, PATH=""), patch("subprocess.Popen", side_effect=AssertionError("runtime subprocess")), patch("os.system", side_effect=AssertionError("runtime shell")):
                    info = backend.create(storage, object_format=format)
                    self.assertEqual(info.capabilities, REQUIRED_CAPABILITIES)
                    engine = Repository.open(backend.root/(storage.value+".git"))
                    blob = Blob(b"binary\0\xff").to_raw();oid = engine.objects.write(blob)
                    self.assertEqual(engine.objects.read(oid), blob)
                    tree = engine.objects.write(Tree(format=engine.object_format).to_raw())
                    identity = b"Example <example@example.invalid> 1700000000 +0000"
                    commit = engine.objects.write(Commit.create(tree, author=identity, committer=identity, message=b"profile\n").to_raw())
                    engine.refs.update("refs/heads/main", commit, expected=None)
                    self.assertEqual(engine.refs.resolve().oid, commit)
                    self.assertEqual(backend.inspect(storage), info)

    def test_missing_or_inconsistent_capabilities_refuse_before_ready_and_open(self):
        with tempfile.TemporaryDirectory() as temporary, Catalog(Path(temporary)/"catalog.sqlite3") as catalog:
            catalog.create_account(OWNER, Slug("owner"), now=1)
            actor = Principal(OWNER);backend = GitdxBackend(Path(temporary)/"git");service = RepositoryService(catalog, backend)
            record = repository();valid = RepositoryInfo("sha1", True, REQUIRED_CAPABILITIES)
            for bad in (replace(valid, bare=False), replace(valid, object_format="sha256"), replace(valid, capabilities={"objects.read"}), None):
                with patch.object(backend, "create", return_value=bad):
                    with self.assertRaises(BackendMismatch):service.create(actor, record, now=2)
                self.assertEqual(catalog.provisioning_state(record.id), "pending")
                self.assertEqual(service.list(actor), ())
            service.recover(actor, record.id, now=3)
            with patch.object(backend, "inspect", return_value=replace(valid, capabilities=())):
                with self.assertRaises(BackendMismatch):service.open(actor, record.id)
