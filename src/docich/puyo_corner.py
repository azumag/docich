"""Offline core for the ぷよぷよクローン corner (Issue #698).

No Twitch I/O, no shell execution, no game switch, no production writes.
A deterministic, JSON-serializable falling-pair puzzle engine:

- 6x12 visible field + 1 hidden spawn row, 4 colors, vertical spawn.
- Tsu-style simplified rotation kicks (wall axis-slide and floor kick).
- Chain resolution (groups of 4+ pop, gravity, repeat) with Tsu-style
  scoring tables, all-clear bonus and ojama (garbage) exchange between
  the two seats of a match.
- A compact deterministic greedy self-play policy so a full match can be
  simulated offline (used by tests and by the future corner tick).

Wiring into a live corner (chat/viewer input, rendering the 960x540 view,
TTS, GameSwitch, corner rotation) is a separate, explicitly enabled step —
see ``docs/features/puyo-puyo-clone-corner.md``.

Design reference: ``docs/features/puyo-puyo-clone-corner.md`` (Issue #698).
Precedents: ``trpg_corner.py`` (offline core + persistence pattern),
``shogi_board.py`` (pure game-state helpers).

This is an original implementation of well-known falling-pair puzzle
rules. It embeds no assets, no decompiled logic and no code from any
commercial game; the numeric tables below are simplified, documented
approximations of commonly published chain-scoring formulas.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA = 1
STATE_FILE = "puyo_corner_state.json"
MATCH_FILE = "puyo_corner_match.json"
LOCK_FILE = "puyo_corner.lock"

# --- field geometry: 6 columns x 12 visible rows + 1 hidden spawn row ---
COLS = 6
ROWS = 12
HIDDEN_ROWS = 1
ROWS_TOTAL = ROWS + HIDDEN_ROWS  # row 0 is the hidden spawn row; row ROWS_TOTAL-1 is the floor
SPAWN_ROW = 1  # axis puyo spawns in the top visible row, child in the hidden row
SPAWN_COL = 2

# --- colors (4 colors fixed, like the classic ruleset) ---
COLORS = ("R", "G", "B", "Y")
OJAMA = "O"  # おじゃまぷよ
EMPTY = "."
COLOR_SET = frozenset(COLORS)
CELL_SET = frozenset({EMPTY, OJAMA}) | COLOR_SET

# --- pair orientation (child puyo position relative to the axis puyo) ---
ROT_UP, ROT_RIGHT, ROT_DOWN, ROT_LEFT = 0, 1, 2, 3
_ROT_OFFSETS = {ROT_UP: (-1, 0), ROT_RIGHT: (0, 1), ROT_DOWN: (1, 0), ROT_LEFT: (0, -1)}

ACTIONS = frozenset({"left", "right", "cw", "ccw", "soft_drop", "hard_drop"})

PHASES = ("IDLE", "PLAYING", "GAME_OVER", "FAILED")
MATCH_PHASES = ("IDLE", "PLAYING", "ENDED", "FAILED")
SIDES = ("A", "B")

# --- scoring (simplified Tsu-style tables; see the design doc) ---
# chain power per chain step, 1-indexed; chains beyond the table clamp at 512
CHAIN_POWER = (0, 8, 16, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 480, 512)
# index = number of distinct colors popped in one step minus 1 (1色=0 .. 4色=12)
COLOR_BONUS = (0, 3, 6, 12)
# connected-group bonus by group size; 11+ clamps at GROUP_BONUS_MAX
GROUP_BONUS = {4: 0, 5: 2, 6: 3, 7: 4, 8: 5, 9: 6, 10: 7}
GROUP_BONUS_MAX = 10
MIN_GROUP = 4
SCORE_PER_PUYO = 10
ALL_CLEAR_BONUS = 2100
MAX_SCORE = 999_999_999

# --- ojama (garbage) rules (Tsu-style, simplified) ---
OJAMA_RATE = 70  # attack ojama = accumulated score // 70; remainder carries over
MAX_OJAMA_QUEUE = 30  # pending ojama reserve cap; overflow is discarded
MAX_OJAMA_DROP = 30  # one lock drops at most 30 ojama (6 cols x 5 rows)

# --- fail-closed limits ---
MAX_CHAIN_STEPS = 24  # one lock resolves at most this many chain steps
MAX_ACTIONS = 100_000  # per game, safety cap for unattended loops
MAX_TICKS = 200_000  # per game, safety cap for unattended loops
MATCH_MAX_TICKS = 15_000  # match time limit; expiry compares total scores
MAX_BYTES = 256 * 1024

# --- deterministic pair RNG (tiny LCG; state is JSON-serializable) ---
_LCG_A = 1103515245
_LCG_C = 12345
_LCG_M = 0x7FFFFFFF


class PuyoCornerError(ValueError):
    """The game state or an action violates the corner contract."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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


