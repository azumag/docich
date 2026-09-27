import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "brains/bastet/brain.py"
PLAY = "┌────────────────────┐\n│                    │ Next block:\n│                    │ Score:     0\n│                    │ Lines:     0\n└────────────────────┘ Level:     0"


# Captured from real bastet 0.43 (Ubuntu 24.04) via `tmux capture-pane -p`: the
# ACS border is rendered as letters (x/q/l/k/m/j), so the side border "x" is
# glued to "Score:" and a `\bScore:` regex never matches.
REAL_PLAY = "\n".join([
    '                            lqqqqqqqqqqqqqqqqqqqqk lqqqqqqqqqqqqqqk',
    '                            x                    x x Next block:  x',
    '                            x                    x x              x',
    '                            x                    x x              x',
    '                            x                    x x              x',
    '                            x                    x x              x',
    '                            x                    x mqqqqqqqqqqqqqqj',
    '                            x                    x lqqqqqqqqqqqqqqk',
    '                            x                    x x              x',
    '                            x                    x xScore:      0 x',
    '                            x                    x x              x',
    '                            x                    x xLines:      0 x',
    '                            x                    x x              x',
    '                            x                    x xLevel:      0 x',
    '                            x                    x x              x',
    '                            x                    x mqqqqqqqqqqqqqqj',
    '                            x                    x',
    '                            x                    x',
    '                            x                    x',
    '                            x                    x',
    '                            x                    x',
    '                            mqqqqqqqqqqqqqqqqqqqqj',
])


def run(raw, weights, color_text=None):
    if color_text is not None:
        raw = json.dumps({
            "game": "bastet", "text": raw,
            "meta": {"bastet_color_text": color_text},
        })
    proc = subprocess.run(
        [sys.executable, str(BRAIN)], input=raw, capture_output=True,
        text=True, timeout=15,
        env={**os.environ, "DOCICH_BRAIN_WEIGHTS": str(weights)},
    )
    assert proc.stderr == ""
    return proc.returncode, json.loads(proc.stdout)


def decide(text, tmp_path):
    code, out = run(json.dumps({"game": "bastet", "text": text}), tmp_path / "weights.json")
    assert code == 0
    return out["actions"]


def incoming_i_board():
    board = [[None] * 10 for _ in range(20)]
    for dx in range(4):
        board[1][3 + dx] = "I"
    return board


def colored_capture(board, *, acs_controls=False):
    """Build an ANSI capture matching Bastet's two-colored-space cell layout."""
    color = {"O": 47, "I": 46, "Z": 41, "T": 45, "J": 44, "S": 42, "L": 43}
    left = "\x0e" if acs_controls else ""
    right = "\x0f" if acs_controls else ""
    lines = ["     " + left + "l" + "q" * 20 + "k" + right]
    for row in board:
        line = "     " + left + "x" + right
        for cell in row:
            if cell is None:
                line += "  "
            else:
                line += f"\x1b[{color[cell]}m  \x1b[0m"
        line += left + "x" + right
        lines.append(line)
    lines.append("     " + left + "m" + "q" * 20 + "j" + right)
    lines.append("Score:      0")
    return "\n".join(lines)


def plain_capture(styled):
    import re
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x0e\x0f]", "", styled)


def colored_decide(styled, tmp_path):
    code, out = run(plain_capture(styled), tmp_path / "weights.json", styled)
    assert code == 0
    return out["actions"]


def test_places_piece(tmp_path):
    assert decide(PLAY, tmp_path) == []
    assert decide(PLAY, tmp_path) == decide(PLAY, tmp_path)


def test_places_piece_on_real_captured_pane(tmp_path):
    assert decide(REAL_PLAY, tmp_path) == []
    # High-scoring boards keep the border letter glued to the label too.
    assert decide(REAL_PLAY.replace("Score:      0", "Score:   1200"), tmp_path) == []
    assert decide(REAL_PLAY + "\nTry again!", tmp_path) == []


