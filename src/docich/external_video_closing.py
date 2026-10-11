"""Spoken impression after the Fly Me To The Home! special corner ends.

The operator ending the corner means the game was cleared (stage 50). One short
line is generated (best effort) and spoken through the shared Soren comment
queue after Soren is back on air. Generation is opt-in: a non-empty
``[external_video_corner] closing_agents`` chain is the billed-run consent.
Any failure or unsafe output falls back to a fixed line, never to silence of
the corner's restore.
"""
from __future__ import annotations

import os
import re
import tomllib

GAME_TITLE = "Fly Me To The Home!"
GENERATE_TIMEOUT_S = 90
MIN_CHARS, MAX_CHARS = 20, 220
_KANA_KANJI = re.compile(r"[぀-ヿ㐀-鿿]")
_UNSAFE = re.compile(r"(?i)thinking|reasoning|as an ai|assistant|<[^>]+>|```|https?://|\$\{|\bprompt\b")


def closing_agents(g) -> str:
    try:
        raw = tomllib.loads(g.config_path.read_text()).get("external_video_corner", {})
        value = raw.get("closing_agents", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


def fallback_text(minutes: int) -> str:
    return (f"{GAME_TITLE}、五十面クリアです。約{minutes}分、最後まで見届けました。"
            "ふわふわ進んでは落ちる緊張感が最後まで続いて、ゴールの家にたどり着いた瞬間はほっとしました。"
            "ご視聴ありがとうございました。")


def build_prompt(minutes: int) -> str:
    return (
        f"あなたは配信の司会です。いま、配信者が実機で遊んでいたゲーム『{GAME_TITLE}』"
        f"(家を目指して進むアクション)を、約{minutes}分かけて全50面クリアし、特別コーナーが終わりました。\n"
        "クリア直後に視聴者へ話す感想を、話し言葉の日本語で2〜3文、120〜200字で書いてください。\n"
        "条件: ゲームの具体的な仕掛けや数値を創作しない / 絵文字・記号・箇条書き・括弧書きを使わない / "
        "最後は視聴者へのひとことで締める。\n"
        "出力は読み上げる本文だけにしてください。前置きや説明は書かないでください。"
    )


def sanitize(text: str) -> str | None:
    """Single spoken paragraph, or None when the model output is unsafe."""
    if not isinstance(text, str):
        return None
    value = " ".join(text.replace("　", " ").split()).strip("「」『』\"' ")
    if not MIN_CHARS <= len(value) <= MAX_CHARS or _UNSAFE.search(value):
        return None
    if len(_KANA_KANJI.findall(value)) < len(value) * 0.5:
        return None
    return value


def _generate(g, agents: str, minutes: int) -> str | None:
    from .ai_generate import run_prompt

    env = {**os.environ, "DOCICH_ALLOW_REAL_AI": "1"}
    result = run_prompt(g, label="RADIO:flyhome-closing", agents=agents, prompt_text=build_prompt(minutes),
                        timeout=GENERATE_TIMEOUT_S, timeout_sec=float(GENERATE_TIMEOUT_S + 30), env=env)
    if result.returncode != 0:
        return None
    return sanitize(result.output)


def speak_closing(g, minutes: int, *, generate=_generate, enqueue=None) -> str:
    """Return ``spoken:ai``, ``spoken:fallback`` (raises only if enqueue fails)."""
    if enqueue is None:
        from .trading.soren_output import enqueue_audio_text as enqueue
    text, kind = None, "fallback"
    agents = closing_agents(g)
    if agents:
        try:
            text = generate(g, agents, minutes)
        except Exception:
            text = None
        if text:
            kind = "ai"
    enqueue(g, text or fallback_text(minutes), context="external_video_closing")
    return "spoken:" + kind
