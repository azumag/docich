"""Fact-grounded narration script for the PAPER corner (Issue #198, Stage 3).

The corner speaks four short segments at start. They may be written by an AI
model, but only from the allowlisted facts built here; when AI is unavailable
(or disabled) a deterministic fallback built from the same facts is spoken
instead. This module never places trades and never logs raw model output.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Mapping

from ..config import GlobalConfig
from .ai_text import generate_text
from .dashboard_snapshot import build_dashboard_snapshot
from .strategy_store import load_strategy_policy, policy_to_payload
from .strategies import StrategyPolicy


SEGMENT_KEYS = ("corner", "strategy", "result", "improve")
MAX_SEGMENT_CHARS = 240
SCRIPT_LABEL = "RADIO:paper-script"
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class CornerScriptError(RuntimeError):
    """Raised when generated narration is malformed or out of bounds."""


def _safe_reason(exc: BaseException) -> str:
    """Reason for state/logs: exception type only, never model output/stderr."""
    return type(exc).__name__


def build_facts(trading_dir, *, now=None, policy: StrategyPolicy | None = None) -> dict:
    """Compact, allowlisted facts for narration and policy improvement.

    Only numbers already present in the public status/cache and the effective
    strategy policy are included. No prompts, credentials or raw responses.
    """
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    snap = build_dashboard_snapshot(target, now=moment)
    effective = policy if policy is not None else load_strategy_policy(target)

    portfolio = snap["portfolio"]
    decision = snap["decision"]
    positions = [
        {"symbol": str(item.get("symbol", "")), "amount": str(item.get("amount", ""))}
        for item in portfolio.get("positions", [])
        if isinstance(item, Mapping)
    ]
    fills = [
        {
            "symbol": str(item.get("symbol", "")),
            "side": str(item.get("side", "")),
            "amount": str(item.get("amount", "")),
            "price": str(item.get("price", "")),
            "quote": str(item.get("quote", "")),
        }
        for item in snap.get("fills", [])
        if isinstance(item, Mapping)
    ]
    return {
        "as_of": moment,
        "worker_state": str(snap["header"].get("worker_state", "unknown")),
        "data_age_sec": snap["header"].get("data_age_sec"),
        "capital_jpy": str(portfolio.get("capital_jpy", "?")),
        "deployed_jpy": str(portfolio.get("deployed_jpy", "?")),
        "open_positions": positions,
        "candidate_count": int(decision.get("candidate_count", 0) or 0),
        "candidate_reasons": [
            str(item.get("label", "")) for item in decision.get("reasons", [])
            if isinstance(item, Mapping)
        ],
        "skipped_reasons": [
            str(item.get("label", "")) for item in decision.get("skipped", [])
            if isinstance(item, Mapping)
        ],
        "recent_fills": fills,
        "fresh_markets": int(portfolio.get("fresh_markets", 0) or 0),
        "total_markets": int(portfolio.get("total_markets", 0) or 0),
        "policy": policy_to_payload(effective),
    }


def build_prompt(facts: Mapping[str, object]) -> str:
    facts_json = json.dumps(dict(facts), ensure_ascii=False, sort_keys=True)
    return (
        "あなたはPAPER暗号資産コーナーのナレーション台本を作る補助です。\n"
        "以下は実データ(facts)です。事実だけを使い、新しい数値を作らないでください。\n"
        f"{facts_json}\n\n"
        "次の4キーだけを持つJSONオブジェクト1つを出力してください。"
        "説明文・マークダウン・コードフェンスは書かないこと。\n"
        "- corner: コーナーの説明（何のコーナーで、視聴者に何が見えるか）\n"
        "- strategy: 現在の戦略パラメータの平易な日本語説明\n"
        "- result: 直近の約定・保有・見送り理由・市場鮮度の結果と感想\n"
        "- improve: 次に確認・改善する方向（前向きに。数値の捏造は禁止）\n"
        "各値は160文字以内の日本語文字列とし、JSON以外は出力しないこと。"
    )


def parse_script(text: str) -> dict:
    """Extract the four narration segments. Malformed output raises."""
    raw = str(text or "")
    match = JSON_FENCE_RE.search(raw)
    payload = match.group(1) if match else raw
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise CornerScriptError(f"台本がJSONではありません: {_safe_reason(exc)}") from exc
    if not isinstance(data, dict):
        raise CornerScriptError("台本がJSONオブジェクトではありません")
    missing = sorted(set(SEGMENT_KEYS) - set(data))
    if missing:
        raise CornerScriptError(f"台本に必要なキーがありません: {', '.join(missing)}")
    extra = sorted(set(data) - set(SEGMENT_KEYS))
    if extra:
        raise CornerScriptError(f"台本に未知のキーがあります: {', '.join(extra)}")
    segments: dict[str, str] = {}
    for key in SEGMENT_KEYS:
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise CornerScriptError(f"台本の値が空です: {key}")
        cleaned = value.strip().replace("\n", " ")
        if len(cleaned) > MAX_SEGMENT_CHARS:
            raise CornerScriptError(f"台本の値が長すぎます: {key}")
        segments[key] = cleaned
    return segments


def _policy_text(policy: Mapping[str, object]) -> str:
    return (
        f"戦略は直近{policy.get('momentum_lookback')}本の上昇が"
        f"{policy.get('momentum_threshold_bps')}bpsを超えたら買い、"
        f"平均回帰は{policy.get('mean_reversion_lookback')}本でz値"
        f"{policy.get('mean_reversion_z')}以下、1回の投入は資金の"
        f"{policy.get('max_notional_fraction')}までです。"
    )


def render_fallback(facts: Mapping[str, object]) -> dict:
    """Deterministic narration from the same facts (used when AI is unavailable)."""
    positions = facts.get("open_positions") or []
    fills = facts.get("recent_fills") or []
    skipped = facts.get("skipped_reasons") or []

    corner = (
        "PAPER・暗号資産の模擬売買コーナーです。実際のお金は使わず、"
        "bitbankの公開データだけでBOTの判断と結果を確認します。"
    )

    result = (
        f"模擬資金{facts.get('capital_jpy')}円、投入{facts.get('deployed_jpy')}円、"
        f"保有は{len(positions)}銘柄です。"
    )
    if fills:
        first = fills[0]
        result += (
            f"直近の約定は{first.get('symbol')} {first.get('side')} "
            f"{first.get('amount')}でした。"
        )
    else:
        result += "直近の約定はありません。"
    if skipped:
        result += "見送り理由は" + "、".join(str(code) for code in skipped[:3]) + "です。"
    result += (
        f"市場鮮度は{facts.get('fresh_markets')}/{facts.get('total_markets')}です。"
    )

    improve = (
        "次回は見送り理由と市場鮮度を確認し、戦略パラメータの妥当性を検証します。"
        "数値は実データに基づく場合だけ見直します。"
    )

    return {
        "corner": corner,
        "strategy": _policy_text(dict(facts.get("policy") or {})),
        "result": result,
        "improve": improve,
    }


def generate_corner_script(
    g: GlobalConfig,
    *,
    trading_dir,
    policy: StrategyPolicy | None = None,
    agents: str,
    timeout: int = 180,
    dry_run: bool = False,
    now=None,
) -> dict:
    """Return the four segments, never raising to the corner caller.

    Result shape: ``{"source": "ai"|"fallback", "reason": str|None,
    "segments": {corner, strategy, result, improve}}``. AI is only attempted
    when ``agents`` is non-empty and ``DOCICH_ALLOW_REAL_AI=1``; every other
    path returns the deterministic fallback built from the same facts.
    """
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    try:
        effective = policy if policy is not None else load_strategy_policy(target)
        facts = build_facts(target, now=moment, policy=effective)
    except Exception as exc:  # defensive: fallback must still speak something
        return {
            "source": "fallback",
            "reason": _safe_reason(exc),
            "segments": render_fallback({}),
        }
    fallback = render_fallback(facts)

    cleaned_agents = (agents or "").strip()
    if dry_run:
        return {"source": "fallback", "reason": "dry-run", "segments": fallback}
    if not cleaned_agents:
        return {"source": "fallback", "reason": "no-agents", "segments": fallback}
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        return {"source": "fallback", "reason": "real-ai-disabled", "segments": fallback}

    try:
        prompt = build_prompt(facts)
        raw = generate_text(
            g, label=SCRIPT_LABEL, agents=cleaned_agents, prompt_text=prompt, timeout=timeout
        )
        segments = parse_script(raw)
    except Exception as exc:
        return {"source": "fallback", "reason": _safe_reason(exc), "segments": fallback}
    return {"source": "ai", "reason": None, "segments": segments}
