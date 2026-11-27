"""Bounded LIVE (real-money) and free-strategy facts for the crypto corner narration.

Owner decision 2026-11-27: the PAPER corner became the 暗号通貨 corner.  It announces the
PAPER results (including the free strategies) and, separately, the LIVE results in JPY.

``live_facts`` opens the LIVE ledger read-only (``LiveLedger.open_readonly``) through the
same allow-listed overview the WebUI uses; it never writes, never imports an exchange and
never raises.  Only aggregate figures leave this module: no order/fill/intent/position
ids, no balances.  ``free_strategy_facts`` reads the free-strategy lab summary and the
promotion report.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

JST = dt.timezone(dt.timedelta(hours=9), "JST")
MAX_SYMBOLS = 5
MAX_RECENT = 5


def _dec(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _yen(value: object) -> str | None:
    number = _dec(value)
    return None if number is None else format(number.quantize(Decimal("1")), "f")


def _day_start(now: float) -> float:
    local = dt.datetime.fromtimestamp(float(now), tz=dt.timezone.utc).astimezone(JST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def live_facts(state_dir, *, now: float) -> dict:
    """``{"available": False}`` when there is no readable LIVE ledger."""
    try:
        from .live.ledger import LiveLedger
        from .live.overview import live_overview
        from .live.paths import live_layout
        from .live import promotion

        layout = live_layout(state_dir)
        if not layout.ledger_path.is_file():
            return {"available": False, "reason": "no_ledger"}
        ledger = LiveLedger.open_readonly(layout)
        free_live = None
        try:
            view = live_overview(ledger, now=now)
            digest = ledger.active_policy_set_digest()
            if digest is not None:
                active = ledger.load_policy_set(digest)
                if active.free is not None:
                    free_live = {"symbols": list(active.free.symbols)[:MAX_SYMBOLS]}
        finally:
            ledger.close()
        if not view.get("ledger_readable"):
            return {"available": False, "reason": "ledger_unreadable"}
        trade = view.get("trade_pnl") or {}
        rows = [row for row in trade.get("rows") or [] if isinstance(row, Mapping)]
        start = _day_start(now)
        today_rows = [row for row in rows if float(row.get("at") or 0) >= start]
        today = sum((_dec(row.get("pnl")) or Decimal(0) for row in today_rows
                     if row.get("status") == "accounted"), Decimal(0))
        by_symbol = sorted(((symbol, _dec(value)) for symbol, value in
                            (trade.get("by_symbol") or {}).items() if _dec(value) is not None),
                           key=lambda item: -abs(item[1]))
        positions = [item for item in view.get("positions") or [] if isinstance(item, Mapping)]
        recent = [item for item in view.get("recent_fills") or [] if isinstance(item, Mapping)]
        pnl = view.get("pnl") or {}
        status = promotion.read_status(state_dir) or {}
        last_promotion = None
        if status.get("applied"):
            last_promotion = {"judged_at": status.get("judged_at"),
                              "symbols": [str(c.get("symbol")) for c in status.get("changes") or []
                                          if isinstance(c, Mapping)][:MAX_SYMBOLS],
                              "experiments": [str(c.get("experiment_id")) for c in status.get("changes") or []
                                              if isinstance(c, Mapping)][:MAX_SYMBOLS]}
        return {
            "available": True,
            "mode_label": "LIVE（本番の資金・実際の約定）",
            "realized_pnl_jpy": _yen(trade.get("total")),
            "realized_complete": bool(trade.get("complete")),
            "today_realized_pnl_jpy": _yen(today),
            # Only the newest page of sell orders is read; a full page today means more may exist.
            "today_complete": len(today_rows) < len(rows) or len(rows) < int(trade.get("page_size") or 20),
            "sell_orders": int(trade.get("total_rows") or 0),
            "round_trips": int(pnl.get("round_trips") or 0),
            "wins": int(pnl.get("wins") or 0),
            "by_symbol_top": [{"symbol": symbol, "realized_pnl_jpy": _yen(value)}
                              for symbol, value in by_symbol[:MAX_SYMBOLS]],
            "open_position_count": len(positions),
            "open_cost_jpy": _yen(sum((_dec(item.get("cost")) or Decimal(0) for item in positions), Decimal(0))),
            "open_symbols": sorted({str(item.get("symbol")) for item in positions})[:MAX_SYMBOLS],
            "recent_fills": [{"symbol": str(item.get("symbol")), "side": str(item.get("side")),
                              "notional_jpy": _yen(item.get("notional")), "at": item.get("at")}
                             for item in recent[:MAX_RECENT]],
            "policy_symbol_count": len((view.get("policy") or {}).get("symbols") or []),
            "safety_trips": list((view.get("safety") or {}).get("trips") or [])[:3],
            "last_promotion": last_promotion,
            # A promoted free (AI-written) strategy trading LIVE by following its PAPER mirror.
            "free_strategy": free_live,
            "since": pnl.get("since"),
        }
    except Exception:
        return {"available": False, "reason": "live_facts_failed"}


def free_strategy_facts(trading_dir, *, now: float) -> dict:
    """The free-strategy PAPER lab: candidates and the promotion judgement."""
    try:
        from .free_strategy.evaluation import public_summary
        from .free_strategy.improve import read_report

        summary = public_summary(Path(trading_dir) / "free-strategies" / "lab.sqlite3", now=now)
        experiments = [{"name": str(item.get("name")), "family": str(item.get("family")),
                        "phase": str(item.get("phase")), "net_pnl_jpy": _yen(item.get("net_pnl_jpy")),
                        "stale": bool(item.get("stale"))}
                       for item in summary.get("experiments") or [] if isinstance(item, Mapping)][:4]
        report = read_report(trading_dir) or {}
        candidate = report.get("candidate") if isinstance(report.get("candidate"), Mapping) else None
        return {
            "available": bool(experiments),
            "mode_label": "PAPER（仮想資金）の自由戦略",
            "experiments": experiments,
            "promotion_candidate": None if candidate is None else {
                "e_value": candidate.get("e_value"), "blocks": candidate.get("blocks")},
        }
    except Exception:
        return {"available": False}


__all__ = ["live_facts", "free_strategy_facts"]
