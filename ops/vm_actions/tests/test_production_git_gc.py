"""Unit tests for the reviewed post-fetch gc helper (docich#3344).

Covers the daily interval gate, the prune-mode knob (grace everywhere,
post_deploy only after a verified deploy), the one-time canary record, the
size monitor warning, and fail-safe verdicts. gc itself runs against real
temporary repositories -- the helper is the only gc path for the production
checkout, so its behaviour is exercised end-to-end rather than mocked.
"""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / 'ops/vm_actions/production_git_gc.py'
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'

spec = importlib.util.spec_from_file_location('git_gc_helper', HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

gw_spec = importlib.util.spec_from_file_location('gateway_gc', GATEWAY)
gw = importlib.util.module_from_spec(gw_spec)
gw_spec.loader.exec_module(gw)


def _init_repo(path: Path) -> None:
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    for k, v in (('user.email', '<mailto:t@example.com|t@example.com>'), ('user.name', 'T')):
        subprocess.run(['git', '-C', str(path), 'config', k, v], check=True)
    (path / 'f.txt').write_text('v1\n')
    subprocess.run(['git', '-C', str(path), 'add', 'f.txt'], check=True)
    subprocess.run(['git', '-C', str(path), 'commit', '-qm', 'c1'], check=True)


class MaintainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='git-gc-test-'))
        self.repo = self.tmp / 'repo'
        self.repo.mkdir()
        _init_repo(self.repo)
        self.state_file = self.tmp / 'state' / 'docich.json'

    def test_first_run_gcs_and_records_canary(self):
        v = helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024)
        self.assertEqual(v['gc'], 'run')
        self.assertEqual(v['prune'], '--prune=2.weeks.ago')
        self.assertIn('size_pack_kib', v)
        self.assertFalse(v['warn_size_exceeded'])
        canary = v['canary']
        self.assertIn('size-pack', canary['count_objects'])
        self.assertIn(canary['fsck']['status'], ('ok', 'error', 'timeout', 'unavailable'))
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state['canary'], canary)
        self.assertIsInstance(state['last_gc_epoch'], int)

    def test_second_run_within_interval_skips(self):
        helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024)
        v = helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024)
        self.assertEqual(v['gc'], 'skipped_interval')
        self.assertIsNotNone(v['last_gc_epoch'])

    def test_stale_marker_runs_again(self):
        helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024)
        state = json.loads(self.state_file.read_text())
        state['last_gc_epoch'] = 1
        self.state_file.write_text(json.dumps(state))
        v = helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024, now=2*86400)
        self.assertEqual(v['gc'], 'run')
        # Canary recorded once is not re-recorded.
        self.assertEqual(v['canary'], state['canary'])

    def test_corrupt_state_treated_as_fresh(self):
        self.state_file.parent.mkdir(parents=True)
        self.state_file.write_text('{not json')
        v = helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024)
        self.assertEqual(v['gc'], 'run')

    def test_post_deploy_mode_uses_prune_now_knob(self):
        v = helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024, 'post_deploy')
        self.assertEqual(v['prune'], '--prune=now')

    def test_bad_prune_mode_is_refused(self):
        v = helper.maintain(self.repo, self.state_file, 86400, 3*1024*1024, 'aggressive')
        self.assertEqual(v['gc'], 'error')
        self.assertEqual(v['reason'], 'bad_prune_mode')

    def test_warn_flag_on_oversized_repo(self):
        v = helper.maintain(self.repo, self.state_file, 86400, 0)
        self.assertTrue(v['warn_size_exceeded'])


class GatewayHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='git-gc-gw-'))
        self.repo = self.tmp / 'docich'
        self.repo.mkdir()
        _init_repo(self.repo)
        self.cfg = {'state': str(self.tmp / 'state'),
                    'repos': {'docich': {'production': str(self.repo), 'mode': 'git'}}}
        self.sha = subprocess.check_output(
            ['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()

    def _commit_helper(self):
        target = self.repo / 'ops/vm_actions/production_git_gc.py'
        target.parent.mkdir(parents=True)
        target.write_bytes(HELPER.read_bytes())
        subprocess.run(['git', '-C', str(self.repo), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'commit', '-qm', 'add gc helper'], check=True)
        return subprocess.check_output(
            ['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()

    def test_unavailable_when_commit_lacks_helper(self):
        v = gw._post_op_git_gc(self.cfg, 'docich', self.repo, self.sha, 'grace')
        self.assertEqual(v, {'status': 'unavailable'})

    def test_runs_helper_and_throttles(self):
        sha = self._commit_helper()
        v = gw._post_op_git_gc(self.cfg, 'docich', self.repo, sha, 'grace')
        self.assertEqual(v['gc'], 'run')
        self.assertEqual(v['prune'], '--prune=2.weeks.ago')
        self.assertIn('canary', v)
        v2 = gw._post_op_git_gc(self.cfg, 'docich', self.repo, sha, 'grace')
        self.assertEqual(v2['gc'], 'skipped_interval')
        self.assertTrue(gw.git_gc_state_file(self.cfg, 'docich').is_file())


if __name__ == '__main__':
    unittest.main()
