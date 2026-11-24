"""Bounded ぷよぷよクローン announcements for the shared overlay/audio queues.

The corner manager owns *when* this runs; this module only formats the
already-validated match result and hands it to the reviewed shared sinks:

- overlay: ``overlay_queue.append_event`` with the ``game`` category,
  deduplicated per slot request through ``source_id``.
- speech: ``soren_output.enqueue_audio_text`` (catalog ``audio_enabled``
  opt-in), one bounded utterance in the shared audio worker's voice. No local
  backlog, retry or direct playback.

Both sinks are best-effort: a failure is a fixed status and never fails the
corner or the game switch. Announcement text is built from fixed phrases and
bounded numbers only — chat display names never reach it (privacy: unverified
free-text names stay in the chat router, matching the other corners' rule
that unvalidated rows never reach the announcement text).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import time

from .overlay_queue import OVERLAY_BODY_LIMIT, OVERLAY_TITLE_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text, resolve_soren_root

TITLE = "ぷよぷよクローン"
CONTEXT = "puyo:match"
#: one bounded utterance; sentences are never cut in half.
MAX_SPEECH = 240
SOURCE_PREFIX = "puyo"
LEDGER_FILE = "puyo_corner_ledger.json"
LEDGER_RECENT_S = 7 * 24 * 3600

START_TITLE = "ぷよぷよ対戦開始"
START_BODY = (
    "ぷよぷよクローンを開始します。チャットで !puyo join と打つと参加でき、"
    "参加者は向かって左の盤を動かせます（!puyo left/right/rotate_cw/"
    "rotate_ccw/soft_drop/hard_drop）。"
)

RESULT_HEAD = "ぷよぷよの対戦結果です。"


def start_speech() -> str:
    """The fixed, bounded participation invitation."""
    return START_BODY[:MAX_SPEECH]


def result_text(entry) -> str | None:
    """One bounded result line from a terminal ledger entry.

    ``None`` means "nothing verified to announce" — a missing, stale or
    unreadable entry is never guessed at (fail-closed, no fallback to an
    older slot's result).
    """
    if not isinstance(entry, dict):
        return None
    winner = entry.get("winner")
    if winner == "user":
        result = "プレイヤーの勝ち"
    elif winner == "ai":
        result = "CPUの勝ち"
    elif winner == "draw":
        result = "引き分け"
    elif entry.get("outcome") == "timeout":
        result = "表示時間が先に来ました。引き分け扱いです。"
    else:
        return None
    user_score = max(0, int(entry.get("user_score") or 0))
    ai_score = max(0, int(entry.get("ai_score") or 0))
    participants = entry.get("participants")
    count = len(participants) if isinstance(participants, list) else 0
    parts = [RESULT_HEAD, result + "。"]
    parts.append(f"プレイヤー{user_score}点、CPU{ai_score}点。")
    if count:
        parts.append(f"参加者は{count}人でした。")
    return "".join(parts)[:MAX_SPEECH]


def announce(g, title, body, *, request_id=None, audio_enabled=False,
             overlay=None, speech=None, now=time.time, speaker="") -> dict:
    """Publish one overlay event and (opt-in) one speech item. Never raises."""
    overlay = append_event if overlay is None else overlay
    speech = enqueue_audio_text if speech is None else speech
    result = {"overlay": "skipped", "speech": "skipped"}
    event = {
        "ts": int(now()),
        "category": "game",
        "level": "info",
        "title": str(title)[:OVERLAY_TITLE_LIMIT],
        "body": str(body)[:OVERLAY_BODY_LIMIT],
    }
    if isinstance(request_id, str) and request_id and title:
        event["source_id"] = f"{SOURCE_PREFIX}:{request_id}"
    try:
        overlay(resolve_soren_root(g), event)
        result["overlay"] = "sent"
    except Exception:
        # Never surface sink errors: they can contain private queue payloads.
        result["overlay"] = "failed"
    if audio_enabled:
        try:
            speech(g, str(body)[:MAX_SPEECH], context=CONTEXT, speaker=speaker)
            result["speech"] = "sent"
        except Exception:
            result["speech"] = "failed"
    return result


def load_result(g, started_at, *, now=None) -> dict | None:
    """The latest corner ledger entry that belongs to this display window.

    The live runtime records every finished round into the shared engine
    ledger; only entries that ended inside this slot are announced. Returns
    ``None`` instead of guessing (fail-closed, no stale announcement).
    """
    import json

    if now is None:
        now = time.time()
    try:
        now = float(now)
        started = float(started_at)
    except (TypeError, ValueError, OverflowError):
        return None
    try:
        raw = (Path(g.state_dir) / LEDGER_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            continue
        ended = entry.get("ended_at")
        if not isinstance(ended, str):
            continue
        try:
            ended_ts = dt.datetime.fromisoformat(ended.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
        if ended_ts < now - LEDGER_RECENT_S:
            continue
        if ended_ts < started - 60:
            continue
        return entry
    return None


def announce_result(g, entry, *, request_id=None, audio_enabled=False,
                    overlay=None, speech=None, now=time.time, speaker="") -> dict:
    """Announce one result entry (or skip silently when none is verifiable)."""
    text = result_text(entry)
    if text is None:
        return {"overlay": "skipped", "speech": "skipped"}
    return announce(g, TITLE, text, request_id=request_id,
                    audio_enabled=audio_enabled, overlay=overlay,
                    speech=speech, now=now, speaker=speaker)
