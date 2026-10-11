import contextlib
import errno
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'audio_priority', pathlib.Path(__file__).resolve().parents[1] / 'priority.py')
prio = importlib.util.module_from_spec(SPEC)
sys.modules['audio_priority'] = prio  # dataclassが型解決にsys.modulesを使う
SPEC.loader.exec_module(prio)

UID = 1001
P = prio.Proc
T = prio.Thread


def encoder(pid=10, uid=UID):
    return P(pid, uid, 'ffmpeg', ('/home/ubuntu/build/x/bin/ffmpeg', '-f', 'x11grab', '-i', ':99.0',
                                   '-f', 'pulse', '-i', 'soren_null.monitor', '-c:v', 'libx264',
                                   '-f', 'flv', 'rtmp://example.invalid/live'))


class WantedTests(unittest.TestCase):
    def test_audio_path_processes_are_selected(self):
        self.assertEqual(prio.wanted(P(1, UID, 'retroarch', ('retroarch', '--config', 'x')), UID), 'retroarch')
        self.assertEqual(prio.wanted(P(2, UID, 'pulseaudio', ('/usr/bin/pulseaudio',)), UID), 'pulseaudio')
        self.assertEqual(prio.wanted(encoder(), UID), 'encoder-ffmpeg')
        feeder = P(3, UID, 'python3', ('/usr/bin/python3', '-m', 'docich.twica_encoder', '6', '9', '4'))
        wrapper = P(4, UID, 'python3', ('python3', '-m', 'docich.twica_ffmpeg', '-hide_banner'))
        self.assertEqual(prio.wanted(feeder, UID), 'twica-feeder')
        self.assertEqual(prio.wanted(wrapper, UID), 'twica-feeder')

    def test_unrelated_processes_are_never_selected(self):
        # 短命のPNGキャプチャffmpeg(FLV出力なし)、ミラーのffplay、他のpython、他ユーザーは対象外。
        png = P(5, UID, 'ffmpeg', ('ffmpeg', '-f', 'x11grab', '-i', ':1', '-frames:v', '1', 'out.png'))
        self.assertIsNone(prio.wanted(png, UID))
        self.assertIsNone(prio.wanted(P(6, UID, 'ffplay', ('ffplay', '-f', 'x11grab')), UID))
        self.assertIsNone(prio.wanted(P(7, UID, 'python3', ('python3', '-m', 'docich.twica_service')), UID))
        self.assertIsNone(prio.wanted(P(8, UID, 'node', ('node', 'soviet_local.mjs')), UID))
        self.assertIsNone(prio.wanted(P(9, 0, 'retroarch', ('retroarch',)), UID))
        self.assertIsNone(prio.wanted(encoder(uid=0), UID))

    def test_feeder_module_must_be_the_actual_python_entrypoint(self):
        for argv in (
            ('python3', '-m', 'docich.twica_encoder_backup'),
            ('python3', '-m', 'docich.twica_ffmpeg_other'),
            ('python3', '-c', 'print(1)', '-m docich.twica_encoder'),
            ('python3', '-c', 'print(1)', '-m', 'docich.twica_encoder'),
            ('python3', 'other.py', '-m', 'docich.twica_encoder'),
            ('python3', '-m', 'unrelated', '-m', 'docich.twica_encoder'),
        ):
            with self.subTest(argv=argv):
                self.assertIsNone(prio.wanted(P(3, UID, 'python3', argv), UID))
        self.assertEqual(prio.wanted(P(3, UID, 'python3', (
            'python3', '-u', '-I', '-m', 'docich.twica_encoder', '6', '9', '4')), UID), 'twica-feeder')


