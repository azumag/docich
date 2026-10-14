#!/usr/bin/env python3
"""Board-aware, stdlib-only Bastet CommandBrain.

The terminal renders each occupied board cell as two spaces with its block
color as the background. The adapter passes that styled capture in
``meta.bastet_color_text``. This brain reconstructs the 10x20 well, recognizes
the newly spawned piece at Bastet's fixed spawn anchor, and evaluates reachable
placements using the exact block matrices and fixed-anchor rotations from
Bastet 0.43. Weights are reloaded on every fresh invocation for live hot-swap.

Placement quality uses the Dellacherie feature set (landing height, rows
cleared, row/column transitions, holes, well sums). Bastet is adversarial, so
seeing the *next* block matters: when the "Next block" preview can be read
confidently, each candidate placement is also scored by the best placement the
next piece could then achieve (2-ply), which makes room for an incoming I
instead of burying it. Colour is unique per tetromino, so the preview needs
only its region, never its pixel geometry.
"""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = Path(os.environ.get(
    "DOCICH_BRAIN_WEIGHTS", str(REPO_ROOT / "run/brain/bastet/weights.json"),
))
# Dellacherie feature set. `landing_height`..`well_sums` are the published
# hand-tuned values; `rows_cleared` and `next_piece_weight` were re-tuned for
# Bastet's adversarial chooser with the local self-play harness (it tops out
# roughly 2.5x later than the published values and 2x later than the previous
# crude heuristic). Every entry is numeric, so the corner improvement loop may
# propose tuned values for any of them.
DEFAULT_WEIGHTS = {
    "hard_drop": 1.0,
    "landing_height": -4.500158825082766,
    "rows_cleared": 8.0,
    "row_transitions": -3.2178882868487753,
    "column_transitions": -9.348695305445199,
    "holes": -7.899265427351652,
    "well_sums": -3.3855972247263626,
    # How much the next piece's best follow-up placement counts. 0 disables the
    # 2-ply search (the preview is ignored whenever it cannot be read).
    "next_piece_weight": 1.0,
    # Deterministic tie-break towards short key sequences.
    "path_length": -0.001,
}
BOARD_WIDTH = 10
BOARD_HEIGHT = 20
SPAWN_X = 3
# A freshly spawned piece sits partly above the well (e.g. only the three-cell
# row of an L is visible), so the search starts above row 0.
MIN_ACTIVE_ANCHOR_Y = -2
MAX_ACTIVE_ANCHOR_Y = 6
MIN_VISIBLE_ACTIVE_CELLS = 2
# The widest plan is 9 lateral steps plus 3 rotations. Placements needing more
# than this are not discarded outright: the best one inside the budget wins.
MAX_PLAN_KEYS = 16
# Column window (relative to the well's left border) scanned for the "Next
# block" preview. The well occupies x0..x0+21 and the preview box follows it.
NEXT_PIECE_MIN_DX = 22
NEXT_PIECE_MAX_DX = 46

# Coordinates are relative to Bastet's BlockPosition anchor.
SHAPES = {
    "O": (
        ((1, 1), (2, 1), (1, 0), (2, 0)),
        ((1, 1), (2, 1), (1, 0), (2, 0)),
        ((1, 1), (2, 1), (1, 0), (2, 0)),
        ((1, 1), (2, 1), (1, 0), (2, 0)),
    ),
    "I": (
        ((0, 1), (1, 1), (2, 1), (3, 1)),
        ((2, 3), (2, 1), (2, 2), (2, 0)),
        ((0, 2), (1, 2), (2, 2), (3, 2)),
        ((1, 3), (1, 1), (1, 2), (1, 0)),
    ),
    "Z": (
        ((1, 1), (2, 1), (0, 0), (1, 0)),
        ((1, 2), (1, 1), (2, 1), (2, 0)),
        ((1, 2), (2, 2), (0, 1), (1, 1)),
        ((0, 2), (0, 1), (1, 1), (1, 0)),
    ),
    "T": (
        ((0, 1), (1, 1), (2, 1), (1, 0)),
        ((1, 2), (1, 1), (2, 1), (1, 0)),
        ((1, 2), (0, 1), (1, 1), (2, 1)),
        ((1, 2), (0, 1), (1, 1), (1, 0)),
    ),
    "J": (
        ((0, 1), (1, 1), (2, 1), (0, 0)),
        ((1, 2), (1, 1), (1, 0), (2, 0)),
        ((2, 2), (0, 1), (1, 1), (2, 1)),
        ((0, 2), (1, 2), (1, 1), (1, 0)),
    ),
    "S": (
        ((0, 1), (1, 1), (1, 0), (2, 0)),
        ((2, 2), (1, 1), (2, 1), (1, 0)),
        ((0, 2), (1, 2), (1, 1), (2, 1)),
        ((1, 2), (0, 1), (1, 1), (0, 0)),
    ),
    "L": (
        ((0, 1), (1, 1), (2, 1), (2, 0)),
        ((1, 2), (2, 2), (1, 1), (1, 0)),
        ((0, 2), (0, 1), (1, 1), (2, 1)),
        ((1, 2), (1, 1), (0, 0), (1, 0)),
    ),
}

