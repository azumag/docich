"""Offline core for the ぷよぷよクローン corner (Issue #698).

No Twitch I/O, no shell execution, no game switch, no production writes.
Board engine + match state machine + fail-closed validation + ledger only.
Wiring into a live corner tick (reading the chat daemon, rendering the
960x540 view, TTS, GameSwitch) is a separate, explicitly enabled step — see
``docs/superpowers/specs/2026-10-18-puyo-puyo-clone-corner-design.md``.

Precedent: ``trpg_corner.py`` (Issue #684 offline core: state machine +
ledger + fail-closed validation, no live I/O).

Ruleset shape follows the synthetic input contract fixture
``tests/fixtures/puyo_puyo_clone_corner/``:

* 6x12 visible board + 1 hidden spawn row,
* 2-block pairs (axis + satellite) falling in real time,
* 4 or more orthogonally adjacent same-color puyo clear at once,
* cleared boards settle per column and the settle→clear loop re-evaluates
  every step, so chains 1, 2, 3, ... build up,
* scoring: perPuyo per cleared block + chain / multi-clear / color bonuses,
  + the all-clear bonus when a full chain empties the board,
* top-out ends the round; the surviving side wins (both top out -> draw).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
STATE_FILE = "puyo_corner_state.json"
LEDGER_FILE = "puyo_corner_ledger.json"
HISTORY_FILE = "puyo_corner_history.jsonl"
LOCK_FILE = "puyo_corner.lock"

# --- bounded board (fail-closed; the contract fixture pins these values) ---
BOARD_COLS = 6
BOARD_ROWS = 12
HIDDEN_ROWS = 1
MAX_COLS = 8
MAX_ROWS = 20
PIECE_SIZE = 2
MIN_CONNECTION = 4
MIN_COLORS = 2
MAX_COLORS = 4

# --- bounded progress (fail-closed; no unbounded loop inside a tick) ---
MAX_ACTIONS = 2048
MAX_CHAIN_STEPS = (BOARD_ROWS * BOARD_COLS) // MIN_CONNECTION + 1
MAX_PAIRS_PER_ROUND = BOARD_ROWS * BOARD_COLS // PIECE_SIZE + 8

PHASES = ("IDLE", "MATCH", "ROUND_OVER", "END", "FAILED", "TIMEOUT")
PHASE_SET = frozenset(PHASES)
TRANSITIONS: dict[str, frozenset[str]] = {
    "IDLE": frozenset({"MATCH"}),
    "MATCH": frozenset({"ROUND_OVER", "FAILED", "TIMEOUT"}),
    "ROUND_OVER": frozenset({"END"}),
    "END": frozenset(),
    "FAILED": frozenset(),
    "TIMEOUT": frozenset(),
}
OUTCOMES = frozenset({
    "user_win", "ai_win", "draw", "completed", "failed", "timeout",
})
ACTIONS = frozenset({
    "left", "right", "rotate_cw", "rotate_ccw", "soft_drop", "hard_drop",
})
SIDES = ("user", "ai")
MAX_HISTORY_ENTRIES = 500
MAX_BYTES = 256 * 1024

# Built-in ruleset: new/legacy states stay usable without an explicit config.
# Only the section defaults are inherited; present values are never widened.
DEFAULT_RULESET: dict[str, Any] = {
    "colors": ["red", "blue", "green", "yellow"],
    "board": {"columns": BOARD_COLS, "rows": BOARD_ROWS, "hiddenRows": HIDDEN_ROWS},
    "piecePair": {"size": PIECE_SIZE},
    "chainMechanics": {
        "minConnection": MIN_CONNECTION,
        "simultaneousClear": True,
        "gravitySettle": True,
        "chainReevaluation": True,
    },
    "scoring": {
        "perPuyo": 10,
        "chainBonus": "bounded table",
        "simultaneousBonus": "bounded table",
        "allClearBonus": "bounded table",
    },
    "winCondition": "the opponent's spawn is blocked by settled puyo",
    "lossCondition": "own spawn cannot place a new pair",
}

# Per-chain power in the scoring table (chain 1, 2, 3, ... at min index 1).
CHAIN_BONUS = {1: 0, 2: 8, 3: 96, 4: 192, 5: 960, 6: 1440, 7: 1920}
MULTI_CLEAR_BONUS = {1: 0, 2: 240, 3: 480, 4: 960}
COLOR_BONUS = {1: 0, 2: 120, 3: 720, 4: 1200}
ALL_CLEAR_BONUS = 2100
SPAWN_COL = BOARD_COLS // 2 - 1


class PuyoCornerError(ValueError):
    """The ruleset, board or state violates the corner contract."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class RulesetError(PuyoCornerError):
    """The cached ruleset digest does not match the live engine."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, what: str, *, limit: int = 200) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= limit):
        raise PuyoCornerError(f"invalid {what}")
    return value


def _int(value: Any, what: str, *, lo: int | None = None, hi: int | None = None) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise PuyoCornerError(f"invalid {what}")
    if lo is not None and value < lo:
        raise PuyoCornerError(f"invalid {what}")
    if hi is not None and value > hi:
        raise PuyoCornerError(f"invalid {what}")
    return value


def _dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise PuyoCornerError(f"invalid {what}")
    return value


def _list(value: Any, what: str, *, lo: int, hi: int) -> list:
    if not isinstance(value, list) or not (lo <= len(value) <= hi):
        raise PuyoCornerError(f"invalid {what}")
    return value


def _side(value: Any) -> str:
    side = _text(value, "side", limit=8)
    if side not in SIDES:
        raise PuyoCornerError("invalid side")
    return side


def empty_board() -> list[list[str | None]]:
    return [[None] * BOARD_COLS for _ in range(BOARD_ROWS)]


def copy_board(board: Any) -> list[list[str | None]]:
    """Deep-copy a validated board. Never trusts caller-owned nested lists."""
    if not isinstance(board, list) or len(board) != BOARD_ROWS:
        raise PuyoCornerError("invalid board")
    out: list[list[str | None]] = []
    for row in board:
        if not isinstance(row, list) or len(row) != BOARD_COLS:
            raise PuyoCornerError("invalid board row")
        for cell in row:
            if cell is not None and not isinstance(cell, str):
                raise PuyoCornerError("invalid board cell")
        out.append(list(row))
    return out


def render_board(board: Any, marker: str = "*") -> str:
    """ASCII render (hidden row first). One row per line, bounded.

    The falling pair is not part of ``board``; callers overlay it themselves.
    """
    board = copy_board(board)
    lines = []
    for row in board:
        lines.append("".join(cell if cell else marker for cell in row))
    return "\n".join(lines)


def blocks_left(board: list[list[str | None]]) -> int:
    return sum(1 for row in board for cell in row if cell is not None)


# ---------------------------------------------------------------------------
# ruleset validation (fixture-derived, fail-closed)
# ---------------------------------------------------------------------------


def _require_true(mapping: Mapping[str, Any], key: str) -> None:
    """The contract fixture commits to these booleans; relax nothing silently."""
    if mapping.get(key) is not True:
        raise PuyoCornerError(f"ruleset {key} must be true")


def validate_ruleset(raw: Any) -> dict:
    """Validate and normalize a ruleset. Fail-closed.

    Missing top-level sections fall back to the built-in DEFAULT_RULESET
    (fresh states and legacy saved states stay usable); present values are
    validated as-is and never silently widened.
    """
    raw = _dict(raw, "ruleset")
    if "colors" not in raw:
        raw = {**raw, "colors": list(DEFAULT_RULESET["colors"])}
    for missing, fallback in (
        ("board", "board"),
        ("piecePair", "piecePair"),
        ("chainMechanics", "chainMechanics"),
        ("scoring", "scoring"),
        ("winCondition", "winCondition"),
        ("lossCondition", "lossCondition"),
    ):
        if missing not in raw:
            raw = {**raw, missing: DEFAULT_RULESET[fallback]}
    colors = _list(raw.get("colors"), "colors", lo=MIN_COLORS, hi=MAX_COLORS)
    for color in colors:
        _text(color, "color", limit=32)
    if len(set(colors)) != len(colors):
        raise PuyoCornerError("duplicate color")

    board_req = _dict(raw.get("board", {}), "board")
    cols = _int(board_req.get("columns", BOARD_COLS), "board.columns", lo=1, hi=MAX_COLS)
    rows = _int(board_req.get("rows", BOARD_ROWS), "board.rows", lo=1, hi=MAX_ROWS)
    hidden = _int(board_req.get("hiddenRows", HIDDEN_ROWS), "board.hiddenRows", lo=0, hi=4)
    if cols != BOARD_COLS or rows != BOARD_ROWS:
        raise PuyoCornerError("unsupported board shape")
    if hidden != HIDDEN_ROWS:
        raise PuyoCornerError("unsupported hidden row count")

    piece_req = _dict(raw.get("piecePair", {}), "piecePair")
    size = _int(piece_req.get("size", PIECE_SIZE), "piecePair.size",
                lo=PIECE_SIZE, hi=PIECE_SIZE)
    if size != PIECE_SIZE:
        raise PuyoCornerError("unsupported piece size")

    chain_req = _dict(raw.get("chainMechanics", {}), "chainMechanics")
    if _int(chain_req.get("minConnection", MIN_CONNECTION),
            "chainMechanics.minConnection", lo=MIN_CONNECTION, hi=MIN_CONNECTION) \
            != MIN_CONNECTION:
        raise PuyoCornerError("unsupported minConnection")
    for key in ("simultaneousClear", "gravitySettle", "chainReevaluation"):
        _require_true(chain_req, key)

    scoring_req = _dict(raw.get("scoring", {}), "scoring")
    per_puyo = _int(scoring_req.get("perPuyo", 10), "scoring.perPuyo", lo=0, hi=1000)
    for key in ("chainBonus", "simultaneousBonus", "allClearBonus"):
        # Descriptors only: exact tables live in this module, fail-closed.
        if scoring_req.get(key, "bounded") is None:
            raise PuyoCornerError(f"invalid scoring.{key}")

    for key in ("winCondition", "lossCondition"):
        _text(raw.get(key, ""), key)

    normalized = dict(raw)
    normalized["board"] = {
        "columns": cols, "rows": rows, "hiddenRows": hidden,
    }
    normalized["piecePair"] = {"size": size, "axisPlusSatellite": True}
    normalized["chainMechanics"] = {
        "minConnection": MIN_CONNECTION,
        "simultaneousClear": True,
        "gravitySettle": True,
        "chainReevaluation": True,
    }
    normalized["scoring"] = {**scoring_req, "perPuyo": per_puyo}
    return normalized


def ruleset_digest(ruleset: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(ruleset), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# pairs
# ---------------------------------------------------------------------------


def spawn_pair(colors: Sequence[str], rng: Any = None,
               now: str | None = None) -> dict:
    """Spawn a fresh pair in the hidden row. ``rng`` keeps replays offline.

    ``rng`` may be ``None`` (module-level ``random``), an int seed, or a live
    ``random.Random``. The engine never mutates a foreign Random.
    """
    colors = _list(list(colors), "colors", lo=MIN_COLORS, hi=MAX_COLORS)
    chooser = random if rng is None else (
        rng if isinstance(rng, random.Random) else random.Random(rng))
    axis = colors[chooser.randrange(len(colors))]
    satellite = colors[chooser.randrange(len(colors))]
    return {
        "axis": axis,
        "axis_pos": [SPAWN_COL, 0],
        "satellite": satellite,
        "satellite_pos": [SPAWN_COL + 1, 0],
        "spawned_at": now or _now(),
        "spawn_digest": ":".join((axis, satellite)),
    }


def pair_cells(pair: Mapping[str, Any]) -> list[tuple[int, int]]:
    """[(x, y)] for the axis + satellite cells. Bounded and fail-closed."""
    pair = _dict(pair, "pair")
    cells = []
    for key in ("axis_pos", "satellite_pos"):
        pos = _list(pair.get(key, []), key, lo=2, hi=2)
        x = _int(pos[0], f"{key}.x", lo=0, hi=BOARD_COLS - 1)
        y = _int(pos[1], f"{key}.y", lo=0, hi=BOARD_ROWS - 1)
        cells.append((x, y))
    if cells[0] == cells[1]:
        raise PuyoCornerError("pair cells overlap")
    return cells


def _can_place(board: list[list[str | None]], x: int, y: int) -> bool:
    if not (0 <= x < BOARD_COLS) or not (0 <= y < BOARD_ROWS):
        return False
    return board[y][x] is None


def _can_place_pair(board: list[list[str | None]],
                    cells: Sequence[tuple[int, int]]) -> bool:
    return all(_can_place(board, x, y) for x, y in cells)


def _bottomout_cells(board: list[list[str | None]],
                     cells: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Lowest reachable position for a pair whose cells fall together."""
    cur = [(x, y) for (x, y) in cells]
    while True:
        lowered = [(x, y + 1) for (x, y) in cur]
        if not _can_place_pair(board, lowered):
            return cur
        cur = lowered


