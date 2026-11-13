from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from ops.vm_actions import realign_hanjuku_presentation as mod


class HanjukuPresentationRealignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "run-soren-live/runtimes/g7-abcdef12").mkdir(parents=True)
        (self.root / "config/docich.soren-live.toml").write_text(
            "[display]\nnumber=99\nviewport_x=0\nviewport_y=90\n"
            "viewport_width=960\nviewport_height=540\n", encoding="utf-8")
        self.active = {
            "game": "hanjuku-hero", "runtime_id": "g7-abcdef12",
            "generation": 7, "lease_id": "lease",
        }
        self.write_json(self.root / "run-soren-live/game_switch.json",
                        {"phase": "ready", "active": self.active})
        self.presentation = self.root / "run-soren-live/runtimes/g7-abcdef12/presentation.json"
        self.write_json(self.presentation, {
            "status": "ready",
            "projection": {
                "align": "left", "viewport": [0, 90, 960, 540],
                "content": [0, 0, 721, 540],
            },
            "display": ":101", "window": "123", "width": 299, "height": 224,
            "groups": [10, 11, 12],
        })

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")

    def test_moves_only_projection_window_and_flips_gap_left(self):
        geometries = [
            {"X": 0, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
            {"X": 239, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
        ]
        with patch.object(mod, "_window_ids", return_value=["555"]),              patch.object(mod, "_geometry", side_effect=geometries),              patch.object(mod, "_move") as move:
            result = mod.realign(self.root)
        self.assertEqual(result["status"], "realigned")
        move.assert_called_once_with(":99", "555", 239, 90)
        state = json.loads(self.presentation.read_text())
        self.assertEqual(state["projection"]["align"], "right")
        self.assertEqual(state["projection"]["content"], [239, 0, 721, 540])
        self.assertEqual(state["display"], ":101")
        self.assertEqual(state["groups"], [10, 11, 12])

    def test_already_right_is_read_only(self):
        state = json.loads(self.presentation.read_text())
        state["projection"]["align"] = "right"
        state["projection"]["content"][0] = 239
        self.write_json(self.presentation, state)
        with patch.object(mod, "_window_ids", return_value=["555"]),              patch.object(mod, "_geometry",
                          return_value={"X": 239, "Y": 90, "WIDTH": 721, "HEIGHT": 540}),              patch.object(mod, "_move") as move:
            result = mod.realign(self.root)
        self.assertEqual(result["status"], "already-right")
        move.assert_not_called()

    def test_stopped_presentation_skips_even_if_switch_still_active(self):
        # A failed handover may leave the canonical switch on Hanjuku after
        # the presenter has stopped. No projection window exists to realign.
        self.write_json(self.presentation, {"status": "stopped"})
        before = self.presentation.read_bytes()
        with patch.object(mod, "_window_ids") as windows, \
             patch.object(mod, "_move") as move:
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_NOT_ACTIVE)
        self.assertEqual(self.presentation.read_bytes(), before)
        windows.assert_not_called()
        move.assert_not_called()

    def test_other_nonready_or_malformed_projection_stays_invalid(self):
        for state in ({"status": "starting"},
                      {"status": "presentation_failed"},
                      {"status": "ready"}):
            with self.subTest(state=state):
                self.write_json(self.presentation, state)
                with patch.object(mod, "_window_ids") as windows, \
                     patch.object(mod, "_move") as move:
                    with self.assertRaises(mod.RealignError) as raised:
                        mod.realign(self.root)
                self.assertEqual(raised.exception.code, mod.EXIT_INVALID_STATE)
                windows.assert_not_called()
                move.assert_not_called()

    def test_center_or_ambiguous_window_refuses_without_move(self):
        state = json.loads(self.presentation.read_text())
        state["projection"]["align"] = "center"
        self.write_json(self.presentation, state)
        with patch.object(mod, "_move") as move:
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_UNSUPPORTED_ALIGNMENT)
        move.assert_not_called()

        state["projection"]["align"] = "left"
        self.write_json(self.presentation, state)
        with patch.object(mod, "_window_ids", return_value=["1", "2"]),              patch.object(mod, "_move") as move:
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_WINDOW_UNAVAILABLE)
        move.assert_not_called()

    def test_geometry_mismatch_refuses_without_move(self):
        with patch.object(mod, "_window_ids", return_value=["555"]),              patch.object(mod, "_geometry",
                          return_value={"X": 1, "Y": 90, "WIDTH": 721, "HEIGHT": 540}),              patch.object(mod, "_move") as move:
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_GEOMETRY_MISMATCH)
        move.assert_not_called()

    def test_state_write_failure_rolls_window_back(self):
        geometry = [
            {"X": 0, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
            {"X": 239, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
        ]
        calls = []
        with patch.object(mod, "_window_ids", return_value=["555"]), \
             patch.object(mod, "_geometry", side_effect=geometry), \
             patch.object(mod, "_move", side_effect=lambda d, w, x, y: calls.append((d, w, x, y))), \
             patch.object(mod, "_atomic_json", side_effect=OSError("write")):
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_WRITE_FAILED)
        self.assertEqual(calls[-1], (":99", "555", 0, 90))

    def test_production_hook_retries_idempotent_realign_after_every_deploy(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/vm-operations.yml").read_text()
        block = workflow.split(
            "- name: Realign active Hanjuku projection after production deploy", 1
        )[1].split("- name:", 1)[0]
        self.assertNotIn("BEFORE_SHA", block)
        self.assertNotIn("hanjuku_presentation_realign_epoch", block)
        self.assertIn("realign_hanjuku_presentation.sh", block)
        self.assertIn("realign_hanjuku_presentation.py", block)
        self.assertIn("steps.deploy_initial.outcome == 'success'", block)
        self.assertIn("steps.deploy_retry.outcome == 'success'", block)

    def test_decorated_window_undecorated_once_then_proceeds(self):
        # The incident in #4051: after the :99 recreation the WM decorated
        # the ffplay window, so the client area sat at X=5/Y=119 (frame
        # extents 5,5,29,5) instead of the expected X=0/Y=90. Stripping the
        # decoration once must let the realign proceed.
        geometries = [
            {"X": 5, "Y": 119, "WIDTH": 721, "HEIGHT": 540},
            {"X": 0, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
            {"X": 239, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
        ]
        with patch.object(mod, "_window_ids", return_value=["555"]), \
             patch.object(mod, "_geometry", side_effect=geometries), \
             patch.object(mod, "_frame_extents", return_value=(5, 5, 29, 5)), \
             patch.object(mod, "_undecorate") as undecorate, \
             patch.object(mod, "_move") as move:
            result = mod.realign(self.root)
        self.assertEqual(result["status"], "realigned")
        undecorate.assert_called_once_with(":99", "555", 0, 90)
        move.assert_called_once_with(":99", "555", 239, 90)

    def test_mismatch_without_frame_extents_stays_fail_closed(self):
        for extents in ((0, 0, 0, 0), None):
            with self.subTest(extents=extents):
                with patch.object(mod, "_window_ids", return_value=["555"]), \
                     patch.object(mod, "_geometry",
                                  return_value={"X": 1, "Y": 90, "WIDTH": 721, "HEIGHT": 540}), \
                     patch.object(mod, "_frame_extents", return_value=extents), \
                     patch.object(mod, "_undecorate") as undecorate, \
                     patch.object(mod, "_move") as move:
                    with self.assertRaises(mod.RealignError) as raised:
                        mod.realign(self.root)
                self.assertEqual(raised.exception.code, mod.EXIT_GEOMETRY_MISMATCH)
                undecorate.assert_not_called()
                move.assert_not_called()

    def test_undecorate_failure_stays_fail_closed(self):
        with patch.object(mod, "_window_ids", return_value=["555"]), \
             patch.object(mod, "_geometry",
                          return_value={"X": 5, "Y": 119, "WIDTH": 721, "HEIGHT": 540}), \
             patch.object(mod, "_frame_extents", return_value=(5, 5, 29, 5)), \
             patch.object(mod, "_undecorate",
                          side_effect=mod.RealignError(mod.EXIT_GEOMETRY_MISMATCH)), \
             patch.object(mod, "_move") as move:
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_GEOMETRY_MISMATCH)
        move.assert_not_called()

    def test_still_mismatched_after_undecorate_stays_fail_closed(self):
        geometries = [
            {"X": 5, "Y": 119, "WIDTH": 721, "HEIGHT": 540},
            {"X": 5, "Y": 119, "WIDTH": 721, "HEIGHT": 540},
        ]
        with patch.object(mod, "_window_ids", return_value=["555"]), \
             patch.object(mod, "_geometry", side_effect=geometries), \
             patch.object(mod, "_frame_extents", return_value=(5, 5, 29, 5)), \
             patch.object(mod, "_undecorate") as undecorate, \
             patch.object(mod, "_move") as move:
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_GEOMETRY_MISMATCH)
        undecorate.assert_called_once_with(":99", "555", 0, 90)
        move.assert_not_called()

    def test_frame_extents_parses_xprop_output(self):
        import subprocess
        with patch("subprocess.check_output",
                   return_value="_NET_FRAME_EXTENTS(CARDINAL) = 5, 5, 29, 5\n"):
            self.assertEqual(mod._frame_extents(":99", "555"), (5, 5, 29, 5))
        with patch("subprocess.check_output",
                   side_effect=subprocess.CalledProcessError(1, "xprop")):
            self.assertIsNone(mod._frame_extents(":99", "555"))
        with patch("subprocess.check_output", return_value="no extents here"):
            self.assertIsNone(mod._frame_extents(":99", "555"))

    def test_undecorate_runs_hint_unmap_map_move_in_order(self):
        import subprocess
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch("time.sleep") as sleep:
            mod._undecorate(":99", "555", 0, 90)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][:6],
                         ["xprop", "-id", "555", "-f", "_MOTIF_WM_HINTS", "32c"])
        self.assertIn("2, 0, 0, 0, 0", calls[0])
        self.assertEqual(calls[1], ["xdotool", "windowunmap", "555"])
        self.assertEqual(calls[2], ["xdotool", "windowmap", "555"])
        self.assertEqual(calls[3],
                         ["xdotool", "windowmove", "--sync", "555", "0", "90"])
        sleep.assert_called_once_with(0.2)

    def test_undecorate_failure_is_geometry_mismatch(self):
        import subprocess
        with patch("subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "xprop")):
            with self.assertRaises(mod.RealignError) as raised:
                mod._undecorate(":99", "555", 0, 90)
        self.assertEqual(raised.exception.code, mod.EXIT_GEOMETRY_MISMATCH)

    def test_runtime_change_after_move_rolls_window_back_and_keeps_state(self):
        original = self.presentation.read_bytes()
        geometry = [
            {"X": 0, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
            {"X": 239, "Y": 90, "WIDTH": 721, "HEIGHT": 540},
        ]
        calls = []
        def moved(display, window, x, y):
            calls.append((display, window, x, y))
            if x == 239:
                self.write_json(self.root / "run-soren-live/game_switch.json",
                                {"phase": "ready", "active": {**self.active, "generation": 8,
                                                              "runtime_id": "g8-deadbeef"}})
        with patch.object(mod, "_window_ids", return_value=["555"]),              patch.object(mod, "_geometry", side_effect=geometry),              patch.object(mod, "_move", side_effect=moved):
            with self.assertRaises(mod.RealignError) as raised:
                mod.realign(self.root)
        self.assertEqual(raised.exception.code, mod.EXIT_CONTEXT_CHANGED)
        self.assertEqual(calls[-1], (":99", "555", 0, 90))
        self.assertEqual(self.presentation.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
