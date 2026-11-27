import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_receive import descriptor_mechanism


def acceptance_module():
    spec = importlib.util.spec_from_file_location('hosting_acceptance', Path(__file__).resolve().parents[1]/'scripts/accept_hosting.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class HostingAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def test_index_identity_refuses_wrong_release_and_direct_sources(self):
        module = acceptance_module()
        profile = {'version': '0.1.0a2', 'commit': 'f'*40, 'source_kind': 'pypi'}
        engine = SimpleNamespace(version='0.0.0', read_text=lambda name: None)
        with (patch.object(module.metadata, 'distribution', return_value=engine),
              patch.object(module.Path, 'read_text', return_value=json.dumps({'gitdx': profile}))):
            with self.assertRaisesRegex(RuntimeError, 'engine version differs'):
                module.identities()
            engine.version = profile['version']
            for direct in ('', 'invalid metadata', json.dumps({'url': 'https://github.com/wispsy/gitdx', 'vcs_info': {'commit_id': profile['commit']}}),
                           json.dumps({'url': 'file:///tmp/gitdx.whl', 'archive_info': {}})):
                engine.read_text = lambda name: direct
                with self.assertRaisesRegex(RuntimeError, 'selected package index'):
                    module.identities()

    async def test_two_user_native_workflow_restart_refusals_and_receipt(self):
        module = acceptance_module()
        with descriptor_mechanism():
            receipt = await module.run_acceptance()
        self.assertEqual(receipt['profile'], 'hosted-sha256-linux')
        self.assertIn('native-push-after-restart', receipt['checks'])
        self.assertIn('protected-and-outsider-push-refusal', receipt['checks'])
        self.assertIn('sha1-write-refusal', receipt['checks'])
        profile = json.loads((Path(__file__).resolve().parents[1]/'docs/backend-sources.json').read_text())
        self.assertEqual(receipt['packages']['gitdx']['commit'], profile['gitdx']['commit'])
        self.assertEqual(receipt['packages']['gitdx']['source_kind'], 'pypi')
        self.assertEqual(receipt['packages']['gitdx']['provenance'], 'published-release-manifest')
        self.assertIn('untrusted-sha1', receipt['unsupported'])
        self.assertNotIn('gtc_', json.dumps(receipt))

    async def test_explicit_read_only_profile_both_formats_on_every_platform(self):
        receipt = await acceptance_module().run_acceptance(read_only=True)
        self.assertEqual(receipt['profile'], 'read-only-native')
        self.assertIn('sha1-restart-fetch-fsck', receipt['checks'])
        self.assertIn('sha256-restart-fetch-fsck', receipt['checks'])
        self.assertNotIn('native-commit-push-main', receipt['checks'])
