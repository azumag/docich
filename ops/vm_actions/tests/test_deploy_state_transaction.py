import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'

spec = importlib.util.spec_from_file_location('gateway_under_test', GATEWAY)
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class DeployStateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='vmops-state-txn-'))
        self.state = self.base / 'state'
        self.state.mkdir()
        self.soren = self.base / 'soren'
        self.soren.mkdir()
        self.docich = self.base / 'docich'
        self.docich.mkdir()
        self.cfg = {
            'state': str(self.state),
            'repos': {
                'soviet_now': {'production': str(self.soren), 'mode': 'overlay'},
                'docich': {'production': str(self.docich), 'mode': 'git'},
            },
        }

    def test_overlay_rolls_back_when_state_commit_fails(self):
        old_sha = 'a' * 40
        new_sha = 'b' * 40
        production = self.soren / 'app.py'
        production.write_text('v1\n')
        state_path = gw.current_file(self.cfg, 'soviet_now')
        old_files = {'app.py': gw.file_meta(production)}
        gw.write_json(state_path, {
            'mode': 'overlay',
            'sha': old_sha,
            'files': old_files,
            'previous_backup': None,
        })
        release = gw.release_dir(self.cfg, 'soviet_now', new_sha)
        release.mkdir(parents=True)
        (release / 'app.py').write_text('v2\n')

        real_write_json = gw.write_json

        def fail_state_write(path, value):
            if Path(path) == state_path:
                raise OSError('state write failed')
            return real_write_json(path, value)

        with mock.patch.object(gw, 'write_json', side_effect=fail_state_write):
            with self.assertRaises(OSError):
                gw.deploy_overlay(self.cfg, 'soviet_now', new_sha)

        self.assertEqual(production.read_text(), 'v1\n')
        self.assertEqual(gw.read_json(state_path)['sha'], old_sha)

    def test_git_rolls_back_when_state_commit_fails(self):
        subprocess.run(['git', 'init', '-q', self.docich], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.name', 'T'], check=True)
        production = self.docich / 'app.py'
        production.write_text('v1\n')
        subprocess.run(['git', '-C', self.docich, 'add', 'app.py'], check=True)
        subprocess.run(['git', '-C', self.docich, 'commit', '-qm', 'v1'], check=True)
        old_sha = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()

        state_path = gw.current_file(self.cfg, 'docich')
        gw.write_json(state_path, {'mode': 'git', 'sha': old_sha, 'previous_head': None})

        candidate = self.base / 'candidate'
        subprocess.run(['git', 'clone', '-q', self.docich, candidate], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.name', 'T'], check=True)
        (candidate / 'app.py').write_text('v2\n')
        subprocess.run(['git', '-C', candidate, 'add', 'app.py'], check=True)
        subprocess.run(['git', '-C', candidate, 'commit', '-qm', 'v2'], check=True)
        new_sha = subprocess.check_output(['git', '-C', candidate, 'rev-parse', 'HEAD'], text=True).strip()
        bundle = gw.bundle_file(self.cfg, 'docich', new_sha)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', candidate, 'bundle', 'create', bundle, 'HEAD'], check=True)

        real_write_json = gw.write_json

        def fail_state_write(path, value):
            if Path(path) == state_path:
                raise OSError('state write failed')
            return real_write_json(path, value)

        with mock.patch.object(gw, 'write_json', side_effect=fail_state_write):
            with self.assertRaises(OSError):
                gw.deploy_git(self.cfg, 'docich', new_sha)

        head = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual(head, old_sha)
        self.assertEqual(production.read_text(), 'v1\n')
        self.assertEqual(gw.read_json(state_path)['sha'], old_sha)


if __name__ == '__main__':
    unittest.main()
