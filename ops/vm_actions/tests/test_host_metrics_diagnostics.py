"""Read-only host metrics ring projection (#2578).

Covers the diagnostics side of ``ops/vm_actions/host_metrics_contract.md``:
fixed shape and bounded size, no path/line/secret leakage, reboot-spanning
deltas, degraded (null) sources, refusal of symlink/oversize rings, the unit
state mask, and the budget rules that keep this forensic evidence alive. Runs
under ``unittest discover -s ops/vm_actions/tests`` (the VM operations CI
step), so the contract stays exercised on every PR that touches ``ops/**``.
"""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
SAMPLER = ROOT / "ops" / "vm_actions" / "host_metrics.py"
RING_RELPATH = Path(".local") / "state" / "docich" / "host-metrics" / "ring.jsonl"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load_module("host_metrics_diagnostics_tested", COLLECTOR)

NOW = 1_800_000_000
BOOT_A = "a1b2c3d4e5f6"
BOOT_B = "0f1e2d3c4b5a"
ALL_ACTIVE = sum(1 << (2 * index) for index in range(len(collector.UNIT_ORDER)))

PROJECTION_KEYS = {
    "status", "retention", "unit_order", "ring_bytes", "points_available",
    "points", "samples_omitted", "oldest_age_sec", "newest_age_sec", "boots",
    "current_boot", "spans_reboot", "runtime", "samples",
}
RUNTIME_KEYS = {
    "service_state", "service_enabled", "timer_state", "timer_enabled",
    "service_result", "service_exec_code", "service_exec_status",
    "service_n_restarts", "journal",
}
JOURNAL_KEYS = {"readable", "lines", "newest_age_sec", "signature",
                "refused_record_bytes"}
FIXED_RUNTIME = {
    "service_state": "failed", "service_enabled": False,
    "timer_state": "active", "timer_enabled": True,
    "service_result": "exit-code", "service_exec_code": 1,
    "service_exec_status": 2, "service_n_restarts": 0,
    "journal": {"readable": True, "lines": 4, "newest_age_sec": 30,
                "signature": "python_traceback", "refused_record_bytes": None},
}
SAMPLE_KEYS = {
    "age_sec", "boot", "t", "uptime_s", "load1", "load5", "load15",
    "cpu_busy_pct", "mem_available_kb", "mem_total_kb", "swap_free_kb",
    "swap_total_kb", "dirty_kb", "writeback_kb", "psi_cpu_some10",
    "psi_cpu_full10", "psi_io_some10", "psi_io_full10", "psi_mem_some10",
    "psi_mem_full10", "disk_read_sectors",
    "disk_write_sectors", "disk_io_ms", "units", "down_units",
}
MEM_DEFAULT = {
    "available": 111, "dirty": 1, "swap_free": 222,
    "swap_total": 333, "total": 444, "writeback": 0,
}


def sample_record(t, boot=BOOT_A, **overrides):
    """One contract v1 record; overrides can null any source (degraded tick)."""
    record = {
        "v": collector.SCHEMA_VERSION,
        "boot": boot,
        "cpu": [1000, 400, 100],
        "disk_ms": [30, 40],
        "disk_sectors": [2048, 4096],
        "load": [0.42, 0.5, 0.6],
        "mem_kb": dict(MEM_DEFAULT),
        "psi": {"cpu": [1.0, 2.0], "io": [3.0, 4.0], "mem": [5.0, 6.0]},
        "t": t,
        "units": ALL_ACTIVE,
        "uptime_s": 1000,
    }
    record.update(overrides)
    return record


def base_payload(**overrides):
    """An envelope with the sections the existing budget cascade touches."""
    payload = {
        "status": "ok",
        "meta": {"generated_at": NOW},
        "ai": {"recent_events": [], "recent_events_omitted": False,
               "anomalous_components": {}},
        "workers": {"details": {}},
        "queues": {},
        "corners": {},
        "nethack_history": {
            "daily": {"records": [], "omitted_records": 0,
                      "output_omitted": False},
            "completed_runs": {"records": [], "omitted_records": 0,
                               "output_omitted": False},
        },
        "nethack_xlog": {},
        "soren91_drop_profile": {"profileStatus": "unavailable", "groups": [],
                                 "games": [], "slowest": []},
    }
    payload.update(overrides)
    return payload


