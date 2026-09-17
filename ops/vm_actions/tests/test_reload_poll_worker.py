import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / 'ops/vm_actions/reload_poll_worker.py'
WORKFLOW = ROOT / '.github/workflows/market-paper-runtime-reload.yml'
spec = importlib.util.spec_from_file_location('reload_poll_worker', HELPER)
reload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reload)


class ReloadPollWorkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / 'tmp/state').mkdir(parents=True)
        (self.root / 'logs').mkdir()
        self.pid = self.root / 'tmp/state/poll_worker.pid'
        self.pid.write_text('123\n')
        self.log = self.root / 'logs/poll_worker.log'
        self.log.write_text('[poll_worker 01:02:03] reload complete (old)\nPRIVATE_CONTENT\n')
        self.proc = self.root / 'proc/123'
        self.proc.mkdir(parents=True)
        (self.proc / 'cwd').symlink_to(self.root)
        (self.proc / 'exe').symlink_to('/bin/bash')
        self.command()
        self.start()
        (self.proc / 'status').write_text(f'SigCgt:\t{1 << (signal.SIGUSR1 - 1):x}\n')
        self.calls = []
        self.on_signal = lambda: self.append_marker()
        self.now = 0
        patches = [
            mock.patch.object(reload, 'PROC_ROOT', self.root / 'proc'),
            mock.patch.object(reload.os, 'pidfd_open', return_value=99, create=True),
            mock.patch.object(reload.signal, 'pidfd_send_signal', side_effect=self.send, create=True),
            mock.patch.object(reload.os, 'close'),
            mock.patch.object(reload.time, 'monotonic', side_effect=lambda: self.now),
            mock.patch.object(reload.time, 'sleep', side_effect=self.sleep),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def command(self, argv=None):
        argv = argv or ['bash', './workers/poll_worker.sh']
        (self.proc / 'cmdline').write_bytes(b'\0'.join(os.fsencode(x) for x in argv) + b'\0')

    def start(self, ticks='100', state='S'):
        (self.proc / 'stat').write_text('123 (bash name) ' + ' '.join([state] + ['0'] * 18 + [ticks]))

    def append_marker(self):
        with self.log.open('a') as stream:
            stream.write('[poll_worker 12:34:56] reload complete (interval=10s enabled=0)\nPRIVATE_NEW_CONTENT\n')

    def send(self, fd, sig, info, flags):
        self.calls.append((fd, sig))
        if sig == signal.SIGUSR1:
            self.on_signal()

    def sleep(self, delay):
        self.now += delay

    def run_helper(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            rc = reload.main(['--root', str(self.root)])
        self.assertNotIn('PRIVATE', out.getvalue())
        self.assertNotIn('interval=', out.getvalue())
        return rc, json.loads(out.getvalue())

    def test_single_usr1_same_pid_and_start_new_log_confirmation(self):
        rc, result = self.run_helper()
        self.assertEqual(rc, 0)
        self.assertEqual(result, {'worker': 'poll_worker', 'status': 'reloaded', 'reason': 'worker_log_marker'})
        self.assertEqual([sig for _, sig in self.calls if sig], [signal.SIGUSR1])
        self.assertEqual(self.pid.read_text(), '123\n')
        reload.os.pidfd_open.assert_called_once_with(123, 0)
        reload.os.close.assert_called_once_with(99)

    def test_paused_and_global_stop_are_preserved_without_signals(self):
        for relative in ('tmp/state/poll_worker.paused', 'tmp/stop'):
            with self.subTest(relative=relative):
                marker = self.root / relative
                marker.write_text('keep')
                rc, result = self.run_helper()
                self.assertEqual((rc, result['reason']), (0, 'paused'))
                self.assertEqual(marker.read_text(), 'keep')
                marker.unlink()
        self.assertEqual(self.calls, [])

    def test_missing_and_stale_pid_skip_without_starting(self):
        for value in (None, '999\n'):
            if value is None:
                self.pid.unlink()
            else:
                self.pid.write_text(value)
            rc, result = self.run_helper()
            self.assertEqual((rc, result['reason']), (0, 'absent'))
        self.assertEqual(self.calls, [])

    def test_invalid_pids_fail_without_signal(self):
        for value in ('0', '1', '-1', '12 3', '123\n456', 'secret', '999999999999'):
            with self.subTest(value=value):
                self.pid.write_text(value)
                rc, result = self.run_helper()
                self.assertEqual((rc, result['reason']), (1, 'invalid_pid'))
        self.assertEqual(self.calls, [])

    def test_exact_script_and_bash_identity_not_substrings(self):
        for argv in (['bash', './workers/radio_worker.sh'], ['bash', './workers/poll_worker.sh.old'],
                     ['bash', '-c', './workers/poll_worker.sh'], ['sleep', './workers/poll_worker.sh'],
                     ['bash', '/other/workers/poll_worker.sh']):
            with self.subTest(argv=argv):
                self.command(argv)
                rc, result = self.run_helper()
                self.assertEqual((rc, result['reason']), (1, 'wrong_command'))
        self.assertEqual(self.calls, [])

    def test_wrong_cwd_and_executable_fail_without_signal(self):
        for name, target in (('cwd', self.root.parent), ('exe', '/bin/sleep')):
            with self.subTest(name=name):
                path = self.proc / name
                old = os.readlink(path)
                path.unlink()
                path.symlink_to(target)
                self.assertEqual(self.run_helper()[0], 1)
                path.unlink()
                path.symlink_to(old)
        self.assertEqual(self.calls, [])

    def test_usr1_trap_required(self):
        (self.proc / 'status').write_text('SigCgt:\t0\n')
        rc, result = self.run_helper()
        self.assertEqual((rc, result['reason']), (1, 'reload_trap_missing'))
        self.assertEqual(self.calls, [])

    def test_identity_change_between_check_and_signal_refuses(self):
        reload.os.pidfd_open.side_effect = lambda *args: (self.start('101') or 99)
        rc, result = self.run_helper()
        self.assertEqual((rc, result['reason']), (1, 'identity_changed'))
        self.assertEqual(self.calls, [])

    def test_pause_added_before_signal_skips(self):
        reload.os.pidfd_open.side_effect = lambda *args: ((self.root / 'tmp/state/poll_worker.paused').touch() or 99)
        self.assertEqual(self.run_helper()[1]['reason'], 'paused')
        self.assertEqual(self.calls, [])

    def test_replacement_pid_start_or_zombie_after_signal_fails(self):
        for change in (lambda: self.pid.write_text('124'), lambda: self.start('101'), lambda: self.start(state='Z')):
            with self.subTest(change=change):
                self.pid.write_text('123')
                self.start()
                self.on_signal = change
                self.assertEqual(self.run_helper()[0], 1)

    def test_old_marker_does_not_confirm_and_wait_is_bounded(self):
        self.on_signal = lambda: None
        rc, result = self.run_helper()
        self.assertEqual((rc, result['reason']), (1, 'reload_unconfirmed'))
        self.assertEqual(self.now, 60)
        self.assertEqual([sig for _, sig in self.calls if sig], [signal.SIGUSR1])

    def test_delayed_pending_reload_is_confirmed(self):
        self.on_signal = lambda: None
        def tick(delay):
            self.now += delay
            if self.now == 2:
                self.append_marker()
        reload.time.sleep.side_effect = tick
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.now, 2)

    def test_missing_log_refuses_before_signal(self):
        self.log.unlink()
        self.assertEqual(self.run_helper()[0], 1)
        self.assertEqual(self.calls, [])

    def test_rotated_log_is_not_treated_as_confirmation(self):
        def rotate():
            self.log.rename(self.log.with_suffix('.old'))
            self.append_marker()
        self.on_signal = rotate
        self.assertEqual(self.run_helper()[1]['reason'], 'log_rotated')

    def test_symlink_pid_and_log_refused(self):
        for path in (self.pid, self.log):
            with self.subTest(path=path):
                target = path.with_suffix('.target')
                path.rename(target)
                path.symlink_to(target)
                self.assertEqual(self.run_helper()[0], 1)
                path.unlink()
                target.rename(path)
        self.assertEqual(self.calls, [])

    def test_signal_failure_is_sanitized(self):
        reload.signal.pidfd_send_signal.side_effect = ProcessLookupError('PRIVATE_ERROR')
        self.assertEqual(self.run_helper()[1]['reason'], 'runtime_unavailable')

    def test_workflow_fixed_reviewed_owner_main_post_deploy_path(self):
        text = WORKFLOW.read_text()
        for required in ("workflow_run.event == 'push'", "workflow_run.conclusion == 'success'",
                         "workflow_run.head_branch == 'main'", 'workflow_run.actor.id == 9018513',
                         'github.actor_id == 9018513', "github.triggering_actor == 'azumag'",
                         "github.repository == 'azumag/docich'", "github.ref == 'refs/heads/main'",
                         'github.ref_protected == true', 'environment: vm-operations',
                         'group: vm-operations-${{ github.repository }}', 'cancel-in-progress: false',
                         'ref: ${{ github.event.workflow_run.head_sha }}',
                         '[[ "$current_main" != "$SHA" ]]', 'cd /home/ubuntu/docich',
                         'git diff --quiet HEAD --', 'python3 ops/vm_actions/reload_poll_worker.py',
                         'exec docich production $SHA'):
            self.assertIn(required, text)
        self.assertIn('printf \'[[ "$(git rev-parse HEAD)" == "%s" ]]\\n\' "$SHA"', text)
        self.assertNotIn('workflow_dispatch:', text)
        self.assertNotIn('inputs.', text)
        self.assertNotIn('--root', text)
        self.assertLess(text.index('Reload only the existing poll worker'), text.index('Restart only already-active PAPER'))


if __name__ == '__main__':
    unittest.main()
