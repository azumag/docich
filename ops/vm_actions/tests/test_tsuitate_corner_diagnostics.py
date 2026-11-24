"""Read-only, fixed-vocabulary Tsuitate failure evidence (docich#1992)."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]


class TsuitateCornerDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "tsuitate_diagnostics_collector",
            ROOT / "ops/vm_actions/collect_diagnostics.py",
        )
        cls.collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.collector)

    def test_persisted_switch_failure_is_visible_without_mutating_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_dir = root / "run"
            state_dir.mkdir()
            path = state_dir / "tsuitate_corner.json"
            path.write_text(json.dumps({
                "status": "failed", "game": "tsuitate-view",
                "last_error_code": "readiness_timeout",
                "last_error": "viewer failed token=DO-NOT-PUBLISH-TOKEN",
                "start_request_id": "DO-NOT-PUBLISH-REQUEST",
                "runtime_id": "DO-NOT-PUBLISH-RUNTIME",
            }), encoding="utf-8")
            before = path.read_bytes()
            with mock.patch.object(self.collector.os, "kill", side_effect=AssertionError(
                    "diagnostics must not signal a process")):
                result = self.collector._collect_programs(state_dir, root / "soren", 100)
            entry = result["tsuitate_corner"]
            self.assertTrue(entry["present"])
            self.assertTrue(entry["readable"])
            self.assertEqual(entry["switch_error_code"], "readiness_timeout")
            # The legacy field still classifies only recovery-required state.
            self.assertIsNone(entry["last_error_code"])
            self.assertFalse(entry["recovery_required"])
            self.assertIn("[REDACTED]", entry["last_error"])
            self.assertLessEqual(len(entry["last_error"]), 200)
            self.assertNotIn("DO-NOT-PUBLISH", json.dumps(result))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in state_dir.iterdir()), [path.name])

    def test_only_fixed_producer_codes_are_emitted(self):
        # GameSwitch ERROR_* tokens, the Tsuitate fallback, and ControlError's
        # fixed local failures. No dynamic error messages become new codes.
        codes = {
            "busy", "queued", "request_conflict", "invalid_game", "already_active",
            "no_active_game", "invalid_rotation", "prepare_failed", "quiesce_failed",
            "round_boundary_unsupported", "start_failed", "readiness_timeout",
            "agent_start_failed", "rollback_failed", "state_corrupt",
            "recovery_required", "probe_failed", "timeout", "internal",
            "source_fence_lost", "switch_failed", "control_unavailable",
            "control_not_configured", "invalid_beta_operation",
            "token_not_configured", "run_locked", "run_mismatch",
            "control_authentication_failed", "control_timeout", "terminal_unconfirmed",
            "recovery_not_available", "recovery_checkpoint_invalid",
            "terminal_result_unavailable", "terminal_storage_failure",
        }
        for code in sorted(codes):
            with self.subTest(code=code):
                data = {"status": "failed", "last_error_code": code}
                before = data.copy()
                result = self.collector._project_tsuitate_corner(data)
                self.assertEqual(result["switch_error_code"], code)
                self.assertEqual(result["last_error_code"],
                                 "recovery_required" if code == "recovery_required" else None)
                self.assertEqual(result["recovery_required"], code == "recovery_required")
                self.assertEqual(data, before)

    def test_fixed_vocabulary_matches_existing_producer_declarations(self):
        # Parse declarations without importing lifecycle or control modules.
        switch = ast.parse((ROOT / "src/docich/game_switch.py").read_text(encoding="utf-8"))
        switch_codes = {
            ast.literal_eval(node.value)
            for node in switch.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id.startswith("ERROR_")
                    for target in node.targets)
        }
        control = ast.parse((ROOT / "src/docich/tsuitate_beta_control.py").read_text(
            encoding="utf-8"))
        control_codes = next(
            ast.literal_eval(node.value)
            for node in control.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "ERRORS"
                    for target in node.targets)
        )
        self.assertEqual(
            self.collector._TSUITATE_SWITCH_ERROR_CODES,
            switch_codes | control_codes | {"switch_failed", "invalid_beta_operation"},
        )

    def test_unknown_wrong_type_and_secret_like_codes_are_not_echoed(self):
        values = [None, True, False, 1, 1.5, [], {}, ["readiness_timeout"],
                  {"code": "readiness_timeout"}, "", "READINESS_TIMEOUT",
                  "readiness_timeout ", "readiness_timeout\n", "x" * 100000,
                  "token=DO-NOT-PUBLISH", "/private/DO-NOT-PUBLISH",
                  "https://example.invalid/?key=DO-NOT-PUBLISH",
                  "unknown_but_token_shaped_secret"]
        for value in values:
            with self.subTest(value=repr(value)[:80]):
                result = self.collector._project_tsuitate_corner({
                    "status": "failed", "last_error_code": value,
                })
                self.assertIsNone(result["switch_error_code"])
                self.assertNotIn("DO-NOT-PUBLISH", json.dumps(result))
                self.assertLess(len(json.dumps(result)), 2500)

    def test_other_corner_projection_contracts_do_not_expand(self):
        data = {"status": "failed", "last_error_code": "readiness_timeout"}
        generic = self.collector._project_corner_state(data)
        self.assertNotIn("switch_error_code", generic)
        self.assertIsNone(generic["last_error_code"])
        soren = self.collector._project_soren91_corner(data)
        self.assertEqual(soren["switch_error_code"], "readiness_timeout")

    def test_absent_or_unreadable_state_does_not_invent_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_dir = root / "run"
            state_dir.mkdir()
            result = self.collector._collect_programs(state_dir, root / "soren", 100)
            self.assertEqual(result["tsuitate_corner"], {"present": False, "readable": False})
            (state_dir / "tsuitate_corner.json").write_text(
                "{DO-NOT-PUBLISH", encoding="utf-8")
            result = self.collector._collect_programs(state_dir, root / "soren", 100)
            self.assertEqual(result["tsuitate_corner"], {"present": True, "readable": False})
            self.assertNotIn("DO-NOT-PUBLISH", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
