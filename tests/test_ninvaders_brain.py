import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1] / "brains" / "ninvaders" / "brain.py"

# Mid-game frame captured from real nInvaders 0.1.1 (tmux capture-pane, 80x24).
# Row -> text.  `!` is the player's own missile (rises ~6 rows per 250ms) and `:`
# is an invader bomb (falls ~2 rows per 250ms); the status row also contains ':'.
REAL_ROWS = {
    5: "                           ,^,,^,,^,,^,,^,,^,,^,,^,,^,,^,",
    7: "                           _O-_O-_O-_O-_O-_O-_O-_O-_O-_O-",
    9: "                           _O-_O-_O-_O-_O-_O-_O-_O-_O-_O-",
    11: "                              -o--o--o--o--o--o--o--o--o-",
    13: "                                 -o--o--o--o--o--o--o--o-",
    16: "                             ###                 ###                 ###",
    17: "        #                   #####               ####                #####",
    18: "        #                   ######             ##### #             #######",
    19: "        #                  ##   ##             ##   ##             ##   ##",
    23: "                 Level: 01 Score: 0000800 Lives: /-\\",
}
PLAYER_ROW = 22


def _pane(player_caret_x: int | None, marks: dict[tuple[int, int], str] | None = None) -> str:
    """Real frame with the ship's '^' at player_caret_x (None: ship not drawn yet)
    and extra (x, y) glyphs."""
    grid = [list(REAL_ROWS.get(y, "").ljust(80)) for y in range(24)]
    ship = list("/-^-\\")
    for i, ch in enumerate(ship):
        if player_caret_x is not None:
            grid[PLAYER_ROW][player_caret_x - 2 + i] = ch
    for (x, y), ch in (marks or {}).items():
        grid[y][x] = ch
    return "\n".join("".join(row).rstrip() for row in grid)


def _run(text, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.run(
        [sys.executable, str(BRAIN)],
        input=json.dumps({"game": "ninvaders", "text": text}),
        capture_output=True, text=True, timeout=15, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["actions"]


def _keys(text, env_extra=None) -> list:
    actions = _run(text, env_extra)
    if not actions:
        return []
    assert len(actions) == 1 and actions[0]["type"] == "key"
    return actions[0]["keys"]


class NinvadersBrainTest(unittest.TestCase):
    def setUp(self):
        # Keep a stray run/brain weights file from leaking into the default policy.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"DOCICH_BRAIN_WEIGHTS": str(Path(self.tmp.name) / "none.json")}

    def keys(self, text):
        return _keys(text, self.env)

    def test_fires_every_turn_and_sends_one_keypress_group(self):
        self.assertEqual(self.keys(_pane(30)), ["Space"])  # under the formation
        self.assertEqual(self.keys(_pane(12)), ["Right", "Space"])  # sweeps toward it

    def test_own_missile_is_not_a_threat(self):
        # '!' directly above the ship is our own shot; dodging it wasted every move.
        clean = self.keys(_pane(12))
        with_missile = self.keys(_pane(12, {(12, 19): "!", (12, 15): "!"}))
        self.assertEqual(with_missile, clean)
        self.assertEqual(with_missile, ["Right", "Space"])

    def test_dodges_bomb_above_left(self):
        self.assertEqual(self.keys(_pane(40, {(39, 18): ":"})), ["Right", "Space"])

    def test_dodges_bomb_above_right(self):
        self.assertEqual(self.keys(_pane(40, {(42, 19): ":"})), ["Left", "Space"])

    def test_dodge_takes_priority_over_approach(self):
        # The formation is on the right (approach = Right) but a bomb on the right
        # forces Left.
        self.assertEqual(self.keys(_pane(12, {(13, 19): ":"})), ["Left", "Space"])

    def test_ignores_far_or_high_bombs(self):
        self.assertEqual(self.keys(_pane(12, {(20, 19): ":"})), ["Right", "Space"])  # too wide
        self.assertEqual(self.keys(_pane(12, {(12, 8): ":"})), ["Right", "Space"])  # too high

    def test_status_row_colons_are_not_bombs(self):
        # The status row (below the ship) contains ':' at x=22..; a ship near them
        # must still just approach the formation.
        self.assertEqual(self.keys(_pane(21)), ["Right", "Space"])

    def test_weights_hot_swap(self):
        text = _pane(40, {(41, 19): ":"})
        self.assertEqual(self.keys(text), ["Left", "Space"])
        weights = Path(self.tmp.name) / "weights.json"
        weights.write_text('{"dodge_radius": 0}')
        self.assertEqual(_keys(text, {"DOCICH_BRAIN_WEIGHTS": str(weights)}), ["Space"])

    def test_invalid_weights_fall_back_to_defaults(self):
        weights = Path(self.tmp.name) / "weights.json"
        weights.write_text('{"dodge_radius": "wide", "dodge_height": null}')
        text = _pane(40, {(39, 18): ":"})
        self.assertEqual(_keys(text, {"DOCICH_BRAIN_WEIGHTS": str(weights)}), ["Right", "Space"])

    def test_undrawn_ship_after_new_match_is_redrawn_by_a_keypress(self):
        # Real nInvaders does not draw the ship for a new match until the first key.
        # Staying silent here left the ship missing for the rest of the run (measured:
        # 697 consecutive frames with no ship and no input).
        self.assertEqual(self.keys(_pane(None)), ["Right", "Space"])

    def test_game_over_screen_is_silent_even_with_status_row(self):
        self.assertEqual(self.keys(_pane(None) + "\n      GAME OVER"), [])
        self.assertEqual(self.keys(_pane(30) + "\n      GAME OVER"), [])

    def test_title_and_non_play_screens_are_silent(self):
        self.assertEqual(_run("Press SPACE to start\nLevel: 01 Score: 0000000"), [])
        self.assertEqual(_run(""), [])
        self.assertEqual(_run("GAME OVER"), [])  # no ship on screen

    def test_bad_input_is_json_error(self):
        proc = subprocess.run(
            [sys.executable, str(BRAIN)], input="not json",
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout), {"actions": []})


if __name__ == "__main__":
    unittest.main()
