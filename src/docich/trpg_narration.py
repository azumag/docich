"""Bounded TRPG / murder-mystery narration for the shared overlay and audio queues.

The corner manager owns *when* this runs; this module only formats facts that
the deterministic case engine already verified and hands them to the reviewed
shared sinks (overlay ``append_event`` with the ``trpg`` category, and
``enqueue_audio_text`` when the catalog row has ``audio_enabled = true``).

Both sinks are best-effort: failures are fixed counters and never fail the
corner or the game switch.
"""
from __future__ import annotations

import os
import re
import time
import tomllib
from pathlib import Path

from .overlay_queue import OVERLAY_BODY_LIMIT, OVERLAY_TITLE_LIMIT, append_event
from .trading.soren_output import enqueue_audio_text, resolve_soren_root

TITLE = "TRPG / マーダーミステリー"
INTRO_TITLE = "TRPG / マーダーミステリー コーナー紹介"
MAX_SPEECH = 300
NARRATION_LABEL = "RADIO:trpg-narration"
CONTEXTS = {
    "intro": "trpg:intro",
    "scene": "trpg:scene",
    "choice": "trpg:choice",
    "accusation": "trpg:accusation",
    "reveal": "trpg:reveal",
}
VOICE_CONTEXTS = tuple(CONTEXTS.values())

_UNSAFE = re.compile(
    r"(?i)thinking|reasoning|as an ai|assistant|<[^>]+>|```|https?://|\$\{|\bprompt\b")
_KANA_KANJI = re.compile(r"[぀-ヿ㐀-鿿]")
_SENTENCE = re.compile(r"[。！？!?]")

_INTRO_TEXT = (
    "ここからはTRPG / マーダーミステリーのコーナーです。"
    "みんなで投票して推理を進めてください。"
)

_SCENE_TEXT = "シーン「{scene}」です。{text}"
_CHOICE_TEXT = "選択肢を投票で決めてください。"
_ACCUSATION_TEXT = "推理タイムです。犯人だと思う容疑者を予想してください。"
_REVEAL_TEXT = "答えの開示です。犯人は{culprit}でした。手法は{method}、動機は{motive}。"


def narration_agents(g) -> str:
    try:
        raw = tomllib.loads(Path(g.config_path).read_text(encoding="utf-8"))
        section = raw.get("trpg_corner", {})
        value = section.get("narration_agents", "") if isinstance(section, dict) else ""
    except (OSError, ValueError, AttributeError, TypeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


def intro_text() -> str:
    return _INTRO_TEXT[:MAX_SPEECH]


def scene_text(scene: str, text: str) -> str:
    return _SCENE_TEXT.format(scene=scene, text=text)[:MAX_SPEECH]


def choice_text() -> str:
    return _CHOICE_TEXT[:MAX_SPEECH]


def accusation_text() -> str:
    return _ACCUSATION_TEXT[:MAX_SPEECH]


def reveal_text(culprit: str, method: str, motive: str) -> str:
    return _REVEAL_TEXT.format(culprit=culprit, method=method, motive=motive)[:MAX_SPEECH]


def _counts(total, *, skipped=0):
    return {"sent": 0, "failed": 0, "skipped": min(total, max(0, skipped))}


def publish(g, *, kind: str, title: str, overlay_body: str, speech_text: str,
            request_id: str, audio_enabled: bool, speaker: str = "",
            overlay=append_event, speech=enqueue_audio_text, now=time.time) -> dict:
    result = {"overlay": _counts(1),
              "speech": _counts(1, skipped=0 if audio_enabled else 1)}
    event = {
        "ts": int(now()),
        "category": "trpg",
        "level": "info",
        "title": title[:OVERLAY_TITLE_LIMIT],
        "body": overlay_body[:OVERLAY_BODY_LIMIT],
    }
    if isinstance(request_id, str) and request_id:
        event["source_id"] = f"trpg:{kind}:{request_id}"
    try:
        overlay(resolve_soren_root(g), event)
        result["overlay"]["sent"] += 1
    except Exception:
        result["overlay"]["failed"] += 1
    if audio_enabled:
        try:
            speech(g, speech_text, context=CONTEXTS.get(kind, "trpg:intro"), speaker=speaker)
            result["speech"]["sent"] += 1
        except Exception:
            result["speech"]["failed"] += 1
    return result
