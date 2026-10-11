"""Spoken impression after the special corner (OBS/SRT view) ends.

The operator ending the corner means the goal was reached. One short line is
generated (best effort) and spoken through the shared Soren comment queue after
Soren is back on air.  The title, the corner description and the optional
closing prompt come from the settings committed for this run in
``external_video_corner.json``. Later WebUI edits only apply to future runs;
legacy runs without a snapshot retain the historic Fly Me To The Home! texts.
Generation is opt-in: a non-empty
``[external_video_corner] closing_agents`` chain is the billed-run consent.
Any failure or unsafe output falls back to a fixed line, never to silence of
the corner's restore.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from .special_corner import (
    CORNER_FILE, DEFAULT_TITLE as GAME_TITLE, DEFAULTS, validate_settings,
)
GENERATE_TIMEOUT_S = 90
MIN_CHARS, MAX_CHARS = 20, 220
_KANA_KANJI = re.compile(r"[぀-ヿ㐀-鿿]")
_UNSAFE = re.compile(r"(?i)thinking|reasoning|as an ai|assistant|<[^>]+>|```|https?://|\$\{|\bprompt\b")
_NEUTRAL_SETTINGS = {
    "title": "今回のゲーム", "title_suffix": "特別コーナー", "category_id": "",
    "category_name": "", "comment_prompt": "", "closing_prompt": "",
}


def closing_agents(g) -> str:
    try:
        raw = tomllib.loads(g.config_path.read_text()).get("external_video_corner", {})
        value = raw.get("closing_agents", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


_RULES = (
    "条件: ゲームの具体的な仕掛けや数値を創作しない / 絵文字・記号・箇条書き・括弧書きを使わない / "
    "最後は視聴者へのひとことで締める。\n"
    "出力は読み上げる本文だけにしてください。前置きや説明は書かないでください。"
)


def corner_settings(g) -> dict:
    """Resolve this run's committed snapshot, never future WebUI settings.

    Old runs have no snapshot requirement and keep their historical defaults.
    Missing or invalid required snapshots cannot justify game-specific claims.
    """
    state_dir = getattr(g, "state_dir", None)
    if not state_dir:
        return dict(DEFAULTS, is_default=True)
    try:
        raw = (Path(state_dir) / CORNER_FILE).read_bytes()
        if len(raw) > 32768:
            raise ValueError
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError
    except FileNotFoundError:
        return dict(DEFAULTS, is_default=True)
    except (OSError, ValueError):
        return dict(_NEUTRAL_SETTINGS, is_default=False)
    try:
        settings = validate_settings(state.get("special_corner_settings"))
    except ValueError:
        defaults = _NEUTRAL_SETTINGS if state.get("special_corner_snapshot_required") is True else DEFAULTS
        return dict(defaults, is_default=defaults is DEFAULTS)
    return dict(settings, is_default=False)


def _is_flyhome(settings) -> bool:
    return settings["title"] == GAME_TITLE and not settings["closing_prompt"]


def fallback_text(minutes: int, settings: dict | None = None) -> str:
    settings = settings or dict(DEFAULTS)
    if settings["title"] == GAME_TITLE:
        return (f"{GAME_TITLE}、五十面クリアです。約{minutes}分、最後まで見届けました。"
                "ふわふわ進んでは落ちる緊張感が最後まで続いて、ゴールの家にたどり着いた瞬間はほっとしました。"
                "ご視聴ありがとうございました。")
    return (f"{settings['title']}の特別コーナー、約{minutes}分、最後まで見届けました。"
            "一緒に見守ってくださった皆さん、ありがとうございました。")


def build_prompt(minutes: int, settings: dict | None = None) -> str:
    settings = settings or dict(DEFAULTS)
    if _is_flyhome(settings):
        return (
            f"あなたは配信の司会です。いま、配信者が実機で遊んでいたゲーム『{GAME_TITLE}』"
            f"(家を目指して進むアクション)を、約{minutes}分かけて全50面クリアし、特別コーナーが終わりました。\n"
            "クリア直後に視聴者へ話す感想を、話し言葉の日本語で2〜3文、120〜200字で書いてください。\n"
            + _RULES
        )
    lines = [f"あなたは配信の司会です。いま、配信者が実機で遊んでいた『{settings['title']}』の特別コーナー"
             f"(約{minutes}分)が終わりました。"]
    if settings["comment_prompt"]:
        lines.append("コーナーの説明: " + " ".join(settings["comment_prompt"].split("\n")))
    if settings["closing_prompt"]:
        lines.append("感想で触れてほしいこと: " + " ".join(settings["closing_prompt"].split("\n")))
    lines.append("終わった直後に視聴者へ話す感想を、話し言葉の日本語で2〜3文、120〜200字で書いてください。")
    return "\n".join(lines) + "\n" + _RULES


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


def _generate(g, agents: str, minutes: int, *, settings: dict | None = None) -> str | None:
    from .ai_generate import run_prompt

    env = {**os.environ, "DOCICH_ALLOW_REAL_AI": "1"}
    prompt = build_prompt(minutes, settings if settings is not None else corner_settings(g))
    result = run_prompt(g, label="RADIO:flyhome-closing", agents=agents, prompt_text=prompt,
                        timeout=GENERATE_TIMEOUT_S, timeout_sec=float(GENERATE_TIMEOUT_S + 30), env=env)
    if result.returncode != 0:
        return None
    return sanitize(result.output)


def speak_closing(g, minutes: int, *, generate=None, enqueue=None) -> str:
    """Return ``spoken:ai``, ``spoken:fallback`` (raises only if enqueue fails)."""
    if enqueue is None:
        from .trading.soren_output import enqueue_audio_text as enqueue
    text, kind = None, "fallback"
    settings = corner_settings(g)
    agents = closing_agents(g)
    if agents:
        try:
            text = (_generate(g, agents, minutes, settings=settings) if generate is None
                    else generate(g, agents, minutes))
        except Exception:
            text = None
        if text:
            kind = "ai"
    enqueue(g, text or fallback_text(minutes, settings), context="external_video_closing")
    return "spoken:" + kind