def pad_section(payload, target_bytes, *, key="webui", section="entries",
                entry_bytes=64):
    """Grow an unrelated section until the envelope exceeds ``target_bytes``."""
    payload[key] = {section: []}
    per_entry = len(json.dumps({"detail": "x" * entry_bytes})) + 1
    need = target_bytes - len(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    payload[key][section] = [
        {"detail": "x" * entry_bytes}
        for _ in range(max(0, need // per_entry + 1))
    ]
    return payload


class HostMetricsFixture(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="docich-host-metrics-")
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.ring = self.home / RING_RELPATH
        # The runtime probes read the host's systemd/journal; the projection
        # tests inject the fixed answer so they stay hermetic and fast.
        patcher = mock.patch.object(
            collector, "_collect_host_metrics_runtime",
            return_value=json.loads(json.dumps(FIXED_RUNTIME)))
        self.addCleanup(patcher.stop)
        patcher.start()

    def write_ring(self, records, *, raw_lines=(), trailing=b""):
        self.ring.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(record, sort_keys=True) for record in records]
        lines.extend(raw_lines)
        body = "\n".join(lines)
        if body:
            body += "\n"
        with open(self.ring, "wb") as handle:
            handle.write(body.encode("utf-8"))
            handle.write(trailing)
        return self.ring

    def fresh_records(self, count=8, *, newest_age=18):
        """``count`` consecutive one-minute points of one boot, newest 18s old."""
        return [
            sample_record(
                NOW - newest_age - collector.INTERVAL_SEC * (count - 1 - index),
                cpu=[1000 + 1000 * index, 400 + 500 * index,
                     100 + 200 * index],
                disk_sectors=[2048 + 100 * index, 4096 + 200 * index],
                disk_ms=[30 + 10 * index, 40 + 10 * index],
            )
            for index in range(count)
        ]

    def project(self, records=None):
        if records is not None:
            self.write_ring(records)
        return collector._collect_host_metrics(self.home, NOW)

    def two_boot_records(self, before=3, after=9):
        """``before`` points of BOOT_A, then ``after`` points of BOOT_B."""
        records = [
            sample_record(NOW - 60 * (before + after - 1 - index), boot=BOOT_A)
            for index in range(before)
        ]
        records += [
            sample_record(NOW - 60 * (after - 1 - index), boot=BOOT_B)
            for index in range(after)
        ]
        return records


class FreshRingProjectionTests(HostMetricsFixture):

    def test_fixed_shape_status_and_bounded_size(self):
        records = self.fresh_records()
        for index, record in enumerate(records):
            record["note"] = f"DO-NOT-PUBLISH-{index}"
        output = self.project(records)

        self.assertEqual(output["status"], "ok")
        self.assertEqual(set(output), PROJECTION_KEYS)
        self.assertEqual(output["retention"], {
            "interval_sec": 60, "max_points": 240, "max_bytes": 262144,
        })
        self.assertEqual(output["unit_order"], list(collector.UNIT_ORDER))
        self.assertEqual(output["points_available"], 8)
        self.assertEqual(output["points"], 8)
        self.assertEqual(output["samples_omitted"], 0)
        self.assertEqual(output["boots"], 1)
        self.assertEqual(output["current_boot"], BOOT_A)
        self.assertFalse(output["spans_reboot"])
        self.assertEqual(output["newest_age_sec"], 18)
        self.assertEqual(output["oldest_age_sec"], 18 + 7 * 60)
        self.assertEqual(set(output["runtime"]), RUNTIME_KEYS)
        self.assertEqual(set(output["runtime"]["journal"]), JOURNAL_KEYS)
        self.assertEqual(output["samples_omitted"], 0)
        self.assertEqual(len(output["samples"]), collector.SAMPLES_MAX)
        for sample in output["samples"]:
            self.assertEqual(set(sample), SAMPLE_KEYS)

        newest = output["samples"][-1]
        self.assertEqual(newest["age_sec"], 18)
        self.assertEqual(newest["t"], NOW - 18)
        self.assertEqual(newest["uptime_s"], 1000)
        self.assertEqual(newest["load1"], 0.42)
        self.assertEqual(newest["load5"], 0.5)
        self.assertEqual(newest["load15"], 0.6)
        self.assertEqual(newest["mem_available_kb"], 111)
        self.assertEqual(newest["mem_total_kb"], 444)
        self.assertEqual(newest["swap_free_kb"], 222)
        self.assertEqual(newest["swap_total_kb"], 333)
        self.assertEqual(newest["dirty_kb"], 1)
        self.assertEqual(newest["writeback_kb"], 0)
        self.assertEqual(newest["psi_cpu_some10"], 1.0)
        self.assertEqual(newest["psi_cpu_full10"], 2.0)
        self.assertEqual(newest["psi_io_some10"], 3.0)
        self.assertEqual(newest["psi_io_full10"], 4.0)
        self.assertEqual(newest["psi_mem_some10"], 5.0)
        self.assertEqual(newest["psi_mem_full10"], 6.0)
        self.assertEqual(newest["units"], ALL_ACTIVE)
        self.assertEqual(newest["down_units"], [])
        self.assertEqual(newest["cpu_busy_pct"], 30.0)

        encoded = json.dumps(output, sort_keys=True, ensure_ascii=False)
        # 8 samples with the PSI some/full pairs stay well under 6 KiB; the
        # 10-sample (three-boot) worst case is bounded separately at 8 KiB.
        self.assertLess(len(encoded.encode("utf-8")), 6144)
        self.assertNotIn(str(self.home), encoded)
        self.assertNotIn("ring.jsonl", encoded)
        self.assertNotIn("DO-NOT-PUBLISH", encoded)

    def test_ring_bytes_reports_the_bounded_file_size(self):
        path = self.write_ring(self.fresh_records())
        output = self.project()

        self.assertGreater(output["ring_bytes"], 0)
        self.assertEqual(output["ring_bytes"], path.stat().st_size)
        self.assertLessEqual(output["ring_bytes"],
                             output["retention"]["max_bytes"])

    def test_malformed_lines_are_skipped_without_losing_valid_points(self):
        records = self.fresh_records(3)
        self.write_ring(records, raw_lines=[
            "{not json",
            json.dumps({"v": 2, "t": NOW, "boot": BOOT_A}),
            json.dumps({"t": "1799999990", "boot": BOOT_A}),
            json.dumps({"v": 1, "t": NOW - 30, "boot": "DO-NOT-PUBLISH"}),
            "",
        ])
        output = self.project()

        self.assertEqual(output["status"], "ok")
        self.assertEqual(output["points_available"], 3)
        self.assertEqual(output["points"], 3)
        self.assertNotIn("DO-NOT-PUBLISH",
                         json.dumps(output, sort_keys=True))


class RebootAndDeltaTests(HostMetricsFixture):

    def test_two_boots_mark_spans_reboot_and_null_the_boundary_deltas(self):
        records = [
            sample_record(NOW - 300, cpu=[1000, 400, 100],
                          disk_sectors=[2048, 4096], disk_ms=[30, 40]),
            sample_record(NOW - 240, cpu=[2000, 900, 300],
                          disk_sectors=[3072, 6144], disk_ms=[50, 60]),
            sample_record(NOW - 180, boot=BOOT_B, cpu=[500, 100, 50],
                          disk_sectors=[100, 200], disk_ms=[10, 20]),
            sample_record(NOW - 120, boot=BOOT_B, cpu=[900, 220, 80],
                          disk_sectors=[300, 600], disk_ms=[25, 35]),
        ]
        output = self.project(records)

        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["spans_reboot"])
        self.assertEqual(output["boots"], 2)
        self.assertEqual(output["current_boot"], BOOT_B)
        samples = output["samples"]
        self.assertEqual([sample["boot"] for sample in samples],
                         [BOOT_A, BOOT_A, BOOT_B, BOOT_B])

        self.assertIsNone(samples[0]["cpu_busy_pct"])
        self.assertIsNone(samples[0]["disk_read_sectors"])
        # hand-computed: d_total=1000, d_idle=500, d_iowait=200 -> 30.0 %
        self.assertEqual(samples[1]["cpu_busy_pct"], 30.0)
        self.assertEqual(samples[1]["disk_read_sectors"], 1024)
        self.assertEqual(samples[1]["disk_write_sectors"], 2048)
        self.assertEqual(samples[1]["disk_io_ms"], 20)

        # The reboot boundary never borrows a delta from the previous boot.
        for key in ("cpu_busy_pct", "disk_read_sectors",
                    "disk_write_sectors", "disk_io_ms"):
            self.assertIsNone(samples[2][key], key)
        # hand-computed: d_total=400, d_idle=120, d_iowait=30 -> 62.5 %
        self.assertEqual(samples[3]["cpu_busy_pct"], 62.5)
        self.assertEqual(samples[3]["disk_read_sectors"], 200)
        self.assertEqual(samples[3]["disk_write_sectors"], 400)
        self.assertEqual(samples[3]["disk_io_ms"], 15)

    def test_cpu_busy_pct_is_clamped_to_the_contract_range(self):
        records = [
            sample_record(NOW - 180, cpu=[1000, 900, 100]),
            # idle delta larger than the total delta: mathematically negative.
            sample_record(NOW - 120, cpu=[1010, 1000, 100]),
            # counters going backwards: mathematically above 100 %.
            sample_record(NOW - 60, cpu=[1110, 900, 100]),
        ]
        samples = self.project(records)["samples"]

        self.assertEqual(samples[1]["cpu_busy_pct"], 0.0)
        self.assertEqual(samples[2]["cpu_busy_pct"], 100.0)

    def test_zero_elapsed_cpu_is_null_not_a_healthy_zero(self):
        records = [
            sample_record(NOW - 60, cpu=[1000, 400, 100]),
            sample_record(NOW - 30, cpu=[1000, 400, 100]),
        ]
        samples = self.project(records)["samples"]

        self.assertIsNone(samples[1]["cpu_busy_pct"])

    def test_null_boot_is_unknown_and_never_counts_as_a_reboot(self):
        records = [
            sample_record(NOW - 120, boot=None, cpu=None,
                          disk_sectors=None, disk_ms=None),
            sample_record(NOW - 60),
        ]
        output = self.project(records)

        self.assertEqual(output["boots"], 1)
        self.assertEqual(output["current_boot"], BOOT_A)
        self.assertFalse(output["spans_reboot"])
        self.assertIsNone(output["samples"][0]["boot"])
        self.assertIsNone(output["samples"][1]["cpu_busy_pct"])
        self.assertIsNone(output["samples"][1]["disk_read_sectors"])

    def test_all_null_boots_report_no_current_boot(self):
        output = self.project([sample_record(NOW - 30, boot=None)])

        self.assertEqual(output["boots"], 0)
        self.assertIsNone(output["current_boot"])
        self.assertFalse(output["spans_reboot"])
        self.assertEqual(output["status"], "ok")


class DegradedRecordTests(HostMetricsFixture):

    def test_a_null_source_record_is_retained_with_null_projection(self):
        record = sample_record(
            NOW - 30, boot=None, cpu=None, load=None, mem_kb=None, psi=None,
            disk_sectors=None, disk_ms=None, units=None, uptime_s=None)
        output = self.project([record])

        self.assertEqual(output["status"], "ok")
        self.assertEqual(output["points_available"], 1)
        self.assertEqual(output["points"], 1)
        self.assertEqual(output["boots"], 0)
        self.assertIsNone(output["current_boot"])
        self.assertFalse(output["spans_reboot"])
        sample = output["samples"][0]
        self.assertEqual(sample["age_sec"], 30)
        for key in ("boot", "uptime_s", "load1", "load5", "load15",
                    "cpu_busy_pct", "mem_available_kb", "mem_total_kb",
                    "swap_free_kb", "swap_total_kb", "dirty_kb",
                    "writeback_kb", "psi_cpu_some10", "psi_io_some10",
                    "psi_mem_some10", "disk_read_sectors",
                    "disk_write_sectors", "disk_io_ms", "units"):
            self.assertIsNone(sample[key], key)
        self.assertEqual(sample["down_units"], [])

    def test_missing_mem_keys_project_null_never_zero(self):
        output = self.project([
            sample_record(NOW - 30,
                          mem_kb={"available": 100, "total": 200}),
        ])
        sample = output["samples"][0]

        self.assertEqual(sample["mem_available_kb"], 100)
        self.assertEqual(sample["mem_total_kb"], 200)
        self.assertIsNone(sample["swap_total_kb"])
        self.assertIsNone(sample["swap_free_kb"])
        self.assertIsNone(sample["dirty_kb"])
        self.assertIsNone(sample["writeback_kb"])


class AbsentAndInvalidTests(HostMetricsFixture):

    def assert_defaults(self, output, status):
        self.assertEqual(output["status"], status)
        self.assertEqual(set(output), PROJECTION_KEYS)
        self.assertEqual(output["samples"], [])
        self.assertEqual(output["points"], 0)
        self.assertEqual(output["points_available"], 0)
        self.assertEqual(output["samples_omitted"], 0)
        self.assertIsNone(output["oldest_age_sec"])
        self.assertIsNone(output["newest_age_sec"])
        self.assertEqual(output["boots"], 0)
        self.assertIsNone(output["current_boot"])
        self.assertFalse(output["spans_reboot"])
        self.assertEqual(output["unit_order"], list(collector.UNIT_ORDER))

    def test_absent_ring(self):
        output = self.project()

        self.assert_defaults(output, "absent")
        self.assertEqual(output["ring_bytes"], 0)

    def test_empty_ring_is_invalid_not_healthy(self):
        path = self.write_ring([])
        self.assertEqual(path.stat().st_size, 0)
        output = self.project()

        self.assert_defaults(output, "invalid")
        self.assertEqual(output["ring_bytes"], 0)

    def test_only_malformed_lines_is_invalid(self):
        self.write_ring([], raw_lines=[
            "{torn", "not json", json.dumps({"v": 2, "t": NOW}),
            json.dumps({"v": 1, "t": None}),
        ])
        output = self.project()

        self.assert_defaults(output, "invalid")


class RefusedRingTests(HostMetricsFixture):

    def test_symlinked_ring_is_refused_without_raising(self):
        target = self.home / "elsewhere.jsonl"
        target.write_text(json.dumps(sample_record(NOW - 30)) + "\n",
                          encoding="utf-8")
        self.ring.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, self.ring)
        output = self.project()

        self.assertEqual(output["status"], "unreadable")
        self.assertEqual(output["samples"], [])
        self.assertEqual(output["ring_bytes"], 0)

    def test_non_regular_ring_is_refused(self):
        self.ring.mkdir(parents=True)
        output = self.project()

        self.assertEqual(output["status"], "unreadable")
        self.assertEqual(output["samples"], [])

    def test_oversize_ring_is_refused(self):
        self.ring.parent.mkdir(parents=True, exist_ok=True)
        self.ring.write_bytes(b"x" * (1024 * 1024))
        output = self.project()

        self.assertEqual(output["status"], "invalid")
        self.assertEqual(output["samples"], [])
        self.assertEqual(output["ring_bytes"], 0)


