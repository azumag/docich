"""Advance notice for the next rolling-rotation corner.

The rotation used to draw the next corner at the moment it was due, so nobody
could say what was coming next. ``CornerRotationManager._sync_notice`` now
decides it *before* it is due (the same seeded ranking dispatch uses) and
publishes one small, bounded projection next to the ledger. The Soren status
dashboard reads only this file, so a viewer-facing notice never has to
understand the rotation ledger: the seed, the slot and the request UUIDs stay
private.

While Soren runs a prediction (the 48-game improvement cycle) the shared corner
boundary waits for it to settle, so the start time cannot be promised. The
record then carries ``hold = "prediction"`` and the round's own
``remaining_games``: the dashboard still shows which corner is next, marks the
planned clock as provisional (``予想中``), and shows ``N試合後`` instead of a
clock. After a corner ends the rotation may publish a short
``hold = "prediction-pending"`` window so the next prediction starts before a
new clock is promised (``予想開始待ち``). The spoken announcement stays
suppressed while held.

Record shape (all fields bounded, no identity that is not already public on the
broadcast)::

    {"schema": 1, "corner": "ninvaders", "label": "スペースインベーダー",
     "due_at": 1794000000, "when": "18:00", "source": "auto",
     "hold": null, "remaining_games": null,
     "decided_at": 1793990000, "updated_at": 1793999600}
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .game_switch import atomic_write_json

FILE = "next_corner.json"
SCHEMA = 1
# A promise closer than this is not worth making: the corner is about to start
# (and a spoken line would run into that corner's own intro on the queue).
MIN_LEAD_SECONDS = 120.0
# The timer refreshes the record every tick so the dashboard can tell a live
# notice from one a stopped timer left behind.
REFRESH_SECONDS = 60.0
MAX_AGE_SECONDS = 600.0
# Display width of the label (CJK counts double). Long names are cut, never
# wrapped: the status sidebar renders one line per row.
LABEL_LIMIT = 28
WHEN_LIMIT = 16
SOURCES = frozenset({"auto", "manual"})
HOLDS = frozenset({"prediction", "prediction-pending"})
# The broadcast runs on Japan time; keep the promise independent of the
# system timezone so the dashboard, the log and the spoken line agree.
DISPLAY_TZ = dt.timezone(dt.timedelta(hours=9))

# Public viewer labels. The corner id stays the operational identity (catalog,
# adapter, state files); only these reviewed labels reach the broadcast.
CORNER_LABELS = {
    "ninvaders": "スペースインベーダー",
    "nsnake": "スネーク",
    "bastet": "バステト",
    "moon-buggy": "ムーンバギー",
    "pacman4console": "パックマン",
    "nethack": "NetHack",
    "hanjuku-hero": "半熟英雄",
    "paper": "ペーパートレード",
    "meriken": "ソ連ゲーム91",
    "tsuitate": "ついたて将棋",
    "weather": "天気予報",
    "yosou-oh": "予想王",
    "doushi-card": "同志カード大賞",
    "puyo-corner": "ぷよぷよ",
    "trpg-corner": "TRPG",
    "mystery": "AI探偵ミステリー",
    "communist-law-simulator": "共産主義法律シミュレーター",
}


def corner_label(corner_id: str) -> str:
    """Reviewed broadcast label for a corner id, bounded for one row."""
    raw = str(corner_id or "")
    return (CORNER_LABELS.get(raw) or raw)[:LABEL_LIMIT]


def notice_path(g) -> Path:
    return Path(g.state_dir) / FILE


def _clean_text(value, limit: int) -> str:
    text = str(value or "")
    if any(ord(ch) < 32 for ch in text):
        return ""
    return text[:limit]


def read(g) -> dict | None:
    """Read the published record, or ``None`` when it is missing or invalid."""
    try:
        raw = json.loads(notice_path(g).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    corner = raw.get("corner")
    label = _clean_text(raw.get("label"), LABEL_LIMIT)
    when = _clean_text(raw.get("when"), WHEN_LIMIT)
    source = raw.get("source")
    hold = raw.get("hold")
    remaining = raw.get("remaining_games")
    due_at = raw.get("due_at")
    updated_at = raw.get("updated_at")
    decided_at = raw.get("decided_at")
    if (not isinstance(corner, str) or not corner or not label or not when
            or source not in SOURCES or (hold is not None and hold not in HOLDS)
            or (remaining is not None and (
                type(remaining) is not int or isinstance(remaining, bool)
                or not 0 <= remaining <= 100))
            or type(due_at) is not int
            or type(updated_at) not in (int, float) or isinstance(updated_at, bool)
            or type(decided_at) is not int):
        return None
    return {
        "schema": SCHEMA,
        "corner": corner[:64],
        "label": label,
        "when": when,
        "due_at": due_at,
        "source": source,
        "hold": hold,
        "remaining_games": remaining,
        "decided_at": decided_at,
        "updated_at": float(updated_at),
    }


def clear(g) -> bool:
    """Remove the published record. Returns True when a file was removed."""
    try:
        notice_path(g).unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def format_when(due_at: float, now: float) -> str:
    """Short clock label for the notice (today, tomorrow, or a date)."""
    due = dt.datetime.fromtimestamp(float(due_at), DISPLAY_TZ)
    current = dt.datetime.fromtimestamp(float(now), DISPLAY_TZ)
    clock = due.strftime("%H:%M")
    if due.date() == current.date():
        return clock
    if due.date() == (current + dt.timedelta(days=1)).date():
        return f"明日 {clock}"
    return f"{due.month}/{due.day} {clock}"


def _spoken_clock(due: dt.datetime, now: dt.datetime) -> str:
    clock = f"{due.hour}時" if due.minute == 0 else f"{due.hour}時{due.minute}分"
    if due.date() == now.date():
        return clock
    if due.date() == (now + dt.timedelta(days=1)).date():
        return f"明日{clock}"
    return f"{due.month}月{due.day}日{clock}"


def speech_text(record: dict, now: float) -> str:
    """One bounded utterance for the shared audio queue."""
    label = _clean_text(record.get("label") or record.get("corner"), LABEL_LIMIT)
    due = dt.datetime.fromtimestamp(float(record["due_at"]), DISPLAY_TZ)
    current = dt.datetime.fromtimestamp(float(now), DISPLAY_TZ)
    return f"次のコーナーは{label}、{_spoken_clock(due, current)}からの予定です。"


def publish(g, *, corner: str, due_at: float, source: str = "auto",
            decided_at: float | None = None, hold: str | None = None,
            remaining_games: int | None = None, now: float) -> dict | None:
    """Publish or refresh the projection. Returns the record, or ``None``.

    Rewrites only when the announcement changed or the previous refresh is a
    tick old, so a minute-by-minute timer does not churn the file.
    """
    if source not in SOURCES:
        source = "auto"
    if hold not in HOLDS:
        hold = None
    if type(remaining_games) is not int or isinstance(remaining_games, bool):
        remaining_games = None
    elif not 0 <= remaining_games <= 100:
        remaining_games = None
    if hold is None:
        remaining_games = None
    existing = read(g)
    decided = int(decided_at if decided_at is not None else now)
    if (existing is not None
            and existing["corner"] == str(corner)[:64]
            and existing["due_at"] == int(due_at)
            and existing["source"] == source):
        # The decision did not change: keep its original timestamp.
        decided = existing["decided_at"]
    record = {
        "schema": SCHEMA,
        "corner": str(corner)[:64],
        "label": corner_label(corner),
        "due_at": int(due_at),
        "when": format_when(due_at, now),
        "source": source,
        "hold": hold,
        "remaining_games": remaining_games,
        "decided_at": decided,
        "updated_at": int(now),
    }
    stable = ("schema", "corner", "label", "due_at", "when", "source", "hold",
              "remaining_games", "decided_at")
    if (existing is not None
            and all(existing.get(key) == record[key] for key in stable)
            and float(now) - existing["updated_at"] < REFRESH_SECONDS):
        return existing
    atomic_write_json(notice_path(g), record)
    return record
