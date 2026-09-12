from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision  # noqa: E402
from docich.trading.notifications import _render_event_with_local_facts  # noqa: E402
from docich.trading.paper import PaperBroker  # noqa: E402
from docich.trading.performance import build_performance, realized_pnl_for_fill  # noqa: E402

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
    assert rendered.speech_text == "利確条件を検出：BTC/JPYを売り、損益プラス20円です。"
