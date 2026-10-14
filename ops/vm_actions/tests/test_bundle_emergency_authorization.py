import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_auth', ROOT / 'authorize_bundle_retention.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)

class BundleEmergencyTests(unittest.TestCase):
    def env(self):
        return dict(EMERGENCY_PRUNE='true', EMERGENCY_CONFIRM='prune-unreferenced-bundles',
                    GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REPOSITORY='azumag/docich',
                    GITHUB_ACTOR_ID='9018513', GITHUB_TRIGGERING_ACTOR='azumag',
                    GITHUB_REF='refs/heads/main', REF_PROTECTED='true',
                    GITHUB_WORKFLOW_REF='azumag/docich/.github/workflows/vm-bundle-retention.yml@refs/heads/main')

    def test_explicit_owner_manual_only(self):
        self.assertTrue(auth.authorize(self.env()))
        for key in self.env():
            value = self.env(); value[key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError):
                auth.authorize(value)

    def test_scheduled_and_workflow_run_keep_normal_policy(self):
        for event in ('schedule', 'workflow_run', 'workflow_dispatch'):
            for mode in ('', 'false'):
                self.assertFalse(auth.authorize(dict(GITHUB_EVENT_NAME=event, EMERGENCY_PRUNE=mode)))
        for event in ('schedule', 'workflow_run'):
            value = self.env(); value['GITHUB_EVENT_NAME'] = event
            with self.assertRaises(ValueError): auth.authorize(value)

    def test_workflow_keeps_fixed_paths_and_count(self):
        text = (ROOT.parents[1] / '.github/workflows/vm-bundle-retention.yml').read_text()
        self.assertIn('--min-age-seconds 0 --keep-unreferenced 2 --json', text)
        self.assertIn('default: false', text)
        self.assertNotIn('${{ inputs.confirm }}\'', text)
        self.assertLess(text.index('Authorize fixed emergency age override'), text.index('Configure pinned SSH client'))
