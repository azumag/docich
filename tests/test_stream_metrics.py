"""Host telemetry sampling is bounded, monotonic-throttled and read-only."""
import ast
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_metrics as sm  # noqa: E402


class ProcRoot:
    def __init__(self, tmp):
        self.root = Path(tmp)

    def write(self, cpu, mem_total_kb=1000, mem_avail_kb=250):
        (self.root / "stat").write_text(f"cpu  {cpu}\ncpu0 1 1 1 1\n", encoding="ascii")
        (self.root / "meminfo").write_text(
            f"MemTotal:       {mem_total_kb} kB\nMemFree:  1 kB\nMemAvailable:   {mem_avail_kb} kB\n", encoding="ascii")


class SamplerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-metrics-")
        self.addCleanup(self.tmp.cleanup)
        self.proc = ProcRoot(self.tmp.name)

    def test_cpu_is_a_delta_between_samples_and_memory_is_exact(self):
        sampler = sm.SystemMetricsSampler()
        self.proc.write("100 0 100 800 0 0 0 0")  # busy 200 / total 1000
        first = sampler.sample(now_epoch=1, now_monotonic=0.0, proc_root=self.proc.root, platform="linux")
        self.assertIsNone(first["cpu_percent"], "no delta on the first sample")
        self.assertEqual((first["memory_used_bytes"], first["memory_total_bytes"], first["memory_percent"]),
                         (750 * 1024, 1000 * 1024, 75.0))
        self.proc.write("200 0 200 1600 0 0 0 0")  # busy 400 / total 2000 -> +200 / +1000
        second = sampler.sample(now_epoch=7, now_monotonic=6.0, proc_root=self.proc.root, platform="linux")
        self.assertEqual(second["cpu_percent"], 20.0)
        self.assertEqual([p["ts"] for p in second["history"]], [1, 7])

    def test_samples_inside_the_interval_are_not_retaken(self):
        sampler = sm.SystemMetricsSampler(interval_sec=5.0)
        self.proc.write("1 0 1 8 0 0 0 0")
        one = sampler.sample(now_epoch=1, now_monotonic=10.0, proc_root=self.proc.root, platform="linux")
        again = sampler.sample(now_epoch=2, now_monotonic=12.0, proc_root=self.proc.root, platform="linux")
        self.assertIs(one, again)
        self.assertEqual(len(sampler.history), 1)

    def test_history_is_bounded(self):
        sampler = sm.SystemMetricsSampler(interval_sec=0)
        self.proc.write("1 0 1 8 0 0 0 0")
        for n in range(sm.SYSTEM_METRICS_HISTORY_LIMIT + 10):
            sampler.sample(now_epoch=n, now_monotonic=float(n), proc_root=self.proc.root, platform="linux")
        self.assertEqual(len(sampler.history), sm.SYSTEM_METRICS_HISTORY_LIMIT)
        self.assertEqual(sampler.history[-1]["ts"], sm.SYSTEM_METRICS_HISTORY_LIMIT + 9)

    def test_non_linux_and_unreadable_proc_report_none_not_errors(self):
        sampler = sm.SystemMetricsSampler(interval_sec=0)
        mac = sampler.sample(now_epoch=1, now_monotonic=0.0, proc_root=self.proc.root, platform="darwin")
        self.assertEqual((mac["cpu_percent"], mac["memory_percent"], mac["disk_percent"]), (None, None, None))
        broken = sampler.sample(now_epoch=2, now_monotonic=1.0, proc_root=Path(self.tmp.name) / "none", platform="linux")
        self.assertEqual((broken["cpu_percent"], broken["memory_percent"]), (None, None))

    def test_malformed_proc_files_are_rejected(self):
        (self.proc.root / "stat").write_text("cpu 1 2\n", encoding="ascii")
        self.assertIsNone(sm.SystemMetricsSampler._read_cpu(self.proc.root / "stat"))
        (self.proc.root / "stat").write_text("cpu 1 2 3 -4 5\n", encoding="ascii")
        self.assertIsNone(sm.SystemMetricsSampler._read_cpu(self.proc.root / "stat"))
        self.proc.write("1 0 1 8", mem_total_kb=100, mem_avail_kb=200)
        self.assertIsNone(sm.SystemMetricsSampler._read_memory(self.proc.root / "meminfo"))
        (self.proc.root / "meminfo").write_text("MemTotal: 5 MB\nMemAvailable: 1 MB\n", encoding="ascii")
        self.assertIsNone(sm.SystemMetricsSampler._read_memory(self.proc.root / "meminfo"))

    def test_disk_usage_uses_used_plus_user_available_as_the_denominator(self):
        import os
        from unittest import mock
        info = os.statvfs_result((4096, 4096, 1000, 400, 300, 0, 0, 0, 0, 255))
        with mock.patch("os.statvfs", return_value=info):
            used, total, free, percent = sm.SystemMetricsSampler._read_disk("/")
        self.assertEqual((used, total, free), (600 * 4096, 1000 * 4096, 300 * 4096))
        self.assertEqual(percent, round(100.0 * 600 / 900, 1))
        with mock.patch("os.statvfs", side_effect=OSError):
            self.assertIsNone(sm.SystemMetricsSampler._read_disk("/"))


class PurityTest(unittest.TestCase):
    def test_only_proc_and_statvfs_reads(self):
        tree = ast.parse((ROOT / "src/docich/stream_metrics.py").read_text())
        imported, attrs = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
        self.assertEqual(imported, {"__future__", "os", "pathlib", "sys"})
        self.assertFalse(attrs & {"write_text", "write_bytes", "open", "kill", "Popen", "system", "mkdir", "unlink", "environ"})


if __name__ == "__main__":
    unittest.main()
