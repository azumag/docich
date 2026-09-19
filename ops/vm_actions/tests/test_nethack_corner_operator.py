import io
import json
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
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

    def test_stop_status_and_recover_are_fixed_operations(self):
        for operation in ('stop', 'status', 'recover'):
            with self.subTest(operation=operation):
                p = self.run_auth(INPUT_OPERATION=operation)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(json.loads(p.stdout)['operation'], operation)

    def test_force_recover_is_a_fixed_owner_operation_with_the_same_guards(self):
        p = self.run_auth(INPUT_OPERATION='force-recover')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['operation'], 'force-recover')
        # every existing guard still applies to it
        self.assertNotEqual(self.run_auth(INPUT_OPERATION='force-recover', INPUT_CONFIRM='').returncode, 0)
        self.assertNotEqual(
            self.run_auth(INPUT_OPERATION='force-recover', GITHUB_ACTOR='collab', GITHUB_ACTOR_ID='42').returncode, 0
        )
        self.assertNotEqual(
            self.run_auth(INPUT_OPERATION='force-recover', GITHUB_REF_PROTECTED='false').returncode, 0
        )
        # only the exact fixed spelling is accepted
        for bad in ('force_recover', 'forcerecover', 'force-recover;id', 'FORCE-RECOVER', 'force-recover '):
            with self.subTest(operation=bad):
                self.assertNotEqual(self.run_auth(INPUT_OPERATION=bad).returncode, 0)

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
             mock.patch.object(
                 operator.subprocess, 'run',
                 return_value=SimpleNamespace(returncode=2, stderr='docich: エラー: docich up が失敗しました (rc=1)'),
             ):
            with self.assertRaises(NethackCornerError):
                operator.stop(fake_g.config_path)
        marker = json.loads(
            (fake_g.state_dir / 'nethack_corner_manual_stop_failure.json').read_text(encoding='utf-8')
        )
        self.assertEqual(marker['returncode'], 2)
        self.assertEqual(marker['category'], 'prepare_runtime_failed')

    def test_stop_failure_category_is_bounded(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        cases = {
            'docich: エラー: docich up が失敗しました (rc=1)': 'prepare_runtime_failed',
            'docich: エラー: game switchに失敗しました: recovery_required': 'switch_back_failed',
            'traceback token=SUPERSECRET': 'unknown',
        }
        for stderr, expected in cases.items():
            with self.subTest(expected=expected):
                operator._record_stop_failure(fake_g, 2, stderr)
                marker = json.loads(
                    (fake_g.state_dir / 'nethack_corner_manual_stop_failure.json').read_text(encoding='utf-8')
                )
                self.assertEqual(marker['category'], expected)
                self.assertNotIn('SUPERSECRET', json.dumps(marker))

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

    def test_recover_restores_only_the_recorded_previous_game(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        manager = mock.MagicMock()
        manager._read_state.return_value = {'status': 'failed', 'previous_game': 'sorengame'}
        manager._active_game_reader.return_value = 'nethack'
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator, 'ManualNethackCornerManager', return_value=manager):
            result = operator.recover(fake_g.config_path)
        self.assertEqual(
            result, {'status': 'recovered', 'from_game': 'nethack', 'to_game': 'sorengame'}
        )
        manager._transition_to.assert_called_once_with('nethack', 'sorengame')

    def test_recover_noops_when_nethack_is_not_active(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        manager = mock.MagicMock()
        manager._read_state.return_value = {'status': 'failed', 'previous_game': 'sorengame'}
        manager._active_game_reader.return_value = 'sorengame'
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator, 'ManualNethackCornerManager', return_value=manager):
            result = operator.recover(fake_g.config_path)
        self.assertEqual(result['status'], 'noop')
        manager._transition_to.assert_not_called()

    def test_recover_fails_closed_without_a_previous_game(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        manager = mock.MagicMock()
        manager._read_state.return_value = {'status': 'failed', 'previous_game': None}
        manager._active_game_reader.return_value = 'nethack'
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator, 'ManualNethackCornerManager', return_value=manager):
            with self.assertRaises(NethackCornerError):
                operator.recover(fake_g.config_path)
        manager._transition_to.assert_not_called()

    def test_main_requires_exactly_one_operation(self):
        self.assertEqual(operator.main(['--config', 'x']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--start', '--stop']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--stop', '--status']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--recover', '--status']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--force-recover', '--recover']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--force-recover', '--stop']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--force-recover', '--status']), 2)
        self.assertEqual(operator.main(['--config', 'x', '--force-recover', '--start']), 2)

    def test_force_recover_exit_codes_are_fixed_unique_and_disjoint(self):
        codes = operator.FORCE_RECOVER_EXIT_CODES
        self.assertEqual(codes['recovered'], 0)
        self.assertEqual(len(set(codes.values())), len(codes))
        reserved = set(operator.STATUS_EXIT_CODES.values()) | {1, 2, 255}
        for name, code in codes.items():
            if name != 'recovered':
                with self.subTest(category=name):
                    self.assertNotIn(code, reserved)
                    self.assertTrue(20 <= code <= 29)

    def test_main_returns_the_category_code_and_prints_only_the_category(self):
        for name, code in operator.FORCE_RECOVER_EXIT_CODES.items():
            with self.subTest(category=name):
                out = io.StringIO()
                with mock.patch.object(operator, 'force_recover', return_value=name), \
                     redirect_stdout(out):
                    self.assertEqual(operator.main(['--config', 'x', '--force-recover']), code)
                self.assertEqual(json.loads(out.getvalue()), {'status': 'force_recover', 'category': name})

    def test_force_recover_never_takes_a_target_or_duration(self):
        parser = operator._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(['--config', 'x', '--force-recover', '--target', 'sorengame'])
        args = parser.parse_args(['--config', 'x', '--force-recover'])
        self.assertTrue(args.force_recover)
        self.assertIsNone(args.duration_minutes)

    def test_stop_timeout_is_a_bounded_category_not_a_traceback(self):
        base = Path(tempfile.mkdtemp(prefix='nethack-op-'))
        fake_g = self._fake_g(base)
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(
                 operator.subprocess, 'run',
                 side_effect=operator.subprocess.TimeoutExpired(cmd='x', timeout=600),
             ):
            with self.assertRaises(NethackCornerError):
                operator.stop(fake_g.config_path)
            self.assertEqual(operator.main(['--config', 'x', '--stop']), 2)
        marker = json.loads(
            (fake_g.state_dir / 'nethack_corner_manual_stop_failure.json').read_text(encoding='utf-8')
        )
        self.assertEqual(marker['category'], 'stop_timeout')

    def test_stop_failure_prefers_the_fixed_blocked_token_over_substring_guessing(self):
        cases = {
            # the draining message used to fall through to ``unknown``
            'docich: エラー: NetHack stop blocked [stop_blocked:switch_not_stable]': 'blocked_switch_not_stable',
            'docich: エラー: NetHack stop blocked [stop_blocked:runtime_unavailable]': 'blocked_runtime_unavailable',
            'docich: エラー: NetHack stop blocked [stop_blocked:switch_state_unreadable]': 'blocked_switch_state_unreadable',
            # an unknown token never becomes a category
            'docich: エラー: [stop_blocked:SUPERSECRET]': 'unknown',
        }
        for stderr, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(operator._classify_stop_failure(stderr), expected)


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
        self.assertIn('id: recover', text)
        self.assertIn('--recover', text)
        self.assertIn('continue-on-error: true', text)
        self.assertIn('NetHack corner start failed', text)
        self.assertIn('NetHack corner recover failed', text)
        self.assertNotIn('pull_request_target:', text)
        self.assertNotIn('inputs.command', text)
        self.assertNotIn('event.comment.body', text)
        self.assertNotIn('event.issue.body', text)

    def _force_recover_step(self):
        text = WF.read_text(encoding='utf-8')
        start = text.index('id: force_recover')
        end = text.index('- name: Report NetHack corner manual status')
        return text[start:end]

    def test_force_recover_is_a_fixed_workflow_operation(self):
        text = WF.read_text(encoding='utf-8')
        self.assertIn('options: [start, stop, status, recover, force-recover]', text)
        self.assertIn("steps.auth.outputs.operation == 'force-recover'", text)
        step = self._force_recover_step()
        self.assertIn('--force-recover', step)
        self.assertIn('bin/docich-nethack-corner-operator', step)
        self.assertIn('config/docich.soren-live.toml', step)
        # the gateway withholds output, so nothing but the code is consumed
        self.assertIn('>/dev/null', step)
        self.assertIn('continue-on-error: true', step)
        self.assertNotIn('inputs.', step)
        self.assertIn("steps.force_recover.outcome == 'failure'", text)
        self.assertIn('NetHack corner force-recover did not reach a startable state', text)

    def test_workflow_code_table_matches_the_operator_exit_codes(self):
        step = self._force_recover_step()
        rows = {}
        for line in step.splitlines():
            m = re.match(r'\s+(\d+)\) reason=(\w+)(.*);;\s*$', line)
            if m:
                rows[int(m.group(1))] = (m.group(2), 'ok=0' not in m.group(3))
        # every operator category is mapped to exactly its own name...
        for name, code in operator.FORCE_RECOVER_EXIT_CODES.items():
            with self.subTest(category=name):
                self.assertEqual(rows[code][0], name)
                # ...and the step is green only when a start may proceed now
                self.assertEqual(rows[code][1], name in ('recovered', 'nothing_to_recover'))
        # transport / uncaught / generic operator errors are never green
        for code, name in ((1, 'uncaught_exception'), (2, 'operator_error'), (255, 'transport_error')):
            with self.subTest(code=code):
                self.assertEqual(rows[code], (name, False))
        self.assertIn('*) reason=unclassified; ok=0 ;;', step)
        self.assertIn('::notice::NetHack corner force-recover: ${reason} (code ${code})', step)

    def test_public_repo_generic_arbitrary_exec_stays_disabled(self):
        text = GENERIC_AUTH.read_text(encoding='utf-8')
        self.assertIn("repo_private == 'false' and op == 'exec'", text)
        self.assertIn('arbitrary VM exec is disabled when the repository is public', text)


if __name__ == '__main__':
    unittest.main()
