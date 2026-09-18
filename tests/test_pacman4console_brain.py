import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "brains/pacman4console/brain.py"


def pane(rows, extra=""):
    # Source layout: 28x29 at (1,1), lives row 30, score row 31.
    board = [r.ljust(28) for r in rows] + [" " * 28] * (29 - len(rows))
    return "\n".join([""] + [" " + r for r in board] + ["  C C C", "   Level: 1     Score: 0", extra])


PLAY = pane(["", "  C..", "  . .", "  ..."])


def run(raw, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(BRAIN)], input=raw, capture_output=True,
        text=True, timeout=15,
        env={**os.environ, "DOCICH_BRAIN_WEIGHTS": str(tmp_path / "weights.json"),
             "DOCICH_PACMAN_LEVELS_DIR": str(tmp_path / "levels")},
    )
    assert proc.stderr == ""
    return proc.returncode, json.loads(proc.stdout)


def decide(text, tmp_path):
    code, out = run(json.dumps({"game": "pacman4console", "text": text}), tmp_path)
    assert code == 0
    return out["actions"]


def write_map(tmp_path, walkable):
    levels = tmp_path / "levels"
    levels.mkdir(exist_ok=True)
    cells = ["0" if (x, y) in walkable else "1" for y in range(29) for x in range(28)]
    (levels / "level01.dat").write_text(" ".join(cells + ["1"]))


def test_moves_to_food_not_lives(tmp_path):
    assert decide(PLAY, tmp_path) == [{"type": "key", "keys": ["Down"]}]
    assert decide(PLAY, tmp_path) == decide(PLAY, tmp_path)


def test_invisible_walls_and_empty_corridor(tmp_path):
    # The visually closer pellet is behind an invisible wall to the right.
    # Reachable food is below, through a previously eaten (blank) corridor.
    text = pane(["", " C .", "", " ."])
    write_map(tmp_path, {(1, 1), (1, 2), (1, 3), (3, 1)})
    assert decide(text, tmp_path)[0]["keys"] == ["Down"]


def test_tunnel(tmp_path):
    text = pane(["C" + " " * 26 + "."])
    assert decide(text, tmp_path)[0]["keys"] == ["Left"]


def test_weight_hot_swap_ghost_avoidance(tmp_path):
    text = pane(["", "  .&", " .C"])
    weights = tmp_path / "weights.json"
    weights.write_text('{"ghost_radius":1}')
    assert decide(text, tmp_path)[0]["keys"] == ["Left"]
    weights.write_text('{"ghost_radius":0}')
    assert decide(text, tmp_path)[0]["keys"] == ["Up"]


def test_no_food_still_moves(tmp_path):
    write_map(tmp_path, {(1, 1), (2, 1)})
    assert decide(pane(["", " C"]), tmp_path)[0]["keys"] == ["Right"]


@pytest.mark.parametrize("text", [
    "", "broken pane", "PACMAN", "Press any key...", "Main Menu",
    "difficulty", "Game Over", PLAY + "\nGame Over", PLAY + "\nPress any key...",
    PLAY + "\n*PAUSED*", pane([" C C."]), pane(["..."]),
])
def test_non_play_is_silent(text, tmp_path):
    assert decide(text, tmp_path) == []


@pytest.mark.parametrize("raw", ["not json", "", "[]", "null", "{}", '{"text":42}', '{"text":null}'])
def test_bad_input(raw, tmp_path):
    assert run(raw, tmp_path) == (2, {"actions": []})


@pytest.mark.parametrize("raw", ['bad', '[]', '{"ghost_radius":"q"}', '{"ghost_radius":null}', '{"ghost_radius":true}', '{"ghost_radius":NaN}', '{"ghost_radius":Infinity}'])
def test_invalid_weights_fall_back(raw, tmp_path):
    (tmp_path / "weights.json").write_text(raw)
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Down"]


def test_bad_or_mismatched_map_uses_visible_cells(tmp_path):
    write_map(tmp_path, set())
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Down"]
    (tmp_path / "levels/level01.dat").write_text("bad map")
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Down"]


def test_config_contract():
    cfg = tomllib.loads((ROOT / "config/games/pacman4console.toml").read_text())
    assert cfg["cli"]["command"] == "/bin/sh games/cli-wrappers/pacman4console_docich.sh"
    assert cfg["cli"]["rows"] == 32
    assert cfg["agent"]["enabled"] is True
    assert cfg["agent"]["brain"] == "command"
    assert cfg["agent"]["command"] == ["python3", "brains/pacman4console/brain.py"]
    assert cfg["corner"]["self_play"] and cfg["corner"]["intro"]
    assert cfg["retro_corner"]["unattended"]
    assert cfg["lifecycle"]["require_round_boundary"] is False
