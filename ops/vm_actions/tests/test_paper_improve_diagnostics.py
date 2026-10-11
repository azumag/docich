import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_paper", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaperImproveDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="paper-diag-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / "state"
        self.soren = root / "soren"
        self.state.mkdir(parents=True)
        (self.soren / "tmp" / "state").mkdir(parents=True)
        self.now = int(time.time())
        self.module = load_collector()

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_manual_corner_and_improve_status_are_allowlisted(self):
        self.write_json(
            self.state / "paper_corner_manual.json",
            {
                "status": "completed",
                "date": "2026-09-12",
                "previous_game": "sorengame",
                "requested_at": 100.0,
                "started_at": 200.0,
                "ends_at": 260.0,
                "completed_at": 270.0,
                "reports": {
                    "opening": {
                        "text": "SECRET ANNOUNCEMENT BODY",
                        "overlay": True,
                        "speech": True,
                    }
                },
                "improve_job": {
                    "spawned": True,
                    "date": "2026-09-12",
                    "log": "/private/path/SECRET-improve.log",
                },
            },
        )
        self.write_json(
            self.state / "trading" / "paper_improve_status.json",
            {
                "schema_version": 1,
                "source": "paper",
                "status": "failed",
                "phase": "facts",
                "progress": 10,
                "detail": "boom token=SUPERSECRET123",
                "started_at": 300.0,
                "updated_at": 301.0,
                "completed_at": 301.0,
                "changed": False,
                "prompt": "SECRET PROMPT BODY",
            },
        )

        result = self.module._collect_programs(self.state, self.soren, self.now)
        manual = result["paper_corner_manual"]
        self.assertTrue(manual["present"])
        self.assertTrue(manual["readable"])
        self.assertEqual(manual["status"], "completed")
        self.assertEqual(manual["announcements"], {"total": 1, "overlay": 1, "speech": 1})
        self.assertEqual(
            manual["improve_job"],
            {"spawned": True, "date": "2026-09-12", "error": None},
        )

        improve = result["paper_improve"]
        self.assertTrue(improve["present"])
        self.assertTrue(improve["readable"])
        self.assertEqual(improve["status"], "failed")
        self.assertEqual(improve["phase"], "facts")
        self.assertEqual(improve["progress"], 10)
        self.assertFalse(improve["changed"])
        self.assertIn("[REDACTED]", improve["detail"])

        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SECRET ANNOUNCEMENT BODY", rendered)
        self.assertNotIn("SECRET-improve.log", rendered)
        self.assertNotIn("SECRET PROMPT BODY", rendered)
        self.assertNotIn("SUPERSECRET123", rendered)
        self.assertNotIn("\"log\"", rendered)
        self.assertNotIn("\"prompt\"", rendered)

    def test_improve_progress_is_bounded_and_absence_is_explicit(self):
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertEqual(
            result["paper_improve"],
            {"present": False, "readable": False, "age_sec": -1},
        )
        self.assertFalse(result["paper_corner_manual"]["present"])

        self.write_json(
            self.state / "trading" / "paper_improve_status.json",
            {"status": "running", "phase": "generate", "progress": 999},
        )
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertEqual(result["paper_improve"]["progress"], 100)

    def test_corrupt_manual_and_improve_state_do_not_crash(self):
        (self.state / "paper_corner_manual.json").write_text("{bad", encoding="utf-8")
        improve = self.state / "trading" / "paper_improve_status.json"
        improve.parent.mkdir(parents=True, exist_ok=True)
        improve.write_text("[1,2,3]", encoding="utf-8")
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertEqual(
            result["paper_corner_manual"],
            {"present": True, "readable": False},
        )
        self.assertTrue(result["paper_improve"]["present"])
        self.assertFalse(result["paper_improve"]["readable"])

    def test_paper_ai_failures_keep_only_fixed_reason_codes(self):
        path = self.state / "trading" / "paper_improve_status.json"
        reasons = (
            "gate-disabled", "invalid-timeout", "invocation-error", "empty-output",
            "timeout", "rate-limit", "queue-giveup", "gate-giveup", "provider-failed",
            "invalid-output", "unknown",
            "pending-exists", "baseline-changed", "candidate-invalid", "no-change",
            "collecting", "pending-activated", "early-stop", "evaluation-unavailable",
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                self.write_json(path, {
                    "status": "failed", "phase": "generate", "detail": "ai-error",
                    "reason_code": reason,
                })
                result = self.module._collect_paper_improve_status(self.state, self.now)
                self.assertEqual(result["reason_code"], reason)
                self.assertEqual(result["detail"], "ai-error")

        for unknown in ("rc-124:timeout", "timeout\nSECRET", {"kind": "timeout"}, None):
            with self.subTest(unknown=unknown):
                self.write_json(path, {
                    "status": "failed", "phase": "generate", "detail": "ai-error",
                    "reason_code": unknown,
                })
                result = self.module._collect_paper_improve_status(self.state, self.now)
                self.assertEqual(result["reason_code"], "unknown")
                self.assertNotIn("SECRET", json.dumps(result))

    def experiment_fixture(self):
        control = {
            "schema_version": 1,
            "active_key": "SECRET-DIGEST",
            "experiment_id": "SECRET-EXPERIMENT-ID",
            "activated_at": 100.0,
            "updated_at": self.now,
            "status": "draining",
            "reason_code": "early-stop",
            "entries_allowed": False,
            "pending_available": True,
            "evaluation_status": "ok",
            "open_position_count": 2,
            "strategy": {"rule": "SECRET-RULE"},
        }
        evaluation = {
            "schema_version": 2,
            "experiment_id": "SECRET-EXPERIMENT-ID",
            "activated_at": 100.0,
            "evaluated_at": self.now,
            "status": "ok",
            "reason_code": "evaluated",
            "closed_sells": 8,
            "realized_pnl_jpy": "-95.25",
            "profit_factor": "0.45",
            "max_realized_drawdown_pct": "1.2",
            "self_entry_closed_sells": 6,
            "self_entry_realized_pnl_jpy": "-12.5",
            "carry_in_closed_sells": 4,
            "carry_in_realized_pnl_jpy": "-82.75",
            "mixed_origin_closed_sells": 2,
            "ignored_unpaired_exits": 0,
            "open_position_count": 2,
            "inventory_complete": True,
            "promotion_ready": False,
            "attribution_note": "SECRET-FREE-TEXT",
            "symbol": "SECRET-SYMBOL",
            "prompt": "SECRET-PROMPT",
        }
        return control, evaluation

    def write_experiment(self, control, evaluation):
        control_path = self.state / "trading" / "paper_experiment_control.json"
        evaluation_path = self.state / "trading" / "paper_strategy_evaluation.json"
        self.write_json(control_path, control)
        self.write_json(evaluation_path, evaluation)
        return control_path, evaluation_path

    def test_periodic_control_is_visible_without_an_improve_job_and_is_read_only(self):
        paths = self.write_experiment(*self.experiment_fixture())
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertFalse(result["paper_improve"]["present"])
        experiment = result["paper_experiment"]
        self.assertEqual(experiment["status"], "draining")
        self.assertEqual(experiment["reason_code"], "early-stop")
        self.assertIs(experiment["entries_allowed"], False)
        self.assertIs(experiment["pending_available"], True)
        self.assertEqual(experiment["open_position_count"], 2)
        evaluation = experiment["evaluation"]
        self.assertIs(evaluation["matches_active"], True)
        self.assertEqual(evaluation["closed_sells"], 8)
        self.assertEqual(evaluation["realized_pnl_jpy"], -95.25)
        self.assertEqual(evaluation["self_entry_realized_pnl_jpy"], -12.5)
        self.assertEqual(evaluation["carry_in_realized_pnl_jpy"], -82.75)
        self.assertEqual(evaluation["profit_factor"], 0.45)
        self.assertIs(evaluation["inventory_complete"], True)
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths])
        self.assertNotIn("SECRET", json.dumps(experiment))

    def test_evaluation_mismatch_during_activation_never_reports_old_results_as_current(self):
        for field, value in (("experiment_id", "another-experiment"), ("activated_at", 101.0)):
            with self.subTest(field=field):
                control, evaluation = self.experiment_fixture()
                evaluation[field] = value
                self.write_experiment(control, evaluation)
                result = self.module._collect_paper_experiment(self.state, self.now)["evaluation"]
                self.assertIs(result["matches_active"], False)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason_code"], "evaluation_identity_mismatch")
                self.assertIsNone(result["realized_pnl_jpy"])
                self.assertIsNone(result["closed_sells"])
                self.assertIsNone(result["promotion_ready"])
                self.assertNotIn("SECRET", json.dumps(result))

    def test_periodic_projection_rejects_malformed_scalars_without_coercion(self):
        control, evaluation = self.experiment_fixture()
        control.update(
            status="SECRET-STATUS", reason_code="SECRET-REASON", updated_at=float("nan"),
            entries_allowed="false", pending_available=1, open_position_count=True,
        )
        evaluation.update(
            evaluated_at="SECRET-TIME", closed_sells=True, carry_in_closed_sells=-1,
            ignored_unpaired_exits="0", open_position_count=10**100,
            realized_pnl_jpy="NaN", self_entry_realized_pnl_jpy="1;SECRET",
            carry_in_realized_pnl_jpy="1e999999", profit_factor="-1",
            max_realized_drawdown_pct=float("inf"), inventory_complete="true",
            promotion_ready=1,
        )
        self.write_experiment(control, evaluation)
        result = self.module._collect_paper_experiment(self.state, self.now)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason_code"], "unknown")
        for key in ("updated_at", "entries_allowed", "pending_available", "open_position_count"):
            self.assertIsNone(result[key], key)
        projected = result["evaluation"]
        for key in (
            "evaluated_at", "closed_sells", "carry_in_closed_sells", "ignored_unpaired_exits",
            "open_position_count", "realized_pnl_jpy", "self_entry_realized_pnl_jpy",
            "carry_in_realized_pnl_jpy", "profit_factor", "max_realized_drawdown_pct",
            "inventory_complete", "promotion_ready",
        ):
            self.assertIsNone(projected[key], key)
        self.assertNotIn("SECRET", json.dumps(result, allow_nan=False))

    def test_periodic_projection_handles_missing_and_corrupt_files_independently(self):
        result = self.module._collect_paper_experiment(self.state, self.now)
        self.assertEqual(result, {
            "present": False, "readable": False, "age_sec": -1,
            "evaluation": {"present": False, "readable": False, "age_sec": -1},
        })
        paths = self.write_experiment(*self.experiment_fixture())
        paths[0].write_text("{bad", encoding="utf-8")
        result = self.module._collect_paper_experiment(self.state, self.now)
        self.assertTrue(result["present"])
        self.assertFalse(result["readable"])
        self.assertTrue(result["evaluation"]["readable"])
        self.assertIsNone(result["evaluation"]["matches_active"])
        paths[1].write_text("[]", encoding="utf-8")
        result = self.module._collect_paper_experiment(self.state, self.now)
        self.assertTrue(result["evaluation"]["present"])
        self.assertFalse(result["evaluation"]["readable"])

    def test_periodic_projection_survives_gateway_sanitizing_in_a_small_envelope(self):
        self.write_experiment(*self.experiment_fixture())
        experiment = self.module._collect_paper_experiment(self.state, self.now)
        spec = importlib.util.spec_from_file_location(
            "paper_diag_gateway", ROOT / "ops" / "vm_actions" / "gateway.py"
        )
        gateway = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gateway)
        payload = {"status": "ok", "corners": {"paper_experiment": experiment}}
        clean = gateway._sanitize_diagnostics(payload)
        self.assertEqual(clean, payload)
        raw = self.module._diagnostics_budget(clean)
        self.assertLess(len(raw.encode("utf-8")), 2048)
        self.assertLess(len(raw.encode("utf-8")), gateway.DIAGNOSTICS_JSON_MAX)
        self.assertNotIn("SECRET", raw)


if __name__ == "__main__":
    unittest.main()
