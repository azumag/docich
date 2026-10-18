"""Offline core for the 参加型ゲームコーナー (Issue #699, MVP: Othello).

Owner-approved MVP scope (2026-10-18):
- start with Othello; other games (puyo/tetris/tsuitate-shogi/FPS) are later
  issues that reuse this platform (room management, spectating, view);
- match form is **viewer vs AI**: a viewer challenges the docich brain inside a
  bounded room, and spectators can watch;
- server runtime will be Cloudflare Workers + Durable Objects, following
  ``workers/tsuitate-bot`` (see ``workers/othello-bot/``).

No Twitch I/O, no shell execution, no game switch, no production writes.
Pure rules + room state machine + validation only, so the Worker (JS) and any
future Python wiring can share the same contract. Wiring into a live corner
tick (chat intake, view rendering, TTS, GameSwitch) is a separate, explicitly
enabled step.

Design reference: ``docs/features/participatory-games-corner.md``.
Precedents: ``trpg_corner.py`` (state machine + persistence),
``tsuitate_protocol.py`` (offline contract before a live worker).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
STATE_FILE = "othello_corner_state.json"
HISTORY_FILE = "othello_corner_history.jsonl"

# --- board (fixed) ---
BOARD_SIZE = 8
CELL_EMPTY = 0
CELL_BLACK = 1
CELL_WHITE = 2
PLAYERS = (CELL_BLACK, CELL_WHITE)
PLAYER_LABELS = {CELL_BLACK: "black", CELL_WHITE: "white"}
#: Viewer always takes black in the MVP (first turn, one clear pronoun).
VIEWER_PLAYER = CELL_BLACK
AI_PLAYER = CELL_WHITE

# --- room lifecycle ---
ROOM_PHASES = ("IDLE", "ROOM_OPEN", "PLAYING", "FINISHED", "FAILED", "TIMEOUT")
PHASE_SET = frozenset(ROOM_PHASES)
TRANSITIONS: dict[str, frozenset[str]] = {
    "IDLE": frozenset({"ROOM_OPEN"}),
    "ROOM_OPEN": frozenset({"PLAYING", "FAILED", "TIMEOUT"}),
    "PLAYING": frozenset({"FINISHED", "FAILED", "TIMEOUT"}),
    "FINISHED": frozenset({"IDLE"}),
    "FAILED": frozenset({"IDLE"}),
    "TIMEOUT": frozenset({"IDLE"}),
}

# --- limits (fail-closed) ---
MAX_TEXT = 256
MAX_ROOM_ID = 64
MAX_USER_ID = 64
MAX_MOVES = 80  # 8x8 minus 4 starting discs is 60; headroom for passes
MAX_ROOMS_PER_LEDGER_KEY = 100000
MAX_HISTORY_ENTRIES = 500
MAX_BYTES = 256 * 1024
MAX_OPEN_ROOMS = 1  # MVP: one shared broadcast room; rooms multiply later
MAX_VIEWER_NAME = 64
MOVE_WINDOW_DEFAULT_S = 45
MOVE_WINDOW_MAX_S = 300

OUTCOMES = frozenset({"viewer_win", "ai_win", "draw", "aborted", "timeout"})

POSITIONAL_WEIGHTS = (
    (120, -20, 20, 5, 5, 20, -20, 120),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (5, -5, 3, 3, 3, 3, -5, 5),
    (20, -5, 15, 3, 3, 15, -5, 20),
    (-20, -40, -5, -5, -5, -5, -40, -20),
    (120, -20, 20, 5, 5, 20, -20, 120),
)


class OthelloCornerError(ValueError):
    """The request or state violates the corner contract."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, what: str, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= limit):
        raise OthelloCornerError(f"invalid {what}")
    return value


def _int(value: Any, what: str, *, lo: int | None = None, hi: int | None = None) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise OthelloCornerError(f"invalid {what}")
    if lo is not None and value < lo:
        raise OthelloCornerError(f"invalid {what}")
    if hi is not None and value > hi:
        raise OthelloCornerError(f"invalid {what}")
    return value