class PlanTests(unittest.TestCase):
    def test_only_threads_below_target_priority_are_planned(self):
        procs = [encoder(10), P(11, UID, 'retroarch', ('retroarch',)), P(12, UID, 'node', ('node',))]
        nices = {10: {10: T(-10, 1), 101: T(0, 2)},
                 11: {11: T(0, 3), 111: T(5, 4), 112: T(-12, 5)}, 12: {12: T(0, 6)}}
        got = sorted(prio.plan(procs, nices, UID))
        # -10(既に目標)と-12(より高優先)は触らない。ノードは対象外。
        self.assertEqual(got, [(10, 101, 'encoder-ffmpeg', 0), (11, 11, 'retroarch', 0), (11, 111, 'retroarch', 5)])

    def test_nothing_to_do_when_already_at_target(self):
        procs = [P(1, UID, 'pulseaudio', ('pulseaudio',))]
        self.assertEqual(prio.plan(procs, {1: {1: T(-10, 1), 2: T(-10, 2)}}, UID), [])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.proc = P(10, UID, 'retroarch', ('retroarch',), 100)
        patches = {
            'scan': {'return_value': ([self.proc], {10: {10: T(0, 100), 11: T(0, 101)}})},
            '_process': {'return_value': self.proc},
            '_thread': {'side_effect': lambda path: T(0, 100 if '/10/stat' in path else 101)},
        }
        self.mocks = {}
        for name, kwargs in patches.items():
            patcher = mock.patch.object(prio, name, **kwargs)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        for name, kwargs in {
            'stat': {'return_value': SimpleNamespace(st_uid=UID)},
            'getpriority': {'return_value': 0},
            'setpriority': {},
        }.items():
            patcher = mock.patch.object(prio.os, name, **kwargs)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.stdout))
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def test_applies_to_every_eligible_thread(self):
        self.assertEqual(prio.apply_once(UID, -10, False), 2)
        self.assertEqual(self.mocks['setpriority'].call_args_list, [
            mock.call(os.PRIO_PROCESS, 10, -10), mock.call(os.PRIO_PROCESS, 11, -10)])
        self.assertIn('threads=2 nice=-10', self.stdout.getvalue())

    def test_dry_run_never_calls_setpriority(self):
        self.assertEqual(prio.apply_once(UID, -10, True), 2)
        self.mocks['setpriority'].assert_not_called()
        self.assertIn('would renice', self.stdout.getvalue())

    def test_does_not_lower_a_priority_changed_since_scan(self):
        self.mocks['getpriority'].side_effect = [-15, -10]
        self.assertEqual(prio.apply_once(UID, -10, False), 0)
        self.mocks['setpriority'].assert_not_called()

    def test_changed_process_identity_is_skipped(self):
        for changes in ({'uid': 0}, {'starttime': 200}, {'comm': 'other'}, {'argv': ('other',)}):
            with self.subTest(changes=changes):
                self.mocks['_process'].return_value = replace(self.proc, **changes)
                self.assertEqual(prio.apply_once(UID, -10, False), 0)
        self.mocks['setpriority'].assert_not_called()

    def test_reused_thread_or_changed_thread_owner_is_skipped(self):
        self.mocks['_thread'].side_effect = None
        self.mocks['_thread'].return_value = T(0, 999)
        self.assertEqual(prio.apply_once(UID, -10, False), 0)
        self.mocks['stat'].return_value = SimpleNamespace(st_uid=0)
        self.assertEqual(prio.apply_once(UID, -10, False), 0)
        self.mocks['setpriority'].assert_not_called()

    def test_exited_thread_is_quiet_and_other_thread_still_runs(self):
        self.mocks['setpriority'].side_effect = [ProcessLookupError(errno.ESRCH, 'exited'), None]
        warnings = prio.Warnings()
        self.assertEqual(prio.apply_once(UID, -10, False, warnings), 1)
        self.assertEqual(warnings.errors, 0)
        self.assertEqual(self.stderr.getvalue(), '')

    def test_permission_errors_are_visible_throttled_and_nonzero_for_cli(self):
        for code in (errno.EPERM, errno.EACCES):
            with self.subTest(code=code):
                self.stderr.seek(0)
                self.stderr.truncate(0)
                self.mocks['setpriority'].side_effect = PermissionError(code, 'secret-target')
                warnings = prio.Warnings()
                with mock.patch.object(prio.time, 'monotonic', return_value=100):
                    self.assertEqual(prio.apply_once(UID, -10, False, warnings), 0)
                    self.assertEqual(prio.apply_once(UID, -10, False, warnings), 0)
                self.assertEqual(warnings.errors, 4)
                self.assertEqual(self.stderr.getvalue().count('warning operation=apply'), 1)
                self.assertIn(f'errno={errno.errorcode[code]}', self.stderr.getvalue())
                self.assertNotIn('secret-target', self.stderr.getvalue())
                with mock.patch.object(prio.time, 'monotonic', return_value=160):
                    prio.apply_once(UID, -10, False, warnings)
                self.assertEqual(self.stderr.getvalue().count('warning operation=apply'), 2)
                self.assertEqual(prio.main(['--uid', str(UID), '--once']), 1)


