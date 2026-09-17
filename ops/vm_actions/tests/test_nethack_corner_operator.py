import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / 'ops/vm_actions/authorize_nethack_corner.py'
WF = ROOT / '.github/workflows/nethack-corner-operator.yml'
GENERIC_AUTH = ROOT / 'ops/vm_actions/authorize.py'
sys.path.insert(0, str(ROOT / 'src'))

from docich import nethack_corner_operator as operator
from docich.nethack_corner import NethackCornerError


class NetHackCornerAuthorizeTests(unittest.TestCase):
    def run_auth(self, **overrides):
        env = {
            'GITHUB_REPOSITORY': 'azumag/docich',
            'GITHUB_REPOSITORY_ID': '1327276249',
            'GITHUB_REPOSITORY_OWNER': 'azumag',
            'GITHUB_REPOSITORY_OWNER_ID': '9018513',
            'GITHUB_ACTOR': 'azumag',
            'GITHUB_ACTOR_ID': '9018513',
            'GITHUB_TRIGGERING_ACTOR': 'azumag',
            'GITHUB_REF': 'refs/heads/main',
            'GITHUB_REF_PROTECTED': 'true',
            'GITHUB_DEFAULT_BRANCH': 'main',
            'GITHUB_WORKFLOW_REF': 'azumag/docich/.github/workflows/nethack-corner-operator.yml@refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_SHA': 'a' * 40,
            'INPUT_OPERATION': 'start',
            'INPUT_DURATION_MINUTES': '5',
            'INPUT_CONFIRM': 'production',
        }
        env.update(overrides)
        return subprocess.run(['python3', str(AUTH)], text=True, capture_output=True, env=env)

    def test_owner_dispatch_allows_bounded_start(self):
        p = self.run_auth()
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data['duration_minutes'], 5)
        self.assertEqual((data['operation'], data['target'], data['ref']), ('start', 'production', 'main'))

    def test_stop_and_status_are_fixed_operations(self):
        for operation in ('stop', 'status'):
            with self.subTest(operation=operation):
                p = self.run_auth(INPUT_OPERATION=operation)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(json.loads(p.stdout)['operation'], operation)

    def test_non_owner_and_non_owner_rerun_fail_closed(self):
        self.assertNotEqual(self.run_auth(GITHUB_ACTOR='collab', GITHUB_ACTOR_ID='42').returncode, 0)
        self.assertNotEqual(self.run_auth(GITHUB_TRIGGERING_ACTOR='collab').returncode, 0)

    def test_requires_protected_main_and_main_workflow_copy(self):
        self.assertNotEqual(self.run_auth(GITHUB_REF_PROTECTED='false').returncode, 0)
        self.assertNotEqual(self.run_auth(GITHUB_REF='refs/heads/feature').returncode, 0)
        self.assertNotEqual(self.run_auth(GITHUB_DEFAULT_BRANCH='release').returncode, 0)
        self.assertNotEqual(self.run_auth(
            GITHUB_WORKFLOW_REF='azumag/docich/.github/workflows/nethack-corner-operator.yml@refs/heads/feature'
        ).returncode, 0)

    def test_dispatch_requires_production_confirmation_and_range(self):
        self.assertNotEqual(self.run_auth(INPUT_CONFIRM='').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_OPERATION='deploy').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='0').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='61').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='5;id').returncode, 0)

    def test_unexpected_event_fails_closed(self):
        self.assertNotEqual(self.run_auth(GITHUB_EVENT_NAME='push').returncode, 0)