# Bastet Block.cpp: pair 7=O/white, 4=I/cyan, 1=Z/red, 5=T/magenta,
# 6=J/blue, 3=S/green, 2=L/yellow.
ANSI_BG_TO_PIECE = {
    41: "Z", 43: "L", 42: "S", 46: "I", 45: "T", 44: "J", 47: "O",
    101: "Z", 103: "L", 102: "S", 106: "I", 105: "T", 104: "J", 107: "O",
}
XTERM_BG_TO_PIECE = {
    1: "Z", 3: "L", 2: "S", 6: "I", 5: "T", 4: "J", 7: "O",
    9: "Z", 11: "L", 10: "S", 14: "I", 13: "T", 12: "J", 15: "O",
}
PIECE_RGB = {
    "Z": (205, 0, 0), "L": (205, 205, 0), "S": (0, 205, 0),
    "I": (0, 205, 205), "T": (205, 0, 205), "J": (0, 0, 238),
    "O": (229, 229, 229),
}
MENU_MARKERS = (
    "try again!", "play!", "difficulty", "game over", "main menu",
    "enter your name", "high score", "get ready", "starting level",
    "to start", "press any key", "resume", "paused", "key you wish",
)
SCORE_RE = re.compile(r"Score:\s*[0-9]+", re.IGNORECASE)


def load_weights() -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in weights:
                value = data.get(key)
                if type(value) in (int, float) and math.isfinite(value):
                    weights[key] = value
    except (OSError, ValueError, OverflowError, RecursionError):
        pass
    return weights


def _apply_sgr(background, params: str):
    """Return the background color after one ANSI SGR sequence."""
    parts = params.replace(":", ";").split(";") if params else ["0"]
    values = []
    for part in parts:
        try:
            values.append(int(part or "0"))
        except ValueError:
            values.append(-1)
    i = 0
    while i < len(values):
        value = values[i]
        if value == 0 or value == 49:
            background = None
        elif 40 <= value <= 47 or 100 <= value <= 107:
            background = value
        elif value == 48 and i + 1 < len(values):
            mode = values[i + 1]
            if mode == 5 and i + 2 < len(values):
                background = values[i + 2]
                i += 2
            elif mode == 2 and i + 4 < len(values):
                background = tuple(values[i + 2:i + 5])
                i += 4
        i += 1
    return background


def _styled_rows(raw: str) -> list[list[tuple[str, object]]]:
    rows: list[list[tuple[str, object]]] = [[]]
    background = None
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "\x1b":
            if i + 1 < len(raw) and raw[i + 1] == "[":
                end = i + 2
                while end < len(raw) and not ("@" <= raw[end] <= "~"):
                    end += 1
                if end < len(raw):
                    if raw[end] == "m":
                        background = _apply_sgr(background, raw[i + 2:end])
                    i = end + 1
                    continue
            elif i + 1 < len(raw) and raw[i + 1] == "]":
                end_bel = raw.find("\x07", i + 2)
                end_st = raw.find("\x1b\\", i + 2)
                ends = [end for end in (end_bel, end_st) if end >= 0]
                if ends:
                    end = min(ends)
                    i = end + (2 if end == end_st else 1)
                else:
                    i = len(raw)
                continue
            i += 1
            continue
        if char == "\n":
            rows.append([])
        elif char == "\r" or ord(char) < 0x20:
            # tmux wraps ncurses ACS glyphs in SO/SI (\x0e/\x0f); these are
            # terminal mode controls, not visible columns in the captured grid.
            pass
        else:
            rows[-1].append((char, background))
        i += 1
    return rows


