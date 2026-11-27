"""LIVE (real money) and free-strategy facts for the 暗号通貨 corner (synthetic, no network)."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from docich.trading.corner_live_facts import free_strategy_facts, live_facts
from docich.trading.corner_script import SEGMENT_KEYS, SEGMENT_LABELS, render_fallback
from docich.trading.live.ledger import FillRecord, OrderObservation

from test_trading_live_support import Harness, Workspace, block_network

NOW = 1795748400.0   # 2026-11-27 12:00 JST


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


def _trade(h, *, key, side, amount, price, at, order_id):
    intent = h.ledger.create_intent(h.lease, h.req(symbol="OAS/JPY", side=side, key=key,
                                                   amount=amount, price=price), now=at)
    h.ledger.record_order_observation(h.lease, OrderObservation(
        exchange_order_id=order_id, intent_id=intent.intent_id, symbol="OAS/JPY", side=side,
        order_type="limit", amount=amount, status="FULLY_FILLED", executed_amount=amount,
        remaining_amount="0", price=price), now=at)
    h.ledger.record_fill(h.lease, FillRecord(
        fill_id=f"f-{order_id}", exchange_order_id=order_id, symbol="OAS/JPY", side=side,
        amount=amount, price=price, quote_notional=str(D(amount) * D(price)), executed_at=at,
        fee_status="known", fee_amount="0", fee_currency="JPY"), now=at)
    for before, after in (("PLANNED", "SUBMITTING"), ("SUBMITTING", "ACKED"), ("ACKED", "FILLED")):
        h.ledger.transition_intent(h.lease, intent.intent_id, expected_state=before,
                                   to_state=after, now=at)


def test_live_facts_without_a_ledger_are_unavailable(ws):
    assert live_facts(ws.state, now=NOW) == {"available": False, "reason": "no_ledger"}


def test_live_facts_report_realized_and_today_in_yen(ws):
    h = Harness(ws, symbols=("OAS/JPY",), jpy="10000")
    try:
        h.ledger.record_owner_activation(h.lease, h.ps, operator="test", note="initial", now=NOW - 86400 * 5)
        _trade(h, key="b1", side="buy", amount="10", price="100", at=NOW - 86400 * 2, order_id="o1")
        _trade(h, key="s1", side="sell", amount="5", price="120", at=NOW - 86400 * 2 + 60, order_id="o2")
        _trade(h, key="s2", side="sell", amount="5", price="90", at=NOW - 600, order_id="o3")
    finally:
        h.close()
    facts = live_facts(ws.state, now=NOW)
    assert facts["available"] is True
    assert facts["realized_pnl_jpy"] == "50"          # +100 then -50
    assert facts["today_realized_pnl_jpy"] == "-50"
    assert facts["today_complete"] is True
    assert facts["sell_orders"] == 2
    assert facts["by_symbol_top"] == [{"symbol": "OAS/JPY", "realized_pnl_jpy": "50"}]
    assert "_id'" not in str(facts)  # no order/fill/intent/position ids


def test_free_strategy_facts_without_a_lab_are_unavailable(ws):
    assert free_strategy_facts(ws.paper, now=NOW)["available"] is False


def test_the_corner_has_a_live_slot_with_a_real_money_fallback():
    assert SEGMENT_KEYS.index("live") == SEGMENT_KEYS.index("result") + 1
    assert "LIVE" in SEGMENT_LABELS["live"]
    text = render_fallback({"live": {"available": True, "realized_pnl_jpy": "1234",
                                     "today_realized_pnl_jpy": "-56", "today_complete": False,
                                     "sell_orders": 7, "by_symbol_top": [], "open_position_count": 0,
                                     "last_promotion": {"symbols": ["OAS/JPY"]}}})["live"]
    assert "実際のお金" in text and "1,234円" in text and "途中まで" in text and "OAS/JPY" in text
    assert "数字はお伝えできません" in render_fallback({})["live"]