def _dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise OthelloCornerError(f"invalid {what}")
    return value


def _cell(value: Any) -> int:
    if value in (CELL_EMPTY, CELL_BLACK, CELL_WHITE):
        return value
    raise OthelloCornerError("invalid cell")


def _board(value: Any) -> list[list[int]]:
    raw = _dict(value, "board") if not isinstance(value, list) else value
    if isinstance(raw, dict) or not isinstance(raw, list):
        raise OthelloCornerError("invalid board")
    rows = []
    for row in raw:
        if not isinstance(row, list) or len(row) != BOARD_SIZE:
            raise OthelloCornerError("invalid board row")
        rows.append([_cell(c) for c in row])
    if len(rows) != BOARD_SIZE:
        raise OthelloCornerError("invalid board height")
    return rows


def _validate_board_counts(board: list[list[int]]) -> None:
    empties = sum(row.count(CELL_EMPTY) for row in board)
    if len(board) * BOARD_SIZE - empties < 4:
        raise OthelloCornerError("board lost its starting discs")


# ---------------------------------------------------------------------------
# rules (pure)
# ---------------------------------------------------------------------------


def new_board() -> list[list[int]]:
    """Standard Othello start: white discs d4/e5, black d5/e4."""
    board = [[CELL_EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    mid = BOARD_SIZE // 2
    board[mid - 1][mid - 1] = CELL_WHITE
    board[mid][mid] = CELL_WHITE
    board[mid - 1][mid] = CELL_BLACK
    board[mid][mid - 1] = CELL_BLACK
    return board


def _opponent(player: int) -> int:
    if player == CELL_BLACK:
        return CELL_WHITE
    if player == CELL_WHITE:
        return CELL_BLACK
    raise OthelloCornerError("invalid player")


def flips_for(board: list[list[int]], player: int, row: Any, col: Any) -> list[tuple[int, int]]:
    """Discs that would flip for ``player`` at (row, col), or [] if illegal."""
    row = _int(row, "move row", lo=0, hi=BOARD_SIZE - 1)
    col = _int(col, "move col", lo=0, hi=BOARD_SIZE - 1)
    board = _board(board)
    if board[row][col] != CELL_EMPTY:
        return []
    me = player if player in PLAYERS else _cell(player)
    if me == CELL_EMPTY:
        raise OthelloCornerError("invalid player")
    opp = _opponent(me)
    directions = (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1), (0, 1),
        (1, -1), (1, 0), (1, 1),
    )
    flipped: list[tuple[int, int]] = []
    for dr, dc in directions:
        line: list[tuple[int, int]] = []
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == opp:
            line.append((r, c))
            r += dr
            c += dc
        if line and 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and board[r][c] == me:
            flipped.extend(line)
    return flipped


def legal_moves(board: Any, player: int) -> list[tuple[int, int]]:
    board = _board(board)
    if player not in PLAYERS:
        raise OthelloCornerError("invalid player")
    moves = []
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] == CELL_EMPTY and flips_for(board, player, r, c):
                moves.append((r, c))
    return moves


def apply_move(board: Any, player: int, row: Any, col: Any) -> list[list[int]]:
    """Return a new board with the move applied; raises when the move is illegal."""
    flipped = flips_for(board, player, row, col)
    if not flipped:
        raise OthelloCornerError("illegal move")
    new = [row[:] for row in _board(board)]
    row = _int(row, "move row", lo=0, hi=BOARD_SIZE - 1)
    col = _int(col, "move col", lo=0, hi=BOARD_SIZE - 1)
    new[row][col] = player if player in PLAYERS else _cell(player)
    for r, c in flipped:
        new[r][c] = new[row][col]
    _validate_board_counts(new)
    return new


def score(board: Any) -> dict[str, int]:
    board = _board(board)
    black = sum(row.count(CELL_BLACK) for row in board)
    white = sum(row.count(CELL_WHITE) for row in board)
    return {"black": black, "white": white}


def is_terminal(board: Any) -> bool:
    board = _board(board)
    return not legal_moves(board, CELL_BLACK) and not legal_moves(board, CELL_WHITE)


