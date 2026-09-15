"""Deterministic stock-universe selection for paper trading.

This module intentionally has no broker or order capability.  It ranks bounded,
read-only candidate observations and keeps the focused universe stable with
hysteresis/cooldown while pinning already-held symbols for exit management.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from pathlib import Path
import re

from .core import decimal, digest, stamp

D = Decimal


@dataclass(frozen=True)
class SelectorPolicy:
    momentum_window_s: int = 60
    volume_window_s: int = 60
    momentum_weight: str = "1.00"
    volume_accel_weight: str = "0.60"
    turnover_weight: str = "0.20"
    volatility_weight: str = "0.35"
    spread_penalty: str = "0.50"
    min_turnover_jpy: str = "10000000"
    max_spread_bps_for_selection: str = "40"
    candidate_count: int = 20
    focused_universe_count: int = 5
    replace_margin: str = "0.20"
    replace_cooldown_s: int = 15
    warmup_s: int = 60
    candidate_age_s: int = 15
    exit_buffer_s: int = 30

    def __post_init__(self):
        for key, lo, hi in (("momentum_window_s", 15, 300), ("volume_window_s", 15, 300),
                            ("candidate_count", 3, 100), ("focused_universe_count", 1, 20),
                            ("replace_cooldown_s", 0, 300), ("warmup_s", 0, 600),
                            ("candidate_age_s", 1, 60), ("exit_buffer_s", 0, 300)):
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"invalid selector policy {key}")
        if self.focused_universe_count > self.candidate_count:
            raise ValueError("focused universe exceeds candidate count")
        for key in ("momentum_weight", "volume_accel_weight", "turnover_weight",
                    "volatility_weight", "spread_penalty", "min_turnover_jpy",
                    "max_spread_bps_for_selection", "replace_margin"):
            value = decimal(getattr(self, key))
            if value < 0:
                raise ValueError(f"negative selector policy {key}")
        if decimal(self.min_turnover_jpy) < D("100000"):
            raise ValueError("selector turnover floor too small")
        if not 0 <= decimal(self.replace_margin) <= D("2"):
            raise ValueError("invalid selector replacement margin")
        if not D("1") <= decimal(self.max_spread_bps_for_selection) <= D("200"):
            raise ValueError("invalid selector spread ceiling")

    @property
    def version(self) -> str:
        return digest(asdict(self))[:20]


@dataclass(frozen=True)
class Candidate:
    symbol: str
    ts: float
    price: str
    turnover_jpy: str
    momentum_bps: str
    volume_accel: str
    volatility_bps: str
    spread_bps: str
    tradeable: bool

    def validate(self, now: float, policy: SelectorPolicy) -> None:
        if not re.fullmatch(r"[0-9A-Z]{4}", self.symbol):
            raise ValueError("invalid TSE symbol")
        age = stamp(now) - stamp(self.ts)
        if not 0 <= age <= policy.candidate_age_s:
            raise ValueError("stale/future candidate")
        if self.tradeable is not True:
            raise ValueError("candidate not tradeable")
        if decimal(self.price) <= 0 or decimal(self.turnover_jpy) < decimal(policy.min_turnover_jpy):
            raise ValueError("candidate below liquidity floor")
        if not 0 <= decimal(self.spread_bps) <= decimal(policy.max_spread_bps_for_selection):
            raise ValueError("candidate spread too wide")
        if decimal(self.volume_accel) < 0 or decimal(self.volatility_bps) < 0:
            raise ValueError("invalid candidate metrics")
        # Bound untrusted provider data before it participates in ranking.
        if abs(decimal(self.momentum_bps)) > D("5000") or decimal(self.volume_accel) > D("100"):
            raise ValueError("candidate metric out of bounds")
        if decimal(self.volatility_bps) > D("5000"):
            raise ValueError("candidate volatility out of bounds")


def active_selector_policy(root: Path) -> SelectorPolicy:
    path = root / "active-selector-policy.json"
    if not path.exists():
        return SelectorPolicy()
    raw = json.loads(path.read_text())
    if set(raw) < {"policy"}:
        raise ValueError("invalid selector policy file")
    return SelectorPolicy(**raw["policy"])


def candidate_score(candidate: Candidate, policy: SelectorPolicy) -> Decimal:
    candidate.validate(candidate.ts, policy)  # value-domain check independent of wall clock
    turnover_ratio = min(D("10"), decimal(candidate.turnover_jpy) / decimal(policy.min_turnover_jpy))
    volume_bonus = max(D(0), decimal(candidate.volume_accel) - 1) * 100
    return (
        abs(decimal(candidate.momentum_bps)) * decimal(policy.momentum_weight)
        + volume_bonus * decimal(policy.volume_accel_weight)
        + turnover_ratio * 10 * decimal(policy.turnover_weight)
        + decimal(candidate.volatility_bps) * decimal(policy.volatility_weight)
        - decimal(candidate.spread_bps) * decimal(policy.spread_penalty)
    )


def read_file_candidates(config: dict, root: Path, now: float, policy: SelectorPolicy) -> list[Candidate]:
    path = root / config.get("candidate_file", "market-stocks-candidates.json")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("candidate input exceeds limit")
    raw = json.loads(path.read_text())
    if raw.get("market") != "stocks" or raw.get("realtime") is not True:
        raise ValueError("wrong market or delayed/synthetic candidate input")
    if abs(stamp(now) - stamp(raw.get("as_of"))) > policy.candidate_age_s:
        raise ValueError("candidate snapshot stale")
    rows = raw.get("candidates")
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("invalid candidate list")
    result: list[Candidate] = []
    seen: set[str] = set()
    for row in rows:
        try:
            candidate = Candidate(**row)
            candidate.validate(now, policy)
        except (TypeError, ValueError, ArithmeticError):
            continue
        if candidate.symbol in seen:
            continue
        seen.add(candidate.symbol)
        result.append(candidate)
    return result


def rank_candidates(candidates: list[Candidate], policy: SelectorPolicy, now: float) -> list[tuple[Candidate, Decimal]]:
    ranked: list[tuple[Candidate, Decimal]] = []
    for candidate in candidates:
        try:
            candidate.validate(now, policy)
            ranked.append((candidate, candidate_score(candidate, policy)))
        except (ValueError, ArithmeticError):
            continue
    ranked.sort(key=lambda item: (-item[1], item[0].symbol))
    return ranked[:policy.candidate_count]


def select_universe(candidates: list[Candidate], policy: SelectorPolicy, *, now: float,
                    current_symbols: list[str], held_symbols: list[str], last_replaced_at: float,
                    session_start: float) -> dict:
    now = stamp(now)
    ranked = rank_candidates(candidates, policy, now)
    scores = {candidate.symbol: score for candidate, score in ranked}
    ranked_symbols = [candidate.symbol for candidate, _ in ranked]
    held = [symbol for symbol in held_symbols if re.fullmatch(r"[0-9A-Z]{4}", symbol)]
    current = [symbol for symbol in current_symbols if symbol in scores and symbol not in held]
    focused = current[:policy.focused_universe_count]
    changed = False

    if now - last_replaced_at >= policy.replace_cooldown_s:
        for challenger in ranked_symbols:
            if challenger in focused or challenger in held:
                continue
            if len(focused) < policy.focused_universe_count:
                focused.append(challenger)
                changed = True
                continue
            weakest = min(focused, key=lambda symbol: (scores.get(symbol, D("-Infinity")), symbol))
            weakest_score = scores.get(weakest, D(0))
            needed = weakest_score + max(D(1), abs(weakest_score)) * decimal(policy.replace_margin)
            if scores[challenger] > needed:
                focused[focused.index(weakest)] = challenger
                changed = True
        focused.sort(key=lambda symbol: (-scores.get(symbol, D("-Infinity")), symbol))

    # Held symbols are pinned even when they leave the scanner ranking, so exit
    # management never disappears merely because selection conditions changed.
    symbols = list(dict.fromkeys(held + focused[:policy.focused_universe_count]))
    replaced_at = now if changed else last_replaced_at
    return {
        "symbols": symbols,
        "focused_symbols": focused[:policy.focused_universe_count],
        "held_symbols": held,
        "ranking": [{"symbol": c.symbol, "score": str(score)} for c, score in ranked],
        "ready": now >= stamp(session_start) + policy.warmup_s and bool(focused),
        "changed": changed,
        "last_replaced_at": replaced_at,
        "policy": policy.version,
        "as_of": now,
    }
