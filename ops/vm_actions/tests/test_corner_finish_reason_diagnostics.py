"""Read-only projection of persisted NetHack finish reasons (#2579).

Use unittest so these checks run in the VM operations CI discovery step.
No game, runtime boundary, or production collector is invoked.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CornerFinishReasonTests(unittest.TestCase):
    def setUp(self):
        self.module = load_collector()

    def test_existing_producer_reasons_are_projected_without_reclassification(self):
        for reason in ("terminal", "stalled", "recover", "force_recover"):
            with self.subTest(reason=reason):
                projected = self.module._project_corner_state({"finish_reason": reason})
                self.assertEqual(projected["finish_reason"], reason)
                self.assertIsNone(projected["game_result"])
                self.assertIsNone(projected["end_reason"])

    def test_unknown_and_malformed_reasons_are_null_not_free_text(self):
        for reason in (None, True, 600, 607.9, [], {}, ["terminal"],
                       {"reason": "terminal"}, "", "TERMINAL", " terminal",
                       "terminal\n", "token=DO-NOT-PUBLISH", "x" * 10000):
            with self.subTest(reason_type=type(reason).__name__):
                projected = self.module._project_corner_state({"finish_reason": reason})
                self.assertIsNone(projected["finish_reason"])
                self.assertNotIn("DO-NOT-PUBLISH", json.dumps(projected))

    def test_legacy_state_does_not_infer_a_reason_from_status_or_elapsed_time(self):
        projected = self.module._project_corner_state({
            "status": "completed", "game": "nethack",
            "started_at": "2026-10-20T08:53:23.493163+09:00",
            "completed_at": "2026-10-20T09:03:31.379249+09:00",
            "screen_unchanged_seconds": 607.9,
        })
        self.assertIsNone(projected["finish_reason"])
        self.assertIsNone(projected["screen_readable"])
        self.assertEqual(projected["screen_unchanged_seconds"], 607.9)
        self.assertEqual(projected["status"], "completed")

    def test_finish_reason_does_not_fabricate_screen_unchanged_seconds(self):
        for reason in ("terminal", "stalled"):
            with self.subTest(reason=reason):
                projected = self.module._project_corner_state({"finish_reason": reason})
                self.assertIsNone(projected["screen_unchanged_seconds"])

    def test_boundary_observation_scalars_are_projected_without_inference(self):
        for reason, seconds, readable in (
            ("stalled", 600.0, True), ("stalled", 605, False),
            ("terminal", 0.0, True),
        ):
            with self.subTest(reason=reason, readable=readable):
                projected = self.module._project_corner_state({
                    "finish_reason": reason, "screen_unchanged_seconds": seconds,
                    "screen_readable": readable,
                })
                self.assertEqual(projected["finish_reason"], reason)
                self.assertEqual(projected["screen_unchanged_seconds"], seconds)
                self.assertIs(projected["screen_readable"], readable)

    def test_screen_readability_requires_an_exact_boolean(self):
        for value in (None, 0, 1, "true", "false", [], {}, "token=DO-NOT-PUBLISH"):
            with self.subTest(value_type=type(value).__name__):
                projected = self.module._project_corner_state({"screen_readable": value})
                self.assertIsNone(projected["screen_readable"])
                self.assertNotIn("DO-NOT-PUBLISH", json.dumps(projected))

    def test_unchanged_seconds_requires_a_finite_nonnegative_number(self):
        for value in (None, True, False, -1, -0.1, float("nan"), float("inf"),
                      -float("inf"), "600", [], {}):
            with self.subTest(value_type=type(value).__name__):
                projected = self.module._project_corner_state({
                    "screen_unchanged_seconds": value,
                })
                self.assertIsNone(projected["screen_unchanged_seconds"])

    def test_automatic_and_manual_state_files_use_the_shared_read_only_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            files = {}
            for name, reason in (("nethack_corner", "stalled"),
                                 ("nethack_corner_manual", "terminal")):
                path = state_dir / (name + ".json")
                path.write_text(json.dumps({
                    "status": "completed", "game": "nethack", "finish_reason": reason,
                    "screen_unchanged_seconds": 600.0 if reason == "stalled" else 0.0,
                    "screen_readable": reason == "terminal",
                }), encoding="utf-8")
                files[path] = (path.read_bytes(), path.stat().st_mtime_ns)
            output = {}
            with mock.patch.object(self.module, "_collect_game_switch_watchdog", return_value={}), \
                    mock.patch.object(self.module, "_collect_corner_rotation_timer_alias", return_value={}), \
                    mock.patch.object(self.module.subprocess, "run", side_effect=AssertionError("no runtime calls")):
                self.module._collect_corner_files(state_dir, output, 1792471283)
            self.assertEqual(output["nethack_corner"]["finish_reason"], "stalled")
            self.assertEqual(output["nethack_corner_manual"]["finish_reason"], "terminal")
            self.assertEqual(output["nethack_corner"]["screen_unchanged_seconds"], 600.0)
            self.assertIs(output["nethack_corner"]["screen_readable"], False)
            self.assertEqual(output["nethack_corner_manual"]["screen_unchanged_seconds"], 0.0)
            self.assertIs(output["nethack_corner_manual"]["screen_readable"], True)
            for name in ("nethack_corner", "nethack_corner_manual"):
                self.assertTrue(output[name]["present"])
                self.assertTrue(output[name]["readable"])
            for path, before in files.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(set(state_dir.iterdir()), set(files))


if __name__ == "__main__":
    unittest.main()
