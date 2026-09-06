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
        self.docich = self.base / 'docich'
        self.docich.mkdir()
        self.cfg = {
            'state': str(self.state),
            'repos': {
                'docich': {'production': str(self.docich), 'mode': 'git'},
            },
        }


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


    def test_git_deploy_updates_owned_submodule_to_parent_gitlink(self):
        subremote = self.base / 'soviet-remote'
        subprocess.run(['git', 'init', '-q', subremote], check=True)
        subprocess.run(['git', '-C', subremote, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', subremote, 'config', 'user.name', 'T'], check=True)
        (subremote / 'game.txt').write_text('v1\n')
        subprocess.run(['git', '-C', subremote, 'add', 'game.txt'], check=True)
        subprocess.run(['git', '-C', subremote, 'commit', '-qm', 'v1'], check=True)

        subprocess.run(['git', 'init', '-q', self.docich], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.name', 'T'], check=True)
        subprocess.run(['git', '-C', self.docich, '-c', 'protocol.file.allow=always',
                        'submodule', 'add', '-q', str(subremote), 'games/soviet_now'], check=True)
        subprocess.run(['git', '-C', self.docich, 'commit', '-qam', 'parent-v1'], check=True)
        old_parent = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()
        gw.write_json(gw.current_file(self.cfg, 'docich'), {'mode': 'git', 'sha': old_parent, 'previous_head': None})

        (subremote / 'game.txt').write_text('v2\n')
        subprocess.run(['git', '-C', subremote, 'commit', '-qam', 'v2'], check=True)
        sub_v2 = subprocess.check_output(['git', '-C', subremote, 'rev-parse', 'HEAD'], text=True).strip()

        candidate = self.base / 'candidate-submodule'
        subprocess.run(['git', 'clone', '-q', self.docich, candidate], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.name', 'T'], check=True)
        subprocess.run(['git', '-C', candidate, 'update-index', '--cacheinfo',
                        f'160000,{sub_v2},games/soviet_now'], check=True)
        subprocess.run(['git', '-C', candidate, 'commit', '-qm', 'bump-submodule'], check=True)
        new_parent = subprocess.check_output(['git', '-C', candidate, 'rev-parse', 'HEAD'], text=True).strip()
        bundle = gw.bundle_file(self.cfg, 'docich', new_parent)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', candidate, 'bundle', 'create', bundle, 'HEAD'], check=True)

        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            gw.deploy_git(self.cfg, 'docich', new_parent)

        actual = subprocess.check_output(
            ['git', '-C', self.docich / 'games/soviet_now', 'rev-parse', 'HEAD'], text=True
        ).strip()
        self.assertEqual(actual, sub_v2)


if __name__ == '__main__':
    unittest.main()