# ---------------------------------------------------------------------------
# chain resolution (settle → clear → re-evaluate, bounded)
# ---------------------------------------------------------------------------


def _settle(board: list[list[str | None]]) -> None:
    """One blast: cells fall straight down per column, in settled parity."""
    for x in range(BOARD_COLS):
        column = [board[y][x] for y in range(BOARD_ROWS)]
        kept = [cell for cell in column if cell is not None]
        pad = [None] * (BOARD_ROWS - len(kept))
        for y in range(BOARD_ROWS):
            board[y][x] = pad[y] if y < len(pad) else kept[y - len(pad)]


def connected_groups(board: list[list[str | None]]) -> list[set[tuple[int, int]]]:
    """All maximal orthogonally connected same-color groups."""
    seen: set[tuple[int, int]] = set()
    groups: list[set[tuple[int, int]]] = []
    for y in range(BOARD_ROWS):
        for x in range(BOARD_COLS):
            color = board[y][x]
            if color is None or (x, y) in seen:
                continue
            stack = [(x, y)]
            group: set[tuple[int, int]] = {(x, y)}
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < BOARD_COLS and 0 <= ny < BOARD_ROWS \
                            and (nx, ny) not in seen and board[ny][nx] == color:
                        seen.add((nx, ny))
                        group.add((nx, ny))
                        stack.append((nx, ny))
            groups.append(group)
    return groups


