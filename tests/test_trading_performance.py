from __future__ import annotations

import datetime as dt
import json
import pytest
import sys
from decimal import Decimal
import sqlite3
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.arbitrage import TopOfBook  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision  # noqa: E402
from docich.trading.notifications import _render_event_with_local_facts, _safe_status  # noqa: E402
from docich.trading.paper import PaperBroker  # noqa: E402
from docich.trading.status import performance_summary_payload  # noqa: E402
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


class _ExactPriceBroker(PaperBroker):
    """Seed accounting fixtures at the decision's own price (zero cost model)."""

    def fill(self, decision, *, timestamp, signal_context=None, market=None, book=None):
        if book is None:
            book = TopOfBook(
                decision.symbol, decision.price, decision.price, float(timestamp),
                bid_amount=decision.amount, ask_amount=decision.amount,
            )
        return super().fill(
            decision, timestamp=timestamp, signal_context=signal_context,
            market=market, book=book,
        )


def _zero_cost_broker(ledger: PaperLedger) -> PaperBroker:
    """These tests isolate accounting math from the production execution-cost model."""
    return _ExactPriceBroker(ledger, taker_fee_rate="0", slippage_bps="0")


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
    assert benchmark["actual_interval_realized_pnl_jpy"] == "10"
    assert benchmark["actual_today_realized_pnl_jpy"] == "10"  # deprecated alias
    assert benchmark["gap_jpy"] == str(D(benchmark["theoretical_pnl_jpy"]) - D("10"))
    assert D(benchmark["capture_rate_pct"]) == D("10") / D(benchmark["theoretical_pnl_jpy"]) * D("100")
    assert benchmark["coverage_complete_to_now"] is True


def test_theoretical_benchmark_preserves_missing_history_and_capital_reasons(tmp_path):
    now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=JST).timestamp()
    missing = build_theoretical_benchmark(
        None, capital_reference="1000", actual_today_realized="0", now=now
    )
    assert missing["reason"] == "history_not_configured"

    cache = tmp_path / "market_cache.json"
    _write_history_cache(cache, now=now)
    no_capital = build_theoretical_benchmark(
        cache, capital_reference=None, actual_today_realized="0", now=now
    )
    assert no_capital["reason"] == "capital_unavailable"


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
    assert perf["interval_realized_pnl_jpy"] == "20"
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
    assert "今回 +20円" in rendered.overlay_event["title"]
    assert "今回" not in rendered.overlay_event["body"]
    assert rendered.speech_text == (
        "利益を確保する狙いでポジションを軽くする利確です。売った後にさらに伸びても、取り逃しより、"
        "決めたルールを守れたかを重視します。利確条件を検出：BTC/JPYを売り。損益はプラス20円です。現在の含み損益と評価込み累計損益は評価待ちです。"
    )



def test_notification_status_uses_only_fresh_paper_performance(tmp_path):
    g = SimpleNamespace(trading=SimpleNamespace(interval_s=60))
    path = tmp_path / "status.json"
    status = {
        "mode": "paper", "schema_version": 1,
        "performance_summary": {
            "as_of": 1999.0, "complete": True,
            "unrealized_pnl_jpy": "25", "cumulative_pnl_jpy": "30",
        },
    }
    path.write_text(json.dumps(status), encoding="utf-8")
    fresh = _safe_status(g, tmp_path, now=2000.0)
    assert fresh["performance_summary"]["cumulative_pnl_jpy"] == "30"
    assert "performance_summary" not in _safe_status(g, tmp_path, now=2300.0)
    del status["performance_summary"]["as_of"]
    path.write_text(json.dumps(status), encoding="utf-8")
    assert "performance_summary" not in _safe_status(g, tmp_path, now=2000.0)


def test_notification_does_not_report_pre_fill_performance_as_current(tmp_path):
    event = {
        "schema_version": 1,
        "event_id": "fill:missing",
        "event_type": "paper_fill",
        "occurred_at": 1100.0,
        "symbol": "BTC/JPY",
        "side": "buy",
        "strategy_id": "test",
        "reason_code": "momentum_breakout",
    }
    status = {"mode": "paper", "performance_summary": {
        "as_of": 1050.0, "complete": True,
        "unrealized_pnl_jpy": "50", "cumulative_pnl_jpy": "50",
    }}
    rendered = _render_event_with_local_facts(
        event, mode="compact", status=status, display_at=1100.0, trading_dir=tmp_path,
    )
    assert "含み 評価待ち" in rendered.overlay_event["title"]
    assert "累計 評価待ち" in rendered.overlay_event["title"]
    assert "評価待ち" not in rendered.overlay_event["body"]


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


