import importlib.util
import json
from pathlib import Path
import unittest

from test_receive import descriptor_mechanism


def acceptance_module():
    spec = importlib.util.spec_from_file_location('hosting_acceptance', Path(__file__).resolve().parents[1]/'scripts/accept_hosting.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class HostingAcceptanceTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIn('untrusted-sha1', receipt['unsupported'])
        self.assertNotIn('gtc_', json.dumps(receipt))

    async def test_explicit_read_only_profile_both_formats_on_every_platform(self):
        receipt = await acceptance_module().run_acceptance(read_only=True)
        self.assertEqual(receipt['profile'], 'read-only-native')
        self.assertIn('sha1-restart-fetch-fsck', receipt['checks'])
        self.assertIn('sha256-restart-fetch-fsck', receipt['checks'])
        self.assertNotIn('native-commit-push-main', receipt['checks'])
