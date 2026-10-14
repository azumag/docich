"""Bounded 予想王 announcement for the shared overlay and audio queues.

The corner manager owns *when* this runs; this module only formats the already
recorded ranking and hands it to the reviewed shared sinks:

- overlay: ``overlay_queue.append_event`` with the existing ``prediction``
  category, deduplicated by the corner request id through ``source_id``.
- speech: ``soren_output.enqueue_audio_text`` (opt-in), one bounded utterance in
  the shared audio worker's voice. No local backlog, retry or direct playback.

Both sinks are best-effort: a failure is reported as a fixed status and never
fails the corner or the game switch. The shared audio enqueue bounds itself
(30 s subprocess timeout), so a stuck queue delays the announcement, not the
display window. Only the sanitized board projection (``yoso_view.board``) is
read here; raw ledger rows, Twitch payloads, tokens and viewer logins never
reach the announcement text.
"""
from __future__ import annotations

import time

from .overlay_queue import OVERLAY_BODY_LIMIT, OVERLAY_TITLE_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text, resolve_soren_root
from .yoso_view import board as read_board

TITLE = "予想王"
CONTEXT = "yosou:ranking"
MAX_SPEECH = 300
SPEECH_ROWS = 3
OVERLAY_ROWS = 5
EMPTY_TEXT = "予想王の発表です。確定した予想がまだランキングの条件に届いていません。"
FOOTER_TEXT = "上位予想者だけを集計しています。"


def _rows(board, limit):
    rows = board.get("ranking") if isinstance(board, dict) else None
    rows = rows if isinstance(rows, list) else []
    return [row for row in rows[:limit] if isinstance(row, dict)]


def _rate(row) -> str:
    value = row.get("hit_rate")
    return f"{round(value * 100)}パーセント" if isinstance(value, (int, float)) else "0パーセント"


def narration_text(board, *, limit=SPEECH_ROWS) -> str:
    """One bounded utterance for the top rows (or the empty-ranking notice)."""
    rows = _rows(board, limit)
    if not rows:
        return EMPTY_TEXT
    parts = ["予想王の発表です。"]
    for row in rows:
        parts.append(
            f"第{row.get('rank')}位、同志{row.get('user_name')}、的中率{_rate(row)}、"
            f"{row.get('rounds')}回中{row.get('hits')}回的中、純増{row.get('net')}ポイント。"
        )
    parts.append(FOOTER_TEXT)
    return "".join(parts)[:MAX_SPEECH]


def overlay_body(board, *, limit=OVERLAY_ROWS) -> str:
    """A compact ranking table for the shared overlay queue (bounded)."""
    rows = _rows(board, limit)
    counts = board if isinstance(board, dict) else {}
    lines = [f"確定 {counts.get('rounds') or 0} 予想 / 集計対象 {counts.get('viewers') or 0} 名"
             f"（最小参加数 {counts.get('min_rounds') or 0}）"]
    if not rows:
        lines.append("ランキング対象の同志はまだいません")
    for row in rows:
        lines.append(
            f"{row.get('rank')}位 同志{row.get('user_name')} "
            f"{round(row.get('hit_rate', 0) * 100)}% "
            f"{row.get('rounds')}回中{row.get('hits')}回 純増{row.get('net')}"
        )
    return "\n".join(lines)[:OVERLAY_BODY_LIMIT]


def announce(g, board, *, request_id, audio_enabled=False, overlay=None,
             speech=None, now=time.time, speaker="") -> dict:
    """Publish one overlay event and (opt-in) one speech item. Never raises."""
    overlay = append_event if overlay is None else overlay
    speech = enqueue_audio_text if speech is None else speech
    result = {"overlay": "skipped", "speech": "skipped"}
    event = {
        "ts": int(now()),
        "category": "prediction",
        "level": "info",
        "title": TITLE[:OVERLAY_TITLE_LIMIT],
        "body": overlay_body(board),
    }
    if isinstance(request_id, str) and request_id:
        event["source_id"] = f"yosou:{request_id}"
    try:
        overlay(resolve_soren_root(g), event)
        result["overlay"] = "sent"
    except Exception:
        # Never surface sink errors: they can contain private queue payloads.
        result["overlay"] = "failed"
    if audio_enabled:
        try:
            speech(g, narration_text(board), context=CONTEXT, speaker=speaker)
            result["speech"] = "sent"
        except Exception:
            result["speech"] = "failed"
    return result


def announcement(g, *, audio_enabled=False, **kwargs) -> dict:
    """Read the current board from ``g.state_dir`` and announce it."""
    return announce(g, read_board(g.state_dir), audio_enabled=audio_enabled, **kwargs)