@pytest.mark.parametrize("text", [
    "", "broken pane", "Bastet", "Play! (normal version)", "difficulty",
    "Try again!", "Game Over", "Press any key...", "Main Menu",
    PLAY + "\nTry again!", PLAY + "\nPlease enter your name",
    PLAY + "\n**Normal difficulty**", PLAY + "\nPress SPACE or ENTER to resume the game",
    PLAY + "\nStarting level = 0 <SPACE> to start",
])
def test_non_play_is_silent(text, tmp_path):
    assert decide(text, tmp_path) == []


@pytest.mark.parametrize("raw", ["not json", "", "[]", "null", "{}", '{"text":42}', '{"text":null}'])
def test_bad_input(raw, tmp_path):
    assert run(raw, tmp_path / "missing") == (2, {"actions": []})


def test_weight_hot_swap(tmp_path):
    weights = tmp_path / "weights.json"
    styled = colored_capture(incoming_i_board())
    text = plain_capture(styled)
    weights.write_text('{"hard_drop":0}')
    code, out = run(text, weights, styled)
    assert code == 0
    assert out["actions"][0]["keys"] == ["Down"]
    weights.write_text('{"hard_drop":1}')
    code, out = run(text, weights, styled)
    assert code == 0
    assert out["actions"][0]["keys"] == ["Enter"]


def test_colored_board_plans_line_clear(tmp_path):
    board = [[None] * 10 for _ in range(20)]
    # The bottom row has a four-cell gap under the incoming horizontal I.
    for x in range(10):
        if x not in range(4, 8):
            board[19][x] = "Z"
    for dx, dy in ((0, 1), (1, 1), (2, 1), (3, 1)):
        board[dy][3 + dx] = "I"

    actions = colored_decide(colored_capture(board), tmp_path)
    assert actions == [{"type": "key", "keys": ["Right", "Enter"]}]


def test_colored_board_waits_until_piece_is_visible(tmp_path):
    board = [[None] * 10 for _ in range(20)]
    assert colored_decide(colored_capture(board), tmp_path) == []


def test_colored_board_does_not_treat_settled_piece_as_active(tmp_path):
    board = [[None] * 10 for _ in range(20)]
    for dx, dy in ((0, 1), (1, 1), (2, 1), (2, 0)):
        board[18 + dy][3 + dx] = "L"
    assert colored_decide(colored_capture(board), tmp_path) == []


def test_colored_board_ignores_tmux_acs_mode_controls(tmp_path):
    assert colored_decide(colored_capture(incoming_i_board(), acs_controls=True), tmp_path) == [
        {"type": "key", "keys": ["Enter"]}
    ]


def test_colored_capture_menu_remains_silent(tmp_path):
    board = [[None] * 10 for _ in range(20)]
    for dx, dy in ((0, 1), (1, 1), (2, 1), (3, 1)):
        board[dy][3 + dx] = "I"
    assert colored_decide(colored_capture(board) + "\nTry again!", tmp_path) == []


@pytest.mark.parametrize("raw", ['bad', '[]', '{"hard_drop":"q"}', '{"hard_drop":null}', '{"hard_drop":true}', '{"hard_drop":NaN}', '{"hard_drop":Infinity}'])
def test_invalid_weights_fall_back(raw, tmp_path):
    (tmp_path / "weights.json").write_text(raw)
    actions = colored_decide(colored_capture(incoming_i_board()), tmp_path)
    assert actions == [{"type": "key", "keys": ["Enter"]}]


def test_config_contract():
    cfg = tomllib.loads((ROOT / "config/games/bastet.toml").read_text())
    assert cfg["cli"]["command"] == "/bin/sh games/cli-wrappers/bastet_docich.sh"
    assert cfg["agent"]["enabled"] is True
    assert cfg["agent"]["brain"] == "command"
    assert cfg["agent"]["command"] == ["python3", "brains/bastet/brain.py"]
    assert cfg["corner"]["self_play"] and cfg["corner"]["intro"]
    assert cfg["retro_corner"]["unattended"]
    assert cfg["lifecycle"]["require_round_boundary"] is False
