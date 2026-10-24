"""Contract regression tests for the docich#2578 host metrics ring sampler.

These run under ``unittest discover -s ops/vm_actions/tests`` (the VM operations
CI step) and through pytest. Every source is a synthetic ``proc_root`` fixture
and unit probing is injected, so no assertion depends on the host's real
``/proc``, ``/sys`` or ``systemctl``.
"""

import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "ops" / "vm_actions" / "host_metrics.py"

EXPECTED_KEYS = {
    "boot", "cpu", "disk_ms", "disk_sectors", "load", "mem_kb", "psi",
    "t", "units", "uptime_s", "v",
}
MEM_KEYS = {"available", "dirty", "swap_free", "swap_total", "total",
            "writeback"}
UNIT_STATES = ("active", "active", "failed", "inactive", "active", "inactive")

BOOT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890\n"
LOADAVG = "0.42 0.50 0.60 1/234 5678\n"
STAT = (
    "cpu  100 0 50 200 10 0 0 0 0 0\n"
    "cpu0 50 0 25 100 5 0 0 0 0 0\n"
    "intr 1 2 3\n"
)
MEMINFO = (
    "MemTotal:       444 kB\n"
    "MemAvailable:   111 kB\n"
    "SwapTotal:      333 kB\n"
    "SwapFree:       222 kB\n"
    "Dirty:            1 kB\n"
    "Writeback:        0 kB\n"
    "HugePages_Total:  0\n"
)
UPTIME = "123456.78 890.12\n"
PRESSURE = {
    "cpu": "some avg10=1.00 avg60=0.20 avg300=0.10 total=100\n"
           "full avg10=2.00 avg60=0.30 avg300=0.10 total=200\n",
    "io": "some avg10=3.00 avg60=0.20 avg300=0.10 total=100\n"
          "full avg10=4.00 avg60=0.30 avg300=0.10 total=200\n",
    "memory": "some avg10=5.00 avg60=0.20 avg300=0.10 total=100\n"
              "full avg10=6.00 avg60=0.30 avg300=0.10 total=200\n",
}
DISKSTATS = (
    "   8       1 sda1 1000 0 1111 50 2000 0 2222 60 0 123456 78901 0 0 0 0 0\n"
)
MOUNTINFO = (
    "36 25 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
    "36 26 0:99 / /proc rw - proc proc rw\n"
)


