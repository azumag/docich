"""Read-only LIVE overview for the WebUI (Issue #3623, PR-C).

Every expected number is hand derived from a tiny fill set: one fee in JPY, one fee in
the base currency, one unknown fee, one closed position and one open position.  No
network, no sleeps, no floating point money.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from docich.trading.live.ledger import Cashflow, FillRecord, OrderObservation
from docich.trading.live.overview import live_overview

from test_trading_live_support import Harness, Workspace, block_network

NOW = 1_800_000_000.0
BANNED_KEYS = {"order_id", "exchange_order_id", "fill_id", "intent_id", "position_id",
               "client_order_id", "decision_key"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def h(ws):
    harness = Harness(ws, symbols=("OAS/JPY", "XYM/JPY"), jpy="10000")
    harness.ledger.record_owner_activation(harness.lease, harness.ps, operator="test",
                                           note="overview", now=1000.0)
    yield harness
    harness.close()


# ---- helpers: every row is created through the ledger's public methods -----------------------
def _intent(h, *, key, side="buy", symbol="OAS/JPY", amount="10", price="100", now=1000.0):
    return h.ledger.create_intent(
        h.lease, h.req(symbol=symbol, side=side, key=key, amount=amount, price=price), now=now)


def _order(h, intent, *, order_id):
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(exchange_order_id=order_id, intent_id=intent.intent_id,
                         symbol=intent.symbol, side=intent.side, order_type="limit",
                         amount=intent.amount, status="FULLY_FILLED",
                         executed_amount=intent.amount, remaining_amount="0",
                         price=intent.price),
        now=1000.0)


def _fill(h, *, fill_id, order_id, symbol, side, amount, price, executed_at,
          fee_status="known", fee_amount="0", fee_currency=None):
    if fee_status == "known":
        fee_currency = symbol.split("/")[1] if fee_currency is None else fee_currency
    else:
        fee_amount, fee_currency = None, None
    h.ledger.record_fill(
        h.lease,
        FillRecord(fill_id=fill_id, exchange_order_id=order_id, symbol=symbol, side=side,
                   amount=amount, price=price,
                   quote_notional=str(D(amount) * D(price)), executed_at=executed_at,
                   fee_status=fee_status, fee_amount=fee_amount, fee_currency=fee_currency),
        now=executed_at)


def _finish(h, intent):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1001.0)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=1001.0)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state="FILLED", now=1001.0)


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _round_trip(h):
    """OAS/JPY: buy 10 @100 with a 2 JPY fee, sell 4 @120 with a 1 JPY fee (position stays open).

    XYM/JPY: buy 20 @50 with a 1 XYM fee, sell 19 @60 with an unknown fee (position closes).
    """
    buy_oas = _intent(h, key="b-oas", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy_oas, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0, fee_amount="2", fee_currency="JPY")
    _finish(h, buy_oas)

    sell_oas = _intent(h, key="s-oas", side="sell", symbol="OAS/JPY", amount="4", price="120")
    _order(h, sell_oas, order_id="o2")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="sell", amount="4",
          price="120", executed_at=1100.0, fee_amount="1", fee_currency="JPY")
    _finish(h, sell_oas)

    buy_xym = _intent(h, key="b-xym", symbol="XYM/JPY", amount="20", price="50")
    _order(h, buy_xym, order_id="o3")
    _fill(h, fill_id="f3", order_id="o3", symbol="XYM/JPY", side="buy", amount="20",
          price="50", executed_at=1200.0, fee_amount="1", fee_currency="XYM")
    _finish(h, buy_xym)

    sell_xym = _intent(h, key="s-xym", side="sell", symbol="XYM/JPY", amount="19", price="60")
    _order(h, sell_xym, order_id="o4")
    _fill(h, fill_id="f4", order_id="o4", symbol="XYM/JPY", side="sell", amount="19",
          price="60", executed_at=1300.0, fee_status="unknown")
    _finish(h, sell_xym)
    h.ledger.close_position(h.lease, buy_xym.position_id, now=1301.0)
    return buy_oas, buy_xym


def test_pnl_positions_and_recent_fills_are_hand_derived(h):
    _round_trip(h)
    view = live_overview(h.ledger, now=NOW)

    assert view["ledger_readable"] is True
    # OAS: (480 - 1000) - (2 + 1) = -523.  XYM: (1140 - 1000) - (1*50) = 90.
    assert view["pnl"]["by_symbol"] == {"OAS/JPY": "-523", "XYM/JPY": "90"}
    assert view["pnl"]["total"] == "-433"
    assert view["pnl"]["round_trips"] == 1
    assert view["pnl"]["wins"] == 1
    assert view["pnl"]["since"] == 1000.0
    assert view["pnl"]["fee_unknown_fills"] == 1
    assert view["pnl"]["fee_other_currency_fills"] == 0
    assert view["pnl"]["unknown_fee_treatment"] == "zero"

    # Only the OAS position is still open; the XYM round trip closed at net 0.
    assert len(view["positions"]) == 1
    position = view["positions"][0]
    assert position["symbol"] == "OAS/JPY"
    assert position["net_base"] == "6"
    assert position["avg_cost"] == "100.2"
    assert position["cost"] == "601.2"
    assert position["entry_policy_digest"] == h.ps.policy_for("OAS/JPY").digest[:12]
    assert position["opened_at"] == 1000.0

    assert [fill["at"] for fill in view["recent_fills"]] == [1300.0, 1200.0, 1100.0, 1000.0]
    assert view["recent_fills"][0] == {
        "at": 1300.0, "symbol": "XYM/JPY", "side": "sell", "amount": "19",
        "price": "60", "notional": "1140",
    }
    assert view["open_intents"] == {"buy": {}, "sell": {}}
    # Recording an unknown fee raises the fee_missing safety latch; no ids are exposed.
    assert view["safety"] == {"trips": ["fee_missing"], "unresolved_unknown_submissions": 0}
    assert view["policy"]["digest"] == h.ps.digest[:12]
    assert view["policy"]["symbols"] == ["OAS/JPY", "XYM/JPY"]
    assert view["opening"] == []

    # The response is allow-listed: no id key at any depth, and no id value leaked.
    assert not (set(_keys(view)) & BANNED_KEYS)
    encoded = json.dumps(view)
    for leak in ("o1", "o2", "o3", "o4", "f1", "f2", "f3", "f4"):
        assert f'"{leak}"' not in encoded


def test_recent_fills_are_newest_first_and_capped_at_twenty(h):
    buy = _intent(h, key="many", amount="25", price="100")
    _order(h, buy, order_id="bulk")
    for index in range(25):
        _fill(h, fill_id=f"f{index:02d}", order_id="bulk", symbol="OAS/JPY", side="buy",
              amount="1", price="100", executed_at=1000.0 + index)

    recent = live_overview(h.ledger, now=NOW)["recent_fills"]
    assert len(recent) == 20
    assert [fill["at"] for fill in recent] == [1024.0 - index for index in range(20)]
    assert recent[0]["at"] == 1024.0 and recent[-1]["at"] == 1005.0


def test_open_intents_are_counted_by_side_and_state_without_ids(h):
    _intent(h, key="planned-buy", amount="5", price="100")
    view = live_overview(h.ledger, now=NOW)
    assert view["open_intents"] == {"buy": {"PLANNED": 1}, "sell": {}}


def test_an_unreadable_ledger_reads_as_fail_closed(ws):
    ledger = ws.open_ledger(now=NOW)
    ledger.close()

    view = live_overview(ledger, now=NOW)

    assert view["ledger_readable"] is False
    assert view["positions"] == []
    assert view["pnl"]["by_symbol"] == {}
    assert view["pnl"]["total"] == "0"
    assert view["pnl"]["round_trips"] == 0
    assert view["pnl"]["wins"] == 0
    assert view["pnl"]["since"] is None
    assert view["recent_fills"] == []
    assert view["policy"] == {"digest": None, "symbols": []}


def test_an_empty_ledger_is_all_zeros(ws):
    ledger = ws.open_ledger(now=NOW)
    try:
        view = live_overview(ledger, now=NOW)
    finally:
        ledger.close()

    assert view["ledger_readable"] is True
    assert view["positions"] == []
    assert view["pnl"] == {
        "by_symbol": {}, "total": "0", "round_trips": 0, "wins": 0, "since": None,
        "fee_unknown_fills": 0, "fee_other_currency_fills": 0,
        "unknown_fee_treatment": "zero",
    }
    assert view["recent_fills"] == []
    assert view["open_intents"] == {"buy": {}, "sell": {}}
    assert view["safety"] == {"trips": [], "unresolved_unknown_submissions": 0}
    assert view["policy"] == {"digest": None, "symbols": []}
    assert view["opening"] == []


def test_opening_is_selected_by_cashflow_id_prefix_not_source(h):
    # The ledger only accepts source owner_manual/exchange_history, so the worker's automatic
    # opening balance carries source 'exchange_history' and is identified by its id prefix.
    h.ledger.record_cashflow(
        h.lease,
        Cashflow("auto_opening:XRP", "deposit", "XRP", "3", "exchange_history", 1500.0),
        now=1500.0)
    h.ledger.record_cashflow(
        h.lease,
        Cashflow("manual:1", "deposit", "XRP", "9", "owner_manual", 1400.0),
        now=1400.0)

    view = live_overview(h.ledger, now=NOW)

    assert view["opening"] == [{"currency": "XRP", "amount": "3", "at": 1500.0}]