def _rgb_piece(color: tuple[int, int, int]) -> str | None:
    if any(value < 0 or value > 255 for value in color):
        return None
    piece, palette = min(
        PIECE_RGB.items(),
        key=lambda item: sum((item[1][i] - color[i]) ** 2 for i in range(3)),
    )
    distance = sum((palette[i] - color[i]) ** 2 for i in range(3))
    return piece if distance <= 110**2 else None


def _background_piece(background) -> str | None:
    if isinstance(background, tuple):
        return _rgb_piece(background)
    if not isinstance(background, int):
        return None
    if background in ANSI_BG_TO_PIECE:
        return ANSI_BG_TO_PIECE[background]
    return XTERM_BG_TO_PIECE.get(background)


def _well_origin(rows: list[list[tuple[str, object]]]) -> tuple[int, int] | None:
    """Find the fixed 20-row well, whose side borders are 21 columns apart."""
    left_by_row: list[int | None] = []
    for row in rows:
        left = None
        for x in range(max(0, len(row) - 21)):
            if row[x][0] != "x" or row[x + 21][0] != "x":
                continue
            if all(row[col][0] == " " for col in range(x + 1, x + 21)):
                left = x
                break
        left_by_row.append(left)
    for y in range(len(left_by_row) - BOARD_HEIGHT + 1):
        left = left_by_row[y]
        if left is not None and all(left_by_row[y + dy] == left for dy in range(BOARD_HEIGHT)):
            return y, left
    return None


def _read_well(rows: list[list[tuple[str, object]]], origin) -> list[list[str | None]]:
    y0, x0 = origin
    board: list[list[str | None]] = []
    # Settled pieces have the same colors and shape as moving pieces. Only
    # accept a candidate close to the spawn area; a hard-dropped block lower
    # in the well must never be mistaken for the next (possibly hidden) piece.
    for y in range(BOARD_HEIGHT):
        line = rows[y0 + y]
        cells: list[str | None] = []
        for x in range(BOARD_WIDTH):
            a = _background_piece(line[x0 + 1 + 2 * x][1])
            b = _background_piece(line[x0 + 2 + 2 * x][1])
            if a == b:
                cells.append(a)
            elif a is None:
                cells.append(b)
            elif b is None:
                cells.append(a)
            else:
                cells.append(None)
        board.append(cells)
    return board


def _read_next_piece(rows: list[list[tuple[str, object]]], origin) -> str | None:
    """Read the preview box right of the well, or None when not confident.

    Bastet paints each preview cell with the same per-tetromino colour as the
    well and the palette is one colour per piece, so a single recognised colour
    identifies the next block. Two different colours in the window mean the
    region is mislocated (or something else there is coloured); report "not
    confident" so the caller falls back to single-ply search.
    """
    y0, x0 = origin
    found = None
    for y in range(y0, min(len(rows), y0 + BOARD_HEIGHT)):
        row = rows[y]
        for x in range(x0 + NEXT_PIECE_MIN_DX, min(len(row), x0 + NEXT_PIECE_MAX_DX)):
            piece = _background_piece(row[x][1])
            if piece is None:
                continue
            if found is None:
                found = piece
            elif found != piece:
                return None
    return found


def parse_screen(raw: str):
    """Read (well board, next piece) from a styled Bastet screen."""
    rows = _styled_rows(raw)
    origin = _well_origin(rows)
    if origin is None:
        return None, None
    return _read_well(rows, origin), _read_next_piece(rows, origin)


def parse_board(raw: str) -> list[list[str | None]] | None:
    """Read occupied piece colors from a styled Bastet screen."""
    return parse_screen(raw)[0]


def _find_active(board: list[list[str | None]]):
    """Find the current piece at its fixed x=3, orientation-0 spawn path.

    An identical settled shape above the falling piece would occupy its descent
    path and prevent it reaching a lower position. Choosing the highest exact
    colored shape therefore disambiguates it without cross-cycle memory.
    """
    for y in range(MIN_ACTIVE_ANCHOR_Y, MAX_ACTIVE_ANCHOR_Y + 1):
        for piece, orientations in SHAPES.items():
            points = tuple((SPAWN_X + dx, y + dy) for dx, dy in orientations[0])
            # Cells above the well are not drawn; the colored visible cells
            # must match exactly and be enough to tell the shape apart.
            visible = tuple((col, row) for col, row in points if row >= 0)
            if len(visible) >= MIN_VISIBLE_ACTIVE_CELLS and all(
                row < BOARD_HEIGHT
                and 0 <= col < BOARD_WIDTH
                and board[row][col] == piece
                for col, row in visible
            ):
                return piece, SPAWN_X, y, 0, visible
    return None


