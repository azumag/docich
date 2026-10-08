"""Synthetic proc/pidfd regressions for the fixed watchdog restart payload.

No live /proc reads, processes, or signals. The only shell execution is the
public lifecycle predicate against temporary JSON fixtures, and the wrapper
against an empty test root. Tests also run on macOS so safety cases cannot be
hidden behind Linux-only skips.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / 'ops/vm_actions/restart_soviet_watchdog.sh'
PREDICATE = Path(__file__).parent / 'fixtures/watchdog_bridge_parked.sh'


def load_payload():
    body = HELPER.read_text()
    source = body.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    module = types.ModuleType('watchdog_payload')
    exec(compile(source, str(HELPER), 'exec'), module.__dict__)
    return module


class RestartSovietWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'soren'
        self.proc = Path(self.tmp.name).resolve() / 'proc'
        (self.root / 'tmp/state/.soviet_watchdog.lock').mkdir(parents=True)
        (self.root / 'lib').mkdir()
        (self.root / 'lib/game_lifecycle.sh').write_text('reviewed predicate\n')
        (self.root / 'soviet_watchdog.sh').write_text('reviewed script\n')
        (self.root / 'start_all.sh').write_text('reviewed supervisor\n')
        self.proc.mkdir()
        self.module = load_payload()
        self.module.PROC_ROOT = self.proc
        self.module.TERM_WAIT = 0.02
        self.module.REPLACEMENT_WAIT = 0.02
        self.module.POLL_INTERVAL = 0.001
        self.handles = {}
        self.incarnations = {}
        self.delivered = []
        self.on_open = None
        self.on_send = None
        self.stubborn = False
        self.respawn = True
        self.env = {}
        self.put(4000, 'start_all.sh', parent=1, extra=['--supervisor'])
        self.put(4100, 'soviet_watchdog.sh', parent=4000)
        self.record(4000, 'start_all.pid')
        self.record(4100)
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(mock.patch.object(self.module.os, 'pidfd_open', self.open_handle, create=True))
        self.patches.enter_context(mock.patch.object(self.module.signal, 'pidfd_send_signal', self.send, create=True))
        self.patches.enter_context(mock.patch.object(self.module.Process, 'exited', lambda process: self.exited(process)))
        self.kill = self.patches.enter_context(mock.patch.object(self.module.os, 'kill', side_effect=AssertionError('bare PID signal')))

    def tearDown(self):
        self.patches.close()
        # Every owned pidfd must have been closed, even on error paths.
        for fd in self.handles:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.tmp.cleanup()

    def put(self, pid, script, *, parent=4000, start=100, argv=None, cwd=None, exe='/usr/bin/bash', extra=(), env=None):
        path = self.proc / str(pid)
        path.mkdir()
        (path / 'fd').mkdir()
        if argv is None:
            argv = ['bash', './' + script, *extra]
        (path / 'cmdline').write_bytes(b'\0'.join(os.fsencode(x) for x in argv) + b'\0')
        (path / 'cwd').symlink_to(cwd or self.root)
        (path / 'exe').symlink_to(exe)
        (path / 'fd/255').symlink_to(self.root / script)
        fields = ['S', str(parent)] + ['0'] * 17 + [str(start)] + ['0'] * 10
        (path / 'stat').write_text(str(pid) + ' (bash) ' + ' '.join(fields))
        (path / 'environ').write_bytes(b'\0'.join(os.fsencode(k + '=' + v) for k, v in (self.env if env is None else env).items()) + b'\0')
        incarnation = {'pid': pid, 'start': start, 'dead': False, 'path': path}
        self.incarnations[(pid, start)] = incarnation
        return incarnation

    def record(self, pid, name='.soviet_watchdog.lock/owner'):
        (self.root / 'tmp/state' / name).write_text(str(pid) + '\n')

    def current(self, pid):
        return next(x for x in self.incarnations.values() if x['path'] == self.proc / str(pid))

    def die(self, incarnation):
        incarnation['dead'] = True
        p = incarnation['path'] / 'stat'
        p.write_text(p.read_text().replace(') S ', ') Z '))

    def reuse(self, pid, *, script='report.py', start=200):
        previous = self.current(pid)
        self.die(previous)
        old_path = self.proc / (str(pid) + '-old-' + str(previous['start']))
        previous['path'].rename(old_path)
        previous['path'] = old_path
        return self.put(pid, script, start=start)

    def open_handle(self, pid, flags=0):
        if self.on_open:
            self.on_open(pid)
        incarnation = self.current(pid)
        fd = os.open(os.devnull, os.O_RDONLY)
        self.handles[fd] = incarnation
        return fd

    def exited(self, process):
        return self.handles[process.pidfd]['dead']

    def send(self, fd, sig, info=None, flags=0):
        if self.on_send:
            self.on_send(fd)
        incarnation = self.handles[fd]
        if incarnation['dead']:
            raise ProcessLookupError()
        self.delivered.append((incarnation['pid'], incarnation['start'], sig))
        if not self.stubborn:
            self.die(incarnation)
            if self.respawn:
                self.put(4200, 'soviet_watchdog.sh', start=200)
                self.record(4200)

    def run_helper(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            code = self.module.main(str(self.root))
        return code, out.getvalue()

    def parked(self, *, directory=None, status='stopped', **updates):
        directory = directory or self.root / 'tmp/state/game_lifecycle'
        directory.mkdir(parents=True, exist_ok=True)
        request = dict(schema=1, request_id='synthetic', game='sorengame', generation=7,
                       deadline_epoch=4102444800, deadline_at='2100-01-01T00:00:00Z')
        request.update(updates)
        ack = {**request, 'status': status}
        (directory / 'request.json').write_text(json.dumps(request))
        (directory / 'ack.json').write_text(json.dumps(ack))
        return directory

    def set_env(self, **values):
        self.env = values
        (self.proc / '4100/environ').write_bytes(b'\0'.join(os.fsencode(k + '=' + v) for k, v in values.items()) + b'\0')

    def test_replaces_only_verified_owner(self):
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [(4100, 100, signal.SIGTERM)])
        self.kill.assert_not_called()

    def test_exact_bash_script_operands(self):
        for operand in ('./soviet_watchdog.sh', 'soviet_watchdog.sh', str(self.root / 'soviet_watchdog.sh')):
            with self.subTest(operand=operand):
                (self.proc / '4100/cmdline').write_bytes(b'bash\0' + os.fsencode(operand) + b'\0')
                process = self.module.Process(4100)
                try:
                    self.assertEqual(process.identity(self.root, 'soviet_watchdog.sh'), (100, 4000))
                finally:
                    process.close()

    def test_refuses_public_reporter_reproduction_and_other_false_positives(self):
        cases = [
            ['python3', '/tmp/report.py', '--input', '/tmp/soviet_watchdog.sh.log'],
            ['python3', '/tmp/report.py', str(self.root / 'soviet_watchdog.sh')],
            ['bash', '-c', 'sleep 30', str(self.root / 'soviet_watchdog.sh')],
            ['bash', '/other-root/soviet_watchdog.sh'],
            ['bash', './soviet_watchdog.sh.log'],
            ['bash', './soviet_watchdog.sh', '--extra'],
            ['bash', '--', './soviet_watchdog.sh'],
            ['bash', './report.sh', str(self.root / 'soviet_watchdog.sh')],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                (self.proc / '4100/cmdline').write_bytes(b'\0'.join(os.fsencode(x) for x in argv) + b'\0')
                self.assertEqual(self.run_helper()[0], 10)
                self.assertEqual(self.delivered, [])

    def test_refuses_wrong_cwd_executable_and_script_fd(self):
        for key, target in [('cwd', '/other-root'), ('exe', '/usr/bin/python3'), ('fd/255', '/other-root/soviet_watchdog.sh')]:
            with self.subTest(key=key):
                path = self.proc / '4100' / key
                previous = os.readlink(path)
                path.unlink()
                path.symlink_to(target)
                self.assertEqual(self.run_helper()[0], 10)
                self.assertEqual(self.delivered, [])
                path.unlink()
                path.symlink_to(previous)

    def test_deleted_old_script_is_valid_for_epoch_replacement(self):
        path = self.proc / '4100/fd/255'
        path.unlink()
        path.symlink_to(str(self.root / 'soviet_watchdog.sh') + ' (deleted)')
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(len(self.delivered), 1)

    def test_pid_reuse_between_initial_identity_and_pin_never_signals_successor(self):
        def before_pin(pid):
            if pid == 4100:
                self.reuse(pid)
        self.on_open = before_pin
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [])
        self.assertFalse(self.current(4100)['dead'])

    def test_pid_reuse_after_final_verification_before_term_never_signals_successor(self):
        self.on_send = lambda fd: self.reuse(4100)
        self.respawn = False
        self.assertEqual(self.run_helper()[0], 12)
        self.assertEqual(self.delivered, [])
        self.assertFalse(self.current(4100)['dead'])
        self.kill.assert_not_called()

    def test_accepts_same_pid_only_for_verified_new_incarnation(self):
        def send(fd, *_):
            previous = self.handles[fd]
            self.delivered.append((previous['pid'], previous['start'], signal.SIGTERM))
            self.reuse(4100, script='soviet_watchdog.sh')
        with mock.patch.object(self.module.signal, 'pidfd_send_signal', send):
            self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [(4100, 100, signal.SIGTERM)])

    def test_lock_owner_changes_under_handle_refuse_without_signal(self):
        def before_pin(pid):
            if pid == 4100:
                self.record(4200)
        self.on_open = before_pin
        self.assertEqual(self.run_helper()[0], 10)
        self.assertEqual(self.delivered, [])

    def test_pause_and_stop_markers_keep_all_processes(self):
        for name in ('tmp/state/soviet_watchdog.paused', 'tmp/stop'):
            with self.subTest(name=name):
                marker = self.root / name
                marker.symlink_to(self.root / 'missing')
                self.assertEqual(self.run_helper()[0], 0)
                self.assertEqual(self.delivered, [])
                self.assertTrue(marker.is_symlink())
                marker.unlink()

    def test_pause_arriving_after_pin_is_respected(self):
        self.on_open = lambda pid: (self.root / 'tmp/state/soviet_watchdog.paused').touch()
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [])

    def test_missing_stale_and_zombie_owner_skip(self):
        path = self.root / 'tmp/state/.soviet_watchdog.lock/owner'
        path.unlink()
        self.assertEqual(self.run_helper()[0], 0)
        self.record(9999)
        self.assertEqual(self.run_helper()[0], 0)
        self.record(4100)
        self.die(self.current(4100))
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [])

    def test_invalid_or_symlink_owner_refused(self):
        path = self.root / 'tmp/state/.soviet_watchdog.lock/owner'
        for raw in ('1\n', '0\n', '-5\n', '4 100\n', '4100\n4200\n'):
            path.write_text(raw)
            self.assertEqual(self.run_helper()[0], 10)
        path.unlink()
        path.symlink_to(self.root / 'start_all.sh')
        self.assertEqual(self.run_helper()[0], 10)
        self.assertEqual(self.delivered, [])

    def test_pidfd_unavailable_refuses_without_fallback(self):
        with mock.patch.object(self.module.os, 'pidfd_open', side_effect=OSError('unsupported')):
            self.assertEqual(self.run_helper()[0], 10)
        self.assertEqual(self.delivered, [])

    def test_exit_wait_polls_only_the_stable_handle(self):
        process = self.module.Process(4100)
        process.pidfd = self.open_handle(4100)
        try:
            poller = mock.Mock()
            poller.poll.return_value = [(process.pidfd, self.module.select.POLLIN)]
            with mock.patch.object(self.module.select, 'poll', return_value=poller):
                # Call the real implementation; other tests simulate handle death.
                real_exited = load_payload().Process.exited
                self.assertTrue(real_exited(process))
            poller.register.assert_called_once_with(process.pidfd, self.module.select.POLLIN)
            poller.poll.assert_called_once_with(0)
        finally:
            process.close()

    def test_foreign_uid_is_refused(self):
        with mock.patch.object(self.module.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assertEqual(self.run_helper()[0], 10)
        self.assertEqual(self.delivered, [])

    def test_stubborn_old_owner_and_absent_replacement_fail(self):
        self.stubborn = True
        self.assertEqual(self.run_helper()[0], 11)
        self.assertEqual(self.delivered, [(4100, 100, signal.SIGTERM)])
        self.delivered.clear()
        self.stubborn = False
        self.respawn = False
        self.assertEqual(self.run_helper()[0], 12)

    def test_foreign_replacement_cannot_confirm_success(self):
        def send(fd, *_):
            self.die(self.handles[fd])
            self.put(4200, 'report.py')
            self.record(4200)
        with mock.patch.object(self.module.signal, 'pidfd_send_signal', send):
            self.assertEqual(self.run_helper()[0], 12)

    def test_supervisor_death_or_replacement_cannot_confirm_success(self):
        original_send = self.send
        def send(fd, *_):
            original_send(fd, signal.SIGTERM)
            self.reuse(4000, script='start_all.sh')
        with mock.patch.object(self.module.signal, 'pidfd_send_signal', send):
            self.assertEqual(self.run_helper()[0], 12)

    def test_absent_foreign_or_adopted_supervisor_refused(self):
        path = self.root / 'tmp/state/start_all.pid'
        path.unlink()
        self.assertEqual(self.run_helper()[0], 13)
        self.record(4100, 'start_all.pid')
        self.assertEqual(self.run_helper()[0], 13)
        self.record(4000, 'start_all.pid')
        (self.proc / '4000/cmdline').write_bytes(b'bash\0-c\0start_all.sh\0')
        self.assertEqual(self.run_helper()[0], 13)
        self.assertEqual(self.delivered, [])

    def test_disabled_inherited_config_ignores_stale_default_park(self):
        # Reproduce .env GAME_LIFECYCLE_ENABLED=0 as the exported environment
        # at watchdog exec. The gateway/helper environment deliberately has
        # no lifecycle keys; stale default parked records must not cause skip.
        (self.root / '.env').write_text('GAME_LIFECYCLE_ENABLED=0\n')
        self.set_env(GAME_LIFECYCLE_ENABLED='0')
        self.parked()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [(4100, 100, signal.SIGTERM)])

    def test_effective_exec_config_survives_later_env_file_change(self):
        # Supervisor is resident: newly edited .env is not its effective config.
        (self.root / '.env').write_text('GAME_LIFECYCLE_ENABLED=0\n')
        self.parked()
        self.assertIn('parked', self.run_helper()[1])
        self.assertEqual(self.delivered, [])

    def test_custom_directory_uses_inherited_not_gateway_configuration(self):
        custom = self.root / 'custom lifecycle'
        self.set_env(GAME_LIFECYCLE_ENABLED='1', SOREN_GAME_LIFECYCLE_DIR=str(custom))
        self.parked(directory=custom)
        with mock.patch.dict(os.environ, {'GAME_LIFECYCLE_ENABLED': '0', 'SOREN_GAME_LIFECYCLE_DIR': '/wrong'}, clear=True):
            self.assertIn('parked', self.run_helper()[1])
        self.assertEqual(self.delivered, [])

    def test_custom_directory_does_not_read_stale_default_park(self):
        self.set_env(SOREN_GAME_LIFECYCLE_DIR='relative-custom')
        self.parked()
        self.assertEqual(self.run_helper()[0], 0)
        self.assertEqual(self.delivered, [(4100, 100, signal.SIGTERM)])

    def test_unreadable_or_invalid_config_is_not_a_success_skip(self):
        self.parked()
        (self.proc / '4100/environ').unlink()
        self.assertEqual(self.run_helper()[0], 13)
        self.set_env(GAME_LIFECYCLE_ENABLED='unexpected')
        self.assertEqual(self.run_helper()[0], 13)
        self.assertEqual(self.delivered, [])

    def test_duplicate_config_keys_refused(self):
        (self.proc / '4100/environ').write_bytes(b'GAME_LIFECYCLE_ENABLED=1\0GAME_LIFECYCLE_ENABLED=0\0')
        self.assertEqual(self.run_helper()[0], 13)
        self.assertEqual(self.delivered, [])

    def test_missing_lifecycle_library_cannot_guess_not_parked(self):
        (self.root / 'lib/game_lifecycle.sh').unlink()
        self.assertEqual(self.run_helper()[0], 13)
        self.assertEqual(self.delivered, [])

    def test_no_env_file_execution_or_secret_output(self):
        sentinel = self.root / 'executed'
        (self.root / '.env').write_text('touch ' + str(sentinel) + '\n')
        self.set_env(PRIVATE_TOKEN='PRIVATE_SYNTHETIC_SECRET')
        code, out = self.run_helper()
        self.assertEqual(code, 0)
        self.assertFalse(sentinel.exists())
        self.assertNotIn('PRIVATE', out)
        self.assertNotIn(str(self.root), out)

    def test_wrapper_remains_standalone_fixed_and_accepts_no_worker(self):
        result = subprocess.run(['bash', str(HELPER), '--root', str(self.root / 'empty')], text=True, capture_output=True)
        self.assertEqual(result.returncode, 10)
        result = subprocess.run(['bash', str(HELPER), 'foreign_worker'], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        body = HELPER.read_text()
        self.assertIn('root="/home/ubuntu/soren"', body)
        self.assertIn('.soviet_watchdog.lock/owner', body)
        for forbidden in ('systemctl', 'sudo', 'tmux', 'pkill', 'os.kill('):
            self.assertNotIn(forbidden, body)

    def test_park_predicate_matches_reviewed_supervisor_fixture(self):
        cases = [
            ('1', 'stopped', 1, True), ('0', 'stopped', 1, False),
            ('1', 'stopping', 4102444800, True), ('1', 'stopping', 1, False),
            ('1', 'stop_requested', 4102444800, True),
            ('1', 'resume_requested', 4102444800, True),
            ('1', 'waiting', 4102444800, False),
        ]
        for enabled, status, deadline, expected in cases:
            with self.subTest(enabled=enabled, status=status, deadline=deadline):
                directory = self.parked(status=status, deadline_epoch=deadline)
                env = {'PATH': os.environ['PATH'], 'GAME_LIFECYCLE_ENABLED': enabled, 'GAME_LIFECYCLE_DIR': str(directory)}
                result = subprocess.run(['bash', '-c', 'source "$1"; game_lifecycle_bridge_parked', '_', str(PREDICATE)], env=env, capture_output=True)
                actual = self.module.bridge_parked(self.root, (enabled, directory))
                self.assertEqual(result.returncode == 0, expected, result.stderr)
                self.assertEqual(actual, expected)
        directory = self.parked()
        ack = json.loads((directory / 'ack.json').read_text())
        ack['generation'] = 8
        (directory / 'ack.json').write_text(json.dumps(ack))
        self.assertFalse(self.module.bridge_parked(self.root, ('1', directory)))


if __name__ == '__main__':
    unittest.main()