def _clear_group(board: list[list[str | None]],
                 group: set[tuple[int, int]]) -> tuple[int, set[str]]:
    """Clear one group (fail-closed against over-clearing)."""
    if len(group) < MIN_CONNECTION:
        raise PuyoCornerError("group below min connection")
    if not all(0 <= x < BOARD_COLS and 0 <= y < BOARD_ROWS for x, y in group):
        raise PuyoCornerError("group outside the board")
    colors: set[str] = set()
    for x, y in sorted(group):
        cell = board[y][x]
        if cell is None:
            raise PuyoCornerError("clearing an empty cell")
        colors.add(cell)
        board[y][x] = None
    return len(group), colors


def _chain_score(ruleset: Mapping[str, Any], chain_index: int,
                 groups: list[int], colors: set[str]) -> int:
    """perPuyo per cell + chain + multi-clear + color bonus, all bounded."""
    per_puyo = _int(ruleset["scoring"]["perPuyo"], "scoring.perPuyo", lo=0, hi=1000)
    blocks = sum(groups)
    group_capped = min(len(groups), max(MULTI_CLEAR_BONUS))
    color_capped = min(len(colors), max(COLOR_BONUS))
    bonus = CHAIN_BONUS.get(min(chain_index, max(CHAIN_BONUS)), CHAIN_BONUS[max(CHAIN_BONUS)])
    bonus += MULTI_CLEAR_BONUS.get(group_capped, MULTI_CLEAR_BONUS[max(MULTI_CLEAR_BONUS)])
    bonus += COLOR_BONUS.get(color_capped, COLOR_BONUS[max(COLOR_BONUS)])
    return per_puyo * blocks + bonus


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