class NetHackCornerOperatorTests(unittest.TestCase):
    def _fake_g(self, base: Path):
        repo = base / 'repo'
        repo.mkdir(exist_ok=True)
        state = base / 'state'
        state.mkdir(exist_ok=True)
        config = repo / 'config.toml'
        config.write_text('x', encoding='utf-8')
        return SimpleNamespace(state_dir=state, repo_root=repo, config_path=config)

    def test_validate_duration_accepts_only_bounded_integers(self):
        for value in (1, 5, 60, '5'):
            with self.subTest(value=value):
                self.assertEqual(operator._validate_duration(value), int(value))
        for value in (True, 0, 61, -1, '5;id', '', None, '5.0'):
            with self.subTest(value=value):
                with self.assertRaises(NethackCornerError):
                    operator._validate_duration(value)

    def test_launcher_uses_fixed_argv_private_log_and_detached_session(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        proc = mock.Mock(pid=1234)
        proc.poll.return_value = None
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator.subprocess, 'Popen', return_value=proc) as popen, \
             mock.patch.object(operator.time, 'sleep'):
            result = operator.launch(fake_g.config_path, 5)
        self.assertEqual(result['status'], 'started')
        self.assertEqual(result['duration_minutes'], 5)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:3], ['-m', 'docich.nethack_corner_manual'])
        self.assertEqual(argv[3:5], ['--config', str(fake_g.config_path)])
        self.assertEqual(argv[5:], ['start', '--duration-minutes', '5'])
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        logs = list((fake_g.state_dir / 'logs').glob('nethack-manual-*.log'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(stat.S_IMODE(logs[0].stat().st_mode), 0o600)

    def test_launcher_rejects_early_exit_and_out_of_range(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        proc = mock.Mock(pid=1234)
        proc.poll.return_value = 0
        with self.assertRaises(NethackCornerError):
            operator.launch(fake_g.config_path, 0)
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator.subprocess, 'Popen', return_value=proc), \
             mock.patch.object(operator.time, 'sleep'):
            with self.assertRaises(NethackCornerError):
                operator.launch(fake_g.config_path, 5)
        logs = list((fake_g.state_dir / 'logs').glob('nethack-manual-*.log'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(stat.S_IMODE(logs[0].stat().st_mode), 0o600)

    def test_stop_runs_fixed_manual_stop_and_surfaces_failure(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(operator.stop(fake_g.config_path), {'status': 'stopped'})
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:3], ['-m', 'docich.nethack_corner_manual'])
        self.assertEqual(argv[-1], 'stop')
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator.subprocess, 'run', return_value=SimpleNamespace(returncode=2)):
            with self.assertRaises(NethackCornerError):
                operator.stop(fake_g.config_path)

    def test_status_category_maps_only_fixed_states(self):
        self.assertEqual(operator.status_category(Path(tempfile.mkdtemp(prefix='nethack-op-'))), 'idle')
        for status, expected in (
            ('starting', 'starting'),
            ('active', 'active'),
            ('failed', 'failed'),
            ('completed', 'terminal'),
            ('interrupted', 'terminal'),
            ('bogus', 'unreadable'),
        ):
            with self.subTest(status=status):
                base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
                (base / operator.MANUAL_STATE_FILE).write_text(
                    json.dumps({'status': status}), encoding='utf-8'
                )
                self.assertEqual(operator.status_category(base), expected)
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        (base / operator.MANUAL_STATE_FILE).write_text('{not json', encoding='utf-8')
        self.assertEqual(operator.status_category(base), 'unreadable')

    def test_status_returns_fixed_exit_code(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        (fake_g.state_dir / operator.MANUAL_STATE_FILE).write_text(
            json.dumps({'status': 'active'}), encoding='utf-8'
        )
        with mock.patch.object(operator, 'load_global', return_value=fake_g):
            self.assertEqual(operator.status(fake_g.config_path), operator.STATUS_EXIT_CODES['active'])

    def test_main_requires_exactly_one_operation(self):
        self.assertEqual(operator.main(['--config', 'x']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--start', '--stop']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--stop', '--status']), 2)


class NetHackCornerWorkflowPolicyTests(unittest.TestCase):
    def test_workflow_is_owner_only_fixed_operation(self):
        text = WF.read_text(encoding='utf-8')
        self.assertIn('github.actor_id == 9018513', text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn('github.ref_protected == true', text)
        self.assertIn('environment: vm-operations', text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('submodules: false', text)
        self.assertIn('StrictHostKeyChecking=yes', text)
        self.assertIn('ForwardAgent=no', text)
        self.assertIn('ClearAllForwardings=yes', text)
        self.assertIn('bin/docich-nethack-corner-operator', text)
        self.assertIn('cd /home/ubuntu/docich', text)
        self.assertIn('/home/ubuntu/docich/bin/docich-nethack-corner-operator', text)
        self.assertIn('/home/ubuntu/docich/config/docich.soren-live.toml', text)
        self.assertIn('id: start', text)
        self.assertIn('id: stop', text)
        self.assertIn('id: status', text)
        self.assertIn('continue-on-error: true', text)
        self.assertIn('NetHack corner start failed', text)
        self.assertNotIn('pull_request_target:', text)
        self.assertNotIn('inputs.command', text)
        self.assertNotIn('event.comment.body', text)
        self.assertNotIn('event.issue.body', text)

    def test_public_repo_generic_arbitrary_exec_stays_disabled(self):
        text = GENERIC_AUTH.read_text(encoding='utf-8')
        self.assertIn("repo_private == 'false' and op == 'exec'", text)
        self.assertIn('arbitrary VM exec is disabled when the repository is public', text)


if __name__ == '__main__':
    unittest.main()
