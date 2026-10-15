"""The 感想戦 show: the kifu the corner steps through, and the board it shows.

A 感想戦 follows the game record move by move.  This module owns the bounded
payload the broadcast view renders and the pacing the corner drives:

- :func:`build_show` turns one *verified* public replay (see ``tsuitate_review``)
  into the move list with Japanese notation and a board snapshot per move.  The
  board is only included when ``shogi_board.reconstruct`` reproduced the site's
  own final position; otherwise the show carries the move list alone.
- :func:`chunk_ranges` splits the game into at most ``MAX_CHUNKS`` stretches so a
  long game still gets a bounded number of commentary lines.
- :func:`fallback_chunk_text` is the deterministic commentary for one stretch
  (captures, promotions, drops, long thinks), used when the model is not
  configured or fails.
- :func:`write_show` / :func:`read_show` are the only file access: one bounded,
  atomic JSON in the corner's state directory, read by the loopback view for the
  same runtime only.

The payload never contains the opponent's hidden information beyond what the
site publishes at ``game:end`` (the replay, the result, the rating change and
the revealed name), and it is dropped with the generation.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SHOW_FILENAME = "tsuitate_review.json"
SHOW_SCHEMA = 1
MAX_SHOW_BYTES = 262144
MAX_SHOW_MOVES = 512
#: Per-move display step while a stretch is being shown.
STEP_SECONDS = 1.4
#: Hard bound for the whole show; past it the corner jumps to the end.
MAX_SHOW_SECONDS = 420.0
#: Commentary is generated per stretch, so a long game stays bounded.
MAX_CHUNKS = 6
MIN_CHUNK_MOVES = 6
#: Speech of one stretch is bounded like every other corner line.
MAX_CHUNK_SPEECH = 240
#: How long a stretch waits for the shared audio queue to drain before moving on.
CHUNK_AUDIO_WAIT_S = 45.0


class ShowError(RuntimeError):
    """The show payload is invalid or unusable."""


def chunk_ranges(move_count: int, *, max_chunks: int = MAX_CHUNKS) -> list[tuple[int, int]]:
    """``(start, end)`` 1-based inclusive stretches covering every move."""
    if type(move_count) is not int or move_count < 0:
        raise ShowError("invalid move count")
    if move_count == 0:
        return []
    chunks = min(max(1, int(max_chunks)), move_count)
    size = max(MIN_CHUNK_MOVES, -(-move_count // chunks))
    ranges = []
    start = 1
    while start <= move_count:
        end = min(move_count, start + size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def _ms(move) -> int:
    value = move.get("ms")
    return value if type(value) is int and 0 <= value <= 3_600_000 else 0


def fallback_chunk_text(moves, start: int, end: int) -> str:
    """Deterministic commentary for one stretch of the kifu.

    Only facts the reconstruction proved: the moves, the pieces that moved,
    captures, promotions, drops and the thinking time the site published.
    """
    window = [move for move in moves if start <= move["n"] <= end]
    if not window:
        return ""
    parts = [f"{start}手目から{end}手目です。"]
    for move in window:
        line = move["notation"]
        if move.get("captured"):
            line += f"、{move['captured']}を取ります"
        elif move.get("promote"):
            line += "、成ります"
        elif move.get("drop"):
            line += "、持ち駒を打ちます"
        parts.append(line + "。")
    slowest = max(window, key=_ms)
    if _ms(slowest) >= 3000:
        parts.append(f"{slowest['n']}手目で{_ms(slowest) // 1000}秒考えました。")
    captures = sum(1 for move in window if move.get("captured"))
    if captures >= 3:
        parts.append(f"この区間だけで{captures}枚の駒が入れ替わりました。")
    return "".join(parts)[:MAX_CHUNK_SPEECH]


def chunk_facts(moves, start: int, end: int) -> str:
    """The reviewed facts of one stretch, as the model's only input."""
    window = [move for move in moves if start <= move["n"] <= end]
    lines = [f"対象: {start}手目から{end}手目まで"]
    lines.append("指し手: " + "、".join(move["notation"] for move in window))
    for move in window:
        if move.get("captured"):
            lines.append(f"{move['n']}手目: {move['notation']}で{move['captured']}を取る")
        if move.get("promote"):
            lines.append(f"{move['n']}手目: {move['notation']}で成る")
        if move.get("drop"):
            lines.append(f"{move['n']}手目: {move['notation']}で持ち駒を打つ")
    slow = [move for move in window if _ms(move) >= 2000]
    if slow:
        lines.append("長考: " + "、".join(f"{move['n']}手目{_ms(move) // 1000}秒" for move in slow[:4]))
    return "\n".join(lines)