def test_round_trips_reject_oversell_fragment_and_reopen_fresh_lot(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("oversell-rt-buy", "buy", "1", "100"), timestamp=1_000.0)
    broker.fill(_decision("oversell-rt-sell", "sell", "2", "120"), timestamp=1_100.0)
    ledger.close()

    # The matched portion of an oversell must not be presented as a valid trip.
    assert build_round_trips(db) == []

    # As in realized_pnl_for_fill(), a later buy starts a clean inventory lot.
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("fresh-rt-buy", "buy", "1", "110"), timestamp=1_200.0)
    broker.fill(_decision("fresh-rt-sell", "sell", "1", "130"), timestamp=1_300.0)
    ledger.close()

    trips = build_round_trips(db)
    assert len(trips) == 1
    assert trips[0]["entry_price"] == "110"
    assert trips[0]["exit_price"] == "130"
    assert trips[0]["realized_jpy"] == "20"
    assert trips[0]["entry_reason"] == "momentum_breakout"
    assert trips[0]["exit_reason"] == "take_profit"


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



def test_performance_missing_ledger_is_unavailable_not_a_zero_result(tmp_path):
    now = dt.datetime(2026, 11, 24, 0, 0, tzinfo=JST).timestamp()
    perf = build_performance(
        tmp_path / "missing.sqlite3",
        capital_reference="1000",
        positions={},
        prices={},
        now=now,
    )

    assert perf["complete"] is False
    assert perf["ledger_status"] == "unavailable"
    assert perf["ledger_reason_code"] == "ledger_missing"
    assert perf["realized_total_jpy"] is None
    assert perf["interval_realized_pnl_jpy"] is None
    assert perf["today_realized_pnl_jpy"] is None
    assert perf["unrealized_pnl_jpy"] is None
    assert perf["cumulative_pnl_jpy"] is None
    assert perf["equity_jpy"] is None