def _fits(board, shape, x: int, y: int) -> bool:
    for dx, dy in shape:
        col, row = x + dx, y + dy
        if col < 0 or col >= BOARD_WIDTH or row >= BOARD_HEIGHT:
            return False
        # Rows above the well are open space for a piece that is still entering.
        if row >= 0 and board[row][col] is not None:
            return False
    return True


def _lock_and_clear(board, shape, x: int, y: int, piece: str):
    if any(y + dy < 0 for _, dy in shape):
        return None  # locking above the well tops out
    locked = [row.copy() for row in board]
    for dx, dy in shape:
        locked[y + dy][x + dx] = piece
    remaining = [row for row in locked if not all(cell is not None for cell in row)]
    cleared = BOARD_HEIGHT - len(remaining)
    return [[None] * BOARD_WIDTH for _ in range(cleared)] + remaining, cleared


def _well_features(board):
    """Row/column transitions, holes, wells and column heights in one pass."""
    heights = [0] * BOARD_WIDTH
    holes = 0
    for x in range(BOARD_WIDTH):
        occupied = False
        for y in range(BOARD_HEIGHT):
            if board[y][x] is not None:
                if not occupied:
                    occupied = True
                    heights[x] = BOARD_HEIGHT - y
            elif occupied:
                holes += 1
    row_transitions = 0
    for y in range(BOARD_HEIGHT):
        previous = True  # the left wall counts as filled
        for x in range(BOARD_WIDTH):
            current = board[y][x] is not None
            if current != previous:
                row_transitions += 1
            previous = current
        if not previous:  # the right wall counts as filled
            row_transitions += 1
    column_transitions = 0
    for x in range(BOARD_WIDTH):
        previous = False  # the ceiling counts as empty
        for y in range(BOARD_HEIGHT):
            current = board[y][x] is not None
            if current != previous:
                column_transitions += 1
            previous = current
        if not previous:  # the floor counts as filled
            column_transitions += 1
    well_sums = 0
    for x in range(BOARD_WIDTH):
        for y in range(BOARD_HEIGHT):
            if board[y][x] is not None:
                continue
            if (x == 0 or board[y][x - 1] is not None) and (
                x == BOARD_WIDTH - 1 or board[y][x + 1] is not None
            ):
                well_sums += 1
                below = y + 1
                while below < BOARD_HEIGHT and board[below][x] is None:
                    well_sums += 1
                    below += 1
    return heights, row_transitions, column_transitions, holes, well_sums


def _board_value(board, cleared: int, landing_height: float, weights: dict) -> float:
    _heights, row_transitions, column_transitions, holes, well_sums = _well_features(board)
    return (
        weights["rows_cleared"] * cleared
        + weights["landing_height"] * landing_height
        + weights["row_transitions"] * row_transitions
        + weights["column_transitions"] * column_transitions
        + weights["holes"] * holes
        + weights["well_sums"] * well_sums
    )


def _placements(static, piece: str, start):
    """Every reachable landing position with the key path that reaches it.

    The search walks Bastet's move set (Left/Right/rotate CW/CCW) at the spawn
    height and hard-drops from each visited state, so a placement is reachable
    exactly when the same key sequence reaches it in the game. States that land
    identically collapse to their shortest key sequence.
    """
    start_x, start_y, start_o = start
    queue = deque([(start_x, start_y, start_o)])
    previous = {(start_x, start_y, start_o): None}
    move_keys = (
        (-1, 0, 0, "Left"),
        (1, 0, 0, "Right"),
        (0, 0, 1, "Space"),
        (0, 0, -1, "Up"),
    )
    landings = {}
    while queue:
        state = queue.popleft()
        x, y, orientation = state
        path = []
        cursor = state
        while previous[cursor] is not None:
            parent, key = previous[cursor]
            path.append(key)
            cursor = parent
        path.reverse()

        shape = SHAPES[piece][orientation]
        landing_y = y
        while _fits(static, shape, x, landing_y + 1):
            landing_y += 1
        if all(landing_y + dy >= 0 for _, dy in shape):
            landing = (x, landing_y, orientation)
            if landing not in landings or len(path) < len(landings[landing]):
                landings[landing] = path

        for dx, dy, turn, key in move_keys:
            next_orientation = (orientation + turn) % 4
            next_state = (x + dx, y + dy, next_orientation)
            if next_state in previous:
                continue
            next_shape = SHAPES[piece][next_orientation]
            if (
                (dx == 0 and dy == 0 and next_shape == shape)
                or not _fits(static, next_shape, next_state[0], next_state[1])
            ):
                continue
            previous[next_state] = (state, key)
            queue.append(next_state)
    return [(x, y, o, path) for (x, y, o), path in landings.items()]