def build_show(moves, *, color: str, opponent=None, result=None, request_id=None,
               runtime_id=None, generation=None) -> dict:
    """The bounded payload the view renders and the corner steps through."""
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_SHOW_MOVES:
        raise ShowError("invalid kifu")
    compact = []
    for move in moves:
        compact.append({
            "n": move["n"],
            "side": move["side"],
            "usi": move["usi"],
            "notation": move["notation"],
            "captured": move.get("captured"),
            "promote": bool(move.get("promote")),
            "drop": bool(move.get("drop")),
            "ms": _ms(move),
            "board": move.get("board"),
        })
    payload = {
        "schema": SHOW_SCHEMA,
        "kind": "tsuitate_review",
        "request_id": request_id if isinstance(request_id, str) else None,
        # The view only serves a payload published for its own generation.
        "runtime_id": runtime_id if isinstance(runtime_id, str) else None,
        "generation": generation if type(generation) is int else None,
        "color": color,
        "opponent": opponent if isinstance(opponent, str) else None,
        "result": result if isinstance(result, dict) else None,
        "move_count": len(compact),
        "index": 0,
        "done": False,
        "moves": compact,
        "commentary": {},
    }
    return payload


def encode_show(payload) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_SHOW_BYTES:
        raise ShowError("show payload is too large")
    return raw


def write_show(state_dir, payload, *, index=None, done=None) -> dict:
    """Atomically publish the payload (and optionally its current index)."""
    directory = Path(state_dir)
    if not directory.is_dir():
        raise ShowError("state directory is missing")
    if index is not None:
        payload["index"] = max(0, min(int(index), int(payload.get("move_count", 0))))
    if done is not None:
        payload["done"] = bool(done)
    raw = encode_show(payload)
    path = directory / SHOW_FILENAME
    temp = directory / (SHOW_FILENAME + f".tmp.{os.getpid()}")
    try:
        with open(temp, "wb") as handle:
            handle.write(raw)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink()
        except OSError:
            pass
        raise ShowError("show payload could not be written") from exc
    return payload


def read_show(state_dir, *, request_id=None, runtime_id=None, generation=None) -> dict | None:
    """Bounded read of the published payload, or ``None``.

    ``runtime_id`` / ``generation`` pin the payload to the view's own runtime, so
    a show published for a previous corner generation is never rendered.  Every
    failure is ``None``: a broken or absent show must never break the view.
    """
    path = Path(state_dir) / SHOW_FILENAME
    try:
        raw = path.read_bytes()[: MAX_SHOW_BYTES + 1]
    except OSError:
        return None
    if len(raw) > MAX_SHOW_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if (not isinstance(payload, dict) or payload.get("schema") != SHOW_SCHEMA
            or payload.get("kind") != "tsuitate_review"
            or not isinstance(payload.get("moves"), list)
            or len(payload["moves"]) > MAX_SHOW_MOVES):
        return None
    if request_id is not None and payload.get("request_id") != request_id:
        return None
    if runtime_id is not None and payload.get("runtime_id") != runtime_id:
        return None
    if generation is not None and payload.get("generation") != generation:
        return None
    return payload


def show_view(payload) -> dict | None:
    """The bounded projection the broadcast view serves."""
    if not isinstance(payload, dict):
        return None
    index = payload.get("index")
    return {
        "active": True,
        "done": bool(payload.get("done")),
        "index": index if type(index) is int and 0 <= index <= MAX_SHOW_MOVES else 0,
        "move_count": payload.get("move_count") if type(payload.get("move_count")) is int else 0,
        "color": payload.get("color") if payload.get("color") in {"sente", "gote"} else None,
        "opponent": payload.get("opponent") if isinstance(payload.get("opponent"), str) else None,
        "result": payload.get("result") if isinstance(payload.get("result"), dict) else None,
        "commentary": payload.get("commentary") if isinstance(payload.get("commentary"), dict) else {},
    }


def step_plan(payload, *, step_seconds: float = STEP_SECONDS,
              monotonic=time.monotonic) -> list[dict]:
    """When each move of the show is displayed, and what is spoken with it.

    One entry per chunk: ``(start, end, text, at)`` where ``at`` is the wall
    clock offset (seconds) the chunk begins at.  The corner sleeps between
    chunks, so the display advances move by move while the commentary of the
    current stretch is on air.
    """
    moves = payload.get("moves") if isinstance(payload, dict) else None
    if not isinstance(moves, list) or not moves:
        return []
    commentary = payload.get("commentary") if isinstance(payload.get("commentary"), dict) else {}
    plan = []
    at = 0.0
    for start, end in chunk_ranges(len(moves)):
        text = commentary.get(str(start))
        if not isinstance(text, str) or not text:
            text = fallback_chunk_text(moves, start, end)
        plan.append({"start": start, "end": end, "text": text, "at": at})
        at += (end - start + 1) * float(step_seconds)
    return plan