def winner_of(board: Any) -> int | None:
    """CELL_BLACK / CELL_WHITE, or None on a draw."""
    counts = score(board)
    if counts["black"] > counts["white"]:
        return CELL_BLACK
    if counts["white"] > counts["black"]:
        return CELL_WHITE
    return None


# ---------------------------------------------------------------------------
# AI (docich brain, bounded and deterministic)
# ---------------------------------------------------------------------------


def ai_choose_move(board: Any, player: int, moves: list[tuple[int, int]] | None = None) -> tuple[int, int]:
    """Docich brain for the MVP: deterministic positional-weighted greedy.

    Corners are gold, X/C squares are toxic, edges are solid. Deterministic so
    a spectator review and the Worker implementation can both verify a move.
    """
    board = _board(board)
    if player not in PLAYERS:
        raise OthelloCornerError("invalid player")
    candidates = legal_moves(board, player) if moves is None else sorted(moves)
    if not candidates:
        raise OthelloCornerError("no legal move")
    best = None
    best_score = None
    for r, c in candidates:
        value = POSITIONAL_WEIGHTS[r][c]
        if best_score is None or value > best_score:
            best, best_score = (r, c), value
    return best


# ---------------------------------------------------------------------------
# room state machine
# ---------------------------------------------------------------------------


def new_state() -> dict:
    return {
        "schema": SCHEMA,
        "phase": "IDLE",
        "room_id": None,
        "viewer": None,
        "board": None,
        "turn": None,
        "moves_played": 0,
        "move_window_seconds": None,
        "move_deadline_at": None,
        "opened_at": None,
        "started_at": None,
        "ended_at": None,
        "outcome": None,
        "result": None,
        "failure_reason": None,
    }


def _transition(state: dict, to: str) -> dict:
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase not in PHASE_SET:
        raise OthelloCornerError("invalid state phase")
    if to not in TRANSITIONS.get(phase, frozenset()):
        raise OthelloCornerError(f"invalid transition {phase} -> {to}")
    state["phase"] = to
    return state


def open_room(state: Any, room_id: Any, viewer: Any, *,
              window_seconds: int = MOVE_WINDOW_DEFAULT_S, now: str | None = None) -> dict:
    """IDLE -> ROOM_OPEN. ``viewer`` names the human seat (viewer vs AI)."""
    state = _dict(state, "state")
    if state.get("phase") != "IDLE":
        raise OthelloCornerError("open_room requires IDLE phase")
    rid = _text(room_id, "room id", limit=MAX_ROOM_ID)
    name = _text(viewer, "viewer name", limit=MAX_VIEWER_NAME)
    ws = _int(window_seconds, "window_seconds", lo=1, hi=MOVE_WINDOW_MAX_S)
    ts = now or _now()
    state["room_id"] = rid
    state["viewer"] = name
    state["move_window_seconds"] = ws
    state["opened_at"] = ts
    state["board"] = new_board()
    state["turn"] = VIEWER_PLAYER
    state["moves_played"] = 0
    state["outcome"] = None
    state["result"] = None
    state["failure_reason"] = None
    state["ended_at"] = None
    return _transition(state, "ROOM_OPEN")


def start_match(state: Any, now: str | None = None) -> dict:
    """ROOM_OPEN -> PLAYING with the standard start position."""
    state = _dict(state, "state")
    if state.get("phase") != "ROOM_OPEN":
        raise OthelloCornerError("start_match requires ROOM_OPEN phase")
    ts = now or _now()
    state["board"] = new_board()
    state["turn"] = VIEWER_PLAYER
    state["moves_played"] = 0
    state["started_at"] = ts
    state["move_deadline_at"] = None
    return _transition(state, "PLAYING")