def apply_action(state: dict, side: str, action: str) -> dict:
    """Apply one move/side action for the falling pair. Fail-closed."""
    state = _dict(state, "state")
    if state.get("phase") != "MATCH":
        raise PuyoCornerError("apply_action requires MATCH phase")
    side = _side(side)
    action = _text(action, "action", limit=16)
    if action not in ACTIONS:
        raise PuyoCornerError("invalid action")
    actions_used = _int(state.get("actions", 0), "actions", lo=0, hi=MAX_ACTIONS)
    if actions_used >= MAX_ACTIONS:
        raise PuyoCornerError("action budget exhausted", "action_budget")
    board = copy_board(state[f"{side}_board"])
    pair = _dict(state[f"{side}_pair"], "pair")
    cells = pair_cells(pair)

    if action in ("left", "right"):
        direction = -1 if action == "left" else 1
        moved = [(x + direction, y) for (x, y) in cells]
        if _can_place_pair(board, moved):
            pair["axis_pos"] = list(cells[0]) if action == "left" else list(moved[0])
            pair["satellite_pos"] = list(cells[1]) if action == "left" else list(moved[1])
            if action == "left":
                pair["axis_pos"][0] += direction
                pair["satellite_pos"][0] += direction
    elif action in ("rotate_cw", "rotate_ccw"):
        _rotate(board, pair, cells, action)
    elif action == "soft_drop":
        lowered = [(x, y + 1) for (x, y) in cells]
        if _can_place_pair(board, lowered):
            pair["axis_pos"] = list(lowered[0])
            pair["satellite_pos"] = list(lowered[1])
        # otherwise fall back to a lock right away (advance handles it)
    elif action == "hard_drop":
        cells = _bottomout_cells(board, cells)
        pair["axis_pos"] = list(cells[0])
        pair["satellite_pos"] = list(cells[1])

    state[f"{side}_board"] = board
    state[f"{side}_pair"] = pair
    state["actions"] = actions_used + 1
    return state


