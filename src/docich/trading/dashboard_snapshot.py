"""Read-only JSON snapshot for the PAPER HTML/canvas dashboard (Issue #198).

The snapshot is the only data the browser page reads. It is allowlisted:
portfolio totals, open positions, the focus pair's stored closes, recent
fills, decision/skip reason codes, freshness counts and staleness. It never
contains credentials, raw API responses, arbitrary file contents or prompts,
and it never makes a trading decision.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from .dashboard import (
    HEADER_TITLE,
    _fills,
    _focus_symbol,
    _fresh_count,
    _positions,
    _reason_ja,
    load_snapshot,
)

DISCLAIMER = "PAPER / 模擬取引（bitbank公開データ・実取引なし）"
MAX_POSITIONS = 8
MAX_FILLS = 5


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _reason_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(code) for code in value[:limit]]


def build_dashboard_snapshot(trading_dir: Path, *, now: float | None = None) -> dict[str, object]:
    """Build the read-only dashboard snapshot. Never raises on bad input."""
    moment = time.time() if now is None else float(now)
    snapshot, closes = load_snapshot(Path(trading_dir))

    positions = [
        {"symbol": symbol, "amount": amount}
        for symbol, amount in _positions(snapshot)[:MAX_POSITIONS]
    ]
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
    skipped = _reason_list(snapshot.get("skipped_reason_codes"), 6)

    fills: list[dict[str, object]] = []
    for fill in _fills(snapshot)[:MAX_FILLS]:
        fills.append(
            {
                "symbol": str(fill.get("symbol", "")),
                "side": str(fill.get("side", "")),
                "amount": str(fill.get("amount", "")),
                "price": str(fill.get("price", "")),
                "quote": str(fill.get("quote", "")),
                "filled_at": _finite(fill.get("filled_at")),
                "reason_code": str(fill.get("reason_code", "")),
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
            "positions": positions,
            "fresh_markets": fresh,
            "total_markets": total,
        },
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
            "skipped": [{"code": code, "label": _reason_ja(code)} for code in skipped],
        },
        "fills": fills,
        "disclaimer": DISCLAIMER,
    }
