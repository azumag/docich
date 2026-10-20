from __future__ import annotations

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
        self.proc = Path(self.tmp.name) / "proc"
        self.proc.mkdir()
        # fake /proc/stat with btime
        (self.proc.parent / "stat").write_text("cpu 0 0 0 0 0 0 0 0 0 0\nbtime 1000\ncpu 0\n", encoding="utf-8")

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
        self._make_proc(201, uid, "ffmpeg", ["ffmpeg", "-secret", "TOKEN=abc"], 500)
        sec = self.mod._collect_shared_runtime_pids(proc_root=self.proc, uid=uid)
        text = str(sec)
        self.assertNotIn("TOKEN", text)
        self.assertNotIn("secret", text.lower().replace("shared", ""))  # loose
        # ensure no cmdline/argv keys
        for role in sec["roles"].values():
            for entry in role["entries"]:
                self.assertNotIn("argv", entry)
                self.assertNotIn("cmdline", entry)

    def test_pid_reuse_detected(self):
        before_sec = {
            "schema_version": 1,
            "scan_complete": True,
            "roles": {
                r: {"count": 0, "pids": [], "entries": []} for r in self.mod.SHARED_RUNTIME_ROLES
            },
        }
        # same pid, different ticks
        before_sec["roles"]["encoder"] = {"count": 1, "pids": [5], "entries": [{"pid": 5, "start_ticks": 100}]}
        after_sec = {
            "schema_version": 1,
            "scan_complete": True,
            "roles": {
                r: {"count": 0, "pids": [], "entries": []} for r in self.mod.SHARED_RUNTIME_ROLES
            },
        }
        after_sec["roles"]["encoder"] = {"count": 1, "pids": [5], "entries": [{"pid": 5, "start_ticks": 200}]}
        res = self.mod.compare_shared_runtime_pids(before_sec, after_sec)
        self.assertFalse(res["match"])
        self.assertIn("encoder", res["mismatched_roles"])

    def test_partial_fails_closed(self):
        sec = {
            "schema_version": 1,
            "scan_complete": False,
            "roles": {r: {"count": 0, "pids": [], "entries": []} for r in self.mod.SHARED_RUNTIME_ROLES},
        }
        res = self.mod.compare_shared_runtime_pids(sec, sec)
        self.assertFalse(res["match"])
        self.assertEqual(res["reason"], "unavailable_or_partial")

    def test_unknown_missing_fails_closed(self):
        res = self.mod.compare_shared_runtime_pids({}, {})
        self.assertFalse(res["match"])

    def test_match_identical(self):
        sec = {
            "schema_version": 1,
            "scan_complete": True,
            "roles": {
                r: {"count": 1, "pids": [7], "entries": [{"pid": 7, "start_ticks": 9}]}
                for r in self.mod.SHARED_RUNTIME_ROLES
            },
        }
        # make all roles have same single entry; compare to identical
        res = self.mod.compare_shared_runtime_pids(sec, sec)
        self.assertTrue(res["match"])
        self.assertEqual(res["reason"], "ok")

    def test_full_diagnostics_dict_accepted(self):
        sec = {
            "schema_version": 1,
            "scan_complete": True,
            "roles": {r: {"count": 0, "pids": [], "entries": []} for r in self.mod.SHARED_RUNTIME_ROLES},
        }
        diag = {"shared_runtime_pids": sec}
        res = self.mod.compare_shared_runtime_pids(diag, diag)
        self.assertTrue(res["match"])


if __name__ == "__main__":
    unittest.main()
