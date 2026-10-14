"""Bounded 予想王 announcement for the shared overlay and audio queues.

The corner manager owns *when* this runs; this module only formats the already
recorded ranking and the fixed corner introduction, and hands them to the
reviewed shared sinks:

- overlay: ``overlay_queue.append_event`` with the existing ``prediction``
  category. The intro and the ranking are two events with distinct ``source_id``
  suffixes (``yosou:intro:<request>`` / ``yosou:ranking:<request>``) because the
  shared queue keeps only the newest event per ``source_id``.
- speech: ``soren_output.enqueue_audio_text`` (opt-in), two bounded utterances in
  the shared audio worker's voice: the corner introduction, then the ranking.
  No local backlog, retry or direct playback.

Both sinks are best-effort: a failure is reported as a fixed count and never
fails the corner or the game switch. The shared audio enqueue bounds itself
(30 s subprocess timeout), so a stuck queue delays the announcement, not the
display window. Note that the sink drops an enqueue whose text was already
queued within its own de-duplication window (~120 s), and reports success for
that drop; a repeated introduction is therefore best-effort, not guaranteed.
Only the sanitized board projection (``yoso_view.board``) is read here; raw
ledger rows, Twitch payloads, tokens and viewer logins never reach the
announcement text.
"""
from __future__ import annotations

from pathlib import Path
import time

from .comment_queue import comment_queue_dir
from .overlay_queue import OVERLAY_BODY_LIMIT, OVERLAY_TITLE_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text, resolve_soren_root
from .yoso_view import board as read_board

TITLE = "予想王"
INTRO_TITLE = "予想王 コーナー紹介"
INTRO_CONTEXT = "yosou:intro"
CONTEXT = "yosou:ranking"
MAX_SPEECH = 300
SPEECH_ROWS = 3
OVERLAY_ROWS = 5
EMPTY_TEXT = "予想王の発表です。確定した予想がまだランキングの条件に届いていません。"
FOOTER_TEXT = "上位予想者だけを集計しています。"
# Views of the corner shown per run. The sink keeps only the newest event per
# `source_id`, so each one needs its own suffix.
OVERLAY_KINDS = ("intro", "ranking")
SPEECH_ITEMS = (("intro", INTRO_CONTEXT), ("ranking", CONTEXT))
# Queue markers of one speech item: the shared audio worker renames the queued
# `<name>_<context>.txt` to `.playing` before playback and removes it only after
# the playback attempt finished, so the marker's absence is the completion
# evidence the generic (non-weather) queue exposes.
VOICE_CONTEXTS = tuple(context for _kind, context in SPEECH_ITEMS)
VOICE_MARKER_SUFFIXES = (".txt", ".playing")
INTRO_TEXT = (
    "ここからは予想王のコーナーです。チャンネルポイントで予想した結果の的中率ランキング、"
    "上位3名を発表します。しばらくお付き合いください。"
)
INTRO_OVERLAY_BODY = (
    "チャンネルポイント予想の的中率ランキングのコーナーです。\n"
    "確定した予想から、的中した上位の予想者を発表します"
    "（全参加者の的中率ではなく、上位予想者だけを集計しています）。"
)


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


def _event(kind, title, body, request_id, now):
    event = {
        "ts": int(now()),
        "category": "prediction",
        "level": "info",
        "title": title[:OVERLAY_TITLE_LIMIT],
        "body": body,
    }
    if isinstance(request_id, str) and request_id:
        event["source_id"] = f"yosou:{kind}:{request_id}"
    return event


def _counts(total, *, skipped=0):
    return {"sent": 0, "failed": 0, "skipped": min(total, max(0, skipped))}


def announce(g, board, *, request_id, audio_enabled=False, overlay=None,
             speech=None, now=time.time, speaker="") -> dict:
    """Publish the corner introduction and the ranking. Never raises.

    Returns bounded per-sink counters (``overlay`` / ``speech``), each over
    ``sent`` / ``failed`` / ``skipped``, so the caller can record how many of the
    two overlay events and two speech items actually reached the shared queues.
    """
    overlay = append_event if overlay is None else overlay
    speech = enqueue_audio_text if speech is None else speech
    events = [
        (kind, INTRO_TITLE if kind == "intro" else TITLE,
         INTRO_OVERLAY_BODY if kind == "intro" else overlay_body(board))
        for kind in OVERLAY_KINDS
    ]
    text = {kind: INTRO_TEXT if kind == "intro" else narration_text(board)
            for kind, _context in SPEECH_ITEMS}
    result = {"overlay": _counts(len(events)),
              "speech": _counts(len(SPEECH_ITEMS),
                                skipped=0 if audio_enabled else len(SPEECH_ITEMS)),
              "overlay_events": len(events), "speech_items": len(SPEECH_ITEMS)}
    for kind, title, body in events:
        try:
            overlay(resolve_soren_root(g), _event(kind, title, body, request_id, now))
            result["overlay"]["sent"] += 1
        except Exception:
            # Never surface sink errors: they can contain private queue payloads.
            result["overlay"]["failed"] += 1
    if audio_enabled:
        for kind, context in SPEECH_ITEMS:
            try:
                speech(g, text[kind], context=context, speaker=speaker)
                result["speech"]["sent"] += 1
            except Exception:
                result["speech"]["failed"] += 1
    return result


def announcement(g, *, audio_enabled=False, **kwargs) -> dict:
    """Read the current board from ``g.state_dir`` and announce it."""
    return announce(g, read_board(g.state_dir), audio_enabled=audio_enabled, **kwargs)


def voice_queue_dir(g) -> Path:
    """Directory of the shared comment queue that this corner speaks through."""
    return comment_queue_dir(resolve_soren_root(g))


def voice_items_in_flight(g) -> int:
    """Count this corner's speech items still queued or being played.

    Zero means nothing is left to read: the item either finished its playback
    attempt (the worker removes the marker afterwards) or the shared sink
    dropped it as a duplicate and never wrote a file. Both cases leave the
    corner with no audio work to wait for. Only this corner's own contexts are
    counted, so a viewer comment or another corner in the queue never extends
    the run.
    """
    queue = voice_queue_dir(g)
    count = 0
    for context in VOICE_CONTEXTS:
        for suffix in VOICE_MARKER_SUFFIXES:
            try:
                count += sum(1 for _ in queue.glob(f"*_{context}{suffix}"))
            except OSError:
                continue
    return count
