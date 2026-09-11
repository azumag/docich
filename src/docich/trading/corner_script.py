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
from .ai_text import extract_json_object, generate_text
from .dashboard_snapshot import build_dashboard_snapshot
from .strategy_store import load_strategy_policy, policy_to_payload
from .strategies import StrategyPolicy


SEGMENT_KEYS = ("corner", "strategy", "result", "improve")
# Narration is read aloud at the start of the corner; each segment should be a
# substantial multi-sentence passage (analysis, outlook, improvement), not a
# one-liner.
MAX_SEGMENT_CHARS = 600
MIN_SEGMENT_CHARS = 300
SCRIPT_LABEL = "RADIO:paper-script"
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class CornerScriptError(RuntimeError):
    """Raised when generated narration is malformed or out of bounds."""


def _safe_reason(exc: BaseException) -> str:
    """Reason for state/logs: exception type only, never model output/stderr."""
    return type(exc).__name__


_POLICY_FACT_KEYS = (
    "momentum_lookback",
    "momentum_threshold_bps",
    "mean_reversion_lookback",
    "mean_reversion_z",
    "max_notional_fraction",
)


def _latest_improvement(logs_dir) -> dict:
    """Latest end-of-corner improvement record (allowlisted policy only).

    The improvement job appends one JSON line per run (status + whether the
    policy changed + the resulting policy). Never raises: unreadable or
    malformed input yields an empty fact.
    """
    try:
        logs = sorted(
            Path(logs_dir).glob("paper-corner-improve-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {}
    if not logs:
        return {}
    try:
        lines = [
            line for line in logs[0].read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        ]
    except OSError:
        return {}
    if not lines:
        return {}
    try:
        data = json.loads(lines[-1])
    except ValueError:
        return {}
    if not isinstance(data, Mapping):
        return {}
    record = {
        "date": logs[0].stem.replace("paper-corner-improve-", ""),
        "status": str(data.get("status", "")),
        "changed": bool(data.get("changed")),
    }
    policy = data.get("policy") if isinstance(data.get("policy"), Mapping) else None
    if policy is not None:
        record["policy"] = {key: policy.get(key) for key in _POLICY_FACT_KEYS}
    return record


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
    chart = snap.get("chart") if isinstance(snap.get("chart"), Mapping) else {}
    focus = None
    if chart.get("symbol"):
        change_pct = None
        try:
            chart_first, chart_last = chart.get("first"), chart.get("last")
            if chart_first is not None and chart_last is not None and float(chart_first) != 0.0:
                change_pct = round(
                    (float(chart_last) - float(chart_first)) / float(chart_first) * 100.0, 2
                )
        except (TypeError, ValueError):
            change_pct = None
        focus = {
            "symbol": str(chart.get("symbol")),
            "bars": chart.get("count"),
            "first": chart.get("first"),
            "last": chart.get("last"),
            "change_pct": change_pct,
        }
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
        "focus": focus,
        "improvement": _latest_improvement(Path(target).parent / "logs"),
        "policy": policy_to_payload(effective),
    }


def build_prompt(facts: Mapping[str, object]) -> str:
    facts_json = json.dumps(dict(facts), ensure_ascii=False, sort_keys=True)
    return (
        "あなたはPAPER暗号資産コーナーの、音声で読み上げるナレーション台本を書きます。\n"
        "以下は実データ(facts)です。事実だけを使い、存在しない数値・銘柄・出来事・ニュースを"
        "作らないでください。\n"
        f"{facts_json}\n\n"
        "視聴者に語りかける、です・ます調の自然な話し言葉で書いてください。各項目は複数の文を"
        "つなげた読み上げ本文にし、箇条書き・見出し・記号の羅列・マークダウン・コードフェンスは"
        "使わないこと。\n"
        "次の4キーだけを持つJSONオブジェクト1つを出力してください。\n"
        "- corner: コーナーの紹介と今日の見どころ。何のコーナーか、画面のどこを・どの順番で"
        "見ればよいか、この後どんな流れで進むか。\n"
        "- strategy: 現在の戦略パラメータを平易に説明し、なぜその設定なのか狙いや設計意図を考察する。\n"
        "- result: 直近の約定・保有・見送り理由・市場鮮度・注目銘柄の値動きを分析し、"
        "良かった点と課題、相場の見立てを述べる。\n"
        "- improve: BOTの改善（前回の改善で何が変わったか、次にどう改善するか）と、"
        "これからの展開予想・注視ポイントを述べる。\n"
        f"各値は日本語で{MIN_SEGMENT_CHARS}〜{MAX_SEGMENT_CHARS}文字程度の、文がつながる本文に"
        "すること。短すぎる台本は不可。JSON以外は出力しないこと。"
    )


def parse_script(text: str) -> dict:
    """Extract the four narration segments. Malformed output raises."""
    data = extract_json_object(text)
    if not isinstance(data, dict):
        raise CornerScriptError("台本のJSONオブジェクトを抽出できません")
    missing = sorted(set(SEGMENT_KEYS) - set(data))
    if missing:
        raise CornerScriptError(f"台本に必要なキーがありません: {', '.join(missing)}")
    # Extra keys are ignored: a model adding one must not lose the narration.
    segments: dict[str, str] = {}
    for key in SEGMENT_KEYS:
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise CornerScriptError(f"台本の値が空です: {key}")
        cleaned = value.strip().replace("\n", " ")
        if len(cleaned) > MAX_SEGMENT_CHARS:
            cleaned = cleaned[:MAX_SEGMENT_CHARS]
        segments[key] = cleaned
    return segments


def _policy_text(policy: Mapping[str, object]) -> str:
    if not policy:
        return (
            "戦略は既定のパラメータで動いており、値動きの勢いと平均回帰を組み合わせて"
            "売買の判断をします。"
        )
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
    focus = facts.get("focus") if isinstance(facts.get("focus"), Mapping) else {}
    improvement = facts.get("improvement") if isinstance(facts.get("improvement"), Mapping) else {}

    corner = (
        "PAPER・暗号資産の模擬売買コーナーです。実際のお金は使わず、bitbankの公開データだけで、"
        "BOTがどのように判断して、どんな結果になったのかを順番に確認していきます。"
        "まず今の戦略、次に直近の売買結果、最後に次の改善方針とこれからの見どころをお伝えします。"
        "画面のチャートと保有、そしてBOT判断の欄をあわせてご覧ください。"
    )

    result = (
        f"まず結果です。模擬資金は{facts.get('capital_jpy')}円、投入は{facts.get('deployed_jpy')}円、"
        f"保有は{len(positions)}銘柄です。"
    )
    if fills:
        first = fills[0]
        result += (
            f"直近の約定は{first.get('symbol')}の{first.get('side')}、数量{first.get('amount')}、"
            f"価格{first.get('price')}でした。"
        )
    else:
        result += "直近の約定はなく、BOTは様子見を選んでいます。"
    if skipped:
        result += "見送り理由は" + "、".join(str(code) for code in skipped[:3]) + "です。"
    result += (
        f"市場鮮度は{facts.get('fresh_markets')}/{facts.get('total_markets')}で、"
        "取得できている範囲のデータで判断しています。"
    )
    if focus.get("symbol"):
        result += (
            f"注目している{focus.get('symbol')}は直近{focus.get('bars')}本で"
            f"{focus.get('change_pct')}パーセント動いています。"
        )

    improve = (
        "最後に改善とこれからの見どころです。コーナーが終わると、BOTは結果をふり返って"
        "戦略パラメータを自動で見直します。"
    )
    if improvement:
        improve += (
            f"前回の改善は{improvement.get('status')}で、"
            f"戦略を更新したかは"
            + ("更新あり" if improvement.get("changed") else "変更なし")
            + "でした。"
        )
    improve += (
        "次は、見送り理由と市場鮮度を確認しながら、エントリー条件と1回あたりの投入額の"
        "バランスを検証していきます。数値は実データに基づく場合だけ見直します。"
    )

    return {
        "corner": corner,
        "strategy": _policy_text(dict(facts.get("policy") or {}))
        + " この設定は、値動きの勢いを捉えつつ、外れ値の影響を抑えて資金を守る狙いです。",
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