def _rotate(board: list[list[str | None]], pair: dict,
            cells: list[tuple[int, int]], action: str) -> None:
    """Rotate the satellite around the axis, wall/stack kick fail-closed."""
    (ax, ay), (sx, sy) = cells
    rel_x, rel_y = sx - ax, sy - ay
    if abs(rel_x) + abs(rel_y) != 1:
        raise PuyoCornerError("satellite not adjacent to the axis")
    if action == "rotate_cw":
        new_rel = (-rel_y, rel_x)
    else:
        new_rel = (rel_y, -rel_x)
    target = (ax + new_rel[0], ay + new_rel[1])
    kicks = ((0, 0), (0, -1), (-1, 0), (1, 0), (0, 1))
    for dx, dy in kicks:
        candidate = (target[0] + dx, target[1] + dy)
        trial = [(ax, ay), candidate]
        if _can_place_pair(board, trial) and candidate != (ax, ay):
            pair["satellite_pos"] = [candidate[0], candidate[1]]
            return
    # No kick fits: the rotation is a no-op, not an error. Keep both cells.
    pair["axis_pos"] = [ax, ay]
    pair["satellite_pos"] = [sx, sy]


def tick(state: Any, now: str | None = None) -> dict:
    """One gravity tick for both sides during MATCH.

    Falls unlocked pairs one row, locks landed pairs, resolves their chain,
    refills pairs from the state-owned RNG stream, and ends the round on a
    top-out. The live corner owns the timer; the engine owns the mechanics.
    """
    state = _dict(state, "state")
    if state.get("phase") != "MATCH":
        raise PuyoCornerError("tick requires MATCH phase")
    ts = now or _now()
    state["frames"] = _int(state.get("frames", 0), "frames", lo=0, hi=1 << 30) + 1
    for side in SIDES:
        if state.get("phase") != "MATCH":
            break
        _tick_side(state, side, ts)
    if state["phase"] == "MATCH":
        state = _check_round_end(state, ts)
    return state


def _tick_side(state: dict, side: str, ts: str) -> None:
    board_key, pair_key = f"{side}_board", f"{side}_pair"
    board = copy_board(state[board_key])
    pair = _dict(state[pair_key], "pair")
    cells = pair_cells(pair)
    lowered = [(x, y + 1) for (x, y) in cells]
    if _can_place_pair(board, lowered):
        pair["axis_pos"] = list(lowered[0])
        pair["satellite_pos"] = list(lowered[1])
        state[pair_key] = pair
        state[board_key] = board
        return
    _lock_and_resolve(state, side, board, pair, cells, ts)


