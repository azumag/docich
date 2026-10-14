from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision  # noqa: E402
from docich.trading.notifications import _render_event_with_local_facts  # noqa: E402
from docich.trading.paper import PaperBroker  # noqa: E402
from docich.trading.performance import (  # noqa: E402
    build_theoretical_benchmark,
    build_performance,
    build_round_trips,
    realized_pnl_for_fill,
)

D = Decimal
JST = dt.timezone(dt.timedelta(hours=9))


def _decision(oid: str, side: str, amount: str, price: str) -> AllocationDecision:
    qty = D(amount)
    px = D(price)
    notional = qty * px
    return AllocationDecision(
        opportunity_id=oid,
        strategy_id="test",
        symbol="BTC/JPY",
        side=side,
        quote="JPY",
        amount=qty,
        price=px,
        quote_notional=notional,
        reference_notional=notional,
        reason_code="take_profit" if side == "sell" else "momentum_breakout",
    )


def _zero_cost_broker(ledger: PaperLedger) -> PaperBroker:
    """These tests isolate accounting math from the production execution-cost model."""
    return PaperBroker(ledger, taker_fee_rate="0", slippage_bps="0")


def _write_history_cache(path: Path, *, now: float, symbol: str = "BTC/JPY") -> None:
    day_start = dt.datetime.fromtimestamp(now, tz=JST).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    history = []
    for index in range(int((now - day_start) // 300)):
        close = {0: "100", 1: "90", 2: "120"}.get(index, "110")
        history.append({"timestamp": day_start + index * 300, "close": close})
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "symbols": {
                symbol: {
                    "fetched_at": now,
                    "data_as_of": now - 300,
                    "timeframe_s": 300,
                    "last_close": "110",
                    "closes": ["100", "90", "120", "110"],
                    "history": history,
                }
            },
        }),
        encoding="utf-8",
    )


def test_theoretical_benchmark_compares_actual_with_best_single_round_trip(tmp_path):
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp()
    cache = tmp_path / "market_cache.json"
    _write_history_cache(cache, now=now)

    benchmark = build_theoretical_benchmark(
        cache,
        capital_reference="1000",
        actual_today_realized="10",
        now=now,
        taker_fee_rate="0",
        slippage_bps="0",
    )

    assert benchmark["status"] == "ready"
    assert benchmark["basis"] == "5m_close"
    assert benchmark["scope"] == "best_single_symbol_round_trip"
    assert benchmark["best_symbol"] == "BTC/JPY"
    assert benchmark["theoretical_pnl_jpy"] == str(D("1000") * (D("120") / D("90") - D("1")))
    assert benchmark["actual_today_realized_pnl_jpy"] == "10"
    assert benchmark["gap_jpy"] == str(D(benchmark["theoretical_pnl_jpy"]) - D("10"))
    assert D(benchmark["capture_rate_pct"]) == D("10") / D(benchmark["theoretical_pnl_jpy"]) * D("100")
    assert benchmark["coverage_complete_to_now"] is True


def test_theoretical_benchmark_fails_closed_without_intraday_history(tmp_path):
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp()
    cache = tmp_path / "market_cache.json"
    cache.write_text(
        json.dumps({"schema_version": 1, "symbols": {
            "BTC/JPY": {"fetched_at": now, "timeframe_s": 300, "closes": ["100"]}
        }}),
        encoding="utf-8",
    )

    benchmark = build_theoretical_benchmark(
        cache, capital_reference="1000", actual_today_realized="0", now=now
    )

    assert benchmark["status"] == "unavailable"
    assert benchmark["theoretical_pnl_jpy"] is None
    assert benchmark["reason"] == "intraday_history_unavailable"


def test_theoretical_benchmark_marks_late_started_history_as_partial(tmp_path):
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp()
    cache = tmp_path / "market_cache.json"
    cache.write_text(
        json.dumps({"schema_version": 1, "symbols": {
            "BTC/JPY": {
                "fetched_at": now,
                "timeframe_s": 300,
                "history": [
                    {"timestamp": now - 600, "close": "100"},
                    {"timestamp": now - 300, "close": "120"},
                ],
            }
        }}),
        encoding="utf-8",
    )

    benchmark = build_theoretical_benchmark(
        cache, capital_reference="1000", actual_today_realized="5", now=now,
        taker_fee_rate="0", slippage_bps="0",
    )

    assert benchmark["status"] == "partial"
    assert benchmark["coverage_complete_to_now"] is False
    assert D(benchmark["theoretical_pnl_jpy"]) == D("200")


def test_theoretical_benchmark_marks_internal_history_gap_as_partial(tmp_path):
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp()
    day_start = dt.datetime(2026, 9, 11, tzinfo=JST).timestamp()
    cache = tmp_path / "market_cache.json"
    cache.write_text(
        json.dumps({"schema_version": 1, "symbols": {
            "BTC/JPY": {
                "fetched_at": now,
                "timeframe_s": 300,
                "history": [
                    {"timestamp": day_start, "close": "100"},
                    {"timestamp": day_start + 300, "close": "90"},
                    {"timestamp": day_start + 600, "close": "120"},
                    {"timestamp": now - 300, "close": "110"},
                ],
            }
        }}),
        encoding="utf-8",
    )

    benchmark = build_theoretical_benchmark(
        cache, capital_reference="1000", actual_today_realized="5", now=now,
        taker_fee_rate="0", slippage_bps="0",
    )

    assert benchmark["status"] == "partial"
    assert benchmark["coverage_complete_to_now"] is False
    assert benchmark["reason"] == "partial_intraday_coverage"