class ProcReadTests(unittest.TestCase):
    def test_stat_parser_preserves_thread_nice_and_starttime_with_unusual_comm(self):
        fields = ['S'] + ['0'] * 21
        fields[16], fields[19] = '-12', '98765'
        with mock.patch.object(prio, '_read', return_value='10 (name with ) spaces) ' + ' '.join(fields)):
            self.assertEqual(prio._thread('/proc/10/task/11/stat'), T(-12, 98765))

    def test_scan_reports_permission_error_but_ignores_disappeared_process(self):
        warnings = prio.Warnings()
        stderr = io.StringIO()
        with mock.patch.object(prio.os, 'listdir', return_value=['10', '11']), \
                mock.patch.object(prio.os, 'stat', side_effect=[
                    PermissionError(errno.EACCES, 'hidden'), FileNotFoundError(errno.ENOENT, 'gone')]), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(prio.scan(UID, warnings), ([], {}))
        self.assertEqual(warnings.errors, 1)
        self.assertIn('operation=scan-process', stderr.getvalue())
        self.assertIn('errno=EACCES', stderr.getvalue())
        self.assertNotIn('ENOENT', stderr.getvalue())


class CliAndUnitTests(unittest.TestCase):
    def test_cli_refuses_to_lower_priority_or_spin(self):
        for bad in (['--nice', '5'], ['--nice', '-21'], ['--interval', '0.1'],
                    ['--interval', 'nan'], ['--interval', 'inf']):
            with self.assertRaises(SystemExit):
                prio.main(bad + ['--uid', str(UID), '--once'])

    def test_default_uid_is_resolved_from_ubuntu(self):
        import pwd
        with mock.patch.object(pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=5432)), \
                mock.patch.object(prio, 'apply_once') as apply:
            self.assertEqual(prio.main(['--once', '--dry-run']), 0)
        self.assertEqual(apply.call_args.args[:3], (5432, -10, True))

    @unittest.skipUnless(shutil.which('systemctl'), 'needs systemctl offline unit support')
    def test_unit_can_be_enabled_for_vm_boot_without_starting_services(self):
        # --rootは隔離した一時ディレクトリにリンクを作るだけ。本機のsystemdには接続しない。
        version = subprocess.run(['systemctl', '--version'], capture_output=True, text=True, timeout=10)
        if version.returncode != 0 or not version.stdout.startswith('systemd '):
            self.skipTest('systemctl is unavailable or replaced by a container compatibility command')
        with tempfile.TemporaryDirectory(prefix='audio-priority-enable-') as temp:
            units = pathlib.Path(temp) / 'etc/systemd/system'
            units.mkdir(parents=True)
            source = pathlib.Path(__file__).resolve().parents[1] / 'docich-audio-priority.service'
            shutil.copyfile(source, units / source.name)
            result = subprocess.run(['systemctl', '--root', temp, 'enable', source.name],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((units / 'multi-user.target.wants' / source.name).is_symlink(), result.stderr)

    @unittest.skipUnless(os.path.isdir('/proc/self'), 'needs Linux /proc')
    def test_scan_dry_run_runs_against_real_proc_without_changing_anything(self):
        # 自分自身のuidで実/procを走査できること(対象がなくても例外にならない)。
        self.assertEqual(prio.main(['--uid', str(os.getuid()), '--once', '--dry-run']), 0)

    def test_unit_is_least_privilege(self):
        unit = (pathlib.Path(__file__).resolve().parents[1] / 'docich-audio-priority.service').read_text()
        self.assertIn('User=ubuntu', unit)
        self.assertIn('AmbientCapabilities=CAP_SYS_NICE', unit)
        self.assertIn('CapabilityBoundingSet=CAP_SYS_NICE', unit)
        self.assertIn('NoNewPrivileges=yes', unit)
        self.assertIn('PrivateNetwork=yes', unit)
        self.assertIsNone(re.search(r'^User=root', unit, re.M))
        self.assertIn('/usr/local/libexec/azumag-vm-ops/audio_priority/priority.py', unit)
        self.assertNotIn('--uid', unit)
        self.assertRegex(unit, r'\[Install\]\s+WantedBy=multi-user\.target')


if __name__ == '__main__':
    unittest.main()
