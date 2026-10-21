from __future__ import annotations

import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_shared_pid", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedRuntimePidTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_collector()
        self.tmp = tempfile.TemporaryDirectory(prefix="sharedpid-")
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name).resolve() / "proc"
        self.proc.mkdir()
        boot = self.proc / "sys/kernel/random"
        boot.mkdir(parents=True)
        (boot / "boot_id").write_text("12345678-1234-1234-1234-123456789abc\n")
        # fake /proc/stat with btime
        (self.proc / "stat").write_text("cpu 0 0 0 0 0 0 0 0 0 0\nbtime 1000\ncpu 0\n", encoding="utf-8")

    def _make_proc(self, pid, uid, comm, cmdline, start_ticks):
        base = self.proc / str(pid)
        base.mkdir()
        (base / "comm").write_text(comm + "\n", encoding="utf-8")
        (base / "cmdline").write_bytes((" ".join(cmdline) + "\x00").encode() if cmdline else b"")
        # /proc/<pid>/stat: comm in parens then fields; starttime at index 19
        stat_fields = ["S", "1", "1", "1", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "1", "0", str(start_ticks)]
        (base / "stat").write_text(f"{pid} ({comm}) " + " ".join(stat_fields) + "\n", encoding="utf-8")
        os.chown(base, uid, uid) if False else None
        # set ownership via chmod not enough; use os.chown if root. Instead store uid in a sidecar? collector uses os.stat uid.
        # We'll monkeypatch by running as current user; set uid param to current euid.
        return base

    def test_collect_emits_roles_and_scan_complete(self):
        uid = os.geteuid()
        self._make_proc(101, uid, "ffmpeg", ["ffmpeg", "-f", "x11grab", "-i", ":0", "out"], 1000)
        self._make_proc(102, uid, "pulseaudio", ["pulseaudio", "--daemonize"], 2000)
        self._make_proc(103, uid, "python3", ["python3", "soren-shared-overlay.py"], 3000)
        sec = self.mod._collect_shared_runtime_pids(proc_root=self.proc, uid=uid)
        self.assertTrue(sec["scan_complete"])
        self.assertEqual(sec["roles"]["encoder"]["count"], 1)
        self.assertEqual(sec["roles"]["encoder"]["pids"], [101])
        self.assertEqual(sec["roles"]["audio"]["pids"], [102])
        self.assertEqual(sec["roles"]["shared_stream"]["pids"], [103])
        # start_ticks present
        self.assertEqual(sec["roles"]["encoder"]["entries"][0]["start_ticks"], 1000)

    def test_redaction_no_argv(self):
        uid = os.geteuid()
        self._make_proc(201, uid, "ffmpeg", ["ffmpeg", "-f", "x11grab", "-secret", "TOKEN=abc"], 500)
        sec = self.mod._collect_shared_runtime_pids(proc_root=self.proc, uid=uid)
        self.assertEqual(sec["roles"]["encoder"]["pids"], [201])
        text = str(sec)
        self.assertNotIn("TOKEN", text)
        self.assertNotIn("secret", text.lower().replace("shared", ""))  # loose
        # ensure no cmdline/argv keys
        for role in sec["roles"].values():
            for entry in role["entries"]:
                self.assertNotIn("argv", entry)
                self.assertNotIn("cmdline", entry)

    def receipt(self):
        return {'schema_version': 2, 'scan_complete': True,
                'boot_id': '12345678-1234-1234-1234-123456789abc',
                'roles': {role: {'count': 1, 'pids': [10+i],
                    'entries': [{'pid': 10+i, 'start_ticks': 100, 'started_at': 1001}]}
                    for i, role in enumerate(self.mod.SHARED_RUNTIME_ROLES)}}

    def test_identical_nonempty_and_full_envelope_match(self):
        before = self.receipt()
        self.assertTrue(self.mod.compare_shared_runtime_pids(before, copy.deepcopy(before))['match'])
        self.assertTrue(self.mod.compare_shared_runtime_pids(
            {'shared_runtime_pids': before}, {'shared_runtime_pids': before})['match'])

    def test_role_entries_and_pid_projection_order_are_irrelevant(self):
        before = self.receipt()
        role = before['roles']['encoder']
        role['entries'].append({'pid': 99, 'start_ticks': 102, 'started_at': 1002})
        role.update(count=2, pids=[11, 99])
        after = copy.deepcopy(before)
        after['roles']['encoder']['entries'].reverse()
        self.assertTrue(self.mod.compare_shared_runtime_pids(before, after)['match'])
        after['roles']['encoder']['pids'].reverse()
        self.assertTrue(self.mod.compare_shared_runtime_pids(before, after)['match'])

    def test_different_boot_pid_or_start_identity_fails(self):
        for change in ('boot', 'pid', 'ticks', 'epoch'):
            with self.subTest(change=change):
                before = self.receipt(); after = copy.deepcopy(before)
                entry = after['roles']['encoder']['entries'][0]
                if change == 'boot': after['boot_id'] = '22345678-1234-1234-1234-123456789abc'
                elif change == 'pid':
                    entry['pid'] = 99; after['roles']['encoder']['pids'] = [99]
                elif change == 'ticks': entry['start_ticks'] += 1
                else: entry['started_at'] += 1
                self.assertFalse(self.mod.compare_shared_runtime_pids(before, after)['match'])

    def test_empty_and_inconsistent_or_incompatible_receipts_fail_closed(self):
        changes = [lambda s: s.update(schema_version=1), lambda s: s.update(schema_version=True),
            lambda s: s.update(scan_complete=False), lambda s: s.pop('boot_id'),
            lambda s: s.update(boot_id='SECRET'), lambda s: s.update(extra=1),
            lambda s: s['roles'].pop('audio'), lambda s: s['roles'].update(extra={}),
            lambda s: s['roles']['audio'].update(count=0, pids=[], entries=[]),
            lambda s: s['roles']['audio'].update(count=2),
            lambda s: s['roles']['audio'].update(pids=[99]),
            lambda s: s['roles']['audio']['entries'][0].update(pid=True),
            lambda s: s['roles']['audio']['entries'][0].update(start_ticks=-1),
            lambda s: s['roles']['audio']['entries'][0].update(started_at=True),
            lambda s: s['roles']['audio']['entries'][0].update(start_ticks=2**64),
            lambda s: s['roles']['audio']['entries'][0].update(secret='PRIVATE'),
            lambda s: s['roles'].update(audio=copy.deepcopy(s['roles']['encoder'])),
            lambda s: s.update(roles={r: {'count': 0, 'pids': [], 'entries': []}
                                     for r in self.mod.SHARED_RUNTIME_ROLES})]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = self.receipt(); change(value)
                self.assertFalse(self.mod.compare_shared_runtime_pids(value, value)['match'])
        self.assertFalse(self.mod.compare_shared_runtime_pids({}, {})['match'])
        self.assertFalse(self.mod.compare_shared_runtime_pids({'status': 'output_omitted'}, {})['match'])

    def test_missing_boot_identity_is_partial(self):
        (self.proc / 'sys/kernel/random/boot_id').unlink()
        self.assertFalse(self.mod._collect_shared_runtime_pids(self.proc)['scan_complete'])

    def test_production_shaped_proc_stat_epoch(self):
        self._make_proc(101, os.geteuid(), 'ffmpeg', ['ffmpeg'], 1000)
        ticks, epoch = self.mod._proc_start_ticks(self.proc, 101)
        self.assertEqual(ticks, 1000)
        self.assertEqual(epoch, 1000 + int(1000 / os.sysconf('SC_CLK_TCK')))

    def test_truncated_global_scan_is_never_complete(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        # All entries belong to another UID; only truncation may make this partial.
        for count in (4096, 4097):
            names = [str(i+2) for i in range(count)]
            with patch.object(self.mod.os, 'listdir', return_value=names), \
                 patch.object(self.mod.os, 'stat', return_value=SimpleNamespace(st_uid=os.geteuid()+1)):
                self.assertEqual(self.mod._collect_shared_runtime_pids(self.proc)['scan_complete'], count == 4096)


if __name__ == '__main__':
    unittest.main()
