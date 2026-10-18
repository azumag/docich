"""No production access: xlogfile writer contract projection (#753).

The collector must stat fixed contract files only, never follow symlinks,
never write, never execute anything except the allowlisted dpkg-query, and
never leak xlog content, names, or death text into the output.
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))

spec = importlib.util.spec_from_file_location('nethack_xlog_collector', ROOT / 'ops/vm_actions/collect_diagnostics.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class XlogPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.playground = self.root / 'nethack'
        self.playground.mkdir()
        self.save = self.playground / 'save'
        self.save.mkdir()

    def settings(self, **overrides):
        resolved = {
            'player': 'docich',
            'save_dir': self.save,
            'xlogfile': self.playground / 'xlogfile',
            'dump_dir': self.playground / 'dumps',
            'source': 'production-config',
        }
        resolved.update(overrides)
        return resolved

    def collect(self, **overrides):
        def guard_run(argv, **_):
            # The only allowed exec is the fixed dpkg-query (package -W and
            # file-list -L); everything else (and any argv drift) fails the test.
            if argv[:2] == ['dpkg-query', '-W']:
                self.assertEqual(
                    argv[2], '-f=${Package}\t${Version}\t${Status}\n')
                self.assertEqual(set(argv[3:]), set(diag.NETHACK_XLOG_PACKAGES))
                return subprocess.CompletedProcess(argv, 1, '', '')
            if argv[:2] == ['dpkg-query', '-L']:
                self.assertEqual(set(argv[2:]), set(diag.NETHACK_XLOG_PACKAGES))
                return subprocess.CompletedProcess(argv, 1, '', '')
            raise AssertionError('no exec: %r' % (argv,))
        with mock.patch.object(diag, '_nethack_xlog_game_paths',
                               return_value=self.settings(**overrides)), \
             mock.patch.object(diag.subprocess, 'run', side_effect=guard_run):
            return diag._collect_nethack_xlog(1790000000)

    def test_missing_files_are_absent_not_zero(self):
        result = self.collect()
        for name in ('xlogfile', 'logfile', 'record'):
            self.assertEqual(result['paths'][name], {'exists': False}, name)
        self.assertEqual(result['paths']['xlogfile_lock'],
                         {'exists': False, 'same_inode_as_xlogfile': None})
        self.assertEqual(result['paths']['logfile_lock'],
                         {'exists': False, 'same_inode_as_logfile': None})
        self.assertEqual(result['dirs']['playground'], {'exists': True, 'symlink': False,
                                                        'regular': False, 'dir': True,
                                                        'size': self.playground.stat().st_size,
                                                        'mtime': self.playground.stat().st_mtime,
                                                        'mode': stat.S_IMODE(self.playground.stat().st_mode),
                                                        'uid': self.playground.stat().st_uid,
                                                        'gid': self.playground.stat().st_gid,
                                                        'nlink': self.playground.stat().st_nlink})
        self.assertIs(result['findings']['xlog_lock_present'], False)
        self.assertIsNone(result['findings']['xlog_lock_shares_inode'])
        self.assertEqual(result['xlog'], {'readable': False})

    def test_fixed_alternates_and_launchers_are_reported(self):
        result = self.collect()
        for key in ('/var/lib/nethack/xlogfile', '/usr/games/lib/nethackdir/xlogfile'):
            self.assertEqual(result['alternates'][key], {'exists': False}, key)
        self.assertIn('/usr/games/nethack', result['launchers'])
        self.assertIn('/usr/local/bin/nethack', result['launchers'])
        self.assertIs(result['xlog_siblings']['present'], True)
        self.assertEqual(result['xlog_siblings']['entries'], [])
        self.assertIs(result['binaries']['available'], False)
        self.assertEqual(result['binaries']['entries'], [])

    def test_stale_lock_is_detected_without_following(self):
        xlogfile = self.playground / 'xlogfile'
        xlogfile.write_bytes(b'')
        lock = self.playground / 'xlogfile_lock'
        os.link(xlogfile, lock)
        result = self.collect()
        self.assertIs(result['paths']['xlogfile_lock']['exists'], True)
        self.assertEqual(result['paths']['xlogfile_lock']['size'], 0)
        self.assertIs(result['findings']['xlog_lock_present'], True)
        self.assertIs(result['findings']['xlog_lock_shares_inode'], True)
        self.assertIs(result['findings']['xlog_file_shared_hardlink'], True)

    def test_unrelated_lock_does_not_share_inode(self):
        (self.playground / 'xlogfile').write_bytes(b'name=x\n')
        (self.playground / 'xlogfile_lock').write_bytes(b'unrelated\n')
        result = self.collect()
        self.assertIs(result['findings']['xlog_lock_present'], True)
        self.assertIs(result['findings']['xlog_lock_shares_inode'], False)
        self.assertIs(result['findings']['xlog_file_shared_hardlink'], False)

    def test_symlink_never_followed(self):
        target = self.root / 'outside-secret'
        target.write_bytes(b'name=SECRET\n')
        (self.playground / 'xlogfile').symlink_to(target)
        result = self.collect()
        self.assertIs(result['paths']['xlogfile']['symlink'], True)
        self.assertIs(result['paths']['xlogfile']['regular'], False)
        self.assertEqual(result['xlog'], {'readable': False})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_xlog_tail_derives_numbers_without_content(self):
        record_own = ('version=3.6.6\tpoints=123\tname=docich\tdeath=SECRET death text\t'
                      'turns=456\tmaxlvl=7\tstarttime=1789990000\tendtime=1789999999\t'
                      'achieve=0x20\n')
        record_other = ('version=3.6.6\tpoints=9\tname=someone\tdeath=SECRET2\t'
                        'turns=1\tmaxlvl=1\tstarttime=1789990001\tendtime=1789990002\tachieve=0x1\n')
        incomplete = b'version=3.6.6\tpoints=5\tname=docich'
        (self.playground / 'xlogfile').write_bytes(
            record_own.encode() + record_other.encode() + incomplete)
        result = self.collect()
        self.assertIs(result['xlog']['readable'], True)
        self.assertEqual(result['xlog']['size'],
                         len(record_own) + len(record_other) + len(incomplete))
        self.assertEqual(result['xlog']['tail_complete_lines'], 2)
        self.assertIs(result['xlog']['tail_head_truncated'], False)
        first = result['xlog']['first_record']
        self.assertIs(first['name_matches_player'], True)
        self.assertEqual(first['points'], 123)
        self.assertEqual(first['starttime'], 1789990000)
        self.assertEqual(first['endtime'], 1789999999)
        self.assertEqual(first['achieve'], 0x20)
        self.assertNotIn('death', first)
        last = result['xlog']['last_record']
        self.assertIs(last['name_matches_player'], False)
        self.assertEqual(last['points'], 9)
        self.assertEqual(last['turns'], 1)
        self.assertEqual(last['maxlvl'], 1)
        self.assertEqual(last['starttime'], 1789990001)
        self.assertEqual(last['endtime'], 1789990002)
        self.assertEqual(last['achieve'], 1)
        self.assertNotIn('death', last)
        self.assertEqual(result['xlog']['record_span'], {
            'first_starttime': 1789990000, 'last_endtime': 1789990002,
            'complete_lines': 2})
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('docich', json.dumps(result['xlog']))

    def test_tail_over_cap_reports_truncated_head(self):
        big = b''.join(
            b'version=3.6.6\tpoints=%020d\tname=docich\tturns=%d\t'
            b'starttime=%d\tendtime=%d\n'
            % (value, value, 1790000000 + value, 1790000000 + value)
            for value in range(diag.NETHACK_XLOG_TAIL_BYTES // 64 + 8))
        self.assertGreater(len(big), diag.NETHACK_XLOG_TAIL_BYTES)
        (self.playground / 'xlogfile').write_bytes(big)
        result = self.collect()
        self.assertIs(result['xlog']['tail_head_truncated'], True)
        self.assertEqual(result['xlog']['size'], len(big))
        # The window starts mid-line: the leading fragment is not a record,
        # and lines entirely before the window are outside the bounded tail.
        self.assertGreater(result['xlog']['tail_complete_lines'], 0)
        self.assertLess(result['xlog']['tail_complete_lines'],
                        len(big.splitlines()))
        span = result['xlog']['record_span']
        self.assertEqual(span['complete_lines'], result['xlog']['tail_complete_lines'])
        self.assertEqual(span['last_endtime'], 1790000000 + len(big.splitlines()) - 1)
        # The first complete in-window record is a real record, but not the
        # file's first one: earlier records exist beyond the truncated head.
        self.assertIsNotNone(result['xlog']['first_record'])
        self.assertGreater(span['first_starttime'], 1790000000)
        self.assertLess(span['first_starttime'], span['last_endtime'])

    def test_head_truncated_single_fragment_has_no_records(self):
        (self.playground / 'xlogfile').write_bytes(b'x' * 65536 + b'\n')
        result = self.collect()
        self.assertIs(result['xlog']['tail_head_truncated'], True)
        self.assertEqual(result['xlog']['tail_complete_lines'], 0)
        self.assertIsNone(result['xlog']['record_span'])
        self.assertIsNone(result['xlog']['first_record'])
        self.assertIsNone(result['xlog']['last_record'])

    def test_head_fragment_is_never_counted_as_a_record(self):
        complete = b'version=3.6.6\tpoints=7\tname=docich\tstarttime=1\tendtime=2\n'
        (self.playground / 'xlogfile').write_bytes(b'x' * 65536 + b'\n' + complete)
        result = self.collect()
        self.assertIs(result['xlog']['tail_head_truncated'], True)
        self.assertEqual(result['xlog']['tail_complete_lines'], 1)
        self.assertEqual(result['xlog']['record_span'], {
            'first_starttime': 1, 'last_endtime': 2, 'complete_lines': 1})
        self.assertEqual(result['xlog']['last_record']['points'], 7)

    def test_fragment_starttime_never_leaks_into_span(self):
        # Review 5561778413 follow-up: the truncated head fragment itself
        # carries a plausible starttime. A naive tail read would parse the
        # fragment as a line and report first_starttime=9999999999. The
        # boundary must fall *inside* that straddling line (the window is
        # the last 64 KiB), with its starttime text in the visible part.
        line_p = b'x' * 264 + b'starttime=9999999999\tendtime=1\tname=docich\n'
        first_complete = (b'version=3.6.6\tpoints=7\tname=docich\t'
                          b'starttime=1789990100\tendtime=1789990200\n')
        last_complete = (b'version=3.6.6\tpoints=8\tname=docich\t'
                         b'starttime=1789990300\tendtime=1789990400\n')
        # file = line_p + blob  =>  window_start = file_size - 64 KiB = 264,
        # so the visible fragment is line_p[264:] = 'starttime=9999999999…'.
        blob_bytes = 65536 + 264 - len(line_p)
        filler_size = blob_bytes - len(first_complete) - len(last_complete)
        self.assertGreater(filler_size, 2)
        blob = first_complete + b'A' * (filler_size - 1) + b'\n' + last_complete
        self.assertEqual(len(blob), blob_bytes)
        (self.playground / 'xlogfile').write_bytes(line_p + blob)
        result = self.collect()
        self.assertIs(result['xlog']['tail_head_truncated'], True)
        # Only the three complete lines survive; the fragment is not a line.
        self.assertEqual(result['xlog']['tail_complete_lines'], 3)
        self.assertEqual(result['xlog']['record_span'], {
            'first_starttime': 1789990100, 'last_endtime': 1789990400,
            'complete_lines': 3})
        self.assertEqual(result['xlog']['first_record']['points'], 7)
        self.assertNotIn('9999999999', json.dumps(result['xlog']['record_span']))
        self.assertNotIn('9999999999', json.dumps(result['xlog']['first_record']))

    def test_save_entries_count_only_matching_regular_files(self):
        (self.save / '1000docich').write_bytes(b'save')
        (self.save / '1000docich.0').write_bytes(b'save2')
        (self.save / '999other').write_bytes(b'nope')
        (self.save / '1000docich.dir').mkdir()
        link = self.save / '2000docich'
        link.symlink_to(self.save / '1000docich')
        result = self.collect()
        entries = result['save_dir_entries']
        self.assertIs(entries['present'], True)
        self.assertEqual(entries['scanned_entries'], 5)
        self.assertEqual(entries['player_saves'], 2)
        self.assertEqual(entries['newest_player_save_mtime'],
                         max((self.save / '1000docich').stat().st_mtime,
                             (self.save / '1000docich.0').stat().st_mtime))
        self.assertIs(entries['scan_complete'], True)

    def test_missing_save_dir(self):
        result = self.collect(save_dir=self.playground / 'absent')
        self.assertIs(result['save_dir_entries']['present'], False)
        self.assertEqual(result['save_dir_entries']['player_saves'], 0)

    def test_read_only_and_gateway_safe(self):
        (self.playground / 'xlogfile').write_bytes(b'name=docich\tpoints=3\n')
        (self.playground / 'record').write_bytes(b'record\n')
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        result = self.collect()
        after = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        text = json.dumps(result, sort_keys=True)
        def depth(value):
            if isinstance(value, dict):
                return 1 + max((depth(item) for item in value.values()), default=0)
            if isinstance(value, list):
                return 1 + max((depth(item) for item in value), default=0)
            return 0
        self.assertLessEqual(depth(result), 8)
        self.assertLess(len(text), 8192)
        self.assertNotIn('SECRET', text)


class XlogConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config_dir = self.root / 'config' / 'games'
        self.config_dir.mkdir(parents=True)

    def write_config(self, body):
        (self.config_dir / 'nethack.toml').write_text(body, encoding='utf-8')

    def resolve(self):
        with mock.patch.object(diag, 'PROD_ROOT', self.root):
            return diag._nethack_xlog_game_paths()

    def test_defaults_without_config(self):
        resolved = self.resolve()
        self.assertEqual(resolved['player'], 'docich')
        self.assertEqual(resolved['source'], 'defaults')
        self.assertEqual(resolved['xlogfile'], Path('/var/games/nethack/xlogfile'))
        self.assertEqual(resolved['save_dir'], Path('/var/games/nethack/save'))

    def test_production_config_overrides(self):
        self.write_config(
            '[nethack]\n'
            'persistent_run = true\n'
            'player_name = "soren"\n'
            'save_dir = "/var/games/nethack/save"\n'
            'xlogfile = "/var/games/nethack/xlogfile"\n'
            'dump_dir = "/var/games/nethack/dumps"\n')
        resolved = self.resolve()
        self.assertEqual(resolved['player'], 'soren')
        self.assertEqual(resolved['source'], 'production-config')
        self.assertEqual(resolved['xlogfile'], Path('/var/games/nethack/xlogfile'))

    def test_relative_and_traversal_paths_rejected(self):
        self.write_config(
            '[nethack]\n'
            'player_name = "docich"\n'
            'xlogfile = "relative/xlogfile"\n'
            'save_dir = "/var/games/../etc"\n')
        resolved = self.resolve()
        self.assertEqual(resolved['source'], 'production-config')
        self.assertEqual(resolved['xlogfile'], Path('/var/games/nethack/xlogfile'))
        self.assertEqual(resolved['save_dir'], Path('/var/games/nethack/save'))

    def test_invalid_player_falls_back(self):
        self.write_config('[nethack]\nplayer_name = "bad name!"\n')
        resolved = self.resolve()
        self.assertEqual(resolved['player'], 'docich')


class XlogSysconfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def collect_sysconf(self, body=None, *, write=True):
        if write:
            (self.root / 'sysconf').write_text(body or '', encoding='utf-8')
        with mock.patch.object(diag, 'NETHACK_XLOG_SYSCONF_PATH', self.root / 'sysconf'), \
             mock.patch.object(diag.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 1, '', '')):
            return diag._collect_nethack_xlog(1790000000)['sysconf']

    def test_missing_sysconf(self):
        result = self.collect_sysconf(write=False)
        self.assertIs(result['present'], False)
        self.assertIs(result['readable'], False)
        self.assertEqual(result['configured'], [])

    def test_allowlisted_keys_only(self):
        body = (
            '# comment\n'
            'WIZARDS=SECRET\n'
            'XLOGFILE=xlogfile\n'
            'DUMPLOGFILE=/var/games/nethack/dumps/%n.%d.log\n'
            'LOGFILE=logfile\n'
            'GDBPATH=/SECRET/gdb\n')
        result = self.collect_sysconf(body)
        self.assertIs(result['readable'], True)
        self.assertEqual(result['configured'], ['xlogfile', 'dumplogfile', 'logfile'])
        self.assertEqual(result['values']['xlogfile'], 'xlogfile')
        self.assertEqual(result['values']['dumplogfile'], '/var/games/nethack/dumps/%n.%d.log')
        self.assertEqual(result['values']['logfile'], 'logfile')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_unsafe_value_hidden_but_counted(self):
        result = self.collect_sysconf('XLOGFILE=value with spaces\n')
        self.assertEqual(result['configured'], ['xlogfile'])
        self.assertIsNone(result['values']['xlogfile'])


class XlogPackagesTests(unittest.TestCase):
    def run_packages(self, stdout='', returncode=0, **kwargs):
        def fake_run(argv, **_):
            if argv[:2] == ['dpkg-query', '-L']:
                return subprocess.CompletedProcess(argv, 1, '', '')
            self.assertEqual(argv[:3], ['dpkg-query', '-W',
                                        '-f=${Package}\t${Version}\t${Status}\n'])
            self.assertEqual(set(argv[3:]), set(diag.NETHACK_XLOG_PACKAGES))
            return subprocess.CompletedProcess(argv, returncode, stdout, '')
        with mock.patch.object(diag.subprocess, 'run', side_effect=fake_run):
            return diag._collect_nethack_xlog(1790000000)['packages']

    def test_installed_entries(self):
        result = self.run_packages(
            'nethack-common\t3.6.7-1\tinstall ok installed\n'
            'nethack-console\t3.6.7-1\tinstall ok installed\n'
            'nethack-x11\t3.6.7-1\tdeinstall ok config-files\n')
        self.assertIs(result['available'], True)
        self.assertEqual(len(result['entries']), 2)
        for entry in result['entries']:
            self.assertIs(entry['installed'], True)
            self.assertEqual(entry['version'], '3.6.7-1')

    def test_query_failure_is_unavailable(self):
        result = self.run_packages(stdout='', returncode=1)
        self.assertIs(result['available'], False)
        self.assertEqual(result['entries'], [])

    def test_exec_unavailable(self):
        with mock.patch.object(diag.subprocess, 'run',
                               side_effect=FileNotFoundError('no dpkg')):
            result = diag._collect_nethack_xlog(1790000000)['packages']
        self.assertIs(result['available'], False)


class XlogProcessTests(unittest.TestCase):
    def test_no_nethack_process(self):
        result = diag._collect_nethack_xlog(1790000000)['processes']
        self.assertEqual(result['count'], result['entries'].__len__())
        self.assertIsInstance(result['count'], int)


class XlogLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def launcher(self, body=None, *, symlink_to=None):
        path = self.root / 'nethack'
        if symlink_to is not None:
            path.symlink_to(symlink_to)
        else:
            path.write_text(body or '', encoding='utf-8')
        return diag._nethack_xlog_launcher(path)

    def test_symlink_target_is_projected(self):
        target = self.root / 'real-nethack'
        target.write_bytes(b'ELF')
        entry = self.launcher(symlink_to=target)
        self.assertIs(entry['stat']['symlink'], True)
        self.assertEqual(entry['target'], str(target))
        self.assertEqual(entry['vars'], {})

    def test_script_vars_allowlist(self):
        entry = self.launcher(
            '#!/bin/sh\nHACKDIR=/usr/lib/games/nethack\n'
            'export SECRET=/PRIVATE\nXLOGFILE=xlogfile\nWIZARDS=root\n')
        self.assertEqual(entry['vars'], {'hackdir': '/usr/lib/games/nethack',
                                         'xlogfile': 'xlogfile'})
        self.assertNotIn('SECRET', json.dumps(entry))

    def test_binary_launcher_has_no_vars(self):
        entry = self.launcher()  # empty regular file
        self.assertEqual(entry['vars'], {})


class XlogBinariesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def collect_binaries(self, dpkg_list_stdout=''):
        def fake_run(argv, **_):
            if argv[:2] == ['dpkg-query', '-L']:
                self.assertEqual(set(argv[2:]), set(diag.NETHACK_XLOG_PACKAGES))
                return subprocess.CompletedProcess(argv, 0, dpkg_list_stdout, '')
            raise AssertionError('no exec: %r' % (argv,))
        with mock.patch.object(diag.subprocess, 'run', side_effect=fake_run):
            return diag._nethack_xlog_binaries([])

    def test_dpkg_binary_containing_xlog_string(self):
        binary = self.root / 'nethack'
        binary.write_bytes(b'\x7fELF...xlogfile\x00...')
        result = self.collect_binaries(f'{binary}\n/usr/share/doc/README\n')
        self.assertIs(result['available'], True)
        self.assertEqual(len(result['entries']), 1)
        self.assertEqual(result['entries'][0]['origin'], 'dpkg')
        self.assertIs(result['entries'][0]['xlog_string_present'], True)
        self.assertIs(result['entries'][0]['binary_scan_complete'], True)

    def test_binary_without_string_reports_false(self):
        binary = self.root / 'nethack'
        binary.write_bytes(b'\x7fELF no xlog here')
        result = self.collect_binaries(f'{binary}\n')
        self.assertIs(result['entries'][0]['xlog_string_present'], False)
        self.assertIs(result['entries'][0]['binary_scan_complete'], True)

    def test_cap_truncated_scan_reports_incomplete(self):
        binary = self.root / 'nethack'
        binary.write_bytes(b'A' * 32 + b'xlogfile' + b'B' * 24)
        with mock.patch.object(
                diag.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, f'{binary}\n', '')), \
             mock.patch.object(diag, 'NETHACK_XLOG_BINARY_BYTES', 16):
            result = diag._nethack_xlog_binaries([])
        self.assertEqual(result['entries'], [{'origin': 'dpkg',
                                              'xlog_string_present': False,
                                              'binary_scan_complete': False}])

    def test_needle_spanning_read_boundary_is_found(self):
        boundary = 256 * 1024
        binary = self.root / 'nethack'
        binary.write_bytes(b'A' * (boundary - 4) + b'xlogfile' + b'B' * 16)
        with mock.patch.object(
                diag.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, f'{binary}\n', '')):
            result = diag._nethack_xlog_binaries([])
        self.assertIs(result['entries'][0]['xlog_string_present'], True)
        self.assertIs(result['entries'][0]['binary_scan_complete'], True)

    def test_missing_binary_is_skipped_and_unavailable(self):
        result = self.collect_binaries('/nonexistent/nethack\n')
        self.assertIs(result['available'], False)
        self.assertEqual(result['entries'], [])


class XlogSiblingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.playground = self.root / 'nethack'
        self.playground.mkdir()

    def test_only_xlogfile_prefixed_siblings(self):
        (self.playground / 'xlogfile').write_bytes(b'')
        (self.playground / 'xlogfile.1').write_bytes(b'old')
        (self.playground / 'xlogfile_lock').write_bytes(b'')
        (self.playground / 'record').write_bytes(b'unrelated')
        result = diag._nethack_xlog_siblings(self.playground)
        self.assertIs(result['present'], True)
        self.assertEqual([item['name'] for item in result['entries']],
                         ['xlogfile', 'xlogfile.1', 'xlogfile_lock'])
        self.assertEqual(result['entries'][1]['size'], 3)
        self.assertIs(result['scan_complete'], True)

    def test_missing_playground(self):
        result = diag._nethack_xlog_siblings(self.root / 'absent')
        self.assertIs(result['present'], False)
        self.assertEqual(result['entries'], [])


class XlogBudgetTests(unittest.TestCase):
    def section(self):
        return {
            'schema_version': 1, 'collected_at': 1790000000,
            'xlog': {'readable': True, 'record_span': {
                         'first_starttime': 1789990000, 'last_endtime': 1789999999,
                         'complete_lines': 2},
                     'first_record': {'points': 5}, 'last_record': {'points': 5}},
            'binaries': {'available': True, 'entries': [{'origin': 'dpkg'}]},
            'xlog_siblings': {'present': True, 'entries': [{'name': 'xlogfile.1'}]},
            'processes': {'count': 1, 'entries': [{'pid': 7}]},
            'sysconf': {'readable': True, 'values': {'xlogfile': 'xlogfile'}},
        }

    def test_budget_drops_optional_detail_only(self):
        payload = {'nethack_xlog': self.section()}
        diag._nethack_xlog_budget(payload)
        section = payload['nethack_xlog']
        self.assertIsNone(section['xlog']['first_record'])
        self.assertIs(section['xlog']['first_record_omitted'], True)
        self.assertIsNone(section['xlog']['last_record'])
        self.assertIs(section['xlog']['last_record_omitted'], True)
        # record_span is the #753 timeline evidence: it survives trimming.
        self.assertEqual(section['xlog']['record_span'], {
            'first_starttime': 1789990000, 'last_endtime': 1789999999,
            'complete_lines': 2})
        self.assertEqual(section['binaries']['entries'], [])
        self.assertIs(section['binaries']['entries_omitted'], True)
        self.assertEqual(section['xlog_siblings']['entries'], [])
        self.assertEqual(section['processes']['entries'], [])
        self.assertEqual(section['sysconf']['values'], {})
        self.assertIs(section['sysconf']['values_omitted'], True)

    def test_drop_collapses_to_findings_stub(self):
        payload = {'nethack_xlog': self.section()}
        diag._nethack_xlog_drop(payload)
        self.assertEqual(payload['nethack_xlog'], {
            'schema_version': 1, 'collected_at': 1790000000,
            'status': 'output_omitted'})

    def test_budget_ignores_missing_section(self):
        payload = {}
        diag._nethack_xlog_budget(payload)
        diag._nethack_xlog_drop(payload)
        self.assertEqual(payload, {})


class HistorySummaryTests(unittest.TestCase):
    """Source-level summaries must cover records that budget trimming drops."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runs = self.root / 'nethack' / 'runs'
        self.runs.mkdir(parents=True)

    def run_record(self, index, *, status, score=None, analysis_error=None):
        raw = {
            'schema_version': 1, 'run_id': f'00000000-0000-0000-0000-{index:012x}',
            'expedition': index, 'status': status,
            'started_at': '2026-10-01T01:00:00+00:00',
            'last_finished_at': '2026-10-01T02:00:00+00:00',
            'score': score, 'turns': None, 'max_depth': None,
        }
        if analysis_error is not None:
            raw['terminal'] = {'source': 'xlogfile', 'analysis_error': analysis_error}
        (self.runs / f"{raw['run_id']}.json").write_text(json.dumps(raw))

    def collect(self):
        return diag._collect_nethack_history(self.root, False)

    def test_summary_counts_all_terminal_records(self):
        self.run_record(1, status='dead', score=100)
        self.run_record(2, status='ended_unknown',
                        analysis_error='no-new-player-xlog-record')
        self.run_record(3, status='ended_unknown',
                        analysis_error='xlogfile-missing')
        self.run_record(4, status='ended_unknown', analysis_error='mystery-code')
        self.run_record(5, status='suspended')
        result = self.collect()
        summary = result['completed_runs']['summary']
        self.assertEqual(summary['terminal_status_counts']['dead'], 1)
        self.assertEqual(summary['terminal_status_counts']['ended_unknown'], 3)
        self.assertEqual(summary['terminal_status_counts']['ascended'], 0)
        self.assertEqual(summary['score_present_count'], 1)
        self.assertEqual(summary['analysis_error_counts']['no-new-player-xlog-record'], 1)
        self.assertEqual(summary['analysis_error_counts']['xlogfile-missing'], 1)
        self.assertEqual(summary['analysis_error_counts']['other'], 1)
        # Non-terminal and invalid runs are never counted as terminal.
        self.assertNotIn('suspended', summary['terminal_status_counts'])

    def test_summary_survives_record_omission(self):
        for index in range(1, 12):
            self.run_record(index, status='ended_unknown',
                            analysis_error='no-new-player-xlog-record')
        payload = {'nethack_history': {
            'daily': {'records': [1] * 3, 'omitted_records': 0,
                      'output_omitted': False},
            'completed_runs': self.collect()['completed_runs']}}
        payload['nethack_history']['completed_runs']['records'] = [
            {'x': index} for index in range(20)]
        with mock.patch.object(diag, 'MAX_JSON_BYTES', 1):
            diag._nethack_history_budget(payload)
        completed = payload['nethack_history']['completed_runs']
        self.assertEqual(completed['records'], [])
        self.assertGreater(completed['omitted_records'], 0)
        self.assertEqual(
            completed['summary']['terminal_status_counts']['ended_unknown'], 11)
        self.assertEqual(
            completed['summary']['analysis_error_counts']['no-new-player-xlog-record'], 11)

    def test_record_projection_of_analysis_error(self):
        self.run_record(1, status='ended_unknown',
                        analysis_error='no-new-player-xlog-record')
        self.run_record(2, status='dead', score=50)
        collected = self.collect()['completed_runs']
        records = {record['expedition']: record for record in collected['records']}
        self.assertEqual(records[1]['analysis_error'], 'no-new-player-xlog-record')
        self.assertIsNone(records[2]['analysis_error'])

    def test_analysis_error_unhashable_values_collapse_to_other(self):
        # A hostile producer value (list/dict) must not kill the diagnostics
        # scan with a TypeError from the frozenset membership test (#2515
        # review); it is counted as the fixed token ``other`` instead.
        self.run_record(1, status='ended_unknown', analysis_error=['x'])
        self.run_record(2, status='ended_unknown', analysis_error={'a': 1})
        self.run_record(3, status='ended_unknown', analysis_error=7)
        collected = self.collect()['completed_runs']
        self.assertEqual(collected['invalid_records'], 0)
        records = {record['expedition']: record for record in collected['records']}
        for expedition in (1, 2, 3):
            self.assertEqual(records[expedition]['analysis_error'], 'other')
        self.assertEqual(collected['summary']['analysis_error_counts']['other'], 3)

    def test_type_error_is_isolated_to_invalid_records(self):
        self.run_record(1, status='dead', score=5)
        with mock.patch.object(diag, '_nethack_record', side_effect=TypeError('boom')):
            collected = self.collect()['completed_runs']
        self.assertEqual(collected['invalid_records'], 1)
        self.assertEqual(collected['records'], [])
        self.assertEqual(collected['summary']['terminal_status_counts']['dead'], 0)

    def test_daily_has_no_summary(self):
        daily = self.root / 'nethack' / 'daily-improvements'
        daily.mkdir(parents=True)
        (daily / '2026-10-01.json').write_text(json.dumps(
            {'schema_version': 1, 'date': '2026-10-01',
             'generated_at': '2026-10-01T02:00:00+00:00', 'status': 'no_new_runs',
             'run_count': 0, 'candidates': [], 'policy_effect': 'none',
             'automatic_promotion': False}))
        result = diag._collect_nethack_history(self.root, True)
        self.assertNotIn('summary', result)


if __name__ == '__main__':
    unittest.main()