def record_move(state: Any, row: Any, col: Any, *, by_ai: bool = False, now: str | None = None) -> dict:
    """Apply one viewer or AI move while PLAYING. Handles passes automatically.

    Raises when it is not that side's turn, or the move is not legal on the
    current board. When the opponent has no reply the turn is granted back;
    when neither side can move the match is terminal.
    """
    state = _dict(state, "state")
    if state.get("phase") != "PLAYING":
        raise OthelloCornerError("record_move requires PLAYING phase")
    board = _board(state["board"])
    turn = state.get("turn")
    if turn not in PLAYERS:
        raise OthelloCornerError("invalid turn")
    mover = AI_PLAYER if by_ai else VIEWER_PLAYER
    if turn != mover:
        raise OthelloCornerError("not your turn")
    if state["moves_played"] >= MAX_MOVES:
        return finish(state, now=now)
    state["board"] = apply_move(board, mover, row, col)
    state["moves_played"] += 1
    state["move_deadline_at"] = None
    # Grant the next turn, skipping a side that has no legal reply.
    opponent = _opponent(mover)
    nxt = _board(state["board"])
    if legal_moves(nxt, opponent):
        state["turn"] = opponent
    elif legal_moves(nxt, mover):
        state["turn"] = mover
    else:
        return finish(state, now=now)
    return state


def ai_turn(state: Any, now: str | None = None) -> dict:
    """Play the docich brain's moves until the viewer can reply or the match ends.

    PLAYING turn must be AI on entry. Loops over consecutive AI moves (when the
    viewer has no legal reply) so a pass cannot freeze the room; the caller does
    not need to re-invoke. Returns the state once the turn leaves AI or the
    match finishes.
    """
    state = _dict(state, "state")
    if state.get("phase") != "PLAYING":
        raise OthelloCornerError("ai_turn requires PLAYING phase")
    if state.get("turn") != AI_PLAYER:
        raise OthelloCornerError("not ai turn")
    while state.get("turn") == AI_PLAYER and state.get("phase") == "PLAYING":
        if state.get("moves_played", 0) >= MAX_MOVES:
            return finish(state, now=now)
        board = _board(state["board"])
        if not legal_moves(board, AI_PLAYER):
            # AI has no move: pass to viewer or finish.
            if legal_moves(board, VIEWER_PLAYER):
                state["turn"] = VIEWER_PLAYER
                state["move_deadline_at"] = None
                return state
            return finish(state, now=now)
        record_move(state, *ai_choose_move(board, AI_PLAYER), by_ai=True, now=now)
    return state


def finish(state: Any, *, outcome: str | None = None, now: str | None = None) -> dict:
    """PLAYING -> FINISHED with the viewer-centred outcome + final score."""
    state = _dict(state, "state")
    if state.get("phase") != "PLAYING":
        raise OthelloCornerError("finish requires PLAYING phase")
    counts = score(state.get("board") or new_board())
    if outcome is None:
        w = winner_of(state["board"])
        outcome = ("draw" if w is None
                   else "viewer_win" if w == VIEWER_PLAYER else "ai_win")
    if outcome not in OUTCOMES:
        raise OthelloCornerError("invalid outcome")
    state["outcome"] = outcome
    state["result"] = {
        "black": counts["black"],
        "white": counts["white"],
        "outcome": outcome,
        "ended_at": now or _now(),
    }
    state["ended_at"] = now or _now()
    return _transition(state, "FINISHED")


def fail(state: Any, reason: str, now: str | None = None) -> dict:
    """ROOM_OPEN/PLAYING -> FAILED. Fail-closed: never invent a result."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase not in ("ROOM_OPEN", "PLAYING"):
        raise OthelloCornerError("fail requires an open or playing room")
    state["failure_reason"] = _text(reason, "failure reason", limit=MAX_TEXT)
    state["outcome"] = "aborted"
    state["ended_at"] = now or _now()
    return _transition(state, "FAILED")


def timeout(state: Any, now: str | None = None) -> dict:
    """ROOM_OPEN/PLAYING -> TIMEOUT. Unfinished stays explicit (no forced end)."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase not in ("ROOM_OPEN", "PLAYING"):
        raise OthelloCornerError("timeout requires an open or playing room")
    state["outcome"] = "timeout"
    state["ended_at"] = now or _now()
    return _transition(state, "TIMEOUT")


