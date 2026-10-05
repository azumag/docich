import importlib.util
import os
import pathlib
import re
import sys
import unittest

SPEC = importlib.util.spec_from_file_location(
    'audio_priority', pathlib.Path(__file__).resolve().parents[1] / 'priority.py')
prio = importlib.util.module_from_spec(SPEC)
sys.modules['audio_priority'] = prio  # dataclassが型解決にsys.modulesを使う
SPEC.loader.exec_module(prio)

UID = 1001
P = prio.Proc


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


class PlanTests(unittest.TestCase):
    def test_only_threads_below_target_priority_are_planned(self):
        procs = [encoder(10), P(11, UID, 'retroarch', ('retroarch',)), P(12, UID, 'node', ('node',))]
        nices = {10: {10: -10, 101: 0}, 11: {11: 0, 111: 5, 112: -12}, 12: {12: 0}}
        got = sorted(prio.plan(procs, nices, UID))
        # -10(既に目標)と-12(より高優先)は触らない。ノードは対象外。
        self.assertEqual(got, [(10, 101, 'encoder-ffmpeg', 0), (11, 11, 'retroarch', 0), (11, 111, 'retroarch', 5)])

    def test_nothing_to_do_when_already_at_target(self):
        procs = [P(1, UID, 'pulseaudio', ('pulseaudio',))]
        self.assertEqual(prio.plan(procs, {1: {1: -10, 2: -10}}, UID), [])


class CliAndUnitTests(unittest.TestCase):
    def test_cli_refuses_to_lower_priority_or_spin(self):
        for bad in (['--nice', '5'], ['--nice', '-21'], ['--interval', '0.1']):
            with self.assertRaises(SystemExit):
                prio.main(bad + ['--uid', str(UID), '--once'])

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


if __name__ == '__main__':
    unittest.main()