def _lock_and_resolve(state: dict, side: str, board: list[list[str | None]],
                      pair: dict, cells: list[tuple[int, int]], ts: str) -> None:
    """Lock the pair, resolve its chain, then refill (bounded, fail-closed)."""
    board_key = f"{side}_board"
    landed = _bottomout_cells(board, cells)
    _lock_pair(board, pair, landed)
    ruleset = _dict(state["ruleset"], "ruleset")
    blocks, chain_depth, bonus, all_clear = _resolve_chain(board, ruleset)
    if blocks:
        state[f"{side}_chain"] = chain_depth
        state[f"{side}_score"] = _int(
            state.get(f"{side}_score", 0) + bonus, f"{side}_score", lo=0, hi=10 ** 9,
        )
        if all_clear:
            state[f"{side}_score"] = _int(
                state.get(f"{side}_score", 0) + ALL_CLEAR_BONUS,
                f"{side}_score", lo=0, hi=10 ** 9,
            )
    else:
        state[f"{side}_chain"] = 0
    state[board_key] = board
    pairs_spawned = _int(state.get("pairs_spawned", 0), "pairs_spawned",
                         lo=0, hi=MAX_PAIRS_PER_ROUND)
    if pairs_spawned >= MAX_PAIRS_PER_ROUND:
        raise PuyoCornerError("pairs per round exhausted", "pair_budget")
    state["pairs_spawned"] = pairs_spawned + 1
    next_pair = _refill_pair(state, side, ts)
    if not _can_place_pair(board, pair_cells(next_pair)):
        _record_top_out(state, side)
    return


def _lock_pair(board: list[list[str | None]], pair: Mapping[str, Any],
               cells: Sequence[tuple[int, int]]) -> None:
    """Commit the pair cells into the board. Fail-closed."""
    if not _can_place_pair(board, [(x, y) for (x, y) in cells]):
        raise PuyoCornerError("lock overlaps an occupied cell")
    colors = [pair["axis"], pair["satellite"]]
    for (x, y), color in zip(cells, colors):
        board[y][x] = _text(color, "block color", limit=32)


def _resolve_chain(board: list[list[str | None]],
                   ruleset: Mapping[str, Any]) -> tuple[int, int, int, bool]:
    """Settle → clear until stable. Returns (blocks, chain, score, all_clear)."""
    total_blocks = 0
    chain = 0
    score = 0
    all_clear = False
    while chain < MAX_CHAIN_STEPS:
        _settle(board)
        clearable = [
            group for group in connected_groups(board)
            if len(group) >= MIN_CONNECTION
        ]
        if not clearable:
            break
        chain += 1
        cleared_counts: list[int] = []
        colors: set[str] = set()
        for group in clearable:
            count, group_colors = _clear_group(board, group)
            cleared_counts.append(count)
            colors |= group_colors
        total_blocks += sum(cleared_counts)
        score += _chain_score(ruleset, chain, cleared_counts, colors)
        if blocks_left(board) == 0:
            all_clear = True
    return total_blocks, chain, score, all_clear


def _record_top_out(state: dict, side: str) -> None:
    state["round_results"].append({
        "side": side, "reason": "top_out", "at": _now(),
    })
    state[f"{side}_pair"] = None
    state[f"{side}_next"] = None


def _refill_pair(state: dict, side: str, ts: str) -> dict:
    """Promote the pending next pair, drawing from the state-owned RNG."""
    pair = state.get(f"{side}_next")
    if pair is None:
        pair = _draw_pair(state, side, ts)
    state[f"{side}_pair"] = pair
    state[f"{side}_next"] = _draw_pair(state, side, ts)
    return state[f"{side}_pair"]


def _draw_pair(state: dict, side: str, ts: str) -> dict:
    colors = state["ruleset"]["colors"]
    chooser = random.Random(state.get("rng", 0))
    pair = spawn_pair(colors, rng=chooser, now=ts)
    state["rng"] = chooser.getrandbits(64)
    return pair


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------


def _transition(state: dict, to: str) -> dict:
    phase = state.get("phase")
    if phase not in PHASE_SET:
        raise PuyoCornerError("invalid state phase")
    if to not in TRANSITIONS.get(phase, frozenset()):
        raise PuyoCornerError(f"invalid transition {phase} -> {to}")
    state["phase"] = to
    state["phase_started_at"] = _now()
    return state


def _check_round_end(state: dict, ts: str) -> dict:
    """A top-out ends the round; the surviving side wins (both -> draw)."""
    if state.get("phase") != "MATCH":
        return state
    top_out = {
        result["side"]
        for result in state.get("round_results", [])
        if result.get("reason") == "top_out"
    }
    if not top_out:
        return state
    user_out, ai_out = "user" in top_out, "ai" in top_out
    if user_out and ai_out:
        winner = "draw"
    elif user_out:
        winner = "ai"
    else:
        winner = "user"
    state["winner"] = winner
    state["round_results"].append({
        "side": winner, "reason": "round_complete",
        "user_score": state.get("user_score"),
        "ai_score": state.get("ai_score"), "at": ts,
    })
    return _transition(state, "ROUND_OVER")


