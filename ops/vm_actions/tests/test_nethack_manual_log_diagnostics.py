"""No production access: fixed projection, hostile names and read-only checks."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]

spec = importlib.util.spec_from_file_location(
    'nethack_manual_log_collector', ROOT / 'ops/vm_actions/collect_diagnostics.py'
)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)

NAME = 'nethack-manual-1792750000-0123abcd.log'


class ManualLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.logs = self.root / 'logs'
        self.logs.mkdir(parents=True)

    def write(self, name, text, mtime=None):
        path = self.logs / name
        path.write_text(text, encoding='utf-8')
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def collect(self):
        return diag._collect_nethack_manual_log(self.root, 1792750000)

    def test_missing_directory_is_not_an_error(self):
        self.logs.rmdir()
        entry = self.collect()
        self.assertFalse(entry['present'])
        self.assertFalse(entry['readable'])
        self.assertEqual(entry['lines'], [])

    def test_reads_only_the_newest_matching_log(self):
        self.write('nethack-manual-1792740000-deadbeef.log', 'old run\n', mtime=1000)
        self.write(NAME, 'docich: エラー: runtime unavailable\nTraceback (most recent call last):\n', mtime=2000)
        self.write('nethack-manual-1792760000-ffffffff.log', 'newest run\n', mtime=3000)
        entry = self.collect()
        self.assertTrue(entry['present'])
        self.assertTrue(entry['readable'])
        self.assertEqual(entry['mtime'], 3000)
        self.assertEqual(entry['lines'][-1], 'newest run')

    def test_rejects_other_names_symlinks_and_directories(self):
        self.write('agent.log', 'agent noise\n')
        self.write('nethack-manual-1792750000-NOTHEX.log', 'bad name\n')
        target = self.root / 'outside.log'
        target.write_text('secret outside\n', encoding='utf-8')
        (self.logs / 'nethack-manual-1792750001-aaaaaaaa.log').symlink_to(target)
        (self.logs / 'nethack-manual-1792750002-bbbbbbbb.log').mkdir()
        entry = self.collect()
        self.assertFalse(entry['present'])
        self.assertEqual(entry['lines'], [])

    def test_redacts_and_caps_lines(self):
        secret = 'TOKEN=' + 'A' * 40 + ' password=hunter2'
        body = '\n'.join([secret] + ['x' * 500] * 12) + '\n'
        self.write(NAME, body)
        entry = self.collect()
        self.assertTrue(entry['tail_truncated'] in (True, False))
        rendered = json.dumps(entry)
        self.assertNotIn('hunter2', rendered)
        self.assertEqual(len(entry['lines']), diag.NETHACK_MANUAL_LOG_LINES)
        for line in entry['lines']:
            self.assertLessEqual(len(line), diag.NETHACK_MANUAL_LOG_LINE_LIMIT)

    def test_tail_only_and_read_only(self):
        self.write(NAME, '\n'.join(f'line {index}' for index in range(4000)) + '\n')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(diag.subprocess, 'run', side_effect=AssertionError('no exec')):
            entry = self.collect()
        self.assertTrue(entry['tail_truncated'])
        self.assertEqual(entry['lines'][-1], 'line 3999')
        self.assertEqual(
            before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        )

    def test_hostile_unicode_and_binary_do_not_raise(self):
        path = self.logs / NAME
        path.write_bytes(b'\xff\xfe\x00bad\n' + b'A' * 200 + b'\n')
        entry = self.collect()
        self.assertTrue(entry['readable'])
        self.assertTrue(all(isinstance(line, str) for line in entry['lines']))


if __name__ == '__main__':
    unittest.main()
