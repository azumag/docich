import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / 'ops/vm_actions/authorize_paper_corner.py'
WF = ROOT / '.github/workflows/paper-corner-operator.yml'
GENERIC_AUTH = ROOT / 'ops/vm_actions/authorize.py'
sys.path.insert(0, str(ROOT / 'src'))

from docich import paper_corner_operator as operator
from docich.adapters.program import PAPER_VIEW_NAME
from docich.paper_corner import PaperCornerError


class PaperCornerAuthorizeTests(unittest.TestCase):
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
            'GITHUB_WORKFLOW_REF': 'azumag/docich/.github/workflows/paper-corner-operator.yml@refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_EVENT_ACTION': '',
            'GITHUB_SHA': 'a' * 40,
            'GITHUB_ISSUE_NUMBER': '',
            'GITHUB_ISSUE_AUTHOR': '',
            'GITHUB_ISSUE_AUTHOR_ID': '',
            'GITHUB_ISSUE_BODY': '',
            'INPUT_OPERATION': 'start',
            'INPUT_DURATION_MINUTES': '15',
            'INPUT_CONFIRM': 'production',
        }
        env.update(overrides)
        return subprocess.run(['python3', str(AUTH)], text=True, capture_output=True, env=env)

    def test_owner_dispatch_allows_bounded_start(self):
        p = self.run_auth()
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data['duration_minutes'], 15)
        self.assertEqual((data['operation'], data['target'], data['ref']), ('start', 'production', 'main'))

    def test_non_owner_and_non_owner_rerun_fail_closed(self):
        self.assertNotEqual(self.run_auth(GITHUB_ACTOR='collab', GITHUB_ACTOR_ID='42').returncode, 0)
        self.assertNotEqual(self.run_auth(GITHUB_TRIGGERING_ACTOR='collab').returncode, 0)

    def test_requires_protected_main_and_main_workflow_copy(self):
        self.assertNotEqual(self.run_auth(GITHUB_REF_PROTECTED='false').returncode, 0)
        self.assertNotEqual(self.run_auth(GITHUB_REF='refs/heads/feature').returncode, 0)
        self.assertNotEqual(self.run_auth(GITHUB_WORKFLOW_REF='azumag/docich/.github/workflows/paper-corner-operator.yml@refs/heads/feature').returncode, 0)

    def test_dispatch_requires_production_confirmation_and_range(self):
        self.assertNotEqual(self.run_auth(INPUT_CONFIRM='').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='0').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='61').returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_DURATION_MINUTES='15;id').returncode, 0)

    def test_owner_issue_bridge_accepts_only_exact_command_issue_and_json(self):
        body = json.dumps({'operation': 'start', 'duration_minutes': 15, 'confirm': 'production', 'nonce': 'test-1'})
        p = self.run_auth(
            GITHUB_EVENT_NAME='issues',
            GITHUB_EVENT_ACTION='edited',
            GITHUB_ISSUE_NUMBER='293',
            GITHUB_ISSUE_AUTHOR='azumag',
            GITHUB_ISSUE_AUTHOR_ID='9018513',
            GITHUB_ISSUE_BODY=body,
            INPUT_OPERATION='', INPUT_DURATION_MINUTES='', INPUT_CONFIRM='',
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['duration_minutes'], 15)
        self.assertNotEqual(self.run_auth(
            GITHUB_EVENT_NAME='issues', GITHUB_EVENT_ACTION='edited', GITHUB_ISSUE_NUMBER='999',
            GITHUB_ISSUE_AUTHOR='azumag', GITHUB_ISSUE_AUTHOR_ID='9018513', GITHUB_ISSUE_BODY=body,
        ).returncode, 0)
        bad = json.dumps({'operation': 'start', 'duration_minutes': '15;id', 'confirm': 'production', 'nonce': 'x'})
        self.assertNotEqual(self.run_auth(
            GITHUB_EVENT_NAME='issues', GITHUB_EVENT_ACTION='edited', GITHUB_ISSUE_NUMBER='293',
            GITHUB_ISSUE_AUTHOR='azumag', GITHUB_ISSUE_AUTHOR_ID='9018513', GITHUB_ISSUE_BODY=bad,
        ).returncode, 0)


