"""Bounded 同志カード大賞 announcement for the shared overlay and audio queues.

The corner manager owns *when* this runs; this module only formats the already
validated ranking payload and hands it to the reviewed shared sinks:

- overlay: ``overlay_queue.append_event`` with the existing ``prediction``
  category, deduplicated by the corner request id through ``source_id``.
- speech: ``soren_output.enqueue_audio_text`` (opt-in), one bounded utterance in
  the shared audio worker's voice. No local backlog, retry or direct playback.

Both sinks are best-effort: a failure is reported as a fixed status and never
fails the corner or the game switch. The shared audio enqueue bounds itself,
so a stuck queue delays the announcement, not the display window.

Only the validated design §6 payload is read here (transient runtime file the
program adapter wrote before the switch). Raw endpoint, streamer identity,
tokens and unvalidated rows never reach the announcement text.

Design §8 PR-5: enabling the announcement (overlay + speech) requires owner
approval, so ``ANNOUNCEMENT_ENABLED`` stays ``False`` in this PR. The text
builders are complete and unit-tested; flipping the flag is the whole enablement
step. Speech additionally keeps the catalog's ``audio_enabled`` opt-in.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .overlay_queue import OVERLAY_BODY_LIMIT, OVERLAY_TITLE_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text, resolve_soren_root

#: design §8 PR-5: オーナー承認までは発話・overlay の両方を出さない。
#: 有効化はこのフラグを立てるだけ（文面は本 PR で完成）。
ANNOUNCEMENT_ENABLED = False

TITLE = "同志カード大賞"
CONTEXT = "doushi:ranking"
#: design §4: 4 部門を 1 発話にまとめる。文面が上限を超えたときは末尾の行から
#: 順に落とし、最後の注意書きは必ず全文を残す（真ん中で切られた文は読まない）。
MAX_SPEECH = 300
SPEECH_ROWS = 3
OVERLAY_ROWS = 5
SOURCE_PREFIX = "doushi"
PAYLOAD_FILENAME = "doushi_ranking.json"

PAGE_KEYS = ("by_count", "by_points", "by_unique", "popular_cards")
SPEECH_HEAD = "同志カード大賞の発表です。"

PAGE_SPEECH_OPENERS = (
    "排出枚数は、",
    "使用チャネルポイントは、",
    "蒐集カード数は、",
    "人気カードは、",
)
EMPTY_TEXT = "同志カード大賞の発表です。まだランキングに該当する同志がいません。"
FOOTER_TEXT = "配信者本人とBOTは集計から除いています。"


def _payload_dict(payload) -> dict:
    return payload if isinstance(payload, dict) else {}


def _rows(payload, key: str, limit: int) -> list[dict]:
    rows = _payload_dict(payload).get(key)
    rows = rows if isinstance(rows, list) else []
    return [row for row in rows[:limit] if isinstance(row, dict)]


def _speech_row(row: dict, *, key: str) -> str:
    rank = row.get("rank")
    head = f"第{rank}位"
    if key == "popular_cards":
        name = row.get("card_name")
        count = row.get("actual_count")
        drawers = row.get("drawer_count")
        return f"{head}、{name}、{count}枚、{drawers}人が獲得。"
    name = row.get("username")
    if key == "by_count":
        return f"{head}、同志{name}、{row.get('redemption_count')}枚。"
    if key == "by_points":
        return f"{head}、同志{name}、{row.get('total_points')}ポイント使用。"
    return f"{head}、同志{name}、{row.get('unique_count')}種類。"


def narration_text(payload, *, limit=SPEECH_ROWS) -> str:
    """One bounded utterance covering all four pages (top rows each).

    Bounds are resolved before speaking: whole rows are dropped and the footer
    is never cut, so no announcement ever stops mid-sentence.
    """
    board = _payload_dict(payload)
    pages = [(index, key, _rows(board, key, limit))
             for index, key in enumerate(PAGE_KEYS)]
    pages = [page for page in pages if page[2]]
    if not pages:
        return EMPTY_TEXT

    def compose(rows_per_page: int) -> str:
        parts = [SPEECH_HEAD]
        for index, key, rows in pages:
            parts.append(PAGE_SPEECH_OPENERS[index])
            parts.extend(_speech_row(row, key=key) for row in rows[:rows_per_page])
        parts.append(FOOTER_TEXT)
        return "".join(parts)

    rows_per_page = limit
    while rows_per_page > 1 and len(compose(rows_per_page)) > MAX_SPEECH:
        rows_per_page -= 1
    text = compose(rows_per_page)
    if len(text) > MAX_SPEECH:
        # One row per page overflowing needs an absurd name: drop the trailing
        # department rather than cutting a sentence in half.
        while pages and len(text) > MAX_SPEECH:
            pages.pop()
            text = compose(1)
    return text[:MAX_SPEECH]


def overlay_body(payload, *, limit=OVERLAY_ROWS) -> str:
    """A compact four-page summary for the shared overlay queue (bounded)."""
    board = _payload_dict(payload)
    period = board.get("period")
    period_label = "全期間" if period == "all" else ("直近7日" if period == "7d" else "直近30日")
    lines = [f"集計期間 {period_label} / 全 {board.get('card_type_total') or 0} 種類"]
    heads = ("排出枚数", "使用チャネルポイント", "蒐集カード数", "人気カード")
    for index, key in enumerate(PAGE_KEYS):
        rows = _rows(board, key, limit)
        if not rows:
            continue
        lines.append(f"■ {heads[index]}")
        for row in rows:
            if key == "popular_cards":
                lines.append(
                    f"{row.get('rank')}位 {row.get('card_name')} "
                    f"{row.get('actual_count')}枚（{row.get('drawer_count')}人が獲得）"
                )
            elif key == "by_points":
                lines.append(
                    f"{row.get('rank')}位 同志{row.get('username')} "
                    f"{row.get('total_points')}pt"
                )
            elif key == "by_unique":
                lines.append(
                    f"{row.get('rank')}位 同志{row.get('username')} "
                    f"{row.get('unique_count')}種類"
                )
            else:
                lines.append(
                    f"{row.get('rank')}位 同志{row.get('username')} "
                    f"{row.get('redemption_count')}枚"
                )
    if len(lines) == 1:
        lines.append("ランキング対象の同志はまだいません")
    return "\n".join(lines)[:OVERLAY_BODY_LIMIT]


def announce(g, payload, *, request_id, audio_enabled=False, overlay=None,
             speech=None, now=time.time, speaker="") -> dict:
    """Publish one overlay event and (opt-in) one speech item. Never raises."""
    overlay = append_event if overlay is None else overlay
    speech = enqueue_audio_text if speech is None else speech
    result = {"overlay": "skipped", "speech": "skipped"}
    if not ANNOUNCEMENT_ENABLED:
        # Owner approval pending (design §8 PR-5): generate text, emit nothing.
        return result
    event = {
        "ts": int(now()),
        "category": "prediction",
        "level": "info",
        "title": TITLE[:OVERLAY_TITLE_LIMIT],
        "body": overlay_body(payload),
    }
    if isinstance(request_id, str) and request_id:
        event["source_id"] = f"{SOURCE_PREFIX}:{request_id}"
    try:
        overlay(resolve_soren_root(g), event)
        result["overlay"] = "sent"
    except Exception:
        # Never surface sink errors: they can contain private queue payloads.
        result["overlay"] = "failed"
    if audio_enabled:
        try:
            speech(g, narration_text(payload), context=CONTEXT, speaker=speaker)
            result["speech"] = "sent"
        except Exception:
            result["speech"] = "failed"
    return result


def _load_payload(g) -> dict | None:
    """Read the transient payload file the program adapter wrote.

    ``None`` means "nothing verified to announce": never fall back to a previous
    ranking (design §4: 取得失敗時は前回値を出さない）。
    """
    try:
        from .doushi_corner import RUNTIME_IDENTITY_KEY, STATE_FILENAME
        from .naming import runtime_directory

        state = json.loads(
            (Path(g.state_dir) / STATE_FILENAME).read_text(encoding="utf-8")
        )
        identity = state.get(RUNTIME_IDENTITY_KEY)
        runtime_id = identity.get("runtime_id") if isinstance(identity, dict) else None
        if not isinstance(runtime_id, str) or not runtime_id:
            return None
        path = runtime_directory(Path(g.state_dir), runtime_id) / PAYLOAD_FILENAME
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(stored, dict) or not isinstance(stored.get("payload"), dict):
        return None
    return stored["payload"]


def announcement(g, *, audio_enabled=False, **kwargs) -> dict:
    """Read the current payload from the runtime directory and announce it."""
    payload = _load_payload(g)
    if payload is None:
        return {"overlay": "skipped", "speech": "skipped"}
    return announce(g, payload, audio_enabled=audio_enabled, **kwargs)
