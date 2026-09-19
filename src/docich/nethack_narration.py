"""Bounded deterministic Japanese commentary for reviewed P3b decisions.

One in-flight enqueue, no local backlog/retry, no model or direct playback.
The existing Soren audio worker owns FIFO playback, pauses and sink deduplication.
"""
from __future__ import annotations

import math
import re
import sys
import threading
import time
from typing import Callable

from .nethack_observation import NethackObservation
from .nethack_policy import PolicyDecision
from .trading.soren_output import enqueue_audio_text


_TEXT = {
    "advance_message": "画面の続きを確認するため、メッセージを送ります。",
    "decline_save": "終了を避けるため、セーブ確認を断って冒険を続けます。",
    "prompt_decision": "文脈が必要な質問なので、回答せず判断を保留します。",
    "survival_emergency": "体力が危険な水準です。無理に動かず、回復を待ちます。",
    "status_emergency": "重い状態異常が見えるので、危険な待機をせず判断を保留します。",
    "food_emergency": "空腹が危険な水準なので、探索を止めて判断を保留します。",
    "seek_food": "空腹が見えます。食料の判断はできないので、無理に進まず状況を確認します。",
    "hold_low_hp": "体力が半分以下なので、無理に進まず状況を確認します。",
    "hold_impaired": "状態異常で移動が不確かなので、無理に進まず状況を確認します。",
    "inspect_screen": "自分の位置を特定できないので、移動を保留します。",
    "assess_contact": "隣に生き物が見えますが敵味方が不明なので、入力せず判断を保留します。",
    "explore_step": "見えている安全な地形を選び、一歩ずつ探索します。",
    "exploration_blocked": "安全に進める道が見えないので、ターンを進めて様子を見ます。",
}


def runtime_table(game, name: str) -> dict:
    raw = game.raw.get("nethack", {})
    if not isinstance(raw, dict):
        raise ValueError("nethack must be a table")
    table = raw.get(name, {})
    if not isinstance(table, dict):
        raise ValueError(f"nethack.{name} must be a table")
    return table


def enabled_flag(raw: dict) -> bool:
    enabled = raw.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("enabled must be boolean")
    return enabled


class NethackNarrator:
    def __init__(self, g, game, *, clock: Callable[[], float] = time.monotonic):
        raw = runtime_table(game, "narration")
        self.enabled = enabled_flag(raw)
        self.cooldown_s = raw.get("cooldown_s", 20.0)
        if (
            type(self.cooldown_s) not in (int, float)
            or not math.isfinite(self.cooldown_s)
            or not 5 <= self.cooldown_s <= 300
        ):
            raise ValueError("narration cooldown_s must be between 5 and 300")
        self.speaker = raw.get("speaker", "")
        if not isinstance(self.speaker, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{0,64}", self.speaker):
            raise ValueError("invalid narration speaker")
        self.g = g
        self.clock = clock
        self._last_at = -math.inf
        self._last_key = ""
        self._last_level: int | None = None
        self._busy = threading.Event()
        self.status = "idle" if self.enabled else "disabled"

    def _deliver(self, text: str) -> None:
        try:
            enqueue_audio_text(self.g, text, context="nethack:policy", speaker=self.speaker)
            self.status = "enqueued"
        except Exception:
            self.status = "delivery_failed"
            # No exception text: providers/queues can include private payloads.
            print("[nethack-narration] status=delivery_failed", file=sys.stderr)
        finally:
            self._busy.clear()

    def consider(self, obs: NethackObservation, decision: PolicyDecision) -> None:
        if not self.enabled:
            return
        level = obs.vitals.dungeon_level
        key = decision.intent
        text = _TEXT.get(key)
        # Report visible facts, never infer death from a temporarily absent @.
        if re.search(r"(?:^|\s)You (?:die|have died)\.", obs.message):
            key, text = "death", "死亡の表示を確認したので、今回の冒険の結果を記録から確認します。"
        elif (
            level is not None and level != self._last_level
            and key in {"explore_step", "advance_message", "inspect_screen"}
        ):
            key = f"level:{level}"
            text = f"現在は地下{level}階で、見えている地形と体力を確認します。"
        elif key == "explore_step" and decision.reason.startswith("visible frontier"):
            text = "未探索部分に近い安全な地形を選び、一歩ずつ探索します。"
        # Deduplicate by semantic intent, not coordinates/HP text changing every
        # observation. Unknown future intents/reasons are never read verbatim.
        now = self.clock()
        if not text or key == self._last_key or now - self._last_at < self.cooldown_s or self._busy.is_set():
            return
        self._last_key = key
        self._last_at = now
        self._last_level = level if level is not None else self._last_level
        self._busy.set()
        self.status = "enqueue_pending"
        try:
            threading.Thread(target=self._deliver, args=(text,), daemon=True, name="nethack-narration").start()
        except Exception:
            self._busy.clear()
            self.status = "delivery_failed"
            print("[nethack-narration] status=delivery_failed", file=sys.stderr)