def new_state(config: Mapping[str, Any] | None = None, seed: int | None = None) -> dict:
    """Fresh corner state. ``config`` follows the fixture contract."""
    cfg = _validate_config(config or {})
    seed = 0 if seed is None else _int(seed, "seed", lo=0, hi=1 << 64)
    return {
        "schema": SCHEMA,
        "phase": "IDLE",
        "ruleset_id": cfg["ruleset_id"],
        "ruleset": cfg["ruleset"],
        "gravity_ms": cfg["gravity_ms"],
        "gravity_pending": cfg["gravity_pending"],
        "seed": seed,
        "rng": seed,
        "round": 0,
        "frames": 0,
        "actions": 0,
        "pairs_spawned": 0,
        "started_at": None,
        "phase_started_at": None,
        "ended_at": None,
        "outcome": None,
        "failure_reason": None,
        "winner": None,
        "round_results": [],
        "user_board": empty_board(),
        "user_pair": None,
        "user_next": None,
        "user_score": 0,
        "user_chain": 0,
        "ai_board": empty_board(),
        "ai_pair": None,
        "ai_next": None,
        "ai_score": 0,
        "ai_chain": 0,
    }


def _validate_config(raw: Any) -> dict:
    raw = _dict(raw, "config")
    ruleset = validate_ruleset(raw.get("ruleset", {}))
    gravity_ms = _int(raw.get("gravity_ms", 700), "gravity_ms", lo=50, hi=10_000)
    gravity_pending = raw.get("gravity_pending", 0)
    if gravity_pending not in (0, 1):
        raise PuyoCornerError("invalid gravity_pending")
    ruleset_id = _text(raw.get("ruleset_id", ruleset_digest(ruleset)),
                       "ruleset_id", limit=128)
    return {
        "ruleset": ruleset,
        "ruleset_id": ruleset_id,
        "gravity_ms": gravity_ms,
        "gravity_pending": gravity_pending,
    }


def start(state: Any, now: str | None = None) -> dict:
    """IDLE -> MATCH round 1. Spawns both sides' first pair."""
    state = _dict(state, "state")
    if state.get("phase") != "IDLE":
        raise PuyoCornerError("start requires IDLE phase")
    ts = now or _now()
    state["started_at"] = ts
    state["round"] = 1
    state["frames"] = 0
    state["actions"] = 0
    state["pairs_spawned"] = 0
    state["winner"] = None
    state["round_results"] = []
    for side in SIDES:
        state[f"{side}_board"] = copy_board(state.get(f"{side}_board") or empty_board())
        pair = _refill_pair(state, side, ts)
        if not _can_place_pair(state[f"{side}_board"], pair_cells(pair)):
            raise PuyoCornerError("spawn blocked at round start")
        state[f"{side}_chain"] = 0
    state["phase_started_at"] = ts
    state["phase"] = "MATCH"
    return state


def finish(state: Any, outcome: str = "completed", now: str | None = None) -> dict:
    """ROUND_OVER -> END. Persists the outcome."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase == "ROUND_OVER":
        _transition(state, "END")
    elif phase != "END":
        raise PuyoCornerError("finish requires ROUND_OVER or END phase")
    if outcome not in OUTCOMES:
        raise PuyoCornerError("invalid outcome")
    if state.get("winner") in ("user", "ai", "draw"):
        outcome = {"user": "user_win", "ai": "ai_win", "draw": "draw"}[state["winner"]]
    state["outcome"] = outcome
    state["ended_at"] = now or _now()
    return state


def fail(state: Any, reason: str, now: str | None = None) -> dict:
    """MATCH -> FAILED with a bounded, sanitized reason."""
    state = _dict(state, "state")
    if state.get("phase") != "MATCH":
        raise PuyoCornerError("fail requires MATCH phase")
    state["failure_reason"] = _text(reason, "failure reason", limit=256)
    state["outcome"] = "failed"
    state["ended_at"] = now or _now()
    return _transition(state, "FAILED")


def timeout(state: Any, now: str | None = None) -> dict:
    """MATCH -> TIMEOUT when the corner's display window ends."""
    state = _dict(state, "state")
    if state.get("phase") != "MATCH":
        raise PuyoCornerError("timeout requires MATCH phase")
    state["outcome"] = "timeout"
    state["ended_at"] = now or _now()
    return _transition(state, "TIMEOUT")