def load_host_metrics():
    spec = importlib.util.spec_from_file_location("host_metrics",
                                                  str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hm = load_host_metrics()


def make_record(t, boot="a1b2c3d4e5f6"):
    """A contract-shaped record (same field sizes as a production sample)."""
    return {
        "v": 1,
        "t": t,
        "boot": boot,
        "uptime_s": 123456,
        "load": [0.42, 0.5, 0.6],
        "cpu": [360, 200, 10],
        "mem_kb": {"available": 111, "dirty": 1, "swap_free": 222,
                   "swap_total": 333, "total": 444, "writeback": 0},
        "psi": {"cpu": [1.0, 2.0], "io": [3.0, 4.0], "mem": [5.0, 6.0]},
        "disk_sectors": [1111, 2222],
        "disk_ms": [123456, 78901],
        "units": 63,
    }


class RecordingProbe:
    """Fixed-argv stand-in for ``systemctl is-active`` (never a real call)."""

    def __init__(self, answers=UNIT_STATES):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        return self.answers[len(self.calls) - 1]


def build_fixture(root, skip=()):
    """Write a synthetic proc/sys tree; ``skip`` names sources to omit."""
    proc = root / "proc"
    sysfs = root / "sys"
    files = {
        "loadavg": (proc / "loadavg", LOADAVG),
        "stat": (proc / "stat", STAT),
        "meminfo": (proc / "meminfo", MEMINFO),
        "uptime": (proc / "uptime", UPTIME),
        "diskstats": (proc / "diskstats", DISKSTATS),
        "mountinfo": (proc / "self" / "mountinfo", MOUNTINFO),
        "boot_id": (sysfs / "kernel" / "random" / "boot_id", BOOT_ID),
    }
    for name, (path, text) in files.items():
        if name in skip:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if "pressure" not in skip:
        for signal, text in PRESSURE.items():
            path = proc / "pressure" / signal
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if "device_link" not in skip:
        link = sysfs / "dev" / "block" / "8:1"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("../devices/platform/sda/sda1")
    return proc, sysfs


class SampleContractTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="host-metrics-sample-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc, self.sysfs = build_fixture(self.root)
        self.probe = RecordingProbe()

    def take_sample(self, proc=None, sysfs=None, probe=None, **kwargs):
        return hm.sample(
            now=1761000000,
            proc_root=self.proc if proc is None else proc,
            sys_root=self.sysfs if sysfs is None else sysfs,
            unit_probe=self.probe if probe is None else probe,
            **kwargs,
        )

    def test_record_matches_the_contract_example(self):
        self.assertEqual(self.take_sample(), {
            "v": 1,
            "t": 1761000000,
            "boot": "a1b2c3d4e5f6",
            "uptime_s": 123456,
            "load": [0.42, 0.5, 0.6],
            "cpu": [360, 200, 10],
            "mem_kb": {"available": 111, "dirty": 1, "swap_free": 222,
                       "swap_total": 333, "total": 444, "writeback": 0},
            "psi": {"cpu": [1.0, 2.0], "io": [3.0, 4.0], "mem": [5.0, 6.0]},
            "disk_sectors": [1111, 2222],
            "disk_ms": [123456, 78901],
            "units": hm.encode_units(UNIT_STATES),
        })

    def test_record_keys_are_exactly_the_contract_keys(self):
        record = self.take_sample()
        self.assertEqual(set(record), EXPECTED_KEYS)
        self.assertEqual(set(record["mem_kb"]), MEM_KEYS)
        self.assertEqual(set(record["psi"]), {"cpu", "io", "mem"})
        self.assertIsInstance(record["v"], int)
        self.assertIsInstance(record["t"], int)
        self.assertIsInstance(record["units"], int)
        self.assertIsInstance(record["uptime_s"], int)
        self.assertTrue(all(isinstance(v, int) for v in record["cpu"]))
        self.assertTrue(all(isinstance(v, float) for v in record["load"]))

    def test_record_carries_no_paths_or_free_text(self):
        text = json.dumps(self.take_sample(), sort_keys=True)
        self.assertNotIn("/", text)
        self.assertNotIn(self.root.name, text)
        self.assertRegex(json.loads(text)["boot"], re.compile(r"^[0-9a-f]{12}$"))

    def test_missing_sources_become_null_instead_of_failing(self):
        proc, sysfs = build_fixture(
            self.root / "empty",
            skip={"loadavg", "stat", "meminfo", "uptime", "diskstats",
                  "mountinfo", "pressure", "boot_id", "device_link"},
        )
        record = self.take_sample(proc=proc, sysfs=sysfs,
                                  probe=RecordingProbe(["", "", "", "", "", ""]))
        self.assertIsNone(record["boot"])
        self.assertIsNone(record["uptime_s"])
        self.assertIsNone(record["load"])
        self.assertIsNone(record["cpu"])
        self.assertEqual(record["mem_kb"], {name: None for name in MEM_KEYS})
        self.assertEqual(record["psi"], {
            "cpu": [None, None], "io": [None, None], "mem": [None, None]})
        self.assertIsNone(record["disk_sectors"])
        self.assertIsNone(record["disk_ms"])
        self.assertEqual(set(record), EXPECTED_KEYS)
        # Unprobeable units are "unknown", not "inactive": with no systemd the
        # reader must not report six stopped units as a host problem.
        self.assertEqual(record["units"],
                         sum(hm.UNIT_STATE_UNKNOWN << (2 * index)
                             for index in range(len(hm.UNIT_ORDER))))

    def test_kernel_without_psi_yields_null_pairs(self):
        proc, _ = build_fixture(self.root / "nopsi", skip={"pressure"})
        record = self.take_sample(proc=proc)
        self.assertEqual(record["psi"], {
            "cpu": [None, None], "io": [None, None], "mem": [None, None]})
        self.assertEqual(record["load"], [0.42, 0.5, 0.6])

    def test_missing_meminfo_keys_stay_null(self):
        proc, _ = build_fixture(self.root / "mem")
        (proc / "meminfo").write_text(
            "MemTotal: 444 kB\nDirty: 1 kB\n", encoding="utf-8")
        record = self.take_sample(proc=proc)
        self.assertEqual(record["mem_kb"], {
            "total": 444, "available": None, "swap_total": None,
            "swap_free": None, "dirty": 1, "writeback": None})

    def test_unresolvable_root_device_yields_null_disk_counters(self):
        proc, sysfs = build_fixture(self.root / "nodev")
        (proc / "self" / "mountinfo").write_text(
            MOUNTINFO.replace("8:1", "8:2"), encoding="utf-8")
        record = self.take_sample(proc=proc, sysfs=sysfs)
        self.assertIsNone(record["disk_sectors"])
        self.assertIsNone(record["disk_ms"])
        self.assertEqual(record["load"], [0.42, 0.5, 0.6])

    def test_injected_disk_device_is_used_instead_of_resolution(self):
        proc, sysfs = build_fixture(self.root / "inject",
                                    skip={"mountinfo", "device_link"})
        record = self.take_sample(proc=proc, sysfs=sysfs,
                                  disk_device="sda1")
        self.assertEqual(record["disk_sectors"], [1111, 2222])
        self.assertEqual(record["disk_ms"], [123456, 78901])

    def test_unit_probe_uses_only_the_fixed_argv(self):
        with mock.patch.object(hm.subprocess, "run",
                               side_effect=AssertionError("real systemctl")):
            record = self.take_sample()
        self.assertEqual(record["units"], hm.encode_units(UNIT_STATES))
        self.assertEqual(self.probe.calls, [
            ("systemctl", "--user", "is-active",
             "docich-corner-rotation.timer"),
            ("systemctl", "--user", "is-active",
             "docich-corner-rotation.service"),
            ("systemctl", "--user", "is-active", "docich-webui.service"),
            ("systemctl", "--user", "is-active",
             "docich-paper-corner.timer"),
            ("systemctl", "is-active", "soren-runtime.service"),
            ("systemctl", "is-active", "docich-audio-priority.service"),
        ])

    def test_unexpected_probe_states_never_report_an_active_unit(self):
        probe = RecordingProbe(
            ["failed", "", "unknown", "activating", "active", "deactivating"])
        record = self.take_sample(probe=probe)
        self.assertEqual(hm.decode_units(record["units"]), [
            "failed", "unknown", "unknown", "unknown", "active",
            "unknown",
        ])

    def test_unknown_unit_state_is_not_reported_as_inactive(self):
        # ``activating``/``reloading``/``unknown`` and a scope without the unit
        # are distinct from an installed-but-stopped unit; the reader only
        # treats inactive/failed as "down".
        probe = RecordingProbe(
            ["activating", "reloading", "unknown", "inactive", "active",
             "failed"])
        record = self.take_sample(probe=probe)
        self.assertEqual(hm.decode_units(record["units"]), [
            "unknown", "unknown", "unknown", "inactive", "active", "failed",
        ])

    def test_units_codec_round_trips_and_rejects_misalignment(self):
        mask = hm.encode_units(["active"] * len(hm.UNIT_ORDER))
        self.assertEqual(hm.decode_units(mask), ["active"] * len(hm.UNIT_ORDER))
        self.assertEqual(hm.decode_units(0b11), ["unknown", "inactive",
                                                 "inactive", "inactive",
                                                 "inactive", "inactive"])
        with self.assertRaises(ValueError):
            hm.encode_units(["active"])
        with self.assertRaises(ValueError):
            hm.encode_units(["bogus"] * len(hm.UNIT_ORDER))
        with self.assertRaises(ValueError):
            hm.decode_units("63")


class ImplausibleCounterTests(unittest.TestCase):
    """A /proc token no healthy kernel produces must read as null, not poison.

    The first production deploy of #2578 failed on every tick with the
    sampler's only clean exit 1: the encoded record did not fit the ring's
    4096-byte line. That is only possible when a numeric token became a huge
    integer, so every numeric parse now has a ceiling and an implausible
    source reads as "not reported" (null), exactly like a missing one.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="host-metrics-cap-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc, self.sysfs = build_fixture(self.root)
        self.probe = RecordingProbe()

    def take_sample(self):
        return hm.sample(now=1761000000, proc_root=self.proc,
                         sys_root=self.sysfs, unit_probe=self.probe)

    def rewrite(self, name, text):
        path = {"meminfo": self.proc / "meminfo",
                "stat": self.proc / "stat",
                "uptime": self.proc / "uptime",
                "diskstats": self.proc / "diskstats",
                "loadavg": self.proc / "loadavg",
                "io": self.proc / "pressure" / "io"}[name]
        path.write_text(text, encoding="utf-8")

    def test_a_gigantic_meminfo_token_reads_as_null(self):
        self.rewrite("meminfo", "MemTotal: " + "9" * 4000 + " kB\n")
        record = self.take_sample()
        self.assertIsNone(record["mem_kb"]["total"])

    def test_a_gigantic_stat_token_reads_as_null(self):
        self.rewrite("stat", "cpu " + " ".join(["7" * 4000] * 4) +
                     " 100 100 100\n")
        self.assertIsNone(record_cpu := self.take_sample()["cpu"])

    def test_a_gigantic_diskstats_token_reads_as_null(self):
        self.rewrite("diskstats", "  8:1 sda1 " + " ".join(["3" * 4000] * 11)
                     + "\n")
        record = self.take_sample()
        self.assertIsNone(record["disk_sectors"])
        self.assertIsNone(record["disk_ms"])

    def test_an_absurd_uptime_reads_as_null(self):
        self.rewrite("uptime", "1e300 1e300\n")
        record = self.take_sample()
        self.assertIsNone(record["uptime_s"])

    def test_an_absurd_load_reads_as_null(self):
        self.rewrite("loadavg", "1e300 1e300 1e300 1/1 1\n")
        record = self.take_sample()
        self.assertIsNone(record["load"])

    def test_an_absurd_psi_value_reads_as_null(self):
        self.rewrite("io", "some avg10=1e300 avg60=0.00 avg300=0.00 total=0\n"
                     "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        record = self.take_sample()
        self.assertEqual(record["psi"]["io"], [None, 0.0])

    def test_a_record_at_every_ceiling_still_fits_the_line_bound(self):
        # The caps are the guarantee that the refusal path can never trigger
        # from real (or hostile) /proc content: with every counter at its own
        # ceiling the encoded line still fits MAX_LINE_BYTES.
        capped = {
            "v": hm.SCHEMA_VERSION, "t": 1761000000,
            "boot": "a1b2c3d4e5f6", "uptime_s": hm.MAX_UPTIME_SEC,
            "load": [hm.MAX_LOAD, hm.MAX_LOAD, hm.MAX_LOAD],
            "cpu": [hm.MAX_COUNTER] * 3,
            "mem_kb": {name: hm.MAX_COUNTER for name in
                       ("available", "dirty", "swap_free", "swap_total",
                        "total", "writeback")},
            "psi": {key: [hm.MAX_LOAD, hm.MAX_LOAD]
                    for key in ("cpu", "io", "mem")},
            "disk_sectors": [hm.MAX_COUNTER, hm.MAX_COUNTER],
            "disk_ms": [hm.MAX_COUNTER, hm.MAX_COUNTER],
            "units": 4095,
        }
        self.assertLessEqual(len(hm._encode_record(capped)), hm.MAX_LINE_BYTES)

    def test_realistic_counters_are_unaffected_by_the_caps(self):
        record = self.take_sample()
        self.assertEqual(record["mem_kb"]["total"], 444)
        self.assertIsNotNone(record["cpu"])
        self.assertIsNotNone(record["disk_sectors"])
        self.assertIsNotNone(record["uptime_s"])


class RefusalMessageTests(unittest.TestCase):

    def test_the_over_long_refusal_reports_the_record_size(self):
        temporary = tempfile.TemporaryDirectory(prefix="host-metrics-refuse-")
        self.addCleanup(temporary.cleanup)
        state = Path(temporary.name) / "host-metrics"
        oversized = hm.sample(now=1761000000, unit_probe=RecordingProbe())
        oversized["junk"] = "x" * (hm.MAX_LINE_BYTES + 1)
        stderr = io.StringIO()
        with mock.patch.object(hm, "sample", return_value=oversized), \
                mock.patch.object(hm.sys, "stderr", stderr):
            code = hm.main(["--state-dir", str(state)])
        self.assertEqual(code, 1)
        self.assertIn("record_bytes=", stderr.getvalue())
        self.assertIn("cannot write host metrics ring", stderr.getvalue())
        self.assertFalse(state.exists())


class ConstantContractTests(unittest.TestCase):

    def test_bounds_match_the_published_contract(self):
        self.assertEqual(hm.SCHEMA_VERSION, 1)
        self.assertEqual(hm.INTERVAL_SEC, 60)
        self.assertEqual(hm.MAX_POINTS, 240)
        self.assertEqual(hm.MAX_BYTES, 256 * 1024)
        self.assertEqual(hm.COMPACT_KEEP, 200)
        self.assertEqual(hm.MAX_LINE_BYTES, 4096)

    def test_write_frequency_fits_the_byte_budget_with_headroom(self):
        self.assertEqual(hm.MAX_POINTS * hm.INTERVAL_SEC, 4 * 60 * 60)
        sample_bytes = len(hm._encode_record(make_record(0)))
        self.assertLess(sample_bytes * hm.MAX_POINTS, hm.MAX_BYTES // 2)

    def test_state_root_is_the_persistent_home_path(self):
        self.assertEqual(
            hm.state_root(home="/home/ubuntu"),
            Path("/home/ubuntu/.local/state/docich/host-metrics"))

    def test_default_state_root_ignores_a_scrubbed_home_environment(self):
        # Production exec may replace HOME; the ring path must stay the passwd
        # home that the deploy hook resolves with getent.
        expected = (Path(hm.pwd.getpwuid(os.getuid()).pw_dir)
                    / ".local" / "state" / "docich" / "host-metrics")
        with mock.patch.dict(os.environ,
                             {"HOME": "/nonexistent-scrubbed-home"}):
            self.assertEqual(hm.state_root(), expected)

    def test_state_root_falls_back_only_when_the_user_entry_is_missing(self):
        with mock.patch.object(hm.pwd, "getpwuid", side_effect=KeyError("uid")), \
                mock.patch.object(hm.Path, "home",
                                  return_value=Path("/fallback-home")):
            self.assertEqual(
                hm.state_root(),
                Path("/fallback-home/.local/state/docich/host-metrics"))


class RingBoundsTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="host-metrics-ring-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "host-metrics"

    def record_path(self):
        return self.state / "ring.jsonl"

    def read_records(self):
        return [json.loads(line)
                for line in self.record_path().read_text(
                    encoding="utf-8").splitlines()]

    def test_state_dir_is_0700_and_ring_is_0600(self):
        hm.append_sample(self.state, make_record(1))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.record_path().stat().st_mode & 0o777, 0o600)

    def test_relative_state_dir_is_rejected(self):
        with self.assertRaises(hm.HostMetricsError):
            hm.append_sample("relative-host-metrics", make_record(1))
        self.assertFalse((Path.cwd() / "relative-host-metrics").exists())

    def test_symlinked_state_dir_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real)
        with self.assertRaises(hm.HostMetricsError):
            hm.append_sample(link, make_record(1))
        self.assertEqual(list(real.iterdir()), [])

    def test_symlinked_ring_file_is_refused(self):
        self.state.mkdir()
        outside = self.root / "outside.jsonl"
        outside.write_bytes(b"")
        self.record_path().symlink_to(outside)
        with self.assertRaises(OSError):
            hm.append_sample(self.state, make_record(1))
        self.assertEqual(outside.read_bytes(), b"")

    def test_300_samples_stay_inside_both_ring_bounds(self):
        for t in range(300):
            hm.append_sample(self.state, make_record(t))
        records = self.read_records()
        timestamps = [record["t"] for record in records]
        self.assertLessEqual(len(records), hm.MAX_POINTS)
        self.assertLessEqual(self.record_path().stat().st_size, hm.MAX_BYTES)
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(timestamps[-1], 299)
        self.assertNotIn(0, timestamps)
        # The newest COMPACT_KEEP points always survive a compaction.
        self.assertTrue(set(range(100, 300)) <= set(timestamps))

    def test_compaction_keeps_the_newest_compact_keep_records(self):
        self.state.mkdir()
        prefilled = "".join(
            json.dumps(make_record(t), sort_keys=True, separators=(",", ":"))
            + "\n"
            for t in range(500)
        )
        self.record_path().write_text(prefilled, encoding="utf-8")
        hm.append_sample(self.state, make_record(500))
        timestamps = [record["t"] for record in self.read_records()]
        self.assertEqual(len(timestamps), hm.COMPACT_KEEP)
        self.assertEqual(timestamps[0], 301)
        self.assertEqual(timestamps[-1], 500)

    def test_over_long_line_is_skipped_never_truncated(self):
        hm.append_sample(self.state, make_record(1))
        before = self.record_path().read_bytes()
        oversized = make_record(2)
        oversized["junk"] = "x" * (hm.MAX_LINE_BYTES + 1)
        self.assertEqual(hm.append_sample(self.state, oversized), 0)
        self.assertEqual(self.record_path().read_bytes(), before)

    def test_compaction_keeps_the_byte_bound_with_long_valid_lines(self):
        # 200 kept lines of ~3 KiB would be ~600 KiB, so the byte bound has to be
        # enforced after compaction too, not only by the point count.
        for t in range(120):
            record = make_record(t)
            record["pad"] = "x" * 3000
            written = hm.append_sample(self.state, record)
            self.assertGreater(written, 0)
            self.assertLessEqual(written, hm.MAX_LINE_BYTES)
            self.assertLessEqual(
                self.record_path().stat().st_size, hm.MAX_BYTES,
                "ring exceeded MAX_BYTES after sample t=%d" % t)
        records = self.read_records()
        self.assertLessEqual(len(records), hm.MAX_POINTS)
        self.assertLessEqual(len(records), hm.COMPACT_KEEP)
        self.assertEqual(records[-1]["t"], 119)

    def test_an_oversized_ring_is_compacted_from_the_newest_tail(self):
        # A ring larger than the read cap must still keep its newest points, not
        # the body of the prefix the window happens to see.
        self.state.mkdir()
        prefilled = "".join(
            json.dumps(make_record(t), sort_keys=True, separators=(",", ":"))
            + "\n"
            for t in range(3000)
        )
        self.record_path().write_text(prefilled, encoding="utf-8")
        self.assertGreater(self.record_path().stat().st_size,
                           hm.MAX_BYTES + hm.MAX_LINE_BYTES)
        hm.append_sample(self.state, make_record(3000))
        records = self.read_records()
        self.assertEqual(len(records), hm.COMPACT_KEEP)
        self.assertEqual(records[-1]["t"], 3000)
        self.assertLessEqual(self.record_path().stat().st_size, hm.MAX_BYTES)

    def test_append_syncs_the_ring_and_compaction_syncs_each_new_file(self):
        # A frozen guest may never flush the page cache, so the newest point and
        # the rewritten ring have to be fsynced explicitly.
        with mock.patch.object(hm.os, "fsync", wraps=os.fsync) as synced:
            hm.append_sample(self.state, make_record(1))
        # the first sample: ring content + the directory entry that creates it
        self.assertEqual(synced.call_count, 2)
        compacted = self.root / "compacted"
        compacted.mkdir()
        prefilled = "".join(
            json.dumps(make_record(t), sort_keys=True, separators=(",", ":"))
            + "\n"
            for t in range(300)
        )
        (compacted / "ring.jsonl").write_text(prefilled, encoding="utf-8")
        with mock.patch.object(hm.os, "fsync", wraps=os.fsync) as synced:
            hm.append_sample(compacted, make_record(300))
        # append + temp file before os.replace + directory after it
        self.assertEqual(synced.call_count, 3)

    def test_torn_tail_line_is_repaired_before_the_next_append(self):
        # A power cut mid-append can leave the last line without a newline. The
        # next sample (the first one after the reboot) must still land on its
        # own line, or it would be glued to the torn record and lost.
        self.state.mkdir()
        self.record_path().write_bytes(b'{"v":1,"t":17')
        self.assertGreater(hm.append_sample(self.state, make_record(120)), 0)
        lines = self.record_path().read_bytes().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["t"], 120)
        self.assertEqual(hm._parse_record(lines[0]), None)

    def test_each_sample_takes_exactly_one_append_open(self):
        opens = []
        real_open = os.open

        def recording_open(path, flags, *args, **kwargs):
            if path == hm.RING_NAME:
                opens.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(hm.os, "open", side_effect=recording_open):
            hm.append_sample(self.state, make_record(1))
        appends = [flags for flags in opens if flags & os.O_APPEND]
        self.assertEqual(len(appends), 1)
        self.assertTrue(appends[0] & os.O_NOFOLLOW)
        self.assertTrue(appends[0] & os.O_CREAT)
        # No second append open can be added without failing this invariant.
        self.assertEqual(MODULE_PATH.read_text(encoding="utf-8").count(
            "os.O_APPEND"), 1)

    def test_no_rewrite_happens_until_a_ring_bound_is_exceeded(self):
        with mock.patch.object(hm, "_open_ring",
                               wraps=hm._open_ring) as opened, \
                mock.patch.object(hm, "_replace_ring",
                                  wraps=hm._replace_ring) as replaced:
            for t in range(100):
                hm.append_sample(self.state, make_record(t))
            self.assertEqual(opened.call_count, 100)
            self.assertEqual(replaced.call_count, 0)
            for t in range(100, 300):
                hm.append_sample(self.state, make_record(t))
        self.assertEqual(opened.call_count, 300)
        # 241 lines compacts to 200; the ring crosses the bound again at 282.
        self.assertEqual(replaced.call_count, 2)


class RebootCrossingTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="host-metrics-reboot-")
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "host-metrics"

    def read_records(self):
        return [json.loads(line)
                for line in (self.state / "ring.jsonl").read_text(
                    encoding="utf-8").splitlines()]

    def test_samples_from_two_boots_are_retained_and_ordered(self):
        for t in range(100, 103):
            hm.append_sample(self.state, make_record(t, boot="aaaa00000000"))
        for t in range(103, 106):
            hm.append_sample(self.state, make_record(t, boot="bbbb11111111"))
        records = self.read_records()
        self.assertEqual([record["boot"] for record in records], [
            "aaaa00000000", "aaaa00000000", "aaaa00000000",
            "bbbb11111111", "bbbb11111111", "bbbb11111111",
        ])
        self.assertEqual([record["t"] for record in records],
                         [100, 101, 102, 103, 104, 105])
        self.assertEqual(len({record["boot"] for record in records}), 2)

    def test_a_full_ring_still_spans_the_reboot_after_compaction(self):
        for t in range(240):
            hm.append_sample(self.state, make_record(t, boot="aaaa00000000"))
        for t in range(240, 280):
            hm.append_sample(self.state, make_record(t, boot="bbbb11111111"))
        records = self.read_records()
        self.assertLessEqual(len(records), hm.MAX_POINTS)
        self.assertEqual([record["boot"] for record in records][-40:],
                         ["bbbb11111111"] * 40)
        self.assertIn("aaaa00000000",
                      {record["boot"] for record in records})


class MainArgumentTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="host-metrics-main-")
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "host-metrics"

    def test_unsupported_arguments_are_rejected_before_any_sample(self):
        for argv in (["x"], ["--exec", "true"], ["--state-dir"],
                     ["--state-dir", "a", "--state-dir", "b"],
                     ["--print", "--print"]):
            with self.subTest(argv=argv):
                self.assertNotEqual(hm.main(argv), 0)
        self.assertFalse(self.state.exists())

    def test_default_production_argv_writes_one_sample_and_prints_on_request(self):
        record = make_record(1761000000)
        with mock.patch.object(hm, "sample", return_value=record):
            printed = io.StringIO()
            with redirect_stdout(printed):
                status = hm.main(["--state-dir", str(self.state), "--print"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(printed.getvalue()), record)
        lines = (self.state / "ring.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), record)


if __name__ == "__main__":
    unittest.main()
