"""Read-only JSON snapshot for the PAPER HTML/canvas dashboard (Issue #198).

The snapshot is the only data the browser page reads. It is allowlisted:
portfolio totals, open positions, P/L summaries, the focus pair's stored
closes, recent fills, decision/skip context, freshness counts and staleness.
It never contains credentials, raw API responses, arbitrary file contents or
prompts, and it never makes a trading decision.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Mapping

from .dashboard import HEADER_TITLE, _focus_symbol, _fresh_count, _positions, _reason_ja, load_snapshot
from .performance import build_performance, realized_pnl_for_fill

DISCLAIMER = "PAPER / 模擬取引（bitbank公開データ・実取引なし）"
MAX_POSITIONS = 8
MAX_FILLS = 5
MAX_SKIPS = 8


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _reason_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(code) for code in value[:limit]]


def _side_ja(value: object) -> str:
    return {"buy": "買い", "sell": "売り"}.get(str(value).lower(), "取引")


def _skip_details(snapshot: Mapping[str, object]) -> list[dict[str, str]]:
    raw = snapshot.get("skipped_decisions")
    result: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw[:MAX_SKIPS]:
            if not isinstance(item, Mapping):
                continue
            code = str(item.get("reason_code") or "")
            symbol = str(item.get("symbol") or "")
            if not code:
                continue
            result.append(
                {
                    "symbol": symbol,
                    "side": str(item.get("side") or "unknown"),
                    "side_label": _side_ja(item.get("side")),
                    "code": code,
                    "label": _reason_ja(code),
                }
            )
    if result:
        return result
    return [
        {"symbol": "", "side": "unknown", "side_label": "取引", "code": code, "label": _reason_ja(code)}
        for code in _reason_list(snapshot.get("skipped_reason_codes"), MAX_SKIPS)
    ]


def build_dashboard_snapshot(trading_dir: Path, *, now: float | None = None) -> dict[str, object]:
    """Build the read-only dashboard snapshot. Never raises on bad input."""
    moment = time.time() if now is None else float(now)
    target = Path(trading_dir)
    snapshot, closes = load_snapshot(target)

    raw_positions = _positions(snapshot)
    prices = {
        str(symbol): values[-1]
        for symbol, values in closes.items()
        if isinstance(values, list) and values
    }
    performance = build_performance(
        target / "paper.sqlite3",
        capital_reference=snapshot.get("capital_reference", "0"),
        positions={symbol: amount for symbol, amount in raw_positions},
        prices=prices,
        now=moment,
    )
    valued_positions = performance.get("positions")
    positions = list(valued_positions)[:MAX_POSITIONS] if isinstance(valued_positions, list) else []
    fresh, total = _fresh_count(snapshot)

    focus = _focus_symbol(snapshot, closes)
    series: list[float] = []
    if focus:
        series = [
            float(value)
            for value in (closes.get(focus) or [])[-24:]
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]

    summary = snapshot.get("signal_summary")
    summary = summary if isinstance(summary, dict) else {}
    try:
        candidates = int(summary.get("candidate_count", 0) or 0)
    except (TypeError, ValueError):
        candidates = 0
    reasons = _reason_list(summary.get("candidate_reason_codes"), 4)
    skipped = _skip_details(snapshot)

    fills: list[dict[str, object]] = []
    raw_fills = snapshot.get("recent_fills")
    if not isinstance(raw_fills, list):
        raw_fills = []
    for fill in raw_fills[:MAX_FILLS]:
        if not isinstance(fill, Mapping):
            continue
        fill_id = str(fill.get("fill_id", ""))
        realized = realized_pnl_for_fill(target / "paper.sqlite3", fill_id) if fill_id else None
        fills.append(
            {
                "fill_id": fill_id,
                "symbol": str(fill.get("symbol", "")),
                "side": str(fill.get("side", "")),
                "amount": str(fill.get("amount", "")),
                "price": str(fill.get("price", "")),
                "quote": str(fill.get("quote", "")),
                "filled_at": _finite(fill.get("filled_at")),
                "reason_code": str(fill.get("reason_code", "")),
                "realized_pnl_jpy": None if realized is None else str(realized),
            }
        )

    data_as_of = _finite(snapshot.get("snapshot_generated_at"))
    return {
        "schema_version": 1,
        "generated_at": moment,
        "header": {
            "title": HEADER_TITLE,
            "worker_state": str(snapshot.get("worker_state", "unknown")),
            "snapshot_seq": snapshot.get("snapshot_seq"),
            "data_as_of": data_as_of,
            "data_age_sec": None if data_as_of is None else max(0, int(moment - data_as_of)),
        },
        "portfolio": {
            "capital_jpy": str(snapshot.get("capital_reference", "?")),
            "deployed_jpy": str(snapshot.get("deployed_reference", "?")),
            "position_count": len(raw_positions),
            "displayed_position_count": len(positions),
            "positions": positions,
            "fresh_markets": fresh,
            "total_markets": total,
        },
        "performance": performance,
        "chart": {
            "symbol": focus,
            "closes": series,
            "count": len(series),
            "first": series[0] if series else None,
            "last": series[-1] if series else None,
            "reason_codes": [{"code": code, "label": _reason_ja(code)} for code in reasons],
        },
        "decision": {
            "candidate_count": candidates,
            "reasons": [{"code": code, "label": _reason_ja(code)} for code in reasons],
            "skipped": skipped,
        },
        "fills": fills,
        "disclaimer": DISCLAIMER,
    }