class StalenessTests(HostMetricsFixture):

    def test_newest_record_older_than_three_intervals_is_stale(self):
        output = self.project([
            sample_record(NOW - 3 * collector.INTERVAL_SEC - 1),
        ])

        self.assertEqual(output["status"], "stale")
        self.assertEqual(output["newest_age_sec"],
                         3 * collector.INTERVAL_SEC + 1)

    def test_newest_record_exactly_at_the_boundary_is_ok(self):
        output = self.project([
            sample_record(NOW - 3 * collector.INTERVAL_SEC),
        ])

        self.assertEqual(output["status"], "ok")
        self.assertEqual(output["newest_age_sec"], 3 * collector.INTERVAL_SEC)


class UnitMaskTests(HostMetricsFixture):

    def test_failed_and_inactive_states_become_down_units(self):
        mask = 0
        # unit 0 active, unit 1 failed, unit 2 inactive, unit 3 active,
        # unit 4 failed, unit 5 active.
        for index, state in enumerate((1, 2, 0, 1, 2, 1)):
            mask |= state << (2 * index)
        sample = self.project([sample_record(NOW - 30, units=mask)])["samples"][0]

        self.assertEqual(sample["units"], mask)
        self.assertEqual(sample["down_units"], [
            collector.UNIT_ORDER[1], collector.UNIT_ORDER[2],
            collector.UNIT_ORDER[4],
        ])

    def test_all_active_has_no_down_units(self):
        sample = self.project(
            [sample_record(NOW - 30, units=ALL_ACTIVE)])["samples"][0]

        self.assertEqual(sample["down_units"], [])

    def test_zero_mask_reports_every_unit_as_not_active(self):
        sample = self.project(
            [sample_record(NOW - 30, units=0)])["samples"][0]

        self.assertEqual(sample["down_units"], list(collector.UNIT_ORDER))

    def test_units_are_little_endian_pairs(self):
        # Unit i owns bits 2*i and 2*i+1: 0b01 at unit 0 vs at unit 5 must
        # decode to different down sets (everything else is 0b00 = not active).
        first = self.project([sample_record(NOW - 30, units=0b01)])["samples"][0]
        last = self.project(
            [sample_record(NOW - 30, units=0b01 << 10)])["samples"][0]

        self.assertEqual(first["down_units"], list(collector.UNIT_ORDER[1:]))
        self.assertEqual(last["down_units"], list(collector.UNIT_ORDER[:5]))

    def test_unknown_mask_is_not_reported_as_down(self):
        # Every unit unknown/activating (boot, or a scope without the unit) is
        # not evidence that the host is degraded.
        unknown = sum(collector.UNIT_STATE_UNKNOWN << (2 * index)
                      for index in range(len(collector.UNIT_ORDER)))
        self.assertEqual(collector._host_metrics_down_units(unknown), [])
        mixed = ((ALL_ACTIVE & ~0b11 & ~(0b11 << 2))
                 | collector.UNIT_STATE_UNKNOWN
                 | collector.UNIT_STATE_FAILED << 2)
        self.assertEqual(collector._host_metrics_down_units(mixed),
                         [collector.UNIT_ORDER[1]])

    def test_null_units_is_unknown_not_down(self):
        sample = self.project(
            [sample_record(NOW - 30, units=None)])["samples"][0]

        self.assertIsNone(sample["units"])
        self.assertEqual(sample["down_units"], [])


