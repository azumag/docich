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

    def test_production_hook_is_epoch_gated(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/vm-operations.yml").read_text()
        block = workflow.split(
            "- name: Realign active Hanjuku projection for reviewed runtime epoch", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("hanjuku_presentation_realign_epoch", block)
        self.assertIn("realign_hanjuku_presentation.sh", block)
        self.assertIn("realign_hanjuku_presentation.py", block)

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
