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
    """Latest end-of-corner improvement record (allowlisted policy only)."""
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
    """Compact, allowlisted facts for narration and policy improvement."""
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    snap = build_dashboard_snapshot(target, now=moment)
    effective = policy if policy is not None else load_strategy_policy(target)

    portfolio = snap["portfolio"]
    decision = snap["decision"]
    performance = snap.get("performance") if isinstance(snap.get("performance"), Mapping) else {}
    positions = [
        {
            "symbol": str(item.get("symbol", "")),
            "amount": str(item.get("amount", "")),
            "market_value_jpy": item.get("market_value_jpy"),
            "unrealized_pnl_jpy": item.get("unrealized_pnl_jpy"),
        }
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
            "realized_pnl_jpy": item.get("realized_pnl_jpy"),
        }
        for item in snap.get("fills", [])
        if isinstance(item, Mapping)
    ]
    skipped = [
        {
            "symbol": str(item.get("symbol", "")),
            "side": str(item.get("side", "")),
            "side_label": str(item.get("side_label", "")),
            "reason": str(item.get("label", "")),
        }
        for item in decision.get("skipped", [])
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
        "position_count": int(portfolio.get("position_count", len(positions)) or 0),
        "open_positions_top": positions,
        "performance": {
            "complete": bool(performance.get("complete")),
            "equity_jpy": performance.get("equity_jpy"),
            "cumulative_pnl_jpy": performance.get("cumulative_pnl_jpy"),
            "today_realized_pnl_jpy": performance.get("today_realized_pnl_jpy"),
            "unrealized_pnl_jpy": performance.get("unrealized_pnl_jpy"),
            "realized_total_jpy": performance.get("realized_total_jpy"),
            "priced_positions": performance.get("priced_positions"),
            "position_count": performance.get("position_count"),
        },
        "candidate_count": int(decision.get("candidate_count", 0) or 0),
        "candidate_reasons": [
            str(item.get("label", "")) for item in decision.get("reasons", [])
            if isinstance(item, Mapping)
        ],
        "skipped_decisions": skipped,
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
        "あなたはPAPER暗号資産コーナーのラジオMCです。数字の読み上げ係ではありません。\n"
        "以下の実データ(facts)だけを根拠に、何が起きているか、戦略がうまく機能しているか、"
        "次に何を見るべきかまで解釈して、番組として聞いて面白い台本を書いてください。"
        "存在しない数値・銘柄・出来事・ニュースは絶対に作らないでください。\n"
        f"{facts_json}\n\n"
        "【話し方】\n"
        "- です・ます調の自然な話し言葉。結論を先に言い、その後に理由や数字を添える。\n"
        "- factsを順番に復唱するだけは禁止。数字同士を比較し、意味を説明する。\n"
        "- ラジオやコメント返しのように、軽いツッコミ、たとえ、意外性のある一言を適度に入れる。"
        "ただし事実を曲げるギャグ、寒い決め台詞の連発、過剰な寸劇は禁止。\n"
        "- 損失なら言い訳せず『今のところ負けています』『この条件は効いていません』と率直に言う。"
        "利益でも一時的な含み益だけで『戦略成功』と断定しない。\n"
        "- 同じ文型・同じオチを各段落で繰り返さない。箇条書き、見出し、マークダウンは禁止。\n"
        "【損益の扱い】\n"
        "- performance.cumulative_pnl_jpy がある場合、resultで累積損益（評価込み）を必ず具体的に言う。\n"
        "- performance.today_realized_pnl_jpy は『本日の確定損益』として必ず触れる。"
        "これは本日0時からの売却で確定した損益で、日中の含み変動を含まない。\n"
        "- performance.unrealized_pnl_jpy がある場合、含み損益も使って、確定損益との違いを視聴者に分かるようにする。\n"
        "- performance.complete=false の場合は、価格不足のため累積評価を断定しない。\n"
        "次の4キーだけを持つJSONオブジェクト1つを出力してください。\n"
        "- corner: 今日の見どころを短く提示。画面の損益、見送り、保有、約定のどこを見ると面白いか案内する。\n"
        "- strategy: 現在の戦略パラメータの狙いを平易に説明し、現在の損益や見送り傾向と結び付けて考察する。\n"
        "- result: 累積損益、本日確定損益、含み損益、直近約定、保有、銘柄ごとの見送り理由を横断して、"
        "良かった点・悪かった点・いまの勝ち負けを率直に分析する。\n"
        "- improve: 損益と実際の判断結果から、今の戦略が効いているかを評価し、次回改善で何を検証するかを述べる。\n"
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


def _pnl_text(facts: Mapping[str, object]) -> str:
    perf = facts.get("performance") if isinstance(facts.get("performance"), Mapping) else {}
    parts: list[str] = []
    cumulative = perf.get("cumulative_pnl_jpy")
    today = perf.get("today_realized_pnl_jpy")
    unrealized = perf.get("unrealized_pnl_jpy")
    if cumulative is not None:
        parts.append(f"評価込みの累積損益は{cumulative}円")
    else:
        parts.append("累積損益は一部の保有価格が不足していて評価待ち")
    if today is not None:
        parts.append(f"本日の確定損益は{today}円")
    if unrealized is not None:
        parts.append(f"現在の含み損益は{unrealized}円")
    return "、".join(parts) + "です。"


def render_fallback(facts: Mapping[str, object]) -> dict:
    """Deterministic narration from the same facts (used when AI is unavailable)."""
    positions = facts.get("open_positions_top") or []
    fills = facts.get("recent_fills") or []
    skipped = facts.get("skipped_decisions") or []
    focus = facts.get("focus") if isinstance(facts.get("focus"), Mapping) else {}
    improvement = facts.get("improvement") if isinstance(facts.get("improvement"), Mapping) else {}

    corner = (
        "PAPER・暗号資産の模擬売買コーナーです。今日は単に何を買ったかだけでなく、"
        "累積損益と本日の確定損益を先に見て、BOTの作戦が本当に働いているのかを確認します。"
        "画面右側には、どの銘柄の買い・売りを、なぜ見送ったのかも出ています。"
        "数字が多い画面ですが、要するに勝っているのか、待つべきなのか、そこを一緒に見ていきます。"
    )

    result = (
        f"まず成績です。{_pnl_text(facts)}模擬資金は{facts.get('capital_jpy')}円、"
        f"投入は{facts.get('deployed_jpy')}円、保有は{facts.get('position_count', len(positions))}銘柄です。"
    )
    if fills:
        first = fills[0]
        result += (
            f"直近の約定は{first.get('symbol')}の{first.get('side')}、数量{first.get('amount')}、"
            f"価格{first.get('price')}でした。"
        )
        if first.get("side") == "sell" and first.get("realized_pnl_jpy") is not None:
            result += f"この売却で確定した損益は{first.get('realized_pnl_jpy')}円です。"
    else:
        result += "直近の約定はなく、BOTは様子見を選んでいます。"
    if skipped:
        examples = [
            f"{item.get('symbol')}の{item.get('side_label')}は{item.get('reason')}"
            for item in skipped[:3] if isinstance(item, Mapping)
        ]
        if examples:
            result += "見送りでは、" + "、".join(examples) + "でした。"
    result += (
        f"市場鮮度は{facts.get('fresh_markets')}/{facts.get('total_markets')}で、"
        "取得できている範囲の公開データで判断しています。"
    )
    if focus.get("symbol"):
        result += (
            f"注目している{focus.get('symbol')}は直近{focus.get('bars')}本で"
            f"{focus.get('change_pct')}パーセント動いています。"
        )

    improve = (
        "最後に改善です。利益が出ていても一回の利確だけで作戦成功とは決めませんし、"
        "損失なら相場のせいにして逃げません。累積損益、本日の確定損益、含み損益と見送りの偏りを"
        "並べて、エントリー条件と投入額がちゃんと仕事をしているかを次の改善で検証します。"
    )
    if improvement:
        improve += (
            f"前回の改善は{improvement.get('status')}で、戦略は"
            + ("更新されています。" if improvement.get("changed") else "変更なしでした。")
        )
    improve += "数字が悪ければ素直に作戦を疑い、良くても再現性があるかを確認してから次へ進みます。"

    return {
        "corner": corner,
        "strategy": _policy_text(dict(facts.get("policy") or {}))
        + " 設定値そのものより、その結果として損益と見送りがどう動いたかを見るのが今回のポイントです。",
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
    """Return the four segments, never raising to the corner caller."""
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    try:
        effective = policy if policy is not None else load_strategy_policy(target)
        facts = build_facts(target, now=moment, policy=effective)
    except Exception as exc:
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
