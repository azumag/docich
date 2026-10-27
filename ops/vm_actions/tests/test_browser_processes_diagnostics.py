"""Fixed-label Chromium inventory for runtime diagnostics (#970 follow-up).

The section must answer "which browser/view is consuming CPU" without exposing
any raw cmdline, URL, path or PID. These tests use a synthetic /proc tree; no
live runtime, network or browser is touched.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserProcessInventoryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_collector()
        self.temp = tempfile.TemporaryDirectory(prefix="browser-inventory-")
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)
        (self.proc / "uptime").write_text("1000.00 0.00\n", encoding="ascii")

    def _write(self, pid, argv, *, ppid=1, ticks=0, starttime=0, rss_kb=1024):
        base = self.proc / str(pid)
        base.mkdir()
        (base / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
        fields = [
            "S", str(ppid), "0", "0", "0", "0", "0", "0", "0", "0", "0",
            str(ticks), "0", "0", "0", "20", "0", "1", "0",
            str(starttime), "0", "0",
        ]
        (base / "stat").write_text(
            f"{pid} (chrome) " + " ".join(fields) + "\n", encoding="utf-8")
        (base / "status").write_text(
            f"Name:\tchrome\nPPid:\t{ppid}\nVmRSS:\t{rss_kb} kB\n",
            encoding="utf-8")
        return base

    def _set_ticks(self, pid, ticks):
        path = self.proc / str(pid) / "stat"
        text = path.read_text(encoding="utf-8")
        head, _, tail = text.partition(") ")
        fields = tail.split()
        fields[11] = str(ticks)
        fields[12] = "0"
        path.write_text(head + ") " + " ".join(fields) + "\n", encoding="utf-8")

    def _fixture(self):
        # Ancestors (argv is only used in-process for classification).
        self._write(1000, ["node", "shared_overlay.mjs"], ppid=1)
        self._write(1001, ["node", "soviet_local.mjs"], ppid=1)
        self._write(1002, ["python3", "twica_service.py"], ppid=1)
        # Chromium processes: two owned by the shared overlay, one Soren game,
        # one TwiCa renderer, one orphan.
        self._write(2000, ["/x/chrome", "--type=gpu-process"], ppid=1000,
                    ticks=100, starttime=100)
        self._write(2001, ["/x/chrome", "--type=renderer"], ppid=1000,
                    ticks=100, starttime=200)
        self._write(2002, ["/x/chrome"], ppid=1001, ticks=100, starttime=300)
        self._write(2003, ["/x/chrome", "--type=renderer"], ppid=1002,
                    ticks=100, starttime=400)
        self._write(2004, ["/x/chrome", "--type=renderer"], ppid=1,
                    ticks=100, starttime=500)
        # Not counted: crashpad helper and a node audio player with "chrom".
        self._write(2005, ["/x/chrome_crashpad_handler"], ppid=1)
        self._write(2006, ["/usr/bin/node", "chrome_audio_player.mjs"], ppid=1)
        # A malformed stat must be skipped, never crash the scan.
        broken = self._write(2007, ["/x/chrome", "--type=renderer"], ppid=1)
        (broken / "stat").write_text("broken\n", encoding="utf-8")

    def test_owner_type_cpu_and_size_are_aggregated(self):
        self._fixture()
        after = {2000: 150, 2001: 200, 2002: 100, 2003: 125, 2004: 110}

        def fake_sleep(_seconds):
            for pid, ticks in after.items():
                self._set_ticks(pid, ticks)

        clock_values = iter([10.0, 10.5])
        result = self.module._collect_browser_processes(
            self.proc, sample_sec=0.5, sleep=fake_sleep,
            clock=lambda: next(clock_values), clk_tck=100)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scan_complete"], 1)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["by_type"], {"browser": 1, "gpu": 1, "renderer": 3})
        self.assertEqual(result["cpu_pct_total"], 370.0)
        self.assertEqual(result["rss_mb_total"], 5.0)
        self.assertEqual(result["oldest_age_sec"], 999)

        shared = result["by_owner"]["soren-shared-overlay"]
        self.assertEqual(shared["count"], 2)
        self.assertEqual(shared["cpu_pct"], 300.0)
        self.assertEqual(shared["gpu_cpu_pct"], 100.0)
        self.assertEqual(shared["renderer_cpu_pct"], 200.0)
        self.assertEqual(result["by_owner"]["soren-game"]["cpu_pct"], 0.0)
        self.assertEqual(result["by_owner"]["twica"]["cpu_pct"], 50.0)
        self.assertEqual(result["by_owner"]["twica"]["renderer_cpu_pct"], 50.0)
        self.assertEqual(result["by_owner"]["other"]["cpu_pct"], 20.0)

        blob = json.dumps(result, sort_keys=True)
        for needle in ("/x/chrome", "shared_overlay.mjs", "soviet_local.mjs",
                       "twica_service.py", "chrome_audio_player.mjs"):
            self.assertNotIn(needle, blob)

    def test_missing_proc_fails_open_to_unknown(self):
        result = self.module._collect_browser_processes(self.proc / "missing")
        self.assertEqual(result, {"schema_version": 1, "status": "unknown",
                                  "scan_complete": 0})

    def test_ancestor_owner_wins_over_url_in_argv(self):
        # A page URL containing "webui" must not re-label a browser launched by
        # the shared overlay subsystem.
        self._write(1000, ["node", "shared_overlay.mjs"], ppid=1)
        self._write(2008, ["/x/chrome", "--app=http://127.0.0.1:8787/webui"],
                    ppid=1000)
        result = self.module._collect_browser_processes(self.proc, sample_sec=0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["by_owner"]["soren-shared-overlay"]["count"], 1)
        self.assertNotIn("webui", result["by_owner"])

    def test_empty_proc_reports_empty_without_error(self):
        result = self.module._collect_browser_processes(self.proc, sample_sec=0)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["scan_complete"], 1)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["by_type"], {})
        self.assertEqual(result["by_owner"], {})

    def test_section_is_wired_into_the_collector_payload(self):
        self.assertIn('"browser_processes": _collect_browser_processes()',
                      COLLECTOR.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