class PaperCornerOperatorTests(unittest.TestCase):
    def test_recover_stale_start_only_when_canonical_is_back_at_previous_game(self):
        manager = mock.Mock()
        manager._read_state.return_value = {'status': 'starting', 'previous_game': 'sorengame'}
        manager._active_game.return_value = 'sorengame'
        with mock.patch.object(operator, 'ManualPaperCornerManager', return_value=manager), \
             mock.patch.object(operator.time, 'time', return_value=123.0):
            self.assertTrue(operator._recover_stale_manual_state(object(), 15))
        saved = manager.save.call_args.args[0]
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['completed_at'], 123.0)
        self.assertIn('stale manual start recovered', saved['detail'])

    def test_recover_refuses_active_or_ambiguous_manual_state(self):
        manager = mock.Mock()
        manager._read_state.return_value = {'status': 'active', 'previous_game': 'sorengame'}
        manager._active_game.return_value = PAPER_VIEW_NAME
        with mock.patch.object(operator, 'ManualPaperCornerManager', return_value=manager):
            with self.assertRaises(PaperCornerError):
                operator._recover_stale_manual_state(object(), 15)
        manager._active_game.return_value = 'other-game'
        with mock.patch.object(operator, 'ManualPaperCornerManager', return_value=manager):
            with self.assertRaises(PaperCornerError):
                operator._recover_stale_manual_state(object(), 15)
        manager.save.assert_not_called()

    def test_launcher_uses_fixed_argv_private_log_and_detached_session(self):
        base = Path(tempfile.mkdtemp(prefix='paper-op-'))
        state = base / 'state'
        repo = base / 'repo'
        state.mkdir(); repo.mkdir()
        config = repo / 'config.toml'; config.write_text('x')
        fake_g = SimpleNamespace(state_dir=state, repo_root=repo, config_path=config)
        proc = mock.Mock(pid=1234)
        proc.poll.return_value = None
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator, '_recover_stale_manual_state', return_value=False), \
             mock.patch.object(operator.subprocess, 'Popen', return_value=proc) as popen, \
             mock.patch.object(operator.time, 'sleep'):
            result = operator.launch(config, 15)
        self.assertEqual(result['duration_minutes'], 15)
        self.assertFalse(result['recovered_stale_state'])
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:3], ['-m', 'docich.paper_corner_manual'])
        self.assertEqual(argv[-2:], ['--duration-minutes', '15'])
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        logs = list((state / 'logs').glob('paper-manual-*.log'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(stat.S_IMODE(logs[0].stat().st_mode), 0o600)

    def test_launcher_rejects_early_exit_and_out_of_range(self):
        base = Path(tempfile.mkdtemp(prefix='paper-op-'))
        state = base / 'state'; repo = base / 'repo'
        state.mkdir(); repo.mkdir()
        config = repo / 'config.toml'; config.write_text('x')
        fake_g = SimpleNamespace(state_dir=state, repo_root=repo, config_path=config)
        proc = mock.Mock(pid=1234)
        proc.poll.return_value = 0
        with self.assertRaises(PaperCornerError):
            operator.launch(config, 0)
        with mock.patch.object(operator, 'load_global', return_value=fake_g), \
             mock.patch.object(operator, '_recover_stale_manual_state', return_value=False), \
             mock.patch.object(operator.subprocess, 'Popen', return_value=proc), \
             mock.patch.object(operator.time, 'sleep'):
            with self.assertRaises(PaperCornerError):
                operator.launch(config, 15)
        self.assertFalse(list((state / 'logs').glob('paper-manual-*.log')))


class PaperCornerWorkflowPolicyTests(unittest.TestCase):
    def test_workflow_is_owner_only_fixed_operation(self):
        text = WF.read_text(encoding='utf-8')
        self.assertIn('github.actor_id == 9018513', text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn('github.ref_protected == true', text)
        self.assertIn('github.event.issue.number == 293', text)
        self.assertIn('environment: vm-operations', text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('submodules: false', text)
        self.assertIn('StrictHostKeyChecking=yes', text)
        self.assertIn('ForwardAgent=no', text)
        self.assertIn('ClearAllForwardings=yes', text)
        self.assertIn('bin/docich-paper-corner-operator', text)
        self.assertIn('cd /home/ubuntu/docich', text)
        self.assertIn('/home/ubuntu/docich/bin/docich-paper-corner-operator', text)
        self.assertIn('/home/ubuntu/docich/config/docich.soren-live.toml', text)
        self.assertNotIn("printf 'bash bin/docich-paper-corner-operator", text)
        self.assertNotIn('pull_request_target:', text)
        self.assertNotIn('inputs.command', text)
        self.assertNotIn('event.comment.body', text)

    def test_public_repo_generic_arbitrary_exec_stays_disabled(self):
        text = GENERIC_AUTH.read_text(encoding='utf-8')
        self.assertIn("repo_private == 'false' and op == 'exec'", text)
        self.assertIn('arbitrary VM exec is disabled when the repository is public', text)


if __name__ == '__main__':
    unittest.main()