def reset(state: Any) -> dict:
    """FAILED/TIMEOUT/FINISHED -> IDLE. Clears all runtime state."""
    state = _dict(state, "state")
    if state.get("phase") not in ("FINISHED", "FAILED", "TIMEOUT"):
        raise OthelloCornerError("reset requires a terminal phase")
    state.clear()
    state.update(new_state())
    return state


# ---------------------------------------------------------------------------
# spectator-safe projection (rebuilt, never handed raw state)
# ---------------------------------------------------------------------------


def spectator_view(state: Any) -> dict:
    """The broadcast view payload. Othello has no hidden information."""
    state = _dict(state, "state")
    phase = state.get("phase")
    if phase == "IDLE":
        return {"phase": "IDLE", "roomId": None, "board": None, "turn": None,
                "scores": None, "movesPlayed": 0, "outcome": None}
    board = _board(state.get("board") or new_board())
    turn = state.get("turn")
    return {
        "phase": phase,
        "roomId": state.get("room_id"),
        "board": board,
        "turn": PLAYER_LABELS[turn] if turn in PLAYERS else None,
        "scores": score(board),
        "movesPlayed": state.get("moves_played", 0),
        "outcome": state.get("outcome"),
        "result": state.get("result"),
        "yourTurn": turn == VIEWER_PLAYER if phase == "PLAYING" else None,
    }


# ---------------------------------------------------------------------------
# chat parsing
# ---------------------------------------------------------------------------


def parse_move(text: Any) -> tuple[int, int] | None:
    """Parse ``!move d3`` / ``d3`` / ``e6`` into 0-based (row, col), or None."""
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if t.startswith("!"):
        t = t[1:].strip()
    parts = t.split()
    if len(parts) == 2 and parts[0] in ("move", "m"):
        t = parts[1]
    elif len(parts) > 1:
        return None
    if not (2 <= len(t) <= 2):
        return None
    col_ch, row_ch = t[0], t[1]
    if col_ch not in "abcdefgh" or row_ch not in "12345678":
        return None
    return ord(row_ch) - ord("1"), ord(col_ch) - ord("a")


# ---------------------------------------------------------------------------
# persistence (atomic write only; the lock lives with the caller)
# ---------------------------------------------------------------------------


def _atomic_write(path: Any, payload: str) -> None:
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


def _trim_history(entries: list[dict]) -> None:
    while len(entries) > MAX_HISTORY_ENTRIES:
        entries.pop(0)
    while entries:
        try:
            size = len(json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            break
        if size <= MAX_BYTES:
            break
        entries.pop(0)


def load_state(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return new_state()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return new_state()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return new_state()
    base = new_state()
    base.update(data)
    return base


def save_state(path: Any, state: Any) -> None:
    path = Path(path)
    state = _dict(state, "state")
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write(path, payload + "\n")


def load_history(path: Any) -> dict:
    path = Path(path)
    if not path.exists():
        return {"schema": SCHEMA, "entries": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA, "entries": []}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA \
            or not isinstance(data.get("entries"), list):
        data = {"schema": SCHEMA, "entries": []}
    _trim_history(data["entries"])
    return data


def record_match(history: Any, state: Any, now: str | None = None) -> bool:
    """Append one match summary from a terminal state. True when recorded."""
    history = _dict(history, "history")
    state = _dict(state, "state")
    if state.get("phase") not in ("FINISHED", "FAILED", "TIMEOUT"):
        return False
    if "entries" not in history or not isinstance(history["entries"], list):
        history["entries"] = []
    board = state.get("board") or new_board()
    counts = score(board)
    entry = {
        "room_id": state.get("room_id"),
        "viewer": state.get("viewer"),
        "started_at": state.get("started_at"),
        "ended_at": now or state.get("ended_at") or _now(),
        "moves": state.get("moves_played", 0),
        "black": counts["black"],
        "white": counts["white"],
        "outcome": state.get("outcome"),
        "failure_reason": state.get("failure_reason"),
    }
    history["entries"].append(entry)
    _trim_history(history["entries"])
    return True


def append_history_jsonl(path: Any, entry: Any) -> None:
    """Append one match summary as a JSONL line (append-only log)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(entry), ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
