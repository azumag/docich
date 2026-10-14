"""Strategy-quality regressions for the Bastet command brain.

``tests/test_bastet_brain.py`` drives the brain through its JSON stdin/stdout
contract. These tests import it directly and replay its key sequences on a
local engine, so a full self-play match stays cheap for CI while still measuring
the placement quality the agent loop produces.

Bastet 0.43 chooses every block with a 2-ply search of its own
(``BastetBlockChooser.cpp``): it hands out the tetromino that minimises the best
score the player can still reach. A 1-ply placement heuristic alone tops out
within a few dozen blocks, which is why the thresholds below are calibrated
against measurements of the previous single-ply brain (it topped out after 85
pieces of ``NO_I_ROUNDS`` with 19 lines cleared).
"""
from __future__ import annotations

import importlib.util
import random
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, SPAWN_X = 10, 20, 3
PIECES = ("O", "I", "Z", "T", "J", "S", "L")
COLOR = {"O": 47, "I": 46, "Z": 41, "T": 45, "J": 44, "S": 42, "L": 43}
# Bastet paints every well and preview cell as two spaces with the block colour
# as background (Ui::DrawDot); the next window sits one cell right of the well.
WELL_LEFT = 5
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x0e\x0f]")


@pytest.fixture(scope="module")
def brain():
    spec = importlib.util.spec_from_file_location(
        "bastet_brain_strategy", ROOT / "brains/bastet/brain.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def panel(brain, row, preview=None):
    """The right-hand boxes of the real pane (Ui::_nextWin / _scoreWin)."""
    if row == 0:
        return " l" + "q" * 14 + "k"
    if 1 <= row <= 5:
        cells = [None] * 14
        if row == 1:
            cells[:11] = list("Next block:")
        if preview is not None:
            for dx, dy in brain.SHAPES[preview][0]:
                if 2 + dy != row - 1:
                    continue
                start = 2 * (2 + dx)
                cells[start] = "\x1b[%dm " % COLOR[preview]
                cells[start + 1] = " \x1b[0m"
        return " x" + "".join(" " if cell is None else cell for cell in cells) + "x"
    if row == 6:
        return " m" + "q" * 14 + "j"
    if row == 7:
        return " l" + "q" * 14 + "k"
    if 8 <= row <= 14:
        label = {9: "Score:      0", 11: "Lines:      0", 13: "Level:      0"}.get(row, "")
        return " x" + label.ljust(14) + "x"
    if row == 15:
        return " m" + "q" * 14 + "j"
    return " " * 16


def capture(brain, board, piece, x=SPAWN_X, y=-1, orientation=0, preview=None):
    """Styled capture of the well plus the real "Next block" window."""
    grid = [row[:] for row in board]
    for dx, dy in brain.SHAPES[piece][orientation]:
        row, col = y + dy, x + dx
        if 0 <= row < HEIGHT and 0 <= col < WIDTH:
            grid[row][col] = piece
    lines = [" " * WELL_LEFT + "l" + "q" * 20 + "k" + panel(brain, 0, preview)]
    for index, row in enumerate(grid):
        line = " " * WELL_LEFT + "x"
        for cell in row:
            line += "  " if cell is None else "\x1b[%dm  \x1b[0m" % COLOR[cell]
        line += "x" + panel(brain, index + 1, preview)
        lines.append(line)
    lines.append(" " * WELL_LEFT + "m" + "q" * 20 + "j")
    lines.append("Score:      0")
    return "\n".join(lines)


def plain(styled):
    return ANSI_RE.sub("", styled)


def fits(board, shape, x, y):
    for dx, dy in shape:
        col, row = x + dx, y + dy
        if col < 0 or col >= WIDTH or row >= HEIGHT:
            return False
        if row >= 0 and board[row][col] is not None:
            return False
    return True


def lock(board, shape, x, y, piece):
    grid = [row[:] for row in board]
    for dx, dy in shape:
        grid[y + dy][x + dx] = piece
    remaining = [row for row in grid if not all(cell is not None for cell in row)]
    cleared = HEIGHT - len(remaining)
    return [[None] * WIDTH for _ in range(cleared)] + remaining, cleared


def replay(brain, board, piece, keys, y=-1):
    """Apply the brain's keys the way the game would: (board, lines, placed)."""
    orientation = 0
    shape = brain.SHAPES[piece][0]
    x = SPAWN_X
    for key in keys:
        if key == "Left" and fits(board, shape, x - 1, y):
            x -= 1
        elif key == "Right" and fits(board, shape, x + 1, y):
            x += 1
        elif key in ("Space", "Up"):
            candidate = (orientation + (1 if key == "Space" else -1)) % 4
            other = brain.SHAPES[piece][candidate]
            if other != shape and fits(board, other, x, y):
                orientation, shape = candidate, other
        elif key == "Down":
            if fits(board, shape, x, y + 1):
                y += 1
        elif key == "Enter":
            while fits(board, shape, x, y + 1):
                y += 1
            if any(y + dy < 0 for _, dy in shape):
                return board, 0, False
            settled, cleared = lock(board, shape, x, y, piece)
            return settled, cleared, True
    while fits(board, shape, x, y + 1):
        y += 1
    if any(y + dy < 0 for _, dy in shape):
        return board, 0, False
    settled, cleared = lock(board, shape, x, y, piece)
    return settled, cleared, True


def play(brain, sequence, weights=None):
    """Self-play `sequence`; returns lines, pieces and the final well."""
    weights = weights or brain.load_weights()
    board = [[None] * WIDTH for _ in range(HEIGHT)]
    lines = 0
    for index, piece in enumerate(sequence):
        preview = sequence[index + 1] if index + 1 < len(sequence) else None
        styled = capture(brain, board, piece, preview=preview)
        keys = brain.decide(plain(styled), weights, styled)
        board, cleared, placed = replay(brain, board, piece, list(keys or []))
        lines += cleared
        if not placed or all(cell is not None for cell in board[0]):
            return dict(lines=lines, pieces=index + 1, topped=True, board=board)
    return dict(lines=lines, pieces=len(sequence), topped=False, board=board)


def heap(board):
    """(max column height, number of holes) of a settled well."""
    max_height = 0
    holes = 0
    for x in range(WIDTH):
        occupied = [y for y in range(HEIGHT) if board[y][x] is not None]
        if not occupied:
            continue
        max_height = max(max_height, HEIGHT - occupied[0])
        holes += sum(board[y][x] is None for y in range(occupied[0], HEIGHT))
    return max_height, holes


# A 160-piece sequence with no I piece: Bastet hands these out to force 1-wide
# gaps and deny four-row clears. The previous brain topped out after 85 of them.
NO_I_ROUNDS = [random.Random(99).choice(("O", "S", "Z", "T", "L", "J")) for _ in range(160)]
RANDOM_ROUNDS = [random.Random(4242).choice(PIECES) for _ in range(160)]


def test_well_features_counts_holes_wells_and_transitions(brain):
    board = [[None] * WIDTH for _ in range(HEIGHT)]
    board[HEIGHT - 1][0] = "Z"
    heights, _rows, columns, holes, wells = brain._well_features(board)
    assert heights == [1] + [0] * 9
    assert holes == 0
    assert wells == 0
    assert columns == WIDTH  # one transition per column (block or the floor)
    # A covered cell is a hole, and the empty channel beside two stacks is a
    # well; the wall counts as a filled neighbour, so both sides score.
    board[HEIGHT - 1][0] = "Z"
    board[HEIGHT - 2][1] = "Z"
    board[HEIGHT - 1][2] = "Z"
    _heights, _rows, _columns, holes, wells = brain._well_features(board)
    assert holes == 1
    assert wells == 2


def with_active_piece(brain, board, piece, y=0, orientation=0):
    """Paint a spawned piece into the well, as the capture shows it."""
    grid = [row[:] for row in board]
    for dx, dy in brain.SHAPES[piece][orientation]:
        row, col = y + dy, SPAWN_X + dx
        if 0 <= row < HEIGHT:
            grid[row][col] = piece
    return grid


def test_plan_drops_a_vertical_i_into_a_one_cell_well(brain):
    settled = [[None] * WIDTH for _ in range(HEIGHT)]
    for x in range(WIDTH - 1):
        for y in range(HEIGHT - 4, HEIGHT):
            settled[y][x] = "Z"
    plan = brain.plan_keys(with_active_piece(brain, settled, "I"), None, brain.load_weights())
    assert plan is not None
    board_after, cleared, placed = replay(brain, settled, "I", list(plan))
    assert placed and cleared == 4
    assert heap(board_after)[1] == 0


def test_plan_prefers_clearing_a_ready_row(brain):
    settled = [[None] * WIDTH for _ in range(HEIGHT)]
    for x in range(WIDTH):
        if x not in range(4, 8):
            settled[HEIGHT - 1][x] = "Z"
    plan = brain.plan_keys(with_active_piece(brain, settled, "I"), None, brain.load_weights())
    board_after, cleared, placed = replay(brain, settled, "I", list(plan))
    assert placed and cleared == 1
    assert heap(board_after)[0] == 0


def test_plan_uses_the_next_block_preview(brain):
    weights = brain.load_weights()
    board = [[None] * WIDTH for _ in range(HEIGHT)]
    for offset, row in enumerate(("I", "I", "I........J", "IT..JJJL.J", "J.SSSSOOJI")):
        for x, cell in enumerate(row):
            if cell != ".":
                board[HEIGHT - 5 + offset][x] = cell
    for dx in range(4):
        board[1][SPAWN_X + dx] = "I"
    without = brain.plan_keys(board, None, weights)
    with_l = brain.plan_keys(board, "L", weights)
    assert without != with_l, "the preview must be able to change the placement"
    # Without the preview the I slides into the one-cell step; knowing an L
    # follows makes dropping straight down the better two-piece outcome.
    assert without == ["Right", "Right", "Right", "Space", "Right"]
    assert with_l == ["Right"]


def test_preview_parse_rejects_a_mixed_window(brain):
    board = [[None] * WIDTH for _ in range(HEIGHT)]
    styled = capture(brain, board, "T", preview="O")
    assert brain.parse_screen(styled)[1] == "O"
    mixed = styled.replace("\x1b[47m ", "\x1b[41m ", 1)
    assert brain.parse_screen(mixed)[1] is None


def test_self_play_survives_a_sequence_with_no_i_pieces(brain):
    result = play(brain, NO_I_ROUNDS)
    assert not result["topped"], result
    assert result["lines"] >= 55, result
    max_height, _holes = heap(result["board"])
    assert max_height <= 14, heap(result["board"])


def test_self_play_keeps_a_flat_low_stack_on_random_pieces(brain):
    result = play(brain, RANDOM_ROUNDS)
    assert not result["topped"], result
    assert result["lines"] >= 55, result
    assert heap(result["board"])[1] <= 2, heap(result["board"])