def _empty_field() -> list[list[str]]:
    return [[EMPTY] * COLS for _ in range(ROWS_TOTAL)]


def _validate_field(field: Any, what: str = "field") -> list[list[str]]:
    if not isinstance(field, list) or len(field) != ROWS_TOTAL:
        raise PuyoCornerError(f"invalid {what} shape")
    for row in field:
        if not isinstance(row, list) or len(row) != COLS:
            raise PuyoCornerError(f"invalid {what} shape")
        for cell in row:
            if not isinstance(cell, str) or cell not in CELL_SET:
                raise PuyoCornerError(f"invalid {what} cell")
    return field


def _game_dict(state: Any) -> dict:
    state = _dict(state, "game state")
    if state.get("schema") != SCHEMA:
        raise PuyoCornerError("invalid game state schema")
    return state


def _lcg_next(state: int) -> tuple[int, int]:
    state = _int(state, "rng state", lo=0, hi=_LCG_M - 1)
    nxt = (_LCG_A * state + _LCG_C) % _LCG_M
    return nxt, nxt


def _pair_for(value: int) -> tuple[str, str]:
    # uniform over the 16 ordered color pairs (4x4), like the classic ruleset
    idx = value % (len(COLORS) * len(COLORS))
    return COLORS[idx // len(COLORS)], COLORS[idx % len(COLORS)]


def _derive_seed(seed: int, salt: int) -> int:
    return (seed + 0x9E3779B9 * (salt + 1)) % _LCG_M


def _child_offset(rot: Any) -> tuple[int, int]:
    if rot not in _ROT_OFFSETS:
        raise PuyoCornerError("invalid pair orientation")
    return _ROT_OFFSETS[rot]


def _cells(pair: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    r = pair["row"]
    c = pair["col"]
    dr, dc = _child_offset(pair["rot"])
    return (r, c), (r + dr, c + dc)


def _cell_free(field: list[list[str]], r: int, c: int) -> bool:
    return 0 <= r < ROWS_TOTAL and 0 <= c < COLS and field[r][c] == EMPTY


def _can_place(field: list[list[str]], pair: Mapping[str, Any]) -> bool:
    a, ch = _cells(pair)
    return _cell_free(field, *a) and _cell_free(field, *ch)


# ---------------------------------------------------------------------------
# game factory + state
# ---------------------------------------------------------------------------


def new_game(seed: int = 0) -> dict:
    """Create a fresh game state with a spawned pair (deterministic by seed)."""
    seed = _int(seed, "seed", lo=0, hi=_LCG_M - 1)
    state = {
        "schema": SCHEMA,
        "phase": "PLAYING",
        "failure_reason": None,
        "seed": seed,
        "rng": seed,
        "field": _empty_field(),
        "pair": None,
        "next_pair": None,
        "score": 0,
        "carry": 0,  # ojama remainder carry (< OJAMA_RATE)
        "attack_pending": 0,  # ojama waiting to be sent to the opponent
        "ojama_in": 0,  # pending incoming ojama, dropped at the next spawn
        "chains_total": 0,
        "all_clear_pending": False,
        "actions": 0,
        "ticks": 0,
        "last_lock": None,
        "started_at": _now(),
        "ended_at": None,
    }
    _draw_pair(state)  # fills next_pair
    _spawn(state)
    return state


def _draw_pair(state: dict) -> tuple[str, str]:
    state["rng"], value = _lcg_next(state["rng"])
    pair = _pair_for(value)
    state["next_pair"] = [pair[0], pair[1]]
    return pair


def _spawn(state: dict) -> None:
    """Promote the next pair to the current pair. Game over if blocked."""
    if state["next_pair"] is None:
        _draw_pair(state)
    colors = state["next_pair"]
    state["next_pair"] = None
    _draw_pair(state)
    state["pair"] = {
        "axis": colors[0],
        "child": colors[1],
        "rot": ROT_UP,
        "row": SPAWN_ROW,
        "col": SPAWN_COL,
    }
    if not _can_place(state["field"], state["pair"]):
        state["pair"] = None
        state["phase"] = "GAME_OVER"
        state["ended_at"] = _now()


# ---------------------------------------------------------------------------
# movement / rotation
# ---------------------------------------------------------------------------


def _shift(state: dict, dc: int) -> bool:
    """Move the pair one column left (-1) or right (+1). False if blocked."""
    pair = state.get("pair")
    if pair is None:
        return False
    cand = {**pair, "col": pair["col"] + dc}
    if _can_place(state["field"], cand):
        state["pair"] = cand
        return True
    return False


def _rotate(state: dict, delta: int) -> bool:
    """Rotate the pair by delta (+1 cw / -1 ccw) with simplified Tsu kicks.

    Kick order: direct rotation -> wall axis-slide (side) -> floor kick (up).
    Blocked by an occupied cell that is neither a wall nor the floor: the
    rotation fails silently (documented simplification; no S-shift tricks).
    """
    pair = state.get("pair")
    if pair is None:
        return False
    target = (pair["rot"] + delta) % 4
    cand = {**pair, "rot": target}
    if _can_place(state["field"], cand):
        state["pair"] = cand
        return True
    dr, dc = _child_offset(target)
    ch_r = pair["row"] + dr
    ch_c = pair["col"] + dc
    if ch_c < 0 or ch_c >= COLS:
        # wall axis-slide: move the whole pair one column away from the wall
        slide = 1 if ch_c < 0 else -1
        cand = {**pair, "rot": target, "col": pair["col"] + slide}
        if _can_place(state["field"], cand):
            state["pair"] = cand
            return True
        return False
    if ch_r >= ROWS_TOTAL:
        # floor kick: shift the whole pair up one row
        cand = {**pair, "rot": target, "row": pair["row"] - 1}
        if _can_place(state["field"], cand):
            state["pair"] = cand
            return True
        return False
    return False


def _fall(state: dict) -> bool:
    """Move the pair one row down. False when it cannot (it should lock)."""
    pair = state.get("pair")
    if pair is None:
        return False
    cand = {**pair, "row": pair["row"] + 1}
    if _can_place(state["field"], cand):
        state["pair"] = cand
        return True
    return False


def apply_action(state: Any, action: Any) -> dict:
    """Apply one control action to a PLAYING game. Blocked moves are no-ops."""
    state = _game_dict(state)
    if state.get("phase") != "PLAYING":
        raise PuyoCornerError("action requires PLAYING phase")
    if action not in ACTIONS:
        raise PuyoCornerError("invalid action")
    state["actions"] = _int(state.get("actions", 0), "actions", lo=0) + 1
    if state["actions"] > MAX_ACTIONS:
        state["phase"] = "FAILED"
        state["failure_reason"] = "action limit"
        return state
    if action == "left":
        _shift(state, -1)
    elif action == "right":
        _shift(state, 1)
    elif action == "cw":
        _rotate(state, 1)
    elif action == "ccw":
        _rotate(state, -1)
    elif action == "soft_drop":
        if not _fall(state):
            _lock(state)
    elif action == "hard_drop":
        while _fall(state):
            pass
        _lock(state)
    return state


def step(state: Any) -> dict:
    """Advance one gravity tick: move the pair down, lock when it lands."""
    state = _game_dict(state)
    if state.get("phase") != "PLAYING":
        return state
    state["ticks"] = _int(state.get("ticks", 0), "ticks", lo=0) + 1
    if state["ticks"] > MAX_TICKS:
        state["phase"] = "FAILED"
        state["failure_reason"] = "tick limit"
        return state
    if not _fall(state):
        _lock(state)
    return state


# ---------------------------------------------------------------------------
# chain resolution
# ---------------------------------------------------------------------------


def find_groups(field: list[list[str]]) -> list[dict]:
    """Return connected same-color groups with MIN_GROUP or more cells."""
    field = _validate_field(field)
    seen = [[False] * COLS for _ in range(ROWS_TOTAL)]
    groups = []
    for r in range(ROWS_TOTAL):
        for c in range(COLS):
            color = field[r][c]
            if color not in COLOR_SET or seen[r][c]:
                continue
            stack = [(r, c)]
            seen[r][c] = True
            cells = []
            while stack:
                cr, cc = stack.pop()
                cells.append((cr, cc))
                for nr, nc in ((cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)):
                    if 0 <= nr < ROWS_TOTAL and 0 <= nc < COLS and not seen[nr][nc] \
                            and field[nr][nc] == color:
                        seen[nr][nc] = True
                        stack.append((nr, nc))
            if len(cells) >= MIN_GROUP:
                groups.append({"color": color, "cells": sorted(cells)})
    return groups


def apply_gravity(field: list[list[str]]) -> None:
    """Settle every column bottom-up, in place."""
    field = _validate_field(field)
    for c in range(COLS):
        stack = [field[r][c] for r in range(ROWS_TOTAL - 1, -1, -1) if field[r][c] != EMPTY]
        for r in range(ROWS_TOTAL - 1, -1, -1):
            field[r][c] = stack[ROWS_TOTAL - 1 - r] if ROWS_TOTAL - 1 - r < len(stack) else EMPTY


def drop_ojama(field: list[list[str]], count: Any, *, max_drop: int = MAX_OJAMA_DROP) -> int:
    """Drop up to max_drop ojama into empty cells, bottom row first, left to
    right (flat fill). Returns the number placed; leftovers stay queued."""
    field = _validate_field(field)
    count = _int(count, "ojama count", lo=0)
    quota = min(count, max_drop)
    placed = 0
    for r in range(ROWS_TOTAL - 1, -1, -1):
        for c in range(COLS):
            if placed >= quota:
                return placed
            if field[r][c] == EMPTY:
                field[r][c] = OJAMA
                placed += 1
    return placed


def _chain_step_score(chain: int, n_colors: int, group_sizes: list[int], n_puyos: int,
                      bonus_ready: bool) -> int:
    bracket = CHAIN_POWER[min(chain, len(CHAIN_POWER)) - 1]
    bracket += COLOR_BONUS[min(max(n_colors, 1), len(COLOR_BONUS)) - 1]
    bracket += sum(GROUP_BONUS.get(size, GROUP_BONUS_MAX) for size in group_sizes)
    if bonus_ready:
        bracket += ALL_CLEAR_BONUS
    return bracket * SCORE_PER_PUYO * n_puyos


def resolve_field(field: list[list[str]], *, all_clear_bonus_ready: bool = False) -> dict:
    """Resolve all chains on a settled field, in place.

    Returns aggregate info: chain steps, score (0 for a lone 1-chain, like
    the original rules), popped puyo/ojama counts, per-step details and the
    all-clear flag (true when the field ends completely empty).
    """
    field = _validate_field(field)
    steps = []
    total_score = 0
    total_popped = 0
    total_ojama = 0
    chain = 0
    while True:
        groups = find_groups(field)
        if not groups:
            break
        chain += 1
        if chain > MAX_CHAIN_STEPS:
            raise PuyoCornerError("chain limit exceeded")
        pop_cells: set[tuple[int, int]] = set()
        colors: set[str] = set()
        group_sizes = []
        for g in groups:
            pop_cells.update(g["cells"])
            colors.add(g["color"])
            group_sizes.append(len(g["cells"]))
        ojama_cleared = 0
        for r, c in sorted(pop_cells):
            for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= nr < ROWS_TOTAL and 0 <= nc < COLS and field[nr][nc] == OJAMA \
                        and (nr, nc) not in pop_cells:
                    pop_cells.add((nr, nc))
                    ojama_cleared += 1
        n_puyos = len(pop_cells) - ojama_cleared
        score = _chain_step_score(chain, len(colors), group_sizes, n_puyos, all_clear_bonus_ready)
        all_clear_bonus_ready = False  # consumed at most once per lock
        for r, c in pop_cells:
            field[r][c] = EMPTY
        apply_gravity(field)
        total_score += score
        total_popped += n_puyos
        total_ojama += ojama_cleared
        steps.append({
            "chain": chain,
            "score": score,
            "popped": n_puyos,
            "ojama_cleared": ojama_cleared,
            "colors": sorted(colors),
            "group_sizes": sorted(group_sizes),
        })
    all_clear = all(field[r][c] == EMPTY for r in range(ROWS_TOTAL) for c in range(COLS))
    return {
        "chain": chain,
        "score": total_score,
        "popped": total_popped,
        "ojama_cleared": total_ojama,
        "all_clear": all_clear,
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# lock + ojama economy
# ---------------------------------------------------------------------------


def _lock(state: dict) -> None:
    """Lock the current pair, resolve chains, drop queued ojama, send attack,
    then spawn the next pair (game over if the spawn cells are blocked).

    Order follows the original rules: own chains resolve first, queued
    ojama drop afterwards (they cannot be popped by this lock), then the
    attack is sent, then the next pair appears.
    """
    pair = state.get("pair")
    if pair is None:
        state["phase"] = "GAME_OVER"
        state["ended_at"] = _now()
        return
    field = state["field"]
    a, ch = _cells(pair)
    field[a[0]][a[1]] = pair["axis"]
    field[ch[0]][ch[1]] = pair["child"]
    info = resolve_field(field, all_clear_bonus_ready=bool(state.get("all_clear_pending")))
    state["all_clear_pending"] = bool(info["all_clear"])
    state["chains_total"] = _int(state.get("chains_total", 0), "chains_total", lo=0) + info["chain"]
    gained = min(info["score"], MAX_SCORE)
    # received ojama falls only after the lock's own chains are done
    dropped = drop_ojama(field, _int(state.get("ojama_in", 0), "ojama_in", lo=0))
    state["ojama_in"] -= dropped
    # attack = (carry + gained) // OJAMA_RATE, remainder carries over
    carry = _int(state.get("carry", 0), "carry", lo=0, hi=OJAMA_RATE - 1) + gained
    sent = carry // OJAMA_RATE
    state["carry"] = carry % OJAMA_RATE
    state["attack_pending"] = _int(state.get("attack_pending", 0), "attack_pending", lo=0) + sent
    state["score"] = min(MAX_SCORE, _int(state.get("score", 0), "score", lo=0) + gained)
    state["last_lock"] = {
        "chain": info["chain"],
        "score": gained,
        "sent": sent,
        "popped": info["popped"],
        "ojama_cleared": info["ojama_cleared"],
        "all_clear": bool(info["all_clear"]),
        "ojama_dropped": dropped,
    }
    state["pair"] = None
    _spawn(state)


# ---------------------------------------------------------------------------
# match (two seats with ojama exchange)
# ---------------------------------------------------------------------------


def new_match(seed: int = 0) -> dict:
    """Create a 2-seat match. Seat B uses a deterministically derived seed."""
    seed = _int(seed, "seed", lo=0, hi=_LCG_M - 1)
    return {
        "schema": SCHEMA,
        "phase": "PLAYING",
        "failure_reason": None,
        "seed": seed,
        "round": 1,
        "games": {"A": new_game(seed), "B": new_game(_derive_seed(seed, 1))},
        "winner": None,  # "A" / "B" / "draw"
        "ticks": 0,
        "started_at": _now(),
        "ended_at": None,
    }


def _match_dict(match: Any) -> dict:
    match = _dict(match, "match state")
    if match.get("schema") != SCHEMA:
        raise PuyoCornerError("invalid match state schema")
    return match


def match_apply_action(match: Any, side: Any, action: Any) -> dict:
    """Apply a control action to one seat of a PLAYING match."""
    match = _match_dict(match)
    if match.get("phase") != "PLAYING":
        raise PuyoCornerError("match action requires PLAYING phase")
    if side not in SIDES:
        raise PuyoCornerError("invalid side")
    apply_action(match["games"][side], action)
    return match


def match_step(match: Any) -> dict:
    """Advance both seats one gravity tick, then exchange ojama attacks and
    decide the match when a seat reaches game over or the time limit hits
    (higher total score wins; equal scores draw)."""
    match = _match_dict(match)
    if match.get("phase") != "PLAYING":
        return match
    match["ticks"] = _int(match.get("ticks", 0), "match ticks", lo=0) + 1
    for side in SIDES:
        game = match["games"][side]
        if game.get("phase") == "PLAYING":
            step(game)
    # transfer pending attacks to the opponent's ojama queue (cap: discard)
    for side in SIDES:
        game = match["games"][side]
        other = match["games"]["B" if side == "A" else "A"]
        pending = _int(game.get("attack_pending", 0), "attack_pending", lo=0)
        if pending:
            game["attack_pending"] = 0
            room = MAX_OJAMA_QUEUE - _int(other.get("ojama_in", 0), "ojama_in", lo=0)
            other["ojama_in"] += max(0, min(pending, room))
    over = [side for side in SIDES if match["games"][side].get("phase") == "GAME_OVER"]
    if len(over) == 1:
        match["winner"] = "B" if over[0] == "A" else "A"
        match["phase"] = "ENDED"
        match["ended_at"] = _now()
    elif len(over) == 2:
        match["winner"] = "draw"
        match["phase"] = "ENDED"
        match["ended_at"] = _now()
    elif match["ticks"] >= MATCH_MAX_TICKS:
        # time limit: never stall an unattended match (AGENTS.md: 試合は完走させる)
        a_score = match["games"]["A"].get("score", 0)
        b_score = match["games"]["B"].get("score", 0)
        match["winner"] = "draw" if a_score == b_score else ("A" if a_score > b_score else "B")
        match["failure_reason"] = "time limit"
        match["phase"] = "ENDED"
        match["ended_at"] = _now()
    return match


# ---------------------------------------------------------------------------
# greedy self-play policy (deterministic placeholder for the future corner AI)
# ---------------------------------------------------------------------------


def _column_heights(field: list[list[str]]) -> list[int]:
    return [sum(1 for r in range(ROWS_TOTAL) if field[r][c] != EMPTY) for c in range(COLS)]


def _evaluate_placement(field: list[list[str]], score_gain: int) -> int:
    heights = _column_heights(field)
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(COLS - 1))
    return score_gain * 100 - max(heights) * 4 - bump * 2 - sum(heights)


def _simulate_placement(state: dict, target_rot: int, shift: int) -> tuple[int, str] | None:
    """Simulate (rotate, shift, hard drop, resolve) on cloned field/pair data.

    Returns (heuristic value, first action of the move sequence), or None
    when the target orientation is unreachable.
    """
    shim = {"field": [row[:] for row in state["field"]], "pair": dict(state["pair"])}
    cur = shim["pair"]["rot"]
    cw = (target_rot - cur) % 4
    ccw = (cur - target_rot) % 4
    name = "cw" if cw <= ccw else "ccw"
    delta = 1 if name == "cw" else -1
    first_action: str | None = None
    for _ in range(cw if name == "cw" else ccw):
        if not _rotate(shim, delta):
            return None
        if first_action is None:
            first_action = name
    step_name = "left" if shift < 0 else "right"
    for _ in range(abs(shift)):
        if not _shift(shim, -1 if shift < 0 else 1):
            break  # blocked mid-way: land where the pair currently is
        if first_action is None:
            first_action = step_name
    pair = shim["pair"]
    if pair is None:
        return None
    while _fall(shim):
        pass
    pair = shim["pair"]
    if pair is None:
        return None
    a, ch = _cells(pair)
    if not (_cell_free(shim["field"], *a) and _cell_free(shim["field"], *ch)):
        return None
    shim["field"][a[0]][a[1]] = pair["axis"]
    shim["field"][ch[0]][ch[1]] = pair["child"]
    info = resolve_field(shim["field"], all_clear_bonus_ready=bool(state.get("all_clear_pending")))
    return _evaluate_placement(shim["field"], info["score"]), first_action or "hard_drop"


def greedy_policy(state: Any) -> str:
    """Return the next action of a compact deterministic greedy player.

    Evaluates every (orientation, column) landing reachable from the current
    pair, resolves chains on a cloned field and picks the placement with the
    best (score-dominant, height-penalized) heuristic. First-best wins on
    ties, so the policy is fully deterministic for a given state.
    """
    state = _game_dict(state)
    if state.get("phase") != "PLAYING" or state.get("pair") is None:
        raise PuyoCornerError("policy requires a PLAYING game with a live pair")
    best: tuple[int, str] | None = None
    for target_rot in range(4):
        for shift in range(-(COLS - 1), COLS):
            got = _simulate_placement(state, target_rot, shift)
            if got is None:
                continue
            if best is None or got[0] > best[0]:
                best = got
    if best is None:
        return "hard_drop"  # nothing reachable: slam and accept the outcome
    return best[1]


MAX_ACTIONS_PER_PAIR = 12  # self-play driver bound: one pair needs at most ~9 actions


def run_greedy_match(match: Any, *, max_ticks: int = MAX_TICKS) -> dict:
    """Drive a whole match with the greedy policy on both seats.

    For every falling pair the driver re-plans after each control action
    (like a human re-pressing keys) and lets the pair fall one gravity cell
    per tick afterwards. Deterministic for a given seed; the match's own
    time limit / caps guarantee it finishes.
    """
    match = _match_dict(match)
    max_ticks = _int(max_ticks, "max_ticks", lo=1)
    while match.get("phase") == "PLAYING" and match.get("ticks", 0) < max_ticks:
        for side in SIDES:
            game = match["games"][side]
            for _ in range(MAX_ACTIONS_PER_PAIR):
                if game.get("phase") != "PLAYING" or game.get("pair") is None:
                    break
                action = greedy_policy(game)
                match_apply_action(match, side, action)
                if action in ("soft_drop", "hard_drop"):
                    break  # the pair locked
        match_step(match)
    return match


# ---------------------------------------------------------------------------
# rendering helper (for logs / tests; the 960x540 view is a later PR)
# ---------------------------------------------------------------------------


def render_ascii(state: Any) -> str:
    """Render the field as ROWS_TOTAL lines of COLS cells (row 0 = hidden)."""
    state = _game_dict(state)
    lines = ["".join(row) for row in state["field"]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# persistence (atomic write + shared lock, same pattern as trpg_corner)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(payload.encode("utf-8")) > MAX_BYTES:
        raise PuyoCornerError("state too large")
    fd, tmp = tempfile.mkstemp(prefix=".puyo.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock_path(directory: Path) -> Path:
    return directory / LOCK_FILE


def with_lock(directory: Any, fn, *args, **kwargs):
    """Run fn under an exclusive lock in directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with _lock_path(directory).open("w", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            return fn(*args, **kwargs)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _normalize_game(data: Any) -> dict | None:
    """Return a minimally validated game state, or None when unusable."""
    try:
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return None
        if data.get("phase") not in PHASES:
            return None
        _validate_field(data.get("field"))
        base = new_game(_int(data.get("seed", 0), "seed", lo=0, hi=_LCG_M - 1))
    except PuyoCornerError:
        return None
    base.update(data)
    return base


def load_game(path: Any) -> dict:
    """Load a game state; a missing or corrupt file yields a fresh game."""
    path = Path(path)
    if not path.exists():
        return new_game()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return new_game()
    state = _normalize_game(data)
    return state if state is not None else new_game()


def save_game(path: Any, state: Any) -> None:
    path = Path(path)
    state = _game_dict(state)
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write(path, payload + "\n")


def _normalize_match(data: Any) -> dict | None:
    try:
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return None
        if data.get("phase") not in MATCH_PHASES:
            return None
        games = data.get("games")
        if not isinstance(games, dict) or set(games.keys()) != set(SIDES):
            return None
        for side in SIDES:
            if _normalize_game(games[side]) is None:
                return None
        base = new_match(_int(data.get("seed", 0), "seed", lo=0, hi=_LCG_M - 1))
    except PuyoCornerError:
        return None
    base.update(data)
    return base


def load_match(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return new_match()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return new_match()
    match = _normalize_match(data)
    return match if match is not None else new_match()


def save_match(path: Any, match: Any) -> None:
    path = Path(path)
    match = _match_dict(match)
    payload = json.dumps(match, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write(path, payload + "\n")
