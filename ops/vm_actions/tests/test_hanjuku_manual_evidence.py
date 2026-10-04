"""Synthetic request history, pagination, resource and privacy regressions."""
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from docich import hanjuku_manual_evidence as evidence
from docich.tmux import TmuxError

RID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER = "11111111-2222-3333-4444-555555555555"
LEASE = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


class AbsentTmux:
    def window_target_exists(self, target, *, strict):
        assert strict
        return False

    def session_target_exists(self, target, *, strict):
        assert strict
        return False


class ManualPositiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name).resolve()
        spec = importlib.util.spec_from_file_location("evidence_collector", ROOT / "ops/vm_actions/collect_diagnostics.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.read = module._rotation_evidence_file
        self.manual = {"corner": "hanjuku-hero", "state_file": "retro_corner_manual.json",
                       "request_id": RID, "selected_at": 100}
        self.write("game_switch.json", {"phase": "ready", "active": None,
                                      "previous": None, "candidate": None, "retiring": []})

    def write(self, relative, data):
        path = self.state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def row(self, **change):
        return {"schema_version": 1, "timestamp": "1970-01-01T00:02:00Z",
                "request_id": RID, "operation": "switch", "target": "hanjuku-hero",
                "event": "accepted", "generation": 1, "runtime_id": "g1-abcdef", **change}

    def log(self, *rows):
        path = self.state / "logs/game_switch.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
        return path

    def project(self, manual=None):
        with mock.patch.object(evidence, "_BoundedProbe", return_value=AbsentTmux()):
            return evidence.project(self.state, self.manual if manual is None else manual, 200, read_fixed=self.read)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}

    def test_all_retained_records_positive_but_not_all_historical_resources(self):
        self.log(self.row(request_id=OTHER, generation=7, runtime_id="g7-abcdef"),
                 self.row(event="requested", generation=None, runtime_id=None), self.row(),
                 self.row(generation=2, runtime_id="g2-fedcba", event="candidate_started"),
                 self.row(event="rollback_ready", result="rolled_back", cleanup_pending=False,
                          detail="PRIVATE_LOG", argv="PRIVATE_ARGV", env={"TOKEN": "PRIVATE_SECRET"}))
        before = self.snapshot()
        out = self.project()
        self.assertTrue(out["requested_seen"] and out["receipt_created_seen"])
        self.assertTrue(out["candidate_started_seen"] and out["terminal_cleanup_clear_seen"])
        self.assertEqual([r["generation"] for r in out["generations"]], [1, 2])
        self.assertTrue(all(r["resources_released"] for r in out["generations"]))
        self.assertTrue(out["log"]["scan_complete"])
        self.assertEqual(out["request_generation_coverage"], "unknown")
        self.assertGreater(out["resource_attribution_unknown"], 0)
        self.assertIsNone(out["all_resources_released"])
        self.assertFalse(out["cancellation_authority"])
        self.assertEqual(self.snapshot(), before)
        public = json.dumps(out)
        for value in (RID, OTHER, "g1-abcdef", "g2-fedcba", "PRIVATE", str(self.state)):
            self.assertNotIn(value, public)

    def test_small_pages_reassemble_lines_without_losing_generations(self):
        self.log(self.row(), self.row(generation=2, runtime_id="g2-fedcba"))
        with mock.patch.object(evidence, "PAGE_BYTES", 17), mock.patch.object(evidence, "MAX_PAGES", 100):
            out = self.project()
        self.assertGreater(out["log"]["pages"], 2)
        self.assertTrue(out["log"]["scan_complete"])
        self.assertEqual(len(out["generations"]), 2)

    def test_prefix_cutoff_never_claims_older_generations_covered(self):
        self.log(self.row(), *[self.row(request_id=OTHER) for _ in range(12)],
                 self.row(generation=2, runtime_id="g2-fedcba"))
        with mock.patch.object(evidence, "PAGE_BYTES", 512), mock.patch.object(evidence, "MAX_PAGES", 1):
            out = self.project()
        self.assertTrue(out["log"]["prefix_truncated"])
        self.assertFalse(out["log"]["scan_complete"])
        self.assertEqual([r["generation"] for r in out["generations"]], [2])
        self.assertGreater(out["resource_attribution_unknown"], 0)

    def test_generations_and_matching_rows_have_independent_limits(self):
        self.log(*[self.row(generation=n, runtime_id=f"g{n}-abcdef") for n in range(1, 12)])
        out = self.project()
        self.assertEqual(len(out["generations"]), evidence.MAX_RUNTIMES)
        self.assertTrue(out["generations_truncated"])
        with mock.patch.object(evidence, "MAX_MATCHES", 1):
            out = self.project()
        self.assertTrue(out["log"]["matches_truncated"])
        self.assertFalse(out["log"]["scan_complete"])

    def test_malformed_overlong_and_partial_lines_remain_unknown(self):
        path = self.log(self.row())
        with path.open("ab") as handle:
            handle.write(b"{\n" + b"x" * 70000 + b"\n{\"partial\":")
        out = self.project()
        self.assertEqual(out["log"]["malformed_records"], 1)
        self.assertTrue(out["log"]["record_truncated"])
        self.assertFalse(out["log"]["scan_complete"])

    def test_unsafe_identity_foreign_future_and_malformed_shapes_do_not_probe(self):
        self.log(self.row(request_id=OTHER), self.row(timestamp="1970-01-01T00:04:00Z"),
                 self.row(operation=[]), self.row(result=[]), self.row(runtime_id="../private"),
                 self.row(generation=True), self.row(schema_version=True))
        with mock.patch.object(evidence, "_released_runtime") as probe:
            out = self.project()
        # The one otherwise valid row with malformed result is only a positive
        # identity observation; it does not become terminal cleanup evidence.
        self.assertFalse(out["terminal_seen"])
        self.assertGreater(out["invalid_matching_records"], 0)
        for manual in ({**self.manual, "request_id": "../private"},
                       {**self.manual, "corner": "nsnake"},
                       {**self.manual, "state_file": "../private"},
                       {**self.manual, "selected_at": float("nan")}):
            with mock.patch.object(evidence, "_open_fixed") as opened:
                self.assertFalse(self.project(manual)["observed"])
            opened.assert_not_called()

    def test_symlink_and_budget_fail_closed_without_reading_other_files(self):
        path = self.log(self.row())
        path.unlink()
        path.symlink_to(self.write("private.json", {"PRIVATE": "SECRET"}))
        self.assertFalse(self.project()["log"]["readable"])
        path.unlink()
        self.log(self.row())
        with mock.patch.object(evidence, "BUDGET_SECONDS", 0):
            out = self.project()
        self.assertTrue(out["log"]["budget_exhausted"])
        self.assertFalse(out["log"]["scan_complete"])

    def test_file_change_during_scan_and_resource_deadline_are_unknown(self):
        path = self.log(self.row())
        original = evidence.os.fstat
        calls = 0
        def changed(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                with path.open("ab") as handle:
                    handle.write(b"{}\n")
            return original(fd)
        with mock.patch.object(evidence.os, "fstat", side_effect=changed):
            out = self.project()
        self.assertTrue(out["log"]["changed_during_scan"])
        self.assertFalse(out["log"]["scan_complete"])
        with mock.patch.object(evidence.time, "monotonic", return_value=10), \
                mock.patch.object(evidence.procs, "run_bounded_output",
                                  return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            probe = evidence._BoundedProbe(9)
            with self.assertRaises(TimeoutError):
                probe._run(["has-session", "-t", "docich-game-g1"])
            run.assert_not_called()
            evidence._BoundedProbe(11)._run(["has-session", "-t", "docich-game-g1"])
            self.assertLessEqual(run.call_args.kwargs["timeout"], 0.3)
            self.assertEqual(run.call_args.kwargs["max_output_bytes"], 16384)
            self.assertEqual(run.call_args.args[0], ["tmux", "list-sessions", "-F", "#{session_name}"])
            with self.assertRaises(ValueError):
                evidence._BoundedProbe(11)._run(["kill-session"])

    def test_bounded_session_list_distinguishes_absence_from_probe_failure(self):
        with mock.patch.object(evidence.procs, "run_bounded_output") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"docich\n", b"")
            probe = evidence._BoundedProbe(evidence.time.monotonic() + 2)
            self.assertTrue(probe.session_target_exists("docich", strict=True))
            self.assertFalse(probe.session_target_exists("docich-g1-adapter", strict=True))
            run.return_value = subprocess.CompletedProcess([], 1, b"", b"PRIVATE_ERROR")
            with self.assertRaises(TmuxError):
                probe.session_target_exists("docich-g1-adapter", strict=True)

    def test_generation_conflict_canonical_owner_and_probe_failure_are_not_release(self):
        self.log(self.row(), self.row(runtime_id="g1-fedcba"))
        with mock.patch.object(evidence, "_released_runtime") as probe:
            out = self.project()
        probe.assert_not_called()
        self.assertTrue(all(r["generation_conflict"] for r in out["generations"]))
        self.log(self.row())
        self.write("game_switch.json", {"phase": "ready", "active": {"generation": 1,
                   "runtime_id": "g1-abcdef"}, "previous": None, "candidate": None, "retiring": []})
        with mock.patch.object(evidence, "_released_runtime") as probe:
            self.assertFalse(self.project()["generations"][0]["resources_released"])
        probe.assert_not_called()
        self.write("game_switch.json", {"phase": "ready", "active": {}, "retiring": []})
        with mock.patch.object(evidence, "_released_runtime") as probe:
            self.assertIsNone(self.project()["generations"][0]["canonical_tracks"])
        probe.assert_not_called()
        self.write("game_switch.json", {"phase": "ready", "retiring": []})
        with mock.patch.object(evidence, "_released_runtime", side_effect=TimeoutError("PRIVATE_ERROR")):
            out = self.project()
        self.assertIsNone(out["generations"][0]["resources_released"])
        self.assertNotIn("PRIVATE_ERROR", json.dumps(out))

    def test_owner_terminal_lease_attribution_is_bound_to_exact_request(self):
        self.log(self.row())
        owner = {"rotation_request_id": RID, "game": "hanjuku-hero", "status": "completed",
                 "bot_identity": {"game": "hanjuku-hero", "generation": 1,
                                  "runtime_id": "g1-abcdef", "lease_id": LEASE}}
        self.write("retro_corner_manual.json", owner)
        out = self.project()
        self.assertEqual(out["terminal_owners"], 1)
        self.assertTrue(out["generations"][0]["owner_terminal"])
        self.assertTrue(out["generations"][0]["lease_identity_observed"])
        self.assertGreater(out["resource_attribution_unknown"], 0)
        self.write("retro_corner_manual.json", {**owner, "rotation_request_id": OTHER})
        self.assertEqual(self.project()["matching_owners"], 0)
        self.write("retro_corner_manual.json", owner)
        self.write("retro_corner.json", {**owner, "bot_identity": {**owner["bot_identity"], "lease_id": OTHER}})
        with mock.patch.object(evidence, "_released_runtime") as probe:
            out = self.project()
        probe.assert_not_called()
        self.assertTrue(out["generations"][0]["owner_identity_conflict"])

    def test_exact_valid_receipt_is_positive_identity_not_historical_coverage(self):
        from docich.game_switch import GameSwitchStore
        (self.state / "game_switch.json").unlink()
        store = GameSwitchStore(self.state)
        store.initialize()
        receipt = dict(store.accept_request(RID, "start", "hanjuku-hero").receipt)
        receipt["updated_at"] = "1970-01-01T00:02:30Z"
        self.write(f"game-switch/requests/{RID}.json", receipt)
        out = self.project()
        self.assertTrue(out["receipt_created_seen"])
        self.assertEqual(len(out["generations"]), 1)
        self.assertIsNone(out["generations"][0]["resources_released"])
        self.assertGreater(out["resource_attribution_unknown"], 0)
        self.write(f"game-switch/requests/{RID}.json", {**receipt, "runtime_dir": "/private/foreign"})
        out = self.project()
        self.assertFalse(out["receipt_created_seen"])
        self.assertEqual(out["generations"], [])
        self.assertGreater(out["invalid_matching_records"], 0)


if __name__ == "__main__":
    unittest.main()
