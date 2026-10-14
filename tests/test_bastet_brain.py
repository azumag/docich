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


COLOR = {"O": 47, "I": 46, "Z": 41, "T": 45, "J": 44, "S": 42, "L": 43}
# Relative cell offsets of the orientation-0 shapes, matching Bastet Block.cpp.
PREVIEW_SHAPES = {
    "O": ((1, 1), (2, 1), (1, 0), (2, 0)),
    "I": ((0, 1), (1, 1), (2, 1), (3, 1)),
    "Z": ((1, 1), (2, 1), (0, 0), (1, 0)),
    "T": ((0, 1), (1, 1), (2, 1), (1, 0)),
    "J": ((0, 1), (1, 1), (2, 1), (0, 0)),
    "S": ((0, 1), (1, 1), (1, 0), (2, 0)),
    "L": ((0, 1), (1, 1), (2, 1), (2, 0)),
}


def _panel(row: int, preview):
    """The right-hand boxes of the real pane (Ui::_nextWin / _scoreWin).

    The next window is 5x14 inner cells and the score window 7x14, stacked one
    row below the other; every dot is painted as two terminal columns.
    """
    def inner(label="", dots=()):
        cols = list(label.ljust(14)[:14])
        for dx, dy in dots:
            for offset in (0, 1):
                cols[2 * (2 + dx) + offset] = f"\x1b[{COLOR[preview]}m \x1b[0m"
        return "".join(cols)

    shape = PREVIEW_SHAPES.get(preview, ())
    if row == 0:
        return " l" + "q" * 14 + "k"
    if 1 <= row <= 5:
        dots = tuple((dx, dy) for dx, dy in shape if 2 + dy == row - 1)
        return " x" + inner("Next block:" if row == 1 else "", dots) + "x"
    if row == 6:
        return " m" + "q" * 14 + "j"
    if row == 7:
        return " l" + "q" * 14 + "k"
    if 8 <= row <= 14:
        label = {9: "Score:      0", 11: "Lines:      0", 13: "Level:      0"}.get(row, "")
        return " x" + inner(label) + "x"
    if row == 15:
        return " m" + "q" * 14 + "j"
    return " " * 16


def colored_capture(board, *, acs_controls=False, preview=None):
    """Build an ANSI capture matching Bastet's two-colored-space cell layout.

    ``preview`` mirrors the real "Next block" window: Bastet paints the preview
    with the same per-piece colours one cell to the right of the well.
    """
    left = "\x0e" if acs_controls else ""
    right = "\x0f" if acs_controls else ""
    lines = ["     " + left + "l" + "q" * 20 + "k" + right + _panel(0, preview)]
    for index, row in enumerate(board):
        line = "     " + left + "x" + right
        for cell in row:
            if cell is None:
                line += "  "
            else:
                line += f"\x1b[{COLOR[cell]}m  \x1b[0m"
        line += left + "x" + right + _panel(index + 1, preview)
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
    # Soft drop only: the placement plan is withheld and the piece just descends.
    assert out["actions"][0]["keys"] == ["Down"]
    weights.write_text('{"hard_drop":1}')
    code, out = run(text, weights, styled)
    assert code == 0
    assert out["actions"][0]["keys"][-1] == "Enter"


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
    # SO/SI must not shift the well's columns; any legal plan proves the grid
    # still lines up, and it must finish with the hard drop.
    actions = colored_decide(colored_capture(incoming_i_board(), acs_controls=True), tmp_path)
    assert len(actions) == 1
    assert actions[0]["keys"][-1] == "Enter"


def test_colored_capture_menu_remains_silent(tmp_path):
    board = [[None] * 10 for _ in range(20)]
    for dx, dy in ((0, 1), (1, 1), (2, 1), (3, 1)):
        board[dy][3 + dx] = "I"
    assert colored_decide(colored_capture(board) + "\nTry again!", tmp_path) == []


@pytest.mark.parametrize("raw", ['bad', '[]', '{"hard_drop":"q"}', '{"hard_drop":null}', '{"hard_drop":true}', '{"hard_drop":NaN}', '{"hard_drop":Infinity}'])
def test_invalid_weights_fall_back(raw, tmp_path):
    (tmp_path / "weights.json").write_text(raw)
    actions = colored_decide(colored_capture(incoming_i_board()), tmp_path)
    # Invalid values fall back to the defaults, which still hard-drop.
    assert len(actions) == 1
    assert actions[0]["keys"][-1] == "Enter"


def test_config_contract():
    cfg = tomllib.loads((ROOT / "config/games/bastet.toml").read_text())
    assert cfg["cli"]["command"] == "/bin/sh games/cli-wrappers/bastet_docich.sh"
    assert cfg["agent"]["enabled"] is True
    assert cfg["agent"]["brain"] == "command"
    assert cfg["agent"]["command"] == ["python3", "brains/bastet/brain.py"]
    assert cfg["corner"]["self_play"] and cfg["corner"]["intro"]
    assert cfg["retro_corner"]["unattended"]
    assert cfg["lifecycle"]["require_round_boundary"] is False


def test_colored_board_acts_on_partially_visible_spawn(tmp_path):
    # Real bastet 0.43: right after spawning, an L shows only its three-cell
    # row on the top line; its fourth cell is still above the well.
    board = [[None] * 10 for _ in range(20)]
    for x in range(3, 6):
        board[0][x] = "L"
    actions = colored_decide(colored_capture(board), tmp_path)
    assert len(actions) == 1
    assert actions[0]["keys"][-1] == "Enter"


def test_partially_visible_spawn_needs_matching_color(tmp_path):
    board = [[None] * 10 for _ in range(20)]
    for x in range(3, 6):
        board[0][x] = "L"
    board[0][4] = "J"  # mixed colors are not one piece
    assert colored_decide(colored_capture(board), tmp_path) == []


# A settled wall with a one-cell step: the "Next block" preview decides whether
# the incoming I is worth keeping a channel for (fixture from self-play).
def _preview_fixture():
    """A mid-game board plus the incoming horizontal I from the spawn anchor."""
    rows = [[None] * 10 for _ in range(20)]
    settled = [
        "I",
        "I",
        "I........J",
        "IT..JJJL.J",
        "J.SSSSOOJI",
    ]
    for offset, row in enumerate(settled):
        for x, cell in enumerate(row):
            if cell != ".":
                rows[15 + offset][x] = cell
    for dx in range(4):
        rows[1][3 + dx] = "I"
    return rows


def test_next_block_preview_parses_from_the_real_panel_layout(tmp_path):
    board = _preview_fixture()
    assert colored_decide(colored_capture(board, preview="T"), tmp_path)[0]["keys"] == [
        "Right", "Right", "Right", "Space", "Right", "Enter",
    ]


def test_next_block_preview_changes_the_placement(tmp_path):
    board = _preview_fixture()
    without = colored_decide(colored_capture(board), tmp_path)
    with_l = colored_decide(colored_capture(board, preview="L"), tmp_path)
    assert without != with_l
    assert without[0]["keys"] == ["Right", "Right", "Right", "Space", "Right", "Enter"]
    assert with_l[0]["keys"] == ["Right", "Enter"]


def test_unreadable_preview_falls_back_to_single_ply(tmp_path):
    board = _preview_fixture()
    plain = colored_decide(colored_capture(board), tmp_path)
    # A pane whose preview window shows two different block colours is not the
    # next block; the brain must not guess and must keep its single-ply plan.
    styled = colored_capture(board, preview="O")
    mixed = styled.replace("\x1b[47m \x1b[0m", "\x1b[41m \x1b[0m", 1)
    assert colored_decide(mixed, tmp_path) == plain
