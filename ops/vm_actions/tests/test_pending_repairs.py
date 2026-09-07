import os
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('pending_gateway', Path(__file__).resolve().parents[1] / 'gateway.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class PendingRepairs(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask,os.umask(0o022))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.root.mkdir()
        self.live = Path(self.tmp.name) / 'live'
        self.live.mkdir()
        g.git(self.root, 'init', '-q')
        g.git(self.root, 'config', 'user.name', 'Test')
        g.git(self.root, 'config', 'user.email', 'test@example.com')
        self.old = self.commit('broken')
        (self.live / 'audio.py').write_text('fixed')
        self.fix = self.commit('fixed')
        self.entry = {'id': 'report1', 'projection': 'games/soviet_now', 'status': 'active', 'files': {
            'audio.py': {'before': g._expected_meta(self.root, g._tree_entry(self.root, self.old, 'audio.py')),
                         'after': g._expected_meta(self.root, g._tree_entry(self.root, self.fix, 'audio.py'))}}}

    def commit(self, value):
        (self.root / 'audio.py').write_text(value)
        g.git(self.root, 'add', 'audio.py')
        g.git(self.root, 'commit', '-qm', value)
        return g.git(self.root, 'rev-parse', 'HEAD')

    def plan(self, new):
        return g._plan_repaired_projection(self.root, self.live, self.old, new, [self.entry])

    def test_unrelated_main_keeps_pending_fix(self):
        changes, remaining = self.plan(self.old)
        self.assertEqual(changes, [])
        self.assertEqual(remaining, [self.entry])
        self.assertEqual((self.live / 'audio.py').read_text(), 'fixed')

    def test_identical_content_adopts_without_rewrite(self):
        changes, remaining = self.plan(self.fix)
        self.assertEqual(changes, [])
        self.assertEqual(remaining, [])

    def test_conflicting_main_is_rejected(self):
        other = self.commit('different fix')
        with self.assertRaisesRegex(ValueError, 'pending repair conflict'):
            self.plan(other)
        self.assertEqual((self.live / 'audio.py').read_text(), 'fixed')

    def test_unknown_live_drift_is_rejected_even_without_gitlink_change(self):
        (self.live / 'audio.py').write_text('manual change')
        with self.assertRaisesRegex(ValueError, 'pending repair drift'):
            self.plan(self.old)

    def test_incomplete_apply_blocks_deploy(self):
        self.entry['status'] = 'applying'
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.plan(self.fix)

    def test_duplicate_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            g._plan_repaired_projection(self.root, self.live, self.old, self.fix, [self.entry, self.entry])

    def test_atomic_write_syncs_file_then_parent_directory(self):
        from unittest import mock
        with mock.patch.object(g.os,'fsync',wraps=g.os.fsync) as sync:
            g.atomic_write(Path(self.tmp.name)/'journal.json',b'{}')
        self.assertEqual(sync.call_count,2)