def test_performance_invalid_fill_row_is_not_silently_dropped(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    ledger.close()
    with sqlite3.connect(db) as conn:
        conn.execute(
            """INSERT INTO paper_fills
               (fill_id, opportunity_id, strategy_id, symbol, side, quote, amount, price,
                quote_notional, reference_notional, reason_code, filled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("bad-fill", "bad-opportunity", "momentum-v1", "BTC/JPY", "buy",
             "JPY", "not-a-number", "100", "100", "100", "test", 1000.0),
        )

    perf = build_performance(
        db,
        capital_reference="1000",
        positions={},
        prices={},
        now=2000.0,
    )

    assert perf["complete"] is False
    assert perf["ledger_status"] == "invalid"
    assert perf["ledger_reason_code"] == "ledger_invalid_row"
    assert perf["realized_total_jpy"] is None
    assert perf["equity_jpy"] is None


def test_performance_unpaired_sell_does_not_publish_partial_realized_total(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("oversell-buy", "buy", "1", "100"), timestamp=1000.0)
    sold = broker.fill(_decision("oversell-sell", "sell", "2", "120"), timestamp=1100.0)
    positions = ledger.positions()
    ledger.close()

    perf = build_performance(
        db,
        capital_reference="1000",
        positions=positions,
        prices={"BTC/JPY": "110"},
        now=1200.0,
    )

    assert perf["complete"] is False
    assert perf["ledger_status"] == "partial"
    assert perf["ledger_reason_code"] == "unpaired_sell"
    assert perf["unpaired_sell_count"] == 1
    assert perf["realized_total_jpy"] is None
    assert perf["interval_realized_pnl_jpy"] is None
    assert perf["cumulative_pnl_jpy"] is None
    assert perf["equity_jpy"] is None
    assert realized_pnl_for_fill(db, sold.fill_id) is None


def test_public_performance_summary_exposes_only_bounded_ledger_health():
    payload = performance_summary_payload({
        "ledger_status": "invalid",
        "ledger_reason_code": "ledger_invalid_row",
        "unpaired_sell_count": 2,
        "private_path": "/private/paper.sqlite3",
    })

    assert payload == {
        "ledger_status": "invalid",
        "ledger_reason_code": "ledger_invalid_row",
        "unpaired_sell_count": 2,
        "realized_total_jpy": None,
        "interval_realized_pnl_jpy": None,
        "today_realized_pnl_jpy": None,
        "unrealized_pnl_jpy": None,
        "cumulative_pnl_jpy": None,
        "equity_jpy": None,
    }


def test_performance_unreadable_ledger_is_not_reported_as_missing(monkeypatch, tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    ledger.close()

    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == db:
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    perf = build_performance(
        db,
        capital_reference="1000",
        positions={},
        prices={},
        now=2000.0,
    )

    assert perf["complete"] is False
    assert perf["ledger_status"] == "unavailable"
    assert perf["ledger_reason_code"] == "ledger_read_error"
    assert perf["realized_total_jpy"] is None
    assert perf["equity_jpy"] is None


def test_performance_valid_empty_ledger_remains_a_complete_zero_result(tmp_path):
    db = tmp_path / "empty.sqlite3"
    ledger = PaperLedger(db)
    ledger.close()

    perf = build_performance(
        db,
        capital_reference="1000",
        positions={},
        prices={},
        now=2000.0,
    )

    assert perf["complete"] is True
    assert perf["ledger_status"] == "ok"
    assert perf["ledger_reason_code"] == "ok"
    assert perf["unpaired_sell_count"] == 0
    assert perf["realized_total_jpy"] == "0"
    assert perf["interval_realized_pnl_jpy"] == "0"
    assert perf["equity_jpy"] == "1000"


def test_performance_missing_fill_table_is_schema_invalid_not_empty(tmp_path):
    db = tmp_path / "wrong-schema.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")

    perf = build_performance(
        db,
        capital_reference="1000",
        positions={},
        prices={},
        now=2000.0,
    )

    assert perf["complete"] is False
    assert perf["ledger_status"] == "invalid"
    assert perf["ledger_reason_code"] == "ledger_schema_invalid"
    assert perf["realized_total_jpy"] is None
    assert perf["equity_jpy"] is None



def test_realized_pnl_after_oversell_uses_the_next_fresh_inventory_lot(tmp_path):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)
    broker.fill(_decision("taint-buy", "buy", "1", "100"), timestamp=1000.0)
    broker.fill(_decision("taint-oversell", "sell", "2", "120"), timestamp=1100.0)
    later_buy = broker.fill(_decision("fresh-buy", "buy", "1", "200"), timestamp=1200.0)
    later_sell = broker.fill(_decision("fresh-sell", "sell", "1", "210"), timestamp=1300.0)
    ledger.close()

    assert realized_pnl_for_fill(db, later_buy.fill_id) is None
    assert realized_pnl_for_fill(db, later_sell.fill_id) == Decimal("10")



@pytest.mark.parametrize("positions", [{}, {"BTC/JPY": "2"}])
def test_performance_position_snapshot_must_match_fill_ledger(tmp_path, positions):
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    _zero_cost_broker(ledger).fill(_decision("snapshot-buy", "buy", "1", "100"), timestamp=1000.0)
    ledger.close()

    perf = build_performance(
        db,
        capital_reference="1000",
        positions=positions,
        prices={"BTC/JPY": "110"},
        now=1200.0,
    )

    assert perf["complete"] is False
    assert perf["ledger_status"] == "partial"
    assert perf["ledger_reason_code"] == "position_snapshot_mismatch"
    assert perf["realized_total_jpy"] == "0"
    assert perf["unrealized_pnl_jpy"] is None
    assert perf["cumulative_pnl_jpy"] is None
    assert perf["equity_jpy"] is None

def test_interval_performance_counts_more_than_twenty_fills(tmp_path):
    """A 30-fill ledger must not be truncated to a recent-20 window."""
    db = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(db)
    broker = _zero_cost_broker(ledger)

    # Fifteen complete round trips produce 30 fill rows. The first two are
    # before the interval cursor; the remaining thirteen belong to it.
    for index in range(15):
        opened = 1000.0 + index * 10
        broker.fill(
            _decision(f"buy-{index}", "buy", "1", "100"),
            timestamp=opened,
        )
        broker.fill(
            _decision(f"sell-{index}", "sell", "1", "101"),
            timestamp=opened + 5,
        )
    ledger.close()

    performance = build_performance(
        db,
        capital_reference="1000",
        positions={},
        prices={},
        now=1200.0,
        interval_start=1020.0,
    )

    assert performance["complete"] is True
    assert performance["ledger_status"] == "ok"
    assert performance["unpaired_sell_count"] == 0
    assert performance["realized_total_jpy"] == "15"
    assert performance["interval_realized_pnl_jpy"] == "13"
    assert performance["equity_jpy"] == "1015"