class HostMetricsBudgetTests(HostMetricsFixture):
    """#2578 evidence must never evict an existing section for a small overflow."""

    CORNERS = {
        "retro_corner": {"scene_narration": {"status": "ok"}},
        "rotation_evidence": {"corners": [
            {"corner": f"corner-{index}", "detail": "x" * 64}
            for index in range(120)
        ]},
    }

    def projection(self, records=None):
        return self.project(records if records is not None else self.fresh_records())

    def test_soft_target_never_trims_the_ring(self):
        # The production envelope already sits above MAX_JSON_BYTES (#2533), so a
        # soft-target trim would shed samples on every run without ever reaching
        # the target. Only the hard ceiling may trim.
        payload = base_payload(host_metrics=self.projection(), corners=self.CORNERS)
        pad_section(payload, collector.MAX_JSON_BYTES + 4096)
        before = len(json.dumps(
            payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        self.assertGreater(before, collector.MAX_JSON_BYTES)
        self.assertLess(before, collector.HARD_JSON_BYTES)

        collector._diagnostics_budget(payload)

        section = payload["host_metrics"]
        self.assertEqual(section["status"], "ok")
        self.assertEqual(len(section["samples"]), collector.SAMPLES_MAX)
        self.assertEqual(section["points"], collector.SAMPLES_MAX)
        self.assertEqual(section["samples_omitted"], 0)
        self.assertEqual(section["points_available"], collector.SAMPLES_MAX)
        self.assertEqual(section["ring_bytes"], self.ring.stat().st_size)
        self.assertEqual(section["samples"][-1]["t"], NOW - 18)
        self.assertNotEqual(payload["corners"].get("status"), "output_omitted")

    def test_hard_overflow_trims_the_ring_and_keeps_the_corners(self):
        payload = base_payload(host_metrics=self.projection(), corners=self.CORNERS)
        pad_section(payload, collector.HARD_JSON_BYTES + 200)
        total = payload["host_metrics"]["points"]
        self.assertGreater(
            len(json.dumps(payload, sort_keys=True,
                           ensure_ascii=False).encode("utf-8")),
            collector.HARD_JSON_BYTES)

        text = collector._diagnostics_budget(payload)

        self.assertLessEqual(len(text.encode("utf-8")), collector.HARD_JSON_BYTES)
        # The whole point: no existing section is evicted for a small overflow.
        self.assertNotEqual(payload["corners"].get("status"), "output_omitted")
        section = payload["host_metrics"]
        self.assertEqual(section["status"], "ok")
        self.assertLess(section["points"], total)
        self.assertGreaterEqual(section["points"], 1)
        self.assertEqual(section["points"], len(section["samples"]))
        self.assertEqual(section["samples_omitted"], total - section["points"])
        # The newest interval always survives, trimmed oldest-first.
        self.assertEqual(section["samples"][-1]["t"], NOW - 18)

    def test_a_fitting_payload_keeps_every_section_when_the_key_is_present(self):
        # The M1 regression: without the new key this envelope fits the hard
        # ceiling; with it, it does not. The key must yield, not `corners`.
        without = base_payload(corners=self.CORNERS)
        pad_section(without, collector.HARD_JSON_BYTES - 2048)
        base = len(json.dumps(without, sort_keys=True,
                              ensure_ascii=False).encode("utf-8"))
        self.assertLess(base, collector.HARD_JSON_BYTES)
        with_key = copy.deepcopy(without)
        with_key["host_metrics"] = self.projection()
        total = with_key["host_metrics"]["points"]
        self.assertGreater(
            len(json.dumps(with_key, sort_keys=True,
                           ensure_ascii=False).encode("utf-8")),
            collector.HARD_JSON_BYTES)

        text = collector._diagnostics_budget(with_key)

        self.assertLessEqual(len(text.encode("utf-8")), collector.HARD_JSON_BYTES)
        self.assertNotEqual(with_key["corners"].get("status"), "output_omitted")
        self.assertEqual(with_key["host_metrics"]["status"], "ok")
        self.assertLess(with_key["host_metrics"]["points"], total)
        self.assertEqual(with_key["host_metrics"]["samples_omitted"],
                         total - with_key["host_metrics"]["points"])

    def test_hard_ceiling_drops_the_section_only_after_the_earlier_sections(self):
        self.assertEqual(collector._HARD_DROP_ORDER[-1], "host_metrics")
        payload = base_payload(host_metrics=self.projection())
        pad_section(payload, collector.HARD_JSON_BYTES + 50 * 1024)

        text = collector._diagnostics_budget(payload)

        self.assertLessEqual(len(text.encode("utf-8")),
                             collector.HARD_JSON_BYTES)
        for key in collector._HARD_DROP_ORDER:
            self.assertEqual(payload[key],
                             {"status": "output_omitted",
                              "reason": "hard_ceiling"}, key)
        # The filler forced every reviewed section out; the pathological
        # fallback then keeps only ``meta`` (the envelope still identifies the
        # deployed SHA) instead of returning a document the gateway rejects.
        self.assertIn("meta", payload)

    def test_payloads_without_host_metrics_are_byte_identical_to_before(self):
        cases = [
            pad_section(base_payload(), collector.MAX_JSON_BYTES + 2048),
            pad_section(base_payload(), collector.HARD_JSON_BYTES + 1024),
            base_payload(corners={
                "retro_corner": {"scene_narration": {"status": "ok"}},
                "rotation_evidence": {"corners": [
                    {"corner": f"corner-{index}", "detail": "x" * 64}
                    for index in range(520)
                ]},
                "retro_scores": {"games": {"bastet": {"records": [
                    {"score": index, "detail": "x" * 64}
                    for index in range(40)
                ]}}},
            }),
        ]
        for payload in cases:
            # Disabling only the new helper must reproduce the pre-#2578
            # envelope byte for byte, proving the branch is inert without the
            # key and that no existing reduction order changed.
            with mock.patch.object(collector, "_host_metrics_hard_trim",
                                   lambda payload, text: text):
                disabled = collector._diagnostics_budget(copy.deepcopy(payload))
            enabled = collector._diagnostics_budget(copy.deepcopy(payload))
            self.assertEqual(enabled, disabled)

    def test_when_the_floor_cannot_fit_the_trim_is_skipped(self):
        # If even the floor (newest + boundary points) is over the hard ceiling,
        # some section is dropped regardless; burning the boundary for a trim
        # that cannot help would only destroy evidence.
        payload = base_payload(
            host_metrics=self.project(self.two_boot_records()),
            corners=self.CORNERS,
        )
        # Deep enough that even the floor stays over the ceiling.
        pad_section(payload, collector.HARD_JSON_BYTES + 6 * 1024)
        total = payload["host_metrics"]["points"]

        collector._diagnostics_budget(payload)

        section = payload["host_metrics"]
        self.assertEqual(section["status"], "ok")
        self.assertEqual(section["points"], total)
        self.assertEqual(section["samples_omitted"], 0)
        self.assertEqual(section["samples"][0]["boot"], BOOT_A)
        # The cascade then had to drop a whole section instead.
        self.assertEqual(payload["corners"],
                         {"status": "output_omitted", "reason": "hard_ceiling"})

    def test_the_pre_hang_boundary_survives_a_trim_that_fits_the_floor(self):
        # Between "one more sample fits" and "nothing fits", the trim must stop
        # at the floor instead of sacrificing the boundary: a whole section is
        # dropped either way in that band.
        payload = base_payload(
            host_metrics=self.project(self.two_boot_records()),
            corners=self.CORNERS,
        )
        # Over the ceiling, but the floor (newest + boundary) still fits.
        pad_section(payload, collector.HARD_JSON_BYTES + 2048)
        total = payload["host_metrics"]["points"]
        self.assertGreater(
            len(json.dumps(payload, sort_keys=True,
                           ensure_ascii=False).encode("utf-8")),
            collector.HARD_JSON_BYTES)

        text = collector._diagnostics_budget(payload)

        self.assertLessEqual(len(text.encode("utf-8")),
                             collector.HARD_JSON_BYTES)
        section = payload["host_metrics"]
        self.assertEqual(section["status"], "ok")
        self.assertGreaterEqual(section["points"], 2)
        self.assertEqual(section["samples"][0]["boot"], BOOT_A)
        self.assertEqual(section["samples"][-1]["t"], NOW)
        self.assertEqual(section["samples_omitted"], total - section["points"])

    def test_same_boot_samples_are_dropped_and_both_boundaries_survive(self):
        # Samples that share a boundary's boot must still yield; with three
        # boots the floor is the newest point plus both boundary points.
        records = [
            sample_record(NOW - 60 * (25 - index), boot=BOOT_A)
            for index in range(3)
        ] + [
            sample_record(NOW - 60 * (22 - index), boot="c3d4e5f6a1b2")
            for index in range(3)
        ] + [
            sample_record(NOW - 60 * (19 - 1 - index), boot=BOOT_B)
            for index in range(19)
        ]
        payload = base_payload(host_metrics=self.project(records),
                               corners=self.CORNERS)
        total = payload["host_metrics"]["points"]
        pad_section(payload, collector.HARD_JSON_BYTES + 1024)

        text = collector._diagnostics_budget(payload)

        self.assertLessEqual(len(text.encode("utf-8")),
                             collector.HARD_JSON_BYTES)
        section = payload["host_metrics"]
        self.assertEqual(section["status"], "ok")
        self.assertLess(section["points"], total)
        retained_boots = {sample["boot"] for sample in section["samples"]}
        self.assertEqual(retained_boots, {BOOT_A, "c3d4e5f6a1b2", BOOT_B})
        self.assertEqual(section["samples"][-1]["t"], NOW)
        self.assertEqual(section["samples_omitted"], total - section["points"])
        self.assertNotEqual(payload["corners"].get("status"), "output_omitted")

    def test_no_ring_content_is_a_no_op(self):
        payload = pad_section(base_payload(), collector.HARD_JSON_BYTES + 1024)
        with mock.patch.object(collector, "_host_metrics_hard_trim",
                               wraps=collector._host_metrics_hard_trim) as trim:
            collector._diagnostics_budget(payload)
        self.assertEqual(trim.call_count, 1)
        self.assertNotIn("host_metrics", payload)


class RebootBoundaryTests(HostMetricsFixture):
    """The pre-hang point must survive a long-delayed post-reboot diagnostics.

    #2578 exists to explain a guest-OS hang *after* the reboot. Once the
    sampler refills the window with post-boot points, a plain "newest N"
    projection would hide the last point before the hang, which is the only
    evidence of the final hour.
    """

    def test_post_reboot_window_keeps_the_last_pre_hang_point(self):
        records = self.two_boot_records()
        output = self.project(records)

        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["spans_reboot"])
        self.assertEqual(output["boots"], 2)
        self.assertEqual(output["current_boot"], BOOT_B)
        # 8 newest (all BOOT_B) plus the last BOOT_A point.
        self.assertEqual(output["points"], collector.SAMPLES_MAX + 1)
        self.assertEqual(output["samples"][0]["boot"], BOOT_A)
        self.assertEqual(output["samples"][0]["t"], records[2]["t"])
        self.assertEqual(output["samples"][0]["cpu_busy_pct"], None)
        self.assertEqual(output["samples"][1]["boot"], BOOT_B)
        self.assertEqual(output["samples"][-1]["t"], NOW)

    def test_hard_ceiling_keeps_the_pre_hang_boundary(self):
        payload = base_payload(host_metrics=self.project(self.two_boot_records()))
        total = payload["host_metrics"]["points"]
        pad_section(payload, collector.HARD_JSON_BYTES + 200)

        text = collector._diagnostics_budget(payload)

        self.assertLessEqual(len(text.encode("utf-8")), collector.HARD_JSON_BYTES)
        section = payload["host_metrics"]
        self.assertNotEqual(section.get("status"), "output_omitted")
        self.assertEqual(section["samples"][0]["boot"], BOOT_A)
        self.assertGreaterEqual(len(section["samples"]), 2)
        self.assertEqual(section["samples"][1]["boot"], BOOT_B)
        self.assertEqual(section["samples"][-1]["t"], NOW)
        self.assertEqual(section["points"], len(section["samples"]))
        self.assertEqual(section["samples_omitted"], total - len(section["samples"]))

    def test_three_boots_with_all_units_down_stay_bounded(self):
        failed = sum(collector.UNIT_STATE_FAILED << (2 * index)
                     for index in range(len(collector.UNIT_ORDER)))
        records = [
            sample_record(NOW - 60 * (20 - index), boot=BOOT_A, units=failed)
            for index in range(3)
        ] + [
            sample_record(NOW - 60 * (17 - index), boot="c3d4e5f6a1b2",
                          units=failed)
            for index in range(3)
        ] + [
            sample_record(NOW - 60 * (14 - index), boot=BOOT_B, units=failed)
            for index in range(14)
        ]
        output = self.project(records)
        self.assertEqual(output["boots"], 3)
        self.assertTrue(output["spans_reboot"])
        self.assertEqual(output["points"], collector.SAMPLES_MAX + 2)
        self.assertEqual(len(output["samples"][-1]["down_units"]),
                         len(collector.UNIT_ORDER))
        encoded = json.dumps(output, sort_keys=True, ensure_ascii=False)
        self.assertLess(len(encoded.encode("utf-8")), 8192)


class RuntimeHealthTests(unittest.TestCase):
    """The sampler units' own health, as fixed enums (a dead sampler is visible).

    The first production deploy failed inside the deploy hook with the VM-side
    output withheld; these fields are the only sanctioned read-back channel.
    """

    def systemctl_answers(self, answers):
        def fake(args, timeout=5):
            for key, value in answers.items():
                if tuple(args[:2]) == key:
                    return value
            return None
        return fake

    def test_states_and_enabled_map_to_fixed_enums(self):
        answers = {
            ("is-active", collector.HOST_METRICS_SERVICE_UNIT): (3, "failed"),
            ("is-active", collector.HOST_METRICS_TIMER_UNIT): (0, "active"),
            ("is-enabled", collector.HOST_METRICS_SERVICE_UNIT): (1, "static"),
            ("is-enabled", collector.HOST_METRICS_TIMER_UNIT): (0, "enabled"),
        }
        with mock.patch.object(collector, "_systemctl_user",
                               side_effect=self.systemctl_answers(answers)):
            runtime = collector._collect_host_metrics_runtime(NOW)
        self.assertEqual(runtime["service_state"], "failed")
        self.assertEqual(runtime["service_enabled"], False)
        self.assertEqual(runtime["timer_state"], "active")
        self.assertEqual(runtime["timer_enabled"], True)

    def test_unknown_probe_results_stay_unknown_not_healthy(self):
        answers = {
            ("is-active", collector.HOST_METRICS_SERVICE_UNIT): (1, "garbage"),
            ("is-active", collector.HOST_METRICS_TIMER_UNIT): (1, ""),
            ("is-enabled", collector.HOST_METRICS_SERVICE_UNIT): (0, "indirect"),
            ("is-enabled", collector.HOST_METRICS_TIMER_UNIT): (0, "bad"),
        }
        with mock.patch.object(collector, "_systemctl_user",
                               side_effect=self.systemctl_answers(answers)):
            runtime = collector._collect_host_metrics_runtime(NOW)
        self.assertEqual(runtime["service_state"], "unknown")
        self.assertIsNone(runtime["service_enabled"])
        self.assertEqual(runtime["timer_state"], "unknown")
        self.assertIsNone(runtime["timer_enabled"])

    def test_show_properties_project_the_last_exit_classification(self):
        show = ("exit-code\n1\n2\n0\n")
        answers = {
            ("show", collector.HOST_METRICS_SERVICE_UNIT): (0, show),
        }
        with mock.patch.object(collector, "_systemctl_user",
                               side_effect=self.systemctl_answers(answers)):
            runtime = collector._collect_host_metrics_runtime(NOW)
        self.assertEqual(runtime["service_result"], "exit-code")
        self.assertEqual(runtime["service_exec_code"], 1)
        self.assertEqual(runtime["service_exec_status"], 2)
        self.assertEqual(runtime["service_n_restarts"], 0)

    def test_show_garbage_never_invents_a_result(self):
        answers = {
            ("show", collector.HOST_METRICS_SERVICE_UNIT): (0, "weird\nx\ny\nz\n"),
        }
        with mock.patch.object(collector, "_systemctl_user",
                               side_effect=self.systemctl_answers(answers)):
            runtime = collector._collect_host_metrics_runtime(NOW)
        self.assertIsNone(runtime["service_result"])
        self.assertIsNone(runtime["service_exec_code"])
        self.assertIsNone(runtime["service_exec_status"])
        self.assertIsNone(runtime["service_n_restarts"])

    def test_journal_tail_is_classified_without_publishing_text(self):
        lines = [
            f"{NOW - 60}.000000 host docich-host-metrics.service: Starting...",
            f"{NOW - 59}.000000 host docich-host-metrics.service: "
            "Main process exited, code=exited, status=2/NOT-SECRET",
            "secret-free: DO-NOT-PUBLISH",
            f"{NOW - 30}.000000 host docich-host-metrics.service: Failed with result 'exit-code'.",
        ]
        completed = mock.Mock(returncode=0, stdout="\n".join(lines) + "\n")
        with mock.patch.object(collector.subprocess, "run",
                               return_value=completed) as run:
            journal = collector._host_metrics_runtime_journal(NOW)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:3], ["journalctl", "--user-unit",
                                    collector.HOST_METRICS_SERVICE_UNIT])
        self.assertTrue(journal["readable"])
        self.assertEqual(journal["lines"], 4)
        self.assertEqual(journal["newest_age_sec"], 30)
        self.assertEqual(journal["signature"], "exited")

    def test_journal_oom_and_traceback_outrank_a_plain_exit(self):
        base = f"{NOW - 30}.000000 host docich-host-metrics.service: "
        for message, expected in (
            ("A process of this unit has been killed by the OOM killer.", "oom"),
            ("Traceback (most recent call last):", "python_traceback"),
            ("Somewhere: Timed out starting.", "timeout"),
            ("Starting Bounded host metrics ring sampler...", "starting"),
        ):
            completed = mock.Mock(returncode=0, stdout=base + message + "\n")
            with mock.patch.object(collector.subprocess, "run",
                                  return_value=completed):
                journal = collector._host_metrics_runtime_journal(NOW)
            self.assertEqual(journal["signature"], expected, message)

    def test_hostile_journal_lines_never_break_the_collection(self):
        # ``inf``/``nan``/``1e400`` in the timestamp field are a corrupt or
        # hostile line; classifying it must never raise into the collector and
        # never invent an age (a raise here kills the whole diagnostics run).
        for tail in ("inf\n", "nan\n", "-inf\n", "1e400\n",
                     "garbage line\n", "1e400 then normal\n"):
            completed = mock.Mock(returncode=0, stdout=tail)
            with mock.patch.object(collector.subprocess, "run",
                                   return_value=completed):
                journal = collector._host_metrics_runtime_journal(NOW)
            self.assertEqual(set(journal), JOURNAL_KEYS)
            self.assertTrue(journal["readable"])
            self.assertIsNone(journal["newest_age_sec"])

    def test_oom_only_matches_explicit_oom_phrases(self):
        base = f"{NOW - 30}.000000 host docich-host-metrics.service: "
        # Words that merely contain "oom", or a plain SIGTERM, are not OOM.
        for message, expected in (
            ("ValueError: boom", "other"),
            ("zoom level reset", "other"),
            ("classroom mode enabled", "other"),
            ("Main process exited, code=killed, status=15/TERM", "exited"),
            ("Memory cgroup out of memory: Killed process 12 (python3)",
             "oom"),
            ("A process of this unit has been killed by the OOM killer.",
             "oom"),
        ):
            completed = mock.Mock(returncode=0, stdout=base + message + "\n")
            with mock.patch.object(collector.subprocess, "run",
                                  return_value=completed):
                journal = collector._host_metrics_runtime_journal(NOW)
            self.assertEqual(journal["signature"], expected, message)

    def test_runtime_key_set_is_asserted_on_the_real_function(self):
        # The projection fixture injects the runtime block; assert the real
        # function's shape here so a renamed key cannot pass CI silently.
        answers = {}
        unavailable = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(collector, "_systemctl_user",
                               side_effect=self.systemctl_answers(answers)), \
                mock.patch.object(collector.subprocess, "run",
                                  return_value=unavailable):
            runtime = collector._collect_host_metrics_runtime(NOW)
        self.assertEqual(set(runtime), RUNTIME_KEYS)
        self.assertEqual(set(runtime["journal"]), JOURNAL_KEYS)
        self.assertEqual(runtime["service_state"], "unknown")
        self.assertIsNone(runtime["service_enabled"])
        self.assertEqual(runtime["timer_state"], "unknown")
        self.assertIsNone(runtime["timer_enabled"])
        self.assertIsNone(runtime["service_result"])
        # journalctl exists on the CI runner and even answers for this user, so
        # the probe must be injected too or the test is not hermetic.
        self.assertEqual(runtime["journal"]["readable"], False)

    def test_sampler_refusal_is_classified_with_its_byte_count(self):
        lines = [
            f"{NOW - 62}.000000 host docich-host-metrics.service: "
            "host_metrics: cannot write host metrics ring (record_bytes=531991)",
            f"{NOW - 60}.000000 host systemd[1]: "
            "docich-host-metrics.service: Main process exited, code=exited, status=1/FAILURE",
        ]
        completed = mock.Mock(returncode=0, stdout="\n".join(lines) + "\n")
        with mock.patch.object(collector.subprocess, "run",
                               return_value=completed):
            journal = collector._host_metrics_runtime_journal(NOW)
        # The refusal outranks the plain exit; the byte count is the only part
        # of the journal line that leaves the collector.
        self.assertEqual(journal["signature"], "sampler_refused")
        self.assertEqual(journal["refused_record_bytes"], 531991)

    def test_a_plain_exit_has_no_refused_byte_count(self):
        lines = [
            f"{NOW - 60}.000000 host systemd[1]: "
            "docich-host-metrics.service: Main process exited, code=exited, status=1/FAILURE",
        ]
        completed = mock.Mock(returncode=0, stdout="\n".join(lines) + "\n")
        with mock.patch.object(collector.subprocess, "run",
                               return_value=completed):
            journal = collector._host_metrics_runtime_journal(NOW)
        self.assertEqual(journal["signature"], "exited")
        self.assertIsNone(journal["refused_record_bytes"])

    def test_unreadable_journal_is_fixed_shape_and_not_healthy(self):
        completed = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(collector.subprocess, "run",
                              return_value=completed):
            journal = collector._host_metrics_runtime_journal(NOW)
        self.assertEqual(journal, {"readable": False, "lines": 0,
                                   "newest_age_sec": None, "signature": None,
                                   "refused_record_bytes": None})
        with mock.patch.object(collector.subprocess, "run",
                               side_effect=OSError("no journalctl")):
            journal = collector._host_metrics_runtime_journal(NOW)
        self.assertEqual(journal, {"readable": False, "lines": 0,
                                   "newest_age_sec": None, "signature": None,
                                   "refused_record_bytes": None})


