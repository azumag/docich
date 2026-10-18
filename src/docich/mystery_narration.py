"""Bounded mystery-corner narration for the shared overlay and audio queues.

The corner manager owns *when* this runs; this module only formats facts that
the deterministic case engine already verified and hands them to the reviewed
shared sinks (overlay ``append_event`` with the ``prediction`` category, and
``enqueue_audio_text`` when the catalog row has ``audio_enabled = true``).

When a non-empty ``[mystery_corner] narration_agents`` chain is configured,
the shared LLM dispatch may turn the same verified facts into natural prose.
Any failure, timeout or unsafe output falls back to the fixed text here. The
model never sees the hidden solution (culprit/method/motive) before the
reveal phase, is never allowed to decide game state, and its output is
display-only: the reveal facts it may retell come from the engine, never the
other way around.

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

TITLE = "AI探偵ミステリー"
INTRO_TITLE = "AI探偵ミステリー コーナー紹介"
MAX_SPEECH = 300
GENERATE_TIMEOUT_S = 20
NARRATION_LABEL = "RADIO:mystery-narration"
CONTEXTS = {
    "intro": "mystery:intro",
    "investigate": "mystery:investigate",
    "interrogate": "mystery:interrogate",
    "deduction": "mystery:deduction",
    "prediction": "mystery:prediction",
    "reveal": "mystery:reveal",
}
VOICE_CONTEXTS = tuple(CONTEXTS.values())

_UNSAFE = re.compile(
    r"(?i)thinking|reasoning|as an ai|assistant|<[^>]+>|```|https?://|\$\{|\bprompt\b")
_KANA_KANJI = re.compile(r"[぀-ヿ㐀-鿿]")
_SENTENCE = re.compile(r"[。！？!?]")

_INTRO_TEXT = (
    "ここからはAI探偵ミステリーのコーナーです。今夜の舞台は{place}。"
    "{victim}が{time}に殺されました。容疑者は{count}人です。"
    "みんなでアンケートに答えて手がかりを集め、最後に犯人をチャンネルポイント予想で当ててください。"
    "事件の答えは配信のハッシュですでに確定しています。公正な事件です。"
)

_INVESTIGATE_TEXT = "現場調査です。「{room}」を調べました。見つかったのは{clue}。"
_INTERROGATE_TEXT = "{suspect}を尋問しました。アリバイは「{alibi}」。"
_DEDUCTION_TEXT = "そろそろ推理の時間です。手がかりを整理して、犯人だと思う容疑者を予想で選んでください。"
_REVEAL_TEXT = (
    "答えの開示です。犯人は{culprit}。手法は{method}、動機は{motive}でした。"
    "開示した種で誰でも事件を再計算できます。みなさんの推理は当たったでしょうか。"
)
_NO_CONTEST_TEXT = "予想は対立投票がなく成立しなかったため、返金処理になりました。"
_RESULT_HIT_TEXT = "視聴者の指名は的中しました。犯人は{culprit}でした。"
_RESULT_MISS_TEXT = "視聴者の指名は外れました。犯人は{culprit}でした。"


def narration_agents(g) -> str:
    """The configured narration chain; empty means deterministic text only."""
    try:
        raw = tomllib.loads(Path(g.config_path).read_text(encoding="utf-8"))
        section = raw.get("mystery_corner", {})
        value = section.get("narration_agents", "") if isinstance(section, dict) else ""
    except (OSError, ValueError, AttributeError, TypeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


# --- Deterministic fallback text --------------------------------------------


def intro_text(case) -> str:
    return _INTRO_TEXT.format(
        place=case.place, victim=case.victim, time=case.time_label,
        count=len(case.suspects))[:MAX_SPEECH]


def investigate_text(room_name: str, clue_text: str | None) -> str:
    clue = clue_text if clue_text else "何も見つかりませんでした"
    return _INVESTIGATE_TEXT.format(room=room_name, clue=clue)[:MAX_SPEECH]


def interrogate_text(suspect_name: str, alibi: str) -> str:
    return _INTERROGATE_TEXT.format(suspect=suspect_name, alibi=alibi)[:MAX_SPEECH]


def deduction_text() -> str:
    return _DEDUCTION_TEXT[:MAX_SPEECH]


def reveal_text(culprit_name: str, method: str, motive: str) -> str:
    return _REVEAL_TEXT.format(culprit=culprit_name, method=method, motive=motive)[:MAX_SPEECH]


def prediction_result_text(*, resolved: bool, hit: bool | None, culprit_name: str) -> str:
    if not resolved:
        return _NO_CONTEST_TEXT[:MAX_SPEECH]
    if hit:
        return _RESULT_HIT_TEXT.format(culprit=culprit_name)[:MAX_SPEECH]
    return _RESULT_MISS_TEXT.format(culprit=culprit_name)[:MAX_SPEECH]


# --- Optional LLM prose over the same verified facts ------------------------

_MODEL_RULES = (
    "条件: 与えられた事実だけを使う / 犯人・手法・動機を推測して新しく書かない / "
    "事実にない手がかり・人物・出来事を発明しない / 絵文字・記号・箇条書き・括弧書き・URLを使わない / "
    "話し言葉の日本語で2〜3文、200字以内 / 最後は視聴者へのひとことで締める。\n"
    "出力は読み上げる本文だけにしてください。前置きや説明は書かないでください。"
)


def sanitize(text) -> str | None:
    """One spoken paragraph, or ``None`` when the model output is unusable."""
    if not isinstance(text, str):
        return None
    value = " ".join(text.replace("　", " ").split()).strip("「」『』\"' ")
    if not 40 <= len(value) <= MAX_SPEECH or _UNSAFE.search(value):
        return None
    if len(_KANA_KANJI.findall(value)) < len(value) * 0.5:
        return None
    if not _SENTENCE.search(value):
        return None
    return value


def generate_text(g, agents: str, facts_text: str, fallback: str) -> tuple[str, str]:
    """Return ``(spoken, kind)`` where kind is ``ai`` or ``fallback``.

    The prompt carries only engine-verified facts; the model's output can
    never change them.
    """
    if not agents:
        return fallback, "fallback"
    prompt = (
        "あなたは推理配信の司会（AI探偵）です。事件の確定した事実は次のとおりです。\n"
        + facts_text + "\nこの場面を、視聴者へ話しかけるように生き生きと読み上げてください。\n"
        + _MODEL_RULES
    )
    try:
        from .ai_generate import run_prompt

        env = {**os.environ, "DOCICH_ALLOW_REAL_AI": "1"}
        result = run_prompt(
            g, label=NARRATION_LABEL, agents=agents, prompt_text=prompt,
            timeout=GENERATE_TIMEOUT_S, timeout_sec=float(GENERATE_TIMEOUT_S + 10), env=env,
        )
    except Exception:
        return fallback, "fallback"
    text = getattr(result, "text", None)
    spoken = sanitize(text)
    if spoken is None:
        return fallback, "fallback"
    return spoken, "ai"


# --- Shared sinks -----------------------------------------------------------


def _counts(total, *, skipped=0):
    return {"sent": 0, "failed": 0, "skipped": min(total, max(0, skipped))}


def publish(g, *, kind: str, title: str, overlay_body: str, speech_text: str,
            request_id: str, audio_enabled: bool, speaker: str = "",
            overlay=append_event, speech=enqueue_audio_text, now=time.time) -> dict:
    """One overlay event + one optional utterance. Never raises.

    Returns bounded per-sink counters like ``yoso_narration.announce`` so the
    corner state can project how the item reached the shared queues.
    """
    result = {"overlay": _counts(1),
              "speech": _counts(1, skipped=0 if audio_enabled else 1)}
    event = {
        "ts": int(now()),
        "category": "prediction",
        "level": "info",
        "title": title[:OVERLAY_TITLE_LIMIT],
        "body": overlay_body[:OVERLAY_BODY_LIMIT],
    }
    if isinstance(request_id, str) and request_id:
        event["source_id"] = f"mystery:{kind}:{request_id}"
    try:
        overlay(resolve_soren_root(g), event)
        result["overlay"]["sent"] += 1
    except Exception:
        # Never surface sink errors: they can contain private queue payloads.
        result["overlay"]["failed"] += 1
    if audio_enabled:
        try:
            speech(g, speech_text, context=CONTEXTS[kind], speaker=speaker)
            result["speech"]["sent"] += 1
        except Exception:
            result["speech"]["failed"] += 1
    return result