def test_performance_combines_realized_unrealized_and_today(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp()
    yesterday = dt.datetime(2026, 9, 10, 18, 0, tzinfo=JST).timestamp()
    broker.fill(_decision("buy", "buy", "2", "100"), timestamp=yesterday)
    sold = broker.fill(_decision("sell", "sell", "1", "120"), timestamp=now - 60)
    ledger.close()

    perf = build_performance(
        db,
        capital_reference="1000",
        positions={"BTC/JPY": "1"},
        prices={"BTC/JPY": "110"},
        now=now,
    )
    assert perf["realized_total_jpy"] == "20"
    assert perf["today_realized_pnl_jpy"] == "20"
    assert perf["unrealized_pnl_jpy"] == "10"
    assert perf["cumulative_pnl_jpy"] == "30"
    assert perf["equity_jpy"] == "1030"
    assert perf["complete"] is True
    assert realized_pnl_for_fill(db, sold.fill_id) == D("20")


def test_performance_fails_closed_when_open_position_lacks_price(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    _zero_cost_broker(ledger).fill(_decision("buy", "buy", "1", "100"), timestamp=1.0)
    ledger.close()

    perf = build_performance(
        db,
        capital_reference="1000",
        positions={"BTC/JPY": "1"},
        prices={},
        now=dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp(),
    )
    assert perf["complete"] is False
    assert perf["cumulative_pnl_jpy"] is None
    assert perf["equity_jpy"] is None
    assert perf["today_realized_pnl_jpy"] == "0"


def test_sell_notification_reads_realized_pnl_from_paper_ledger(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("buy", "buy", "2", "100"), timestamp=1000.0)
    sold = broker.fill(_decision("sell", "sell", "1", "120"), timestamp=1100.0)
    ledger.close()

    event = {
        "schema_version": 1,
        "event_id": f"fill:{sold.fill_id}",
        "event_type": "paper_fill",
        "occurred_at": 1100.0,
        "symbol": "BTC/JPY",
        "strategy_id": "exit-v1",
        "side": "sell",
        "amount": "1",
        "price": "120",
        "reference_notional": "120",
        "reason_code": "take_profit",
    }
    rendered = _render_event_with_local_facts(
        event, mode="compact", status=None, display_at=1100.0, trading_dir=tmp_path
    )
    assert "実現損益 +20円" in rendered.overlay_event["body"]
    assert rendered.speech_text == (
        "利益を確保する狙いでポジションを軽くする利確です。売った後にさらに伸びても、取り逃しより、"
        "決めたルールを守れたかを重視します。利確条件を検出：BTC/JPYを売り。損益はプラス20円です。"
    )


def test_round_trips_pair_buys_sells_and_keep_entry_context(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    entry_context = {
        "kind": "builtin_entry",
        "conditions": [
            {"feature": "return_bps", "observed": "320", "threshold": "150",
             "op": ">=", "lookback": 6}
        ],
    }
    exit_context = {
        "kind": "builtin_exit",
        "conditions": [
            {"feature": "pnl_bps", "observed": "200", "threshold": "100", "op": ">="}
        ],
    }
    broker.fill(
        _decision("rt-buy", "buy", "2", "100"), timestamp=1_000.0, signal_context=entry_context
    )
    broker.fill(
        _decision("rt-sell", "sell", "2", "120"), timestamp=1_600.0, signal_context=exit_context
    )
    ledger.close()

    trips = build_round_trips(db)
    assert len(trips) == 1
    trip = trips[0]
    assert trip["symbol"] == "BTC/JPY"
    assert trip["realized_jpy"] == "40"
    assert trip["entry_reason"] == "momentum_breakout"
    assert trip["exit_reason"] == "take_profit"
    assert trip["entry_signal"]["conditions"][0]["feature"] == "return_bps"
    assert trip["exit_signal"]["conditions"][0]["feature"] == "pnl_bps"
    assert float(trip["hold_sec"]) == 600.0


def test_ledger_migrates_old_schema_and_persists_signal_context(tmp_path):
    import sqlite3

    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE paper_fills (
             fill_id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL UNIQUE,
             strategy_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
             quote TEXT NOT NULL, amount TEXT NOT NULL, price TEXT NOT NULL,
             quote_notional TEXT NOT NULL, reference_notional TEXT NOT NULL,
             reason_code TEXT NOT NULL, filled_at REAL NOT NULL)"""
    )
    conn.commit()
    conn.close()

    ledger = PaperLedger(db)  # must add the signal_context column idempotently
    broker = _zero_cost_broker(ledger)
    fill = broker.fill(
        _decision("mig-1", "buy", "1", "100"),
        timestamp=1_000.0,
        signal_context={"kind": "builtin_entry"},
    )
    assert fill.signal_context == {"kind": "builtin_entry"}
    ledger.close()

    reopened = PaperLedger(db)
    stored = reopened.get_fill_for_opportunity("mig-1")
    assert stored is not None and stored.signal_context == {"kind": "builtin_entry"}
    reopened.close()


def test_round_trips_ignore_unmatched_sells(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("orphan-sell", "sell", "1", "100"), timestamp=1_000.0)
    ledger.close()
    assert build_round_trips(db) == []


def test_recent_orders_returns_allowlisted_rows(tmp_path):
    from docich.trading.performance import recent_orders

    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("ord-1", "buy", "1", "100"), timestamp=1_000.0)
    ledger.close()
    orders = recent_orders(db)
    assert len(orders) == 1
    assert orders[0]["symbol"] == "BTC/JPY"
    assert orders[0]["reason_code"] == "momentum_breakout"