class ConstantParityTests(unittest.TestCase):

    # Constants the sampler publishes; the projection must not drift from them.
    SHARED_NAMES = ("INTERVAL_SEC", "MAX_POINTS", "MAX_BYTES",
                    "SCHEMA_VERSION", "UNIT_ORDER")

    def test_collector_constants_match_the_sampler(self):
        if not SAMPLER.exists():
            self.skipTest(
                "ops/vm_actions/host_metrics.py is not present in this "
                "worktree yet (the sampler owner adds it in the same change); "
                "rerun this parity check once it lands")
        sampler = load_module("host_metrics_sampler_tested", SAMPLER)
        for name in self.SHARED_NAMES:
            self.assertTrue(
                hasattr(sampler, name),
                f"sampler must define the contract constant {name}")
            self.assertEqual(
                getattr(collector, name), getattr(sampler, name),
                f"{name} drifted from the sampler contract")

    def test_collector_reads_the_path_the_sampler_writes(self):
        sampler = load_module("host_metrics_sampler_path_tested", SAMPLER)
        home = Path("/home/ubuntu")
        self.assertEqual(
            sampler.state_root(home) / sampler.RING_NAME,
            home / collector.HOST_METRICS_RING_RELPATH,
        )

    def test_diagnostics_only_constants_derive_from_the_shared_interval(self):
        # SAMPLES_MAX / STALE_SEC are projection-side bounds, so they are not
        # in the sampler; they must still derive from the one cadence constant.
        self.assertEqual(collector.SAMPLES_MAX, 8)
        self.assertEqual(collector.STALE_SEC, 3 * collector.INTERVAL_SEC)


class WiringTests(unittest.TestCase):

    def test_main_projects_the_ring_from_the_deployed_checkout(self):
        source = COLLECTOR.read_text(encoding="utf-8")
        self.assertIn(
            '"host_metrics": _collect_host_metrics(_host_metrics_home(), now)',
            source)
        # The ring lives in the runtime user's home, resolved with the same
        # passwd rule the sampler uses -- not the projection target and not a
        # config/state_dir value that could move between deploys.
        self.assertIn("import pwd", source)
        self.assertIn("pwd.getpwuid(os.getuid()).pw_dir", source)


if __name__ == "__main__":
    unittest.main()