def _landing_height(shape, landing_y: int) -> float:
    """Height of the piece's centre above the floor (0.5 rests on the floor)."""
    rows = [landing_y + dy for _, dy in shape]
    return BOARD_HEIGHT - 0.5 - (min(rows) + max(rows)) / 2.0


def _single_ply(static, piece: str, weights: dict):
    """Best (value, path) that `piece` can reach on `static`, without lookahead."""
    best_value = float("-inf")
    best_path = None
    for x, landing_y, orientation, path in _placements(
        static, piece, (SPAWN_X, MIN_ACTIVE_ANCHOR_Y, 0)
    ):
        locked = _lock_and_clear(static, SHAPES[piece][orientation], x, landing_y, piece)
        if locked is None:
            continue
        settled, cleared = locked
        value = _board_value(
            settled, cleared, _landing_height(SHAPES[piece][orientation], landing_y), weights,
        )
        if value > best_value or (
            value == best_value and (best_path is None or len(path) < len(best_path))
        ):
            best_value, best_path = value, path
    return best_value, best_path


def _followup_value(static, piece: str, weights: dict) -> float:
    """Best value `piece` could still reach on `static` (the 2-ply term)."""
    value, _path = _single_ply(static, piece, weights)
    if value == float("-inf"):
        return -1e9
    return value


def plan_keys(board: list[list[str | None]], next_piece: str | None = None,
              weights: dict | None = None) -> list[str] | None:
    """Choose a reachable placement that clears lines and keeps the well open."""
    active = _find_active(board)
    if active is None:
        return None
    weights = weights or load_weights()
    piece, start_x, start_y, start_o, points = active
    static = [row.copy() for row in board]
    for col, row in points:
        if row >= 0:
            static[row][col] = None

    lookahead = weights["next_piece_weight"]
    use_lookahead = next_piece is not None and lookahead != 0

    best_value = float("-inf")
    best_path = None
    for x, landing_y, orientation, path in _placements(
        static, piece, (start_x, start_y, start_o)
    ):
        locked = _lock_and_clear(static, SHAPES[piece][orientation], x, landing_y, piece)
        if locked is None:
            continue
        settled, cleared = locked
        value = _board_value(
            settled, cleared, _landing_height(SHAPES[piece][orientation], landing_y), weights,
        )
        if use_lookahead:
            value += lookahead * _followup_value(settled, next_piece, weights)
        value += weights["path_length"] * len(path)
        if value > best_value or (
            value == best_value and (best_path is None or len(path) < len(best_path))
        ):
            best_value, best_path = value, path

    if best_path is None or len(best_path) > MAX_PLAN_KEYS:
        return None
    return best_path


def decide(text: str, weights: dict, color_text: str | None = None,
           next_piece: str | None = None) -> list[str] | None:
    lower = text.lower()
    if any(marker in lower for marker in MENU_MARKERS):
        return None
    # The ACS side border is often captured as the letter "x", glued to Score.
    if not SCORE_RE.search(text):
        return None
    if color_text is None:
        # Without cell colors, any gameplay key would repeat the old blind-drop
        # policy; wait for the next observation instead.
        return None
    board, preview = parse_screen(color_text)
    if board is None:
        return None
    plan = plan_keys(board, preview if next_piece is None else next_piece, weights)
    if plan is None:
        # In colored mode, do not send blind inputs when the piece is hidden or
        # cannot be identified from the current board.
        return None
    if weights["hard_drop"] < 0.5:
        return ["Down"]
    return plan + ["Enter"]


def main() -> int:
    code = 0
    keys = None
    try:
        obs = json.load(sys.stdin)
        if not isinstance(obs, dict) or not isinstance(obs.get("text"), str):
            code = 2
        else:
            meta = obs.get("meta")
            color_text = meta.get("bastet_color_text") if isinstance(meta, dict) else None
            if color_text is not None and not isinstance(color_text, str):
                color_text = None
            keys = decide(obs["text"], load_weights(), color_text)
    except (ValueError, OSError, RecursionError):
        code = 2
    actions = [] if not keys else [{"type": "key", "keys": keys}]
    print(json.dumps({"actions": actions}), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
