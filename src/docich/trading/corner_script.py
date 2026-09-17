"""Fact-grounded narration script for the PAPER corner.

The corner speaks four substantial segments. Trading facts are augmented, when
real AI narration is enabled, with bounded public crypto-news research and one
actually-held asset spotlight. Research failures never fail the corner and raw
model output is never persisted.
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
from .corner_research import (
    finalize_research_result,
    load_research_result,
    prepare_research_context,
)
from .dashboard_snapshot import build_dashboard_snapshot
from .performance import build_round_trips
from .strategy_store import load_strategy_policy, policy_to_payload
from .strategies import StrategyPolicy


SEGMENT_KEYS = ("corner", "news", "chart", "strategy", "result", "fills", "review", "improve")
MAX_SEGMENT_CHARS = 700
MIN_SEGMENT_CHARS = 300
SCRIPT_LABEL = "RADIO:paper-script"
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class CornerScriptError(RuntimeError):
    """Raised when generated narration is malformed or out of bounds."""


def _safe_reason(exc: BaseException) -> str:
    """Reason for state/logs: exception type only, never model output/stderr."""
    return type(exc).__name__


def _fmt_num(value, ndigits: int = 2) -> str | None:
    """Format a spoken number with at most ``ndigits`` decimals.

    Long raw decimals (e.g. ``7.843078654615100``) are tedious when read aloud,
    so narration rounds to two places. Trailing zeros are stripped (``10000``
    stays ``10,000``, not ``10,000.00``). Returns None when the value is not
    numeric, or when rounding would collapse a nonzero value to zero (tiny
    quantities like ``0.001``); callers then fall back to the raw string.
    """
    try:
        if value is None or isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != 0.0 and round(number, ndigits) == 0.0:
        return None
    import math
    if not math.isfinite(number):
        return None
    text = f"{number:,.{ndigits}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


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


def build_facts(trading_dir, *, now=None, policy: StrategyPolicy | None = None,
                timeframes: Mapping[str, object] | None = None) -> dict:
    """Compact, allowlisted facts for narration and policy improvement.

    ``timeframes`` is the bounded multi-timeframe block from
    :func:`docich.trading.timeframe_chart.build_narration_facts` (public
    candlesticks only). It is optional, so callers that must not touch the
    network still get the trading facts unchanged.
    """
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
            "reason_code": str(item.get("reason_code", "")),
            "signal": item.get("signal_context") if isinstance(item.get("signal_context"), Mapping) else None,
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
    tf_block = timeframes if isinstance(timeframes, Mapping) else {}
    tf_timeframes = tf_block.get("timeframes")
    tf_timeframes = list(tf_timeframes) if isinstance(tf_timeframes, list) else []
    tf_fills = tf_block.get("fill_timeframes")
    tf_fills = list(tf_fills) if isinstance(tf_fills, list) else []
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
        # Realized round trips (buy -> sell) with entry/exit context, for the
        # review segment and the strategy-improvement pass.
        "round_trips": build_round_trips(target / "paper.sqlite3", limit=6),
        # Finalized research contains only bounded public/news analysis and
        # structured improvement hints. A prepared same-corner record is also
        # useful while the narration AI is running.
        "research": load_research_result(target),
        # Public multi-timeframe candlestick views (Issue #348). Empty when the
        # public data could not be fetched; narration must then not imply it.
        "timeframes": tf_timeframes,
        "fill_timeframes": tf_fills,
    }


def build_prompt(facts: Mapping[str, object]) -> str:
    facts_json = json.dumps(dict(facts), ensure_ascii=False, sort_keys=True)
    return (
        "あなたはPAPER暗号資産コーナーのラジオMC兼リサーチャーです。数字の読み上げ係ではありません。\n"
        "以下の実データ(facts)だけを根拠に、何が起きているか、戦略がうまく機能しているか、"
        "ニュースが何を意味しそうか、次に何を見るべきかまで自分の視点で噛み砕いてください。"
        "存在しない数値・銘柄・ニュース・因果関係は絶対に作らないでください。\n"
        f"{facts_json}\n\n"
        "【ニュースの扱い】\n"
        "- research.news_items はGoogle News RSSから取得した見出し・媒体・時刻・RSS要約です。記事全文ではありません。"
        "見出しだけで断定せず、複数項目の共通点や相違点を見て、事実とあなたの推測を言い分けてください。\n"
        "- **1件で済ませず、重要そうなものを5〜6件選び**、それぞれ『何が起きたか』→『市場やBOTにどう効き得るか』→"
        "『実際に何を観測すべきか』の順で、他の項目と関連づけながら具体的に掘り下げます。"
        "価格が動いた理由をニュースだけで決めつけないでください。\n"
        "- research.asset があれば、そのsymbolは実際にPAPERで現在保有中です。指定されたangle_labelを中心に、"
        "backgroundとasset.news_itemsを根拠に、その銘柄ならではの特徴、歴史、面白いエピソード、弱点などを視聴者向けに説明してください。\n"
        "【約定の「なぜ」— 最重要】\n"
        "- recent_fills[i].signal がその発注の理由です。signal.kind（builtin_entry / builtin_exit）、"
        "signal.conditions の feature（return_bps / zscore / pnl_bps / hold_minutes）、observed（観測値）、"
        "threshold（閾値）、op（比較）、lookback（本数）を使います。\n"
        "- fills では直近の約定を1件ずつ、『どの指標が・どの観測値で・どの閾値を越えたから発注したか』を"
        "数値で具体的に説明します。例: 『6本のリターンが+320bpsで閾値150bpsを上回ったので買った』。"
        "signalが無い約定は『記録が無い』と正直に言い、根拠を捏造しない。\n"
        "- そのうえで、その指標がチャートのどこに現れているか（facts.fill_timeframes の各足の位置・バンド・勢い）を"
        "結び付け、『だからこの値段で入った』まで踏み込みます。表示は参考で判断は保存済み5分足と戦略、と必要なら一言。\n"
        "【往復の振り返り】\n"
        "- facts.round_trips は買い→売りで損益が確定した往復です。entry_reason/entry_signal（買った根拠）、"
        "exit_reason/exit_signal（売った根拠）、realized_jpy、hold_sec を持ちます。\n"
        "- review では往復を1件ずつ、**その判断が正しかったのか間違っていたのか**を、エントリーと出口の根拠・"
        "保有時間・実現損益を並べて具体的に評価します。勝ちでも根拠が弱ければ『たまたま』と言い、"
        "負けでも条件どおりなら『ルール通り』と区別します。次に同じ状況でどうするかまで述べます。\n"
        "- ここで出た教訓は improve と improvement_hints に渡し、次回のパラメータ検証・反証条件につなげます。\n"
        "【改善への接続】\n"
        "- ニュース分析からBOT改善に有用な仮説がある場合だけ improvement_hints に構造化してください。無理に案を作らないでください。\n"
        "- kind は parameter / feature / risk / data のいずれか。featureは新機能、riskはリスク制御、dataは新しい観測データ、"
        "parameterは既存パラメータ調整です。各案に根拠(evidence)と確信度(confidence: low/medium/high)を付けます。\n"
        "- ニュース単発を根拠に自動売買ルールを直接追加する提案は禁止。検証方法・反証条件をrationaleに含めてください。\n"
        "【時間足チャートの解説】\n"
        "- facts.timeframes は注目銘柄の日足・1時間足・15分足・1分足の公開ローソクです。各要素は bars(本数)、"
        "last_close、range_change_pct(表示区間の変化率)、momentum_pct(直近数本の勢い)、sma、bb_upper/bb_lower"
        "(ボリンジャーバンド)、bb_position(0が下限・1が上限)、trend、bb_phrase を持ちます。\n"
        "- chart では4つの時間足を1つずつ、『事実』→『解釈』→『次に何を見るか』の順で解説します。"
        "数値は facts にあるものだけを使い、無い足や空のtimeframesは正直に『取得できていない』と述べる。作らない。\n"
        "- ボリンジャーバンドは終値が上限寄りか下限寄りか、モメンタムは勢いが強いか弱いかを、価格の丸暗記より先に噛み砕く。\n"
        "- facts.fill_timeframes は直近の模擬約定銘柄の1時間足・15分足です。直近の約定を1つずつ、"
        "どの時間足のどの位置（バンド位置・勢い）だったかを recent_fills と突き合わせて説明します。"
        "売買判断の根拠を捏造してはいけません。\n"
        "- 表示中のチャートは表示専用で、売買判断は保存済みの5分足と戦略パラメータで行っている点を必要なら一言添える。\n"
        "【話し方】\n"
        "- です・ます調の自然な話し言葉。結論を先に言い、その後に理由や数字を添える。\n"
        "- factsを順番に復唱するだけは禁止。数字同士を比較し、意味を説明する。\n"
        "- 金額・価格・指標などの数値は小数第2位までに丸めて言うこと（0.001のような小さい数量はそのまま）。factsの桁数をそのまま読み上げない。\n"
        "- 「見出しの段階なので」のような決まり文句を各項目で繰り返さないこと。\n"
        "- 軽いツッコミ、たとえ、意外性のある一言を適度に入れる。ただし事実を曲げるギャグは禁止。\n"
        "- 損失なら言い訳せず率直に言う。利益でも一時的な含み益だけで戦略成功と断定しない。\n"
        "- 同じ文型・同じオチを各段落で繰り返さない。箇条書き、見出し、マークダウンは禁止。\n"
        "【損益の扱い】\n"
        "- performance.cumulative_pnl_jpy がある場合、resultで累積損益（評価込み）を具体的に言う。\n"
        "- performance.today_realized_pnl_jpy は本日の確定損益として触れる。\n"
        "- performance.unrealized_pnl_jpy がある場合、含み損益も使う。complete=falseなら累積評価を断定しない。\n"
        "次の11キーを持つJSONオブジェクト1つを出力してください。\n"
        "- corner: 今日の相場の見取り図と、今日いちばん見るべき点を語るオープニング。\n"
        "- news: research.news_items から5〜6件を個別に、事実→含意→観測点の順で詳しく解説する。\n"
        "- chart: facts.timeframes の4つの時間足を1つずつ解説する。timeframesが空なら正直に言い、数値を作らない。\n"
        "- strategy: 現在の戦略パラメータの狙いを、損益・見送り傾向・ニュースから観測すべき点と結び付ける。\n"
        "- result: 累積損益・直近約定・保有を分析し、research.assetがあれば保有銘柄の面白い解説を自然に織り込む。\n"
        "- fills: 直近約定を1件ずつ、signalの観測値と閾値で『なぜ発注したか』を数値つきで説明する。\n"
        "- review: facts.round_trips の往復を1件ずつ、判断の良し悪しを根拠つきで評価し、次に活かす点を述べる。\n"
        "- improve: 取引結果・往復レビュー・ニュース分析を分離して評価し、次回改善で何を検証するかを述べる。\n"
        "- news_analysis: ニュースから得た考察だけを400〜1200文字で要約。事実と推測を区別する。\n"
        "- asset_spotlight: 選択保有銘柄の解説だけを300〜1200文字。research.assetが無ければ空文字。\n"
        "- improvement_hints: 改善価値がある時だけ最大4件の配列。各要素は kind,title,rationale,evidence,confidence。無ければ空配列。\n"
         f"corner/news/chart/strategy/result/fills/review/improveの各値は日本語で{MIN_SEGMENT_CHARS}〜{MAX_SEGMENT_CHARS}文字程度の本文にすること。"
         "JSONは1行で出力し、文字列値の中に改行や制御文字を入れないこと（整形のための改行も禁止）。"
         "JSON以外は出力しないこと。"
    )


def parse_script(text: str) -> dict:
    """Extract whichever narration segments the model returned.

    Partial output is intentionally accepted: a missing or empty segment is
    dropped here and filled from the deterministic fallback by
    :func:`generate_corner_script`, so one bad key cannot silence the whole
    AI narration. Only unparseable output or a response with no usable
    segment raises.
    """
    data = extract_json_object(text)
    if not isinstance(data, dict):
        raise CornerScriptError("台本のJSONオブジェクトを抽出できません")
    segments: dict[str, str] = {}
    for key in SEGMENT_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned = value.strip().replace("\n", " ")
        if len(cleaned) > MAX_SEGMENT_CHARS:
            cleaned = cleaned[:MAX_SEGMENT_CHARS]
        segments[key] = cleaned
    if not segments:
        raise CornerScriptError("台本に有効なセグメントがありません")
    return segments


def merge_script_segments(fallback: Mapping[str, object], parsed: Mapping[str, object]) -> dict:
    """Overlay parsed AI segments on the deterministic fallback.

    Every key in :data:`SEGMENT_KEYS` is guaranteed present, so a partial AI
    response still yields a complete, fact-grounded script.
    """
    merged = {key: str(fallback.get(key, "")) for key in SEGMENT_KEYS}
    for key in SEGMENT_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value
    return merged


def _policy_text(policy: Mapping[str, object]) -> str:
    if not policy:
        return (
            "戦略は既定のパラメータで動いており、値動きの勢いと平均回帰を組み合わせて"
            "売買の判断をします。"
        )
    return (
        f"戦略は直近{policy.get('momentum_lookback')}本の上昇が"
        f"{_fmt_num(policy.get('momentum_threshold_bps')) or policy.get('momentum_threshold_bps')}bpsを超えたら買い、"
        f"平均回帰は{policy.get('mean_reversion_lookback')}本でz値"
        f"{_fmt_num(policy.get('mean_reversion_z')) or policy.get('mean_reversion_z')}以下、1回の投入は資金の"
        f"{_fmt_num(policy.get('max_notional_fraction')) or policy.get('max_notional_fraction')}までです。"
    )


def _pnl_text(facts: Mapping[str, object]) -> str:
    perf = facts.get("performance") if isinstance(facts.get("performance"), Mapping) else {}
    parts: list[str] = []
    cumulative = perf.get("cumulative_pnl_jpy")
    today = perf.get("today_realized_pnl_jpy")
    unrealized = perf.get("unrealized_pnl_jpy")
    if cumulative is not None:
        parts.append(f"評価込みの累積損益は{_fmt_num(cumulative) or cumulative}円")
    else:
        parts.append("累積損益は一部の保有価格が不足していて評価待ち")
    if today is not None:
        parts.append(f"本日の確定損益は{_fmt_num(today) or today}円")
    if unrealized is not None:
        parts.append(f"現在の含み損益は{_fmt_num(unrealized) or unrealized}円")
    return "、".join(parts) + "です。"


def _fmt_pct(value) -> str:
    try:
        if value is None:
            return "算出待ち"
        return f"{float(value):+.2f}"
    except (TypeError, ValueError, OverflowError):
        return "算出待ち"


def _chart_text(facts: Mapping[str, object]) -> str:
    """Deterministic multi-timeframe walk from the public candlestick facts."""
    raw = facts.get("timeframes")
    timeframes = [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    if not timeframes:
        return (
            "時間足チャートの解説です。今回は公開ローソクの取得が間に合わず、"
            "日足・1時間足・15分足・1分足の数字をお伝えできません。データが取れ次第、次のコーナーで扱います。"
        )
    parts = ["時間足チャートの解説です。注目銘柄を4つの時間足で見ていきます。"]
    for view in timeframes[:4]:
        label = str(view.get("label") or view.get("timeframe") or "")
        trend = str(view.get("trend") or "不明")
        change = _fmt_pct(view.get("range_change_pct"))
        momentum = _fmt_pct(view.get("momentum_pct"))
        phrase = str(view.get("bb_phrase") or "バンド位置は算出待ち")
        bars = view.get("bars")
        parts.append(
            f"{label}は{bars}本で{trend}基調、表示区間の変化率は{change}パーセント、"
            f"直近の勢いは{momentum}パーセント、{phrase}です。"
        )
    raw_fills = facts.get("fill_timeframes")
    fills = [item for item in raw_fills if isinstance(item, Mapping)] if isinstance(raw_fills, list) else []
    if fills:
        first = fills[0]
        views = first.get("timeframes")
        view = views[0] if isinstance(views, list) and views and isinstance(views[0], Mapping) else None
        if view is not None:
            parts.append(
                f"なお直近約定の{first.get('symbol')}は、{view.get('label')}で{view.get('trend')}基調、"
                f"{view.get('bb_phrase')}です。発注の理由は約定の解説で扱います。"
            )
    parts.append("表示中のローソクは公開データの表示専用で、売買判断は保存済みの5分足と戦略パラメータで行っています。")
    return "".join(parts)


def _condition_text(signal: object) -> str:
    if not isinstance(signal, Mapping):
        return ""
    raw = signal.get("conditions")
    rows = raw if isinstance(raw, list) else []
    parts: list[str] = []
    for item in rows[:3]:
        if not isinstance(item, Mapping):
            continue
        feature = str(item.get("feature") or "")
        observed = str(item.get("observed") or "")
        threshold = str(item.get("threshold") or "")
        op = str(item.get("op") or "")
        unit = str(item.get("unit") or "")
        lookback = item.get("lookback")
        look = f"{lookback}本" if isinstance(lookback, int) else ""
        if feature and observed and threshold:
            parts.append(
                f"{feature}が{_fmt_num(observed) or observed}{unit}で閾値"
                f"{_fmt_num(threshold) or threshold}{unit}を{op}（{look}）"
            )
    return "、".join(parts)


def _news_text(facts: Mapping[str, object]) -> str:
    research = facts.get("research") if isinstance(facts.get("research"), Mapping) else {}
    raw = research.get("news_items") if isinstance(research.get("news_items"), list) else []
    news = [item for item in raw if isinstance(item, Mapping) and str(item.get("title") or "").strip()]
    if not news:
        return (
            "暗号資産ニュースのコーナーです。今回は見出しの取得が間に合わず、"
            "個別のニュース解説はお休みします。取得できた次のコーナーでまとめて扱います。"
        )
    shown = news[:6]
    parts = [f"暗号資産ニュースです。今回は{len(shown)}件を取り上げます。"]
    for item in shown:
        title = str(item.get("title") or "").strip()
        source = str(item.get("source") or "").strip()
        who = f"（{source}）" if source else ""
        parts.append(f"「{title}」{who}。")
    return "".join(parts)


def _fills_text(facts: Mapping[str, object]) -> str:
    raw = facts.get("recent_fills")
    fills = [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    if not fills:
        return "約定の解説です。直近は約定がなく、条件がそろうまで見送っています。無理に売買しないのも作戦のうちです。"
    parts = ["約定の解説です。直近の約定を1件ずつ、なぜ発注したのかまで掘り下げます。"]
    for item in fills[:3]:
        symbol = str(item.get("symbol") or "銘柄不明")
        side = "買い" if str(item.get("side")) == "buy" else "売り"
        reason = str(item.get("reason_code") or "")
        condition = _condition_text(item.get("signal"))
        if condition:
            parts.append(f"{symbol}の{side}は、{condition}を満たしたため発注しています。")
        else:
            parts.append(
                f"{symbol}の{side}約定は記録がありますが、発注根拠の詳細が残っていないため、"
                "価格と理由コードだけをお伝えします。"
            )
    parts.append("表示中のチャートは参考で、実際の判断は保存済みの5分足と戦略パラメータで行っています。")
    return "".join(parts)


def _review_text(facts: Mapping[str, object]) -> str:
    raw = facts.get("round_trips")
    trips = [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    if not trips:
        return (
            "往復の振り返りです。買ってから売って損益が確定した取引はまだありません。"
            "保有中の分は決済した時点で、判断の良し悪しをここで検証します。"
        )
    parts = ["往復の振り返りです。買ってから売って損益が確定した取引を1件ずつ、判断が正しかったのか見ます。"]
    for item in trips[:3]:
        symbol = str(item.get("symbol") or "銘柄不明")
        entry_reason = str(item.get("entry_reason") or "不明")
        exit_reason = str(item.get("exit_reason") or "不明")
        entry = _condition_text(item.get("entry_signal"))
        realized = item.get("realized_jpy")
        hold = item.get("hold_sec")
        hold_min = f"{float(hold) / 60:.0f}分" if isinstance(hold, (int, float)) else "保有時間不明"
        try:
            pnl = float(realized)
        except (TypeError, ValueError):
            pnl = None
        realized_text = _fmt_num(realized) if pnl is not None else None
        if realized_text is None:
            realized_text = str(realized)
        verdict = "利益" if pnl is not None and pnl > 0 else "損失"
        entry_text = f"（{entry}）" if entry else ""
        parts.append(
            f"{symbol}は{entry_reason}{entry_text}で入り、{exit_reason}で出口、{hold_min}保有で"
            f"{realized_text}円の{verdict}です。根拠どおりの結果だったか、次に同じ形が来たらどうするかを"
            "ここで切り分け、改善側の検証条件に渡します。"
        )
    return "".join(parts)


def render_fallback(facts: Mapping[str, object]) -> dict:
    """Deterministic narration from the same trading/research facts."""
    positions = facts.get("open_positions_top") or []
    fills = facts.get("recent_fills") or []
    skipped = facts.get("skipped_decisions") or []
    focus = facts.get("focus") if isinstance(facts.get("focus"), Mapping) else {}
    improvement = facts.get("improvement") if isinstance(facts.get("improvement"), Mapping) else {}
    research = facts.get("research") if isinstance(facts.get("research"), Mapping) else {}
    news = research.get("news_items") if isinstance(research.get("news_items"), list) else []
    asset = research.get("asset") if isinstance(research.get("asset"), Mapping) else {}

    corner = (
        "PAPER・暗号資産の模擬売買コーナーです。今日は単に何を買ったかだけでなく、"
        "なぜその注文を出したのか、その判断が正しかったのかまで、時間をかけて見ていきます。"
        "ニュース、時間足チャート、約定の根拠、往復の振り返り、改善の順でお送りします。"
    )

    result = (
        f"まず成績です。{_pnl_text(facts)}模擬資金は{_fmt_num(facts.get('capital_jpy')) or facts.get('capital_jpy')}円、"
        f"投入は{_fmt_num(facts.get('deployed_jpy')) or facts.get('deployed_jpy')}円、保有は{facts.get('position_count', len(positions))}銘柄です。"
    )
    if fills:
        first = fills[0]
        amount_text = _fmt_num(first.get("amount")) or first.get("amount")
        price_text = _fmt_num(first.get("price")) or first.get("price")
        result += (
            f"直近の約定は{first.get('symbol')}の{first.get('side')}、数量{amount_text}、"
            f"価格{price_text}でした。"
        )
        if first.get("side") == "sell" and first.get("realized_pnl_jpy") is not None:
            pnl_text = _fmt_num(first.get("realized_pnl_jpy")) or first.get("realized_pnl_jpy")
            result += f"損益は{pnl_text}円です。"
    else:
        result += "直近の約定はなく、BOTは様子見を選んでいます。"
    if skipped:
        examples = [
            f"{item.get('symbol')}の{item.get('side_label')}は{item.get('reason')}"
            for item in skipped[:3] if isinstance(item, Mapping)
        ]
        if examples:
            result += "見送りでは、" + "、".join(examples) + "でした。"
    if focus.get("symbol"):
        result += (
            f"注目している{focus.get('symbol')}は直近{focus.get('bars')}本で"
            f"{focus.get('change_pct')}パーセント動いています。"
        )
    if asset.get("symbol"):
        result += (
            f"保有中の{asset.get('symbol')}については、今回は{asset.get('angle_label')}という切り口で調べています。"
            "AI分析が使えない場合でも、実際に保有している銘柄だけを対象にしています。"
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
    improve += "ニュースは仮説の材料にとどめ、取引結果で裏付けが取れない案は採用しません。"

    return {
        "corner": corner,
        "news": _news_text(facts),
        "chart": _chart_text(facts),
        "strategy": _policy_text(dict(facts.get("policy") or {}))
        + " 設定値そのものより、その結果として損益と見送りがどう動いたかを見るのが今回のポイントです。",
        "result": result,
        "fills": _fills_text(facts),
        "review": _review_text(facts),
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
    timeframe_facts: Mapping[str, object] | None = None,
) -> dict:
    """Return the narration segments; optional public data never fails the caller."""
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    cleaned_agents = (agents or "").strip()
    real_ai = (
        not dry_run
        and bool(cleaned_agents)
        and g is not None
        and os.environ.get("DOCICH_ALLOW_REAL_AI") == "1"
    )

    research_context: dict = {}
    if real_ai:
        try:
            research_context = prepare_research_context(target, now=moment)
        except Exception:
            # Network/public-research failures are commentary degradation only.
            research_context = {}

    # Multi-timeframe public candles (Issue #348). Fetched once per corner for
    # both the AI and the deterministic fallback; a failure is commentary
    # degradation only. Gated on the real-AI contract so tests and offline
    # calls never touch the network, and tests can inject a fixed block.
    tf_context: Mapping[str, object] = timeframe_facts if isinstance(timeframe_facts, Mapping) else {}
    if not tf_context and real_ai:
        try:
            from .timeframe_chart import build_narration_facts

            tf_context = build_narration_facts(target, now=moment)
        except Exception:
            tf_context = {}

    try:
        effective = policy if policy is not None else load_strategy_policy(target)
        facts = build_facts(target, now=moment, policy=effective, timeframes=tf_context)
        if research_context:
            facts["research"] = research_context
    except Exception as exc:
        return {
            "source": "fallback",
            "reason": _safe_reason(exc),
            "segments": render_fallback({}),
        }
    fallback = render_fallback(facts)

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
        model_data = extract_json_object(raw)
        parsed = parse_script(raw)
    except CornerScriptError as exc:
        # Record which half failed (type only, never model output): no JSON
        # object at all vs JSON without usable narration segments.
        message = str(exc)
        kind = "no-json-object" if "抽出" in message else "no-usable-segments"
        return {"source": "fallback", "reason": f"CornerScriptError:{kind}", "segments": fallback}
    except Exception as exc:
        return {"source": "fallback", "reason": _safe_reason(exc), "segments": fallback}

    # A partial AI response keeps its good segments; only the missing ones use
    # the deterministic fallback text. `source` records whether that happened.
    missing = [key for key in SEGMENT_KEYS if key not in parsed]
    segments = merge_script_segments(fallback, parsed)
    research_status = None
    if research_context and isinstance(model_data, Mapping):
        try:
            finalized = finalize_research_result(target, research_context, model_data, now=moment)
            research_status = finalized.get("status")
        except Exception:
            research_status = "finalize-failed"
    return {
        "source": "ai" if not missing else "ai-partial",
        "reason": None,
        "segments": segments,
        "fallback_segments": missing,
        "research_status": research_status,
    }
