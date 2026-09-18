import json
import subprocess
import sys
import unittest
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1] / "brains" / "nsnake" / "brain.py"


def _pane(rows: list[str], extra: str = "") -> str:
    lines = ["lnsnake 3.0.0qqqqqqqqqqqqArcade Modek"]
    lines.append("x" + "a" * 78 + "x")
    for r in rows:
        lines.append("x" + "a" + r.ljust(76) + "a" + "x" + extra)
    lines.append("x" + "a" * 78 + "x")
    lines.append("xHi-Score 0                Score 0                   Speed 1                   x")
    return "\n".join(lines)


def _decide(text: str) -> tuple[int, list]:
    obs = {"game": "nsnake", "text": text}
    proc = subprocess.run(
        [sys.executable, str(BRAIN)],
        input=json.dumps(obs),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.returncode, json.loads(proc.stdout)["actions"]


class NsnakeBrainTest(unittest.TestCase):
    def test_moves_toward_food(self):
        rows = ["", "  oo@  ", "", "", "", "", "                                   $"]
        _, actions = _decide(_pane(rows))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["keys"], ["Down"])

    def test_never_reverses_into_neck(self):
        # head facing right with neck on the left is fine, but a food setup
        # that would require moving back into the neck must pick another way
        rows = ["", "  o@o  ", "                                   $"]
        _, actions = _decide(_pane(rows))
        self.assertEqual(len(actions), 1)
        self.assertNotEqual(actions[0]["keys"], ["Left"])

    def test_game_over_and_menu_are_silent(self):
        rows = ["", "  oo@  ", "                                   $"]
        _, actions = _decide(_pane(rows, extra="lGame Overqqqqqqqk"))
        self.assertEqual(actions, [])
        _, actions = _decide("Main Menu\nArcade Mode\nQuit")
        self.assertEqual(actions, [])

    def test_unparseable_pane_is_silent(self):
        _, actions = _decide("ffmpeg log\nnative=1204x724\n")
        self.assertEqual(actions, [])

    def test_bad_input_is_json_error(self):
        proc = subprocess.run(
            [sys.executable, str(BRAIN)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
