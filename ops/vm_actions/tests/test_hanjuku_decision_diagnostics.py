import ast
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def _source_screen_kinds():
    """Re-derive the screen-kind vocabulary the runtime can actually emit."""
    kinds = set()
    for path in (ROOT / "src" / "docich" / "hanjuku_screen.py",
                 ROOT / "src" / "docich" / "hanjuku_bot.py",
                 ROOT / "src" / "docich" / "hanjuku_policy.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "classify_text"):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Return)
                            and isinstance(inner.value, ast.Constant)
                            and isinstance(inner.value.value, str)):
                        kinds.add(inner.value.value)
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Attribute) and target.attr == "kind"
                    and isinstance(target.value, ast.Name) and target.value.id == "screen"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                continue
            kinds.add(node.value.value)
    return kinds


class HanjukuDecisionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hanjuku-decision-diagnostics-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        spec = importlib.util.spec_from_file_location("decision_diagnostics", COLLECTOR)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.identity = dict(game="hanjuku-hero", runtime_id="g574-0123abcd",
                             generation=574, lease_id="PRIVATE_LEASE")
        self.runtime = self.root / "runtimes" / self.identity["runtime_id"]
        self.runtime.mkdir(parents=True)
        self.canonical = self.root / "game_switch.json"
        self.canonical.write_text(json.dumps(dict(phase="ready", active=self.identity)))
        self.run = self.runtime / "hanjuku_run.json"
        self.run.write_text(json.dumps({**self.identity, "playing": True}))
        self.log = self.runtime / "hanjuku_decisions.jsonl"

    def plan(self, screen_kind="month_menu", buttons=("a",), at=99.0, **overrides):
        fields = dict(
            schema=1, **self.identity, at=at, event="action_plan",
            screen_kind=screen_kind,
            planned_actions=[{"type": "pad", "buttons": list(buttons), "hold_ms": 100}],
            chart_step="PRIVATE_CHART_STEP", reason_decisions=["PRIVATE_DECISION"],
            decision_id="PRIVATE_DECISION_ID", frame_sha256="PRIVATE_FRAME",
            strategy_variant="chart_adjusted",
            observed_metric={"general": "PRIVATE_GENERAL"},
        )
        fields.update(overrides)
        return fields

    def decision(self, **overrides):
        fields = dict(schema=1, **self.identity, at=99.0, event="decision",
                      decision="PRIVATE_DECISION", reason="PRIVATE_REASON",
                      chart_step="PRIVATE_CHART_STEP")
        fields.update(overrides)
        return fields

    def write(self, rows):
        self.log.write_text("".join(json.dumps(row) + "\n" for row in rows),
                            encoding="utf-8")
        return self.log

    def collect(self, now=100):
        return self.module._collect_hanjuku_decision_plans(self.root, now)

    def test_repeated_operation_is_projected_without_private_tokens(self):
        self.write([self.plan("month_menu", ("b",), at=90),
                    self.plan("text", ("b",), at=95),
                    self.plan("text", ("b",), at=96),
                    self.plan("text", ("b",), at=97),
                    self.decision(at=98),
                    self.plan("text", ("b",), at=99)])
        before = {path.name: path.read_bytes() for path in self.runtime.iterdir()}

        output = self.collect()

        self.assertEqual(output["status"], "available")
        self.assertEqual(output["matched_records"], 6)
        self.assertEqual(output["rejected_records"], 0)
        self.assertEqual(output["plan_records"], 5)
        self.assertEqual(output["window_plans"], 5)
        self.assertEqual(output["distinct_signatures"], 2)
        self.assertEqual(output["distinct_screen_kinds"], 2)
        self.assertEqual(output["screen_kind_counts"], {"text": 4, "month_menu": 1})
        self.assertEqual(output["button_counts"]["b"], 5)
        self.assertEqual(output["button_counts"]["a"], 0)
        self.assertEqual(output["top_signature"],
                         {"screen_kind": "text", "buttons": ["b"], "count": 4})
        self.assertEqual(output["trailing_repeat"],
                         {"period": 1, "repeats": 4,
                          "signatures": [{"screen_kind": "text", "buttons": ["b"]}]})
        self.assertEqual(output["last_plan_age_sec"], 1)
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertNotIn(self.identity["runtime_id"], json.dumps(output))
        self.assertEqual(before, {path.name: path.read_bytes()
                                  for path in self.runtime.iterdir()})

    def test_alternating_actions_are_reported_as_a_two_step_cycle(self):
        self.write([self.plan("text", ("b",) if index % 2 else ("a",), at=99)
                    for index in range(8)])
        output = self.collect()
        self.assertEqual(output["distinct_signatures"], 2)
        self.assertEqual(output["top_signature"],
                         {"screen_kind": "text", "buttons": ["a"], "count": 4})
        self.assertEqual(output["trailing_repeat"],
                         {"period": 2, "repeats": 4,
                          "signatures": [{"screen_kind": "text", "buttons": ["a"]},
                                         {"screen_kind": "text", "buttons": ["b"]}]})

    def test_a_plan_that_sends_nothing_is_not_reported_as_an_operation(self):
        self.write([self.plan("unknown", (), at=99, planned_actions=[])] * 3
                   + [self.plan("text", ("a",), at=99)])
        output = self.collect()
        self.assertEqual(output["top_signature"],
                         {"screen_kind": "unknown", "buttons": [], "count": 3})
        self.assertEqual(output["trailing_repeat"],
                         {"period": None, "repeats": 1,
                          "signatures": [{"screen_kind": "text", "buttons": ["a"]}]})
        self.assertEqual(output["button_counts"]["a"], 1)

    def test_unlisted_kind_button_and_shape_fail_closed(self):
        self.write([
            self.plan("PRIVATE_KIND", ("PRIVATE_BUTTON",), at=90),
            self.plan("month_menu", (), at=91, planned_actions="PRIVATE_ACTIONS"),
            self.plan("month_menu", ("a",), at=92,
                      planned_actions=[{"type": "PRIVATE_TYPE", "buttons": ["a"]}]),
            self.plan("month_menu", ("a",), at=93,
                      planned_actions=[{"type": "pad", "buttons": ["a"] * 9}]),
            self.plan("month_menu", ("a",), at=94,
                      planned_actions=[{"type": "pad", "buttons": ["a"]}] * 5),
            self.plan(True, ("a",), at=95),
        ])
        output = self.collect()
        self.assertEqual(output["status"], "available")
        self.assertEqual(output["screen_kind_counts"],
                         {"other": 2, "month_menu": 4})
        self.assertEqual(output["top_signature"],
                         {"screen_kind": "month_menu", "buttons": None, "count": 4})
        self.assertEqual(output["trailing_repeat"],
                         {"period": None, "repeats": 1,
                          "signatures": [{"screen_kind": "other", "buttons": ["a"]}]})
        self.assertEqual(output["button_counts"]["other"], 1)
        self.assertNotIn("PRIVATE", json.dumps(output))

    def test_foreign_and_malformed_rows_never_count_as_current_plans(self):
        self.write([
            self.plan(lease_id="PRIVATE_OTHER_LEASE", at=99),
            self.plan(generation=True, at=99),
            self.plan(event="PRIVATE_EVENT", at=99),
            self.plan(schema=True, at=99),
            self.plan(at=float("nan")),
            self.plan(at=101),
            self.plan("month_menu", ("b",), at=99),
        ])
        with self.log.open("a") as stream:
            stream.write("not json\n")
        output = self.collect()
        self.assertEqual(output["status"], "partial")
        self.assertEqual(output["matched_records"], 1)
        self.assertEqual(output["rejected_records"], 7)
        self.assertEqual(output["plan_records"], 1)
        self.assertEqual(output["trailing_repeat"],
                         {"period": None, "repeats": 1,
                          "signatures": [{"screen_kind": "month_menu", "buttons": ["b"]}]})
        self.assertNotIn("PRIVATE", json.dumps(output))

    def test_only_malformed_rows_do_not_report_zero_repeats(self):
        self.write([self.plan(lease_id="PRIVATE_OTHER_LEASE")])
        output = self.collect()
        self.assertEqual(output["status"], "unavailable")
        self.assertIsNone(output["window_plans"])
        self.assertIsNone(output["trailing_repeat"])
        self.assertIsNone(output["screen_kind_counts"])

    def test_missing_and_empty_sources_report_unknown_not_health(self):
        output = self.collect()
        self.assertEqual(output["status"], "unavailable")
        self.assertIsNone(output["sampled_bytes"])
        self.assertIsNone(output["plan_records"])
        self.write([])
        output = self.collect()
        self.assertEqual(output["status"], "unavailable")
        self.assertEqual(output["sampled_lines"], 0)
        self.assertIsNone(output["trailing_repeat"])

    def test_bounded_tail_and_window_are_not_full_history(self):
        self.write([])
        self.log.write_text("padding\n" * 20000 + json.dumps(self.plan("text", ("b",))) + "\n")
        output = self.collect()
        self.assertIs(output["tail_truncated"], True)
        self.assertLessEqual(output["sampled_bytes"], self.module.HANJUKU_DECISION_LOG_MAX_BYTES)
        self.assertLessEqual(output["sampled_lines"], self.module.HANJUKU_DECISION_LOG_MAX_LINES)
        self.assertEqual(output["plan_records"], 1)
        self.log.write_text("x" * (self.module.HANJUKU_DECISION_LOG_MAX_BYTES + 10))
        output = self.collect()
        self.assertIs(output["tail_truncated"], True)
        self.assertIsNone(output["plan_records"])
        self.write([self.plan("text", ("a",), at=99)] * 200)
        output = self.collect()
        self.assertEqual(output["plan_records"], 200)
        self.assertEqual(output["window_plans"], self.module.HANJUKU_DECISION_WINDOW_PLANS)
        self.assertEqual(output["trailing_repeat"],
                         {"period": 1, "repeats": 128,
                          "signatures": [{"screen_kind": "text", "buttons": ["a"]}]})

    def test_line_limit_omits_older_plans_even_when_byte_limit_allows_them(self):
        self.write([self.plan("battle", ("y",), at=99)] + [self.plan("month_menu", ("a",), at=99)] * 1099)
        with mock.patch.object(self.module, "HANJUKU_DECISION_LOG_MAX_BYTES", 4 * 1024 * 1024):
            output = self.collect()
        self.assertIs(output["tail_truncated"], True)
        self.assertEqual(output["sampled_lines"], 1024)
        self.assertEqual(output["plan_records"], 1024)
        self.assertEqual(output["screen_kind_counts"], {"month_menu": 128})
        self.assertEqual(output["button_counts"]["y"], 0)

    def test_gate_requires_the_active_playing_hanjuku_runtime(self):
        self.write([self.plan()])
        for change in ({"playing": False}, {"terminal_reason": "game_over"},
                       {"terminal_candidate": "game_over"}, {"generation": 573}):
            with self.subTest(change=change):
                self.run.write_text(json.dumps({**self.identity, "playing": True, **change}))
                self.assertEqual(self.collect()["status"], "unavailable")
        self.run.write_text(json.dumps({**self.identity, "playing": True}))
        self.canonical.write_text(json.dumps(dict(phase="draining", active=self.identity)))
        self.assertEqual(self.collect()["status"], "unavailable")
        self.canonical.write_text(json.dumps(dict(
            phase="ready", active={**self.identity, "runtime_id": "../PRIVATE_PATH"})))
        self.assertEqual(self.collect()["status"], "unavailable")

    def test_bad_clock_and_generation_change_during_read_discard_the_projection(self):
        self.write([self.plan()])
        for now in (float("nan"), float("inf"), -1, True):
            with self.subTest(now=now):
                self.assertEqual(self.collect(now)["status"], "unavailable")
        original = self.module._read_hanjuku_scene_record
        calls = 0

        def read(path):
            nonlocal calls
            data = original(path)
            if path.name == "game_switch.json":
                calls += 1
                if calls == 2:
                    data["active"]["generation"] = 575
            return data

        with mock.patch.object(self.module, "_read_hanjuku_scene_record", side_effect=read):
            output = self.collect()
        self.assertEqual(output["status"], "identity_changed")
        self.assertIsNone(output["trailing_repeat"])
        self.assertIsNone(output["plan_records"])

    def test_symlink_fifo_and_unsafe_runtime_directory_are_rejected(self):
        self.write([self.plan()])
        data = self.log.read_bytes()
        self.log.unlink()
        os.mkfifo(self.log)
        self.assertEqual(self.collect()["status"], "unavailable")
        self.log.unlink()
        self.log.symlink_to(self.run)
        self.assertEqual(self.collect()["status"], "unavailable")
        self.log.unlink()
        self.log.write_bytes(data)
        moved = self.runtime.with_name("private-moved-runtime")
        self.runtime.rename(moved)
        self.runtime.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.collect()["status"], "unavailable")

    def test_programs_project_decision_plans_only_for_the_active_hanjuku_corner(self):
        self.write([self.plan("text", ("b",))])

        def collect_corner(_state_dir, payload, _now):
            payload["game_switch"] = {"active_game": "hanjuku-hero"}
            payload["retro_corner"] = {"readable": True, "status": "active",
                                       "game": "hanjuku-hero"}

        with ExitStack() as stack:
            for name, result in {
                "_rotation_timer_selection": ("docich-corner-rotation.timer", False),
                "_unit_is_active": True, "_unit_is_enabled": True,
                "_collect_boundary": {}, "_collect_ab": {}, "_collect_soren_game": {},
                "_collect_hanjuku_predictions": {}, "_collect_hanjuku_narration_playback": {},
                "_collect_hanjuku_scene_narration": {},
            }.items():
                stack.enter_context(mock.patch.object(self.module, name, return_value=result))
            stack.enter_context(mock.patch.object(self.module, "_collect_corner_files",
                                                   side_effect=collect_corner))
            output = self.module._collect_programs(self.root, self.root, 100)
        self.assertEqual(output["retro_corner"]["decision_plans"]["status"], "available")

        def collect_other(_state_dir, payload, _now):
            payload["game_switch"] = {"active_game": "gnurobots"}
            payload["retro_corner"] = {"readable": True, "status": "active",
                                       "game": "gnurobots"}

        with ExitStack() as stack:
            for name, result in {
                "_rotation_timer_selection": ("docich-corner-rotation.timer", False),
                "_unit_is_active": True, "_unit_is_enabled": True,
                "_collect_boundary": {}, "_collect_ab": {}, "_collect_soren_game": {},
                "_collect_hanjuku_predictions": {}, "_collect_hanjuku_narration_playback": {},
                "_collect_hanjuku_scene_narration": {},
            }.items():
                stack.enter_context(mock.patch.object(self.module, name, return_value=result))
            stack.enter_context(mock.patch.object(self.module, "_collect_corner_files",
                                                   side_effect=collect_other))
            output = self.module._collect_programs(self.root, self.root, 100)
        self.assertNotIn("decision_plans", output["retro_corner"])

    def test_repeat_detail_is_omitted_before_tactical_evidence_at_output_limit(self):
        payload = {
            "corners": {"retro_corner": {"decision_plans": {"large": "x" * 2000}}},
            "hanjuku_tactical": {"status": "ok", "chapter": 1},
            "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
            "ai": {"recent_events": [], "anomalous_components": {}},
            "workers": {"details": {}}, "soren91_drop_profile": {"profileStatus": "missing"},
        }
        with mock.patch.object(self.module, "MAX_JSON_BYTES", 600):
            text = self.module._diagnostics_budget(payload)
        self.assertLessEqual(len(text.encode()), 600)
        self.assertEqual(payload["hanjuku_tactical"]["status"], "ok")
        self.assertEqual(payload["corners"]["retro_corner"]["decision_plans"],
                         {"status": "output_omitted"})

    def test_screen_kind_allowlist_covers_the_runtime_vocabulary(self):
        kinds = _source_screen_kinds()
        self.assertGreaterEqual(len(kinds), 30)
        self.assertTrue(kinds <= self.module.HANJUKU_DECISION_SCREEN_KINDS,
                        msg=f"unlisted screen kinds: {sorted(kinds - self.module.HANJUKU_DECISION_SCREEN_KINDS)}")


if __name__ == "__main__":
    unittest.main()