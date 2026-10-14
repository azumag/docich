"""Record Soren's settled 12-game predictions in the 予想王 leaderboard.

Soren's ``twitch_predictions.sh resolve`` appends every settled Helix row to
``<soren_root>/tmp/state/prediction_resolved.jsonl`` (bounded, append-only, see
``tools/prediction_resolved.py``). This module is the ledger side: the common
rotation timer reads that handoff and records new rounds through
``prediction_leaderboard.record_resolved``, which owns the shared lock, the
atomic replace and the per-prediction idempotency.

The module is read-only towards Soren: it never writes, starts or stops a Soren
process and never calls Twitch (the row is already in the handoff). Every failure
is a fixed, non-sensitive code and never aborts the rotation tick.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .game_switch import atomic_write_json
from .prediction_leaderboard import LeaderboardError, extract, record_resolved
from .trading.soren_output import resolve_soren_root

HANDOFF = Path("tmp/state/prediction_resolved.jsonl")
CURSOR_FILE = "soren_predictions_cursor.json"
MAX_BYTES = 512 * 1024
MAX_ROWS = 200
MAX_TEXT = 128

# Fixed error codes; the exception text never reaches state or logs.
ERRORS = frozenset({"handoff-unavailable", "handoff-oversized", "handoff-invalid", "leaderboard"})


class SorenPredictionError(RuntimeError):
    """A fixed, non-sensitive handoff failure."""

    def __init__(self, code="handoff-unavailable"):
        self.code = code if code in ERRORS else "handoff-unavailable"
        super().__init__(self.code)


def handoff_path(g) -> Path:
    return resolve_soren_root(g) / HANDOFF


def cursor_path(g) -> Path:
    return Path(g.state_dir) / CURSOR_FILE


def read_handoff(path) -> list:
    """Return the bounded tail of the handoff as validated Helix rows.

    A symlink, an oversized file or any line that is not a settled prediction row
    is refused whole: the writer already validated each row, so a malformed line
    means tampering or corruption and must not be recorded partially.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SorenPredictionError("handoff-unavailable")
    try:
        if path.stat().st_size > MAX_BYTES:
            raise SorenPredictionError("handoff-oversized")
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise SorenPredictionError("handoff-unavailable") from None
    rows = []
    for line in lines[-MAX_ROWS:]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise SorenPredictionError("handoff-invalid") from None
        if (not isinstance(row, dict) or row.get("status") != "RESOLVED"
                or not isinstance(row.get("id"), str) or not 1 <= len(row["id"]) <= MAX_TEXT):
            raise SorenPredictionError("handoff-invalid")
        try:
            extract(row)
        except LeaderboardError:
            raise SorenPredictionError("handoff-invalid") from None
        rows.append(row)
    return rows


def _stamp(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _read_cursor(g):
    try:
        data = json.loads(cursor_path(g).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or type(data.get("size")) is not int
            or type(data.get("mtime_ns")) is not int):
        return None
    return {"size": data["size"], "mtime_ns": data["mtime_ns"]}


def tick(g, *, clock=time.time) -> dict:
    """Best-effort: record newly settled Soren rounds. Never raises."""
    try:
        return _record_new_rows(g, clock=clock)
    except SorenPredictionError as exc:
        return {"status": "error", "reason": exc.code}
    except Exception:
        # A best-effort side channel must never break the rotation timer.
        return {"status": "error", "reason": "handoff-unavailable"}


def _record_new_rows(g, *, clock) -> dict:
    path = handoff_path(g)
    if not path.exists():
        return {"status": "idle", "reason": "no-handoff"}
    if path.is_symlink() or not path.is_file():
        raise SorenPredictionError("handoff-unavailable")
    stamp = _stamp(path)
    if stamp is None:
        return {"status": "idle", "reason": "no-handoff"}
    if _read_cursor(g) == stamp:
        return {"status": "unchanged"}
    rows = read_handoff(path)
    recorded = 0
    try:
        for row in rows:
            if record_resolved(g.state_dir, row, corner="soren", now=int(clock())):
                recorded += 1
    except (LeaderboardError, OSError):
        # The handoff is untouched and the cursor does not advance, so the same
        # rows are retried once the ledger is writable again.
        raise SorenPredictionError("leaderboard") from None
    atomic_write_json(cursor_path(g), stamp)
    return {"status": "recorded" if recorded else "unchanged", "recorded": recorded}