def reset(state: Any, now: str | None = None) -> dict:
    """Terminal -> IDLE. Keeps the ruleset/config; clears boards and scores."""
    state = _dict(state, "state")
    if state.get("phase") not in ("FAILED", "TIMEOUT", "END"):
        raise PuyoCornerError("reset requires a terminal phase")
    seed = state.get("seed")
    fresh = new_state({
        "ruleset": state.get("ruleset"),
        "ruleset_id": state.get("ruleset_id"),
        "gravity_ms": state.get("gravity_ms"),
        "gravity_pending": state.get("gravity_pending"),
    }, seed=seed)
    state.clear()
    state.update(fresh)
    return state


def require_live_ruleset(state: Any, ruleset_id: Any) -> dict:
    """Reject a stale ruleset before acting on a running state."""
    state = _dict(state, "state")
    expected = _text(ruleset_id, "ruleset_id", limit=128)
    if state.get("ruleset_id") != expected:
        raise RulesetError(
            f"state ruleset {state.get('ruleset_id')!r} != live {expected!r}",
            "ruleset_mismatch",
        )
    return state


# ---------------------------------------------------------------------------
# ledger + persistence
# ---------------------------------------------------------------------------


def new_ledger() -> dict:
    return {"schema": SCHEMA, "entries": []}


def _trim_ledger(ledger: dict) -> None:
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return
    while len(entries) > MAX_HISTORY_ENTRIES:
        entries.pop(0)
    while entries:
        size = len(json.dumps(ledger, ensure_ascii=False).encode("utf-8"))
        if size <= MAX_BYTES:
            break
        entries.pop(0)


def record_match(ledger: Any, state: Any, outcome: str | None = None,
                 now: str | None = None) -> bool:
    """Append a match summary from a terminal state. Returns True if recorded."""
    ledger = _dict(ledger, "ledger")
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase not in ("END", "FAILED", "TIMEOUT"):
        return False
    if not isinstance(ledger.get("entries"), list):
        ledger["entries"] = []
    if outcome is None:
        outcome = state.get("outcome") or "completed"
    if outcome not in OUTCOMES:
        raise PuyoCornerError("invalid outcome")
    entry = {
        "ruleset_id": state.get("ruleset_id"),
        "started_at": state.get("started_at"),
        "ended_at": now or state.get("ended_at") or _now(),
        "outcome": outcome,
        "phase": phase,
        "round": state.get("round"),
        "frames": state.get("frames"),
        "actions": state.get("actions"),
        "pairs_spawned": state.get("pairs_spawned"),
        "user_score": state.get("user_score"),
        "ai_score": state.get("ai_score"),
        "winner": state.get("winner"),
        "failure_reason": state.get("failure_reason"),
    }
    ledger["entries"].append(entry)
    _trim_ledger(ledger)
    return True


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def _lock_path(directory: Path) -> Path:
    return Path(directory) / LOCK_FILE


def _atomic_write(path: Path, payload: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def load_state(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return new_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_state()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return new_state()
    base = new_state({
        "ruleset": data.get("ruleset"),
        "ruleset_id": data.get("ruleset_id"),
        "gravity_ms": data.get("gravity_ms"),
        "gravity_pending": data.get("gravity_pending"),
    }, seed=data.get("seed"))
    base.update(data)
    base["user_board"] = copy_board(data.get("user_board") or base["user_board"])
    base["ai_board"] = copy_board(data.get("ai_board") or base["ai_board"])
    return base


def save_state(path: Any, state: Any) -> None:
    state = _dict(state, "state")
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    _atomic_write(Path(path), payload + "\n")


def load_ledger(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return new_ledger()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_ledger()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return new_ledger()
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    return data


def save_ledger(path: Any, ledger: Any) -> None:
    ledger = _dict(ledger, "ledger")
    payload = json.dumps(ledger, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    _atomic_write(Path(path), payload + "\n")


def append_history_jsonl(path: Any, entry: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(entry), ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def with_lock(directory: Any, fn, *args, **kwargs):
    """Run ``fn`` under an exclusive lock in ``directory``."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with _lock_path(directory).open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            return fn(*args, **kwargs)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
