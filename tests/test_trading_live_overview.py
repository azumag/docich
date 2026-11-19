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


def _unlinked_order(h, *, order_id, symbol="OAS/JPY", side="sell", amount="5", price="100"):
    """An order no intent points at: its fills have no position_id to prove inventory."""
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(exchange_order_id=order_id, intent_id=None, symbol=symbol, side=side,
                         order_type="limit", amount=amount, status="FULLY_FILLED",
                         executed_amount=amount, remaining_amount="0", price=price),
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


def _cancel(h, intent):
    """Cancel a buy that never reached the exchange; its position row stays empty."""
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1001.0)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=1001.0)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state="CANCELED", now=1001.0)


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


def test_a_closed_unfilled_buy_is_not_a_round_trip(h):
    # A buy is planned, so its position row exists, then cancelled without any fill.
    # The executor closes the flat row; that is not a completed buy/sell trip.
    cancelled = _intent(h, key="cancel-oas", amount="10", price="100")
    _cancel(h, cancelled)
    h.ledger.close_position(h.lease, cancelled.position_id, now=1002.0)

    view = live_overview(h.ledger, now=NOW)

    assert view["pnl"]["round_trips"] == 0
    assert view["pnl"]["wins"] == 0
    assert view["pnl"]["total"] == "0"
    assert view["pnl"]["by_symbol"] == {}
    assert view["positions"] == []


def test_a_closed_buy_without_a_sell_is_not_a_round_trip_but_keeps_its_pnl(h):
    # A funded buy that never got a sell lowers the cash-result total (open buys are in
    # the formula), but it is not a round trip: it must not join the win denominator.
    buy = _intent(h, key="buy-only", amount="10", price="100")
    _order(h, buy, order_id="buy-only-order")
    _fill(h, fill_id="buy-only-fill", order_id="buy-only-order", symbol="OAS/JPY",
          side="buy", amount="10", price="100", executed_at=1000.0)
    _finish(h, buy)
    h.ledger.close_position(h.lease, buy.position_id, now=1002.0)

    view = live_overview(h.ledger, now=NOW)

    assert view["pnl"]["by_symbol"] == {"OAS/JPY": "-1000"}
    assert view["pnl"]["total"] == "-1000"
    assert view["pnl"]["round_trips"] == 0
    assert view["pnl"]["wins"] == 0
    assert view["positions"] == []


def test_round_trip_denominator_and_wins_use_only_completed_buy_sell_trips(h):
    # OAS: buy 10 @100, sell 10 @90 -> -100 (a losing completed trip).
    buy_oas = _intent(h, key="trip-oas-buy", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy_oas, order_id="trip-oas-buy-order")
    _fill(h, fill_id="trip-oas-buy-fill", order_id="trip-oas-buy-order", symbol="OAS/JPY",
          side="buy", amount="10", price="100", executed_at=1000.0)
    _finish(h, buy_oas)
    sell_oas = _intent(h, key="trip-oas-sell", side="sell", symbol="OAS/JPY",
                       amount="10", price="90")
    _order(h, sell_oas, order_id="trip-oas-sell-order")
    _fill(h, fill_id="trip-oas-sell-fill", order_id="trip-oas-sell-order", symbol="OAS/JPY",
          side="sell", amount="10", price="90", executed_at=1100.0)
    _finish(h, sell_oas)
    h.ledger.close_position(h.lease, buy_oas.position_id, now=1101.0)

    # XYM: buy 20 @50, sell 20 @60 -> +200 (a winning completed trip).
    buy_xym = _intent(h, key="trip-xym-buy", symbol="XYM/JPY", amount="20", price="50")
    _order(h, buy_xym, order_id="trip-xym-buy-order")
    _fill(h, fill_id="trip-xym-buy-fill", order_id="trip-xym-buy-order", symbol="XYM/JPY",
          side="buy", amount="20", price="50", executed_at=1200.0)
    _finish(h, buy_xym)
    sell_xym = _intent(h, key="trip-xym-sell", side="sell", symbol="XYM/JPY",
                       amount="20", price="60")
    _order(h, sell_xym, order_id="trip-xym-sell-order")
    _fill(h, fill_id="trip-xym-sell-fill", order_id="trip-xym-sell-order", symbol="XYM/JPY",
          side="sell", amount="20", price="60", executed_at=1300.0)
    _finish(h, sell_xym)
    h.ledger.close_position(h.lease, buy_xym.position_id, now=1301.0)

    # A third, cancelled buy has no fills and must not change either count.
    cancelled = _intent(h, key="trip-cancelled", symbol="OAS/JPY", amount="5", price="100")
    _cancel(h, cancelled)
    h.ledger.close_position(h.lease, cancelled.position_id, now=1302.0)

    view = live_overview(h.ledger, now=NOW)

    assert view["pnl"]["by_symbol"] == {"OAS/JPY": "-100", "XYM/JPY": "200"}
    assert view["pnl"]["total"] == "100"
    assert view["pnl"]["round_trips"] == 2       # only the two completed trips
    assert view["pnl"]["wins"] == 1              # the XYM trip; wins <= round_trips


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


# ---- realized trade P&L (overview.trade_pnl) ------------------------------------------------
def test_trade_pnl_partial_sell_order_aggregates_multiple_fills(h):
    buy = _intent(h, key="tp-buy", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy, order_id="tp-buy-order")
    _fill(h, fill_id="tp-buy-fill", order_id="tp-buy-order", symbol="OAS/JPY", side="buy",
          amount="10", price="100", executed_at=1000.0)
    _finish(h, buy)

    sell = _intent(h, key="tp-sell", side="sell", symbol="OAS/JPY", amount="5", price="120")
    _order(h, sell, order_id="tp-sell-order")
    # 2 @150 with an unknown fee: 300 - 200 = 100.
    _fill(h, fill_id="tp-sell-fill-1", order_id="tp-sell-order", symbol="OAS/JPY", side="sell",
          amount="2", price="150", executed_at=1100.0, fee_status="unknown")
    # 3 @120 with a 1 JPY quote fee: 360 - 1 - 300 = 59.
    _fill(h, fill_id="tp-sell-fill-2", order_id="tp-sell-order", symbol="OAS/JPY", side="sell",
          amount="3", price="120", executed_at=1110.0, fee_amount="1", fee_currency="JPY")
    _finish(h, sell)

    view = live_overview(h.ledger, now=NOW)
    trade = view["trade_pnl"]

    assert trade["available"] is True
    assert trade["total"] == "159"
    assert trade["by_symbol"] == {"OAS/JPY": "159"}
    assert trade["rows"] == [{
        "at": 1110.0, "symbol": "OAS/JPY", "amount": "5", "pnl": "159",
        "status": "accounted", "unaccounted_reason": None,
        "fee_unknown": True, "fee_other_currency": False, "fills": 2,
    }]
    assert trade["fee_unknown_fills"] == 1
    assert trade["fee_other_currency_fills"] == 0
    assert trade["unaccounted_orders"] == 0
    assert trade["complete"] is True
    assert (trade["page"], trade["page_count"], trade["page_size"], trade["total_rows"]) == (1, 1, 20, 1)
    # The remaining position keeps the proportional 5 @100 basis.
    assert view["positions"][0]["net_base"] == "5"
    assert view["positions"][0]["cost"] == "500"
    # The cash-flow view is unchanged.
    assert view["pnl"]["total"] == "-341"


def test_trade_pnl_uses_base_and_quote_fees_without_changing_existing_pnl(h):
    buy_oas = _intent(h, key="tp-fee-oas-buy", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy_oas, order_id="tp-fee-oas-buy-order")
    # 1 OAS base fee reduces acquired base to 9; cost stays 1000.
    _fill(h, fill_id="tp-fee-oas-buy-fill", order_id="tp-fee-oas-buy-order", symbol="OAS/JPY",
          side="buy", amount="10", price="100", executed_at=1000.0,
          fee_amount="1", fee_currency="OAS")
    _finish(h, buy_oas)
    sell_oas = _intent(h, key="tp-fee-oas-sell", side="sell", symbol="OAS/JPY",
                       amount="9", price="150")
    _order(h, sell_oas, order_id="tp-fee-oas-sell-order")
    _fill(h, fill_id="tp-fee-oas-sell-fill", order_id="tp-fee-oas-sell-order", symbol="OAS/JPY",
          side="sell", amount="9", price="150", executed_at=1100.0)
    _finish(h, sell_oas)

    buy_xym = _intent(h, key="tp-fee-xym-buy", symbol="XYM/JPY", amount="10", price="100")
    _order(h, buy_xym, order_id="tp-fee-xym-buy-order")
    # 2 JPY quote fee lifts cost to 1002.
    _fill(h, fill_id="tp-fee-xym-buy-fill", order_id="tp-fee-xym-buy-order", symbol="XYM/JPY",
          side="buy", amount="10", price="100", executed_at=1200.0,
          fee_amount="2", fee_currency="JPY")
    _finish(h, buy_xym)
    sell_xym = _intent(h, key="tp-fee-xym-sell", side="sell", symbol="XYM/JPY",
                       amount="9", price="120")
    _order(h, sell_xym, order_id="tp-fee-xym-sell-order")
    # 5 @120, 1 JPY fee: 600 - 1 - 501 = 98.
    _fill(h, fill_id="tp-fee-xym-sell-fill-1", order_id="tp-fee-xym-sell-order", symbol="XYM/JPY",
          side="sell", amount="5", price="120", executed_at=1300.0,
          fee_amount="1", fee_currency="JPY")
    # 4 @120, 2 JPY fee: 480 - 2 - 400.8 = 77.2.
    _fill(h, fill_id="tp-fee-xym-sell-fill-2", order_id="tp-fee-xym-sell-order", symbol="XYM/JPY",
          side="sell", amount="4", price="120", executed_at=1310.0,
          fee_amount="2", fee_currency="JPY")
    _finish(h, sell_xym)

    view = live_overview(h.ledger, now=NOW)
    trade = view["trade_pnl"]

    assert trade["by_symbol"] == {"OAS/JPY": "350", "XYM/JPY": "175.2"}
    assert trade["total"] == "525.2"
    assert [row["pnl"] for row in trade["rows"]] == ["175.2", "350"]
    # The cash view values the OAS base fee at the fill price (1 * 100).
    assert view["pnl"]["by_symbol"] == {"OAS/JPY": "250", "XYM/JPY": "75"}
    assert view["pnl"]["total"] == "325"


def test_trade_pnl_preserves_rebate_signs(h):
    buy_oas = _intent(h, key="tp-rebate-oas-buy", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy_oas, order_id="tp-rebate-oas-buy-order")
    # A -1 base rebate raises acquired base to 11 at the same 1000 cost.
    _fill(h, fill_id="tp-rebate-oas-buy-fill", order_id="tp-rebate-oas-buy-order",
          symbol="OAS/JPY", side="buy", amount="10", price="100", executed_at=1000.0,
          fee_amount="-1", fee_currency="OAS")
    _finish(h, buy_oas)
    sell_oas = _intent(h, key="tp-rebate-oas-sell", side="sell", symbol="OAS/JPY",
                       amount="11", price="100")
    _order(h, sell_oas, order_id="tp-rebate-oas-sell-order")
    _fill(h, fill_id="tp-rebate-oas-sell-fill", order_id="tp-rebate-oas-sell-order",
          symbol="OAS/JPY", side="sell", amount="11", price="100", executed_at=1100.0)
    _finish(h, sell_oas)

    buy_xym = _intent(h, key="tp-rebate-xym-buy", symbol="XYM/JPY", amount="10", price="100")
    _order(h, buy_xym, order_id="tp-rebate-xym-buy-order")
    # A -2 quote rebate lowers cost to 998; a -1 quote sell rebate raises proceeds.
    _fill(h, fill_id="tp-rebate-xym-buy-fill", order_id="tp-rebate-xym-buy-order",
          symbol="XYM/JPY", side="buy", amount="10", price="100", executed_at=1200.0,
          fee_amount="-2", fee_currency="JPY")
    _finish(h, buy_xym)
    sell_xym = _intent(h, key="tp-rebate-xym-sell", side="sell", symbol="XYM/JPY",
                       amount="5", price="120")
    _order(h, sell_xym, order_id="tp-rebate-xym-sell-order")
    _fill(h, fill_id="tp-rebate-xym-sell-fill", order_id="tp-rebate-xym-sell-order",
          symbol="XYM/JPY", side="sell", amount="5", price="120", executed_at=1300.0,
          fee_amount="-1", fee_currency="JPY")
    _finish(h, sell_xym)

    trade = live_overview(h.ledger, now=NOW)["trade_pnl"]
    # Equal Decimal prices may serialize as "102.0" or "102"; compare numerically.
    assert ({symbol: D(value) for symbol, value in trade["by_symbol"].items()}
            == {"OAS/JPY": D("100"), "XYM/JPY": D("102")})
    assert D(trade["total"]) == D("202")
    assert [D(row["pnl"]) for row in trade["rows"]] == [D("102"), D("100")]
    assert all(row["fee_unknown"] is False and row["fee_other_currency"] is False
               for row in trade["rows"])


def test_trade_pnl_unknown_buy_fee_marks_every_affected_partial_sale(h):
    buy = _intent(h, key="tp-unknown-buy", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy, order_id="tp-unknown-buy-order")
    _fill(h, fill_id="tp-unknown-buy-fill", order_id="tp-unknown-buy-order", symbol="OAS/JPY",
          side="buy", amount="10", price="100", executed_at=1000.0, fee_status="unknown")
    _finish(h, buy)

    sell_one = _intent(h, key="tp-unknown-sell-1", side="sell", symbol="OAS/JPY",
                       amount="5", price="120")
    _order(h, sell_one, order_id="tp-unknown-sell-order-1")
    _fill(h, fill_id="tp-unknown-sell-fill-1", order_id="tp-unknown-sell-order-1",
          symbol="OAS/JPY", side="sell", amount="5", price="120", executed_at=1100.0,
          fee_status="unknown")
    _finish(h, sell_one)

    sell_two = _intent(h, key="tp-unknown-sell-2", side="sell", symbol="OAS/JPY",
                       amount="2", price="130")
    _order(h, sell_two, order_id="tp-unknown-sell-order-2")
    _fill(h, fill_id="tp-unknown-sell-fill-2", order_id="tp-unknown-sell-order-2",
          symbol="OAS/JPY", side="sell", amount="2", price="130", executed_at=1200.0)
    _finish(h, sell_two)

    trade = live_overview(h.ledger, now=NOW)["trade_pnl"]
    # The unknown buy fee stays in the remaining basis; both sells are marked.
    assert trade["total"] == "160"
    assert trade["by_symbol"] == {"OAS/JPY": "160"}
    assert [row["pnl"] for row in trade["rows"]] == ["60", "100"]
    assert all(row["fee_unknown"] is True for row in trade["rows"])
    # The buy and the first (unknown) sell are the fee-anomalous fills; the second
    # sell has a known fee but is still marked because the buy estimate is in its basis.
    assert trade["fee_unknown_fills"] == 2
    assert trade["unknown_fee_treatment"] == "zero"


def test_trade_pnl_third_currency_fees_are_zero_and_counted(h):
    buy = _intent(h, key="tp-other-buy", symbol="XYM/JPY", amount="10", price="100")
    _order(h, buy, order_id="tp-other-buy-order")
    _fill(h, fill_id="tp-other-buy-fill", order_id="tp-other-buy-order", symbol="XYM/JPY",
          side="buy", amount="10", price="100", executed_at=1000.0,
          fee_amount="5", fee_currency="USD")
    _finish(h, buy)
    sell = _intent(h, key="tp-other-sell", side="sell", symbol="XYM/JPY",
                   amount="5", price="120")
    _order(h, sell, order_id="tp-other-sell-order")
    _fill(h, fill_id="tp-other-sell-fill", order_id="tp-other-sell-order", symbol="XYM/JPY",
          side="sell", amount="5", price="120", executed_at=1100.0,
          fee_amount="5", fee_currency="USD")
    _finish(h, sell)

    trade = live_overview(h.ledger, now=NOW)["trade_pnl"]
    assert trade["total"] == "100"
    assert trade["rows"][0]["fee_other_currency"] is True
    assert trade["fee_other_currency_fills"] == 2   # buy + sell
    assert trade["fee_unknown_fills"] == 0


def test_trade_pnl_buy_only_and_a_loss(h):
    buy = _intent(h, key="tp-buy-only", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy, order_id="tp-buy-only-order")
    _fill(h, fill_id="tp-buy-only-fill", order_id="tp-buy-only-order", symbol="OAS/JPY",
          side="buy", amount="10", price="100", executed_at=1000.0,
          fee_amount="2", fee_currency="JPY")
    _finish(h, buy)

    view = live_overview(h.ledger, now=NOW)
    assert view["trade_pnl"]["total"] == "0"
    assert view["trade_pnl"]["by_symbol"] == {}
    assert view["trade_pnl"]["rows"] == []
    assert view["trade_pnl"]["total_rows"] == 0
    assert view["trade_pnl"]["available"] is True
    # The existing cash-flow view still includes the open buy.
    assert view["pnl"]["total"] == "-1002"

    sell = _intent(h, key="tp-loss-sell", side="sell", symbol="OAS/JPY",
                   amount="10", price="90")
    _order(h, sell, order_id="tp-loss-sell-order")
    _fill(h, fill_id="tp-loss-sell-fill", order_id="tp-loss-sell-order", symbol="OAS/JPY",
          side="sell", amount="10", price="90", executed_at=1100.0)
    _finish(h, sell)

    trade = live_overview(h.ledger, now=NOW)["trade_pnl"]
    assert trade["total"] == "-102"
    assert trade["rows"][0]["pnl"] == "-102"
    assert trade["rows"][0]["status"] == "accounted"


def test_trade_pnl_surfaces_unaccounted_sales_without_assuming_zero_basis(h):
    # OAS: a sell fill whose position never had a funded buy -> missing basis.
    buy_plan = _intent(h, key="tp-missing-buy", symbol="OAS/JPY", amount="10", price="100")
    sell_missing = _intent(h, key="tp-missing-sell", side="sell", symbol="OAS/JPY",
                           amount="5", price="100")
    _order(h, sell_missing, order_id="tp-missing-order")
    _fill(h, fill_id="tp-missing-fill", order_id="tp-missing-order", symbol="OAS/JPY",
          side="sell", amount="5", price="100", executed_at=1000.0)
    _finish(h, sell_missing)
    assert buy_plan.position_id == sell_missing.position_id

    # OAS: an order with no intent has no position_id at all.
    _unlinked_order(h, order_id="tp-unlinked-order")
    _fill(h, fill_id="tp-unlinked-fill", order_id="tp-unlinked-order", symbol="OAS/JPY",
          side="sell", amount="5", price="100", executed_at=1010.0)

    # XYM: a funded, accounted sale remains in the numeric totals.
    buy_xym = _intent(h, key="tp-accounted-buy", symbol="XYM/JPY", amount="10", price="100")
    _order(h, buy_xym, order_id="tp-accounted-buy-order")
    _fill(h, fill_id="tp-accounted-buy-fill", order_id="tp-accounted-buy-order",
          symbol="XYM/JPY", side="buy", amount="10", price="100", executed_at=1100.0)
    _finish(h, buy_xym)
    sell_xym = _intent(h, key="tp-accounted-sell", side="sell", symbol="XYM/JPY",
                       amount="10", price="110")
    _order(h, sell_xym, order_id="tp-accounted-sell-order")
    _fill(h, fill_id="tp-accounted-sell-fill", order_id="tp-accounted-sell-order",
          symbol="XYM/JPY", side="sell", amount="10", price="110", executed_at=1200.0)
    _finish(h, sell_xym)

    trade = live_overview(h.ledger, now=NOW)["trade_pnl"]
    assert trade["total"] == "100"
    assert trade["by_symbol"] == {"XYM/JPY": "100"}
    assert trade["unaccounted_orders"] == 2
    assert trade["unaccounted_fills"] == 2
    assert trade["complete"] is False
    assert [(row["symbol"], row["status"], row["pnl"]) for row in trade["rows"]] == [
        ("XYM/JPY", "accounted", "100"),
        ("OAS/JPY", "unaccounted", None),
        ("OAS/JPY", "unaccounted", None),
    ]
    assert {row["unaccounted_reason"] for row in trade["rows"][1:]} == {
        "missing_basis", "unlinked_position"}
    # No order/fill/position ids leak through the new rows either.
    assert not (set(_keys(trade)) & BANNED_KEYS)
    encoded = json.dumps(trade)
    for leak in ("tp-missing", "tp-unlinked", "tp-accounted", "tp-missing-fill",
                 "tp-unlinked-fill", "tp-accounted-fill"):
        assert leak not in encoded


def test_trade_pnl_one_unaccounted_fill_excludes_the_whole_sell_order(h):
    buy = _intent(h, key="tp-excess-buy", symbol="OAS/JPY", amount="5", price="100")
    _order(h, buy, order_id="tp-excess-buy-order")
    _fill(h, fill_id="tp-excess-buy-fill", order_id="tp-excess-buy-order", symbol="OAS/JPY",
          side="buy", amount="5", price="100", executed_at=1000.0)
    _finish(h, buy)
    sell = _intent(h, key="tp-excess-sell", side="sell", symbol="OAS/JPY",
                   amount="6", price="120")
    _order(h, sell, order_id="tp-excess-sell-order")
    _fill(h, fill_id="tp-excess-sell-fill-1", order_id="tp-excess-sell-order",
          symbol="OAS/JPY", side="sell", amount="3", price="120", executed_at=1100.0)
    # The second fill exceeds the 2 remaining base, so the order cannot be proved.
    _fill(h, fill_id="tp-excess-sell-fill-2", order_id="tp-excess-sell-order",
          symbol="OAS/JPY", side="sell", amount="3", price="120", executed_at=1110.0)
    _finish(h, sell)

    # The oversell left -1 inventory in this still-open position.  A later buy must not
    # reset the broken replay, so the following sell stays unaccounted too.
    _flat_buy = _intent(h, key="tp-excess-flat-buy", symbol="OAS/JPY", amount="1",
                        price="1150.0")
    _order(h, _flat_buy, order_id="tp-excess-flat-buy-order")
    _fill(h, fill_id="tp-excess-flat-buy-fill", order_id="tp-excess-flat-buy-order",
          symbol="OAS/JPY", side="buy", amount="1", price="1150.0", executed_at=1150.0)
    _finish(h, _flat_buy)
    buy_more = _intent(h, key="tp-excess-rebuy", symbol="OAS/JPY", amount="10", price="100")
    _order(h, buy_more, order_id="tp-excess-rebuy-order")
    _fill(h, fill_id="tp-excess-rebuy-fill", order_id="tp-excess-rebuy-order",
          symbol="OAS/JPY", side="buy", amount="10", price="100", executed_at=1200.0)
    _finish(h, buy_more)
    sell_more = _intent(h, key="tp-excess-resell", side="sell", symbol="OAS/JPY",
                        amount="5", price="120")
    _order(h, sell_more, order_id="tp-excess-resell-order")
    _fill(h, fill_id="tp-excess-resell-fill", order_id="tp-excess-resell-order",
          symbol="OAS/JPY", side="sell", amount="5", price="120", executed_at=1300.0)
    _finish(h, sell_more)

    trade = live_overview(h.ledger, now=NOW)["trade_pnl"]
    # Every sale is unaccounted, so there is no valid total (not a zero result).
    assert trade["total"] is None
    assert trade["by_symbol"] == {}
    assert len(trade["rows"]) == 2
    newest, oldest = trade["rows"]
    assert newest["pnl"] is None
    assert newest["status"] == "unaccounted"
    assert newest["unaccounted_reason"] == "basis_unavailable"
    assert newest["fills"] == 1
    assert oldest["pnl"] is None
    assert oldest["status"] == "unaccounted"
    assert oldest["unaccounted_reason"] == "excess_basis"
    assert oldest["fills"] == 2
    assert trade["unaccounted_orders"] == 2
    assert trade["unaccounted_fills"] == 3
    assert trade["complete"] is False


def test_trade_pnl_pages_are_newest_first_and_share_totals_and_clamp(h):
    buy = _intent(h, key="tp-page-buy", symbol="OAS/JPY", amount="30", price="100")
    _order(h, buy, order_id="tp-page-buy-order")
    _fill(h, fill_id="tp-page-buy-fill", order_id="tp-page-buy-order", symbol="OAS/JPY",
          side="buy", amount="30", price="100", executed_at=1000.0)
    _finish(h, buy)
    for index in range(25):
        sell = _intent(h, key=f"tp-page-sell-{index}", side="sell", symbol="OAS/JPY",
                       amount="1", price="110", now=2000.0 + index)
        _order(h, sell, order_id=f"tp-page-sell-order-{index}")
        _fill(h, fill_id=f"tp-page-sell-fill-{index}", order_id=f"tp-page-sell-order-{index}",
              symbol="OAS/JPY", side="sell", amount="1", price="110",
              executed_at=2000.0 + index)
        _finish(h, sell)

    first = live_overview(h.ledger, now=NOW, trade_page=1)["trade_pnl"]
    second = live_overview(h.ledger, now=NOW, trade_page=2)["trade_pnl"]
    last_clamped = live_overview(h.ledger, now=NOW, trade_page=999)["trade_pnl"]
    invalid = live_overview(h.ledger, now=NOW, trade_page="not-a-page")["trade_pnl"]
    below = live_overview(h.ledger, now=NOW, trade_page=0)["trade_pnl"]

    assert first["total"] == second["total"] == "250"
    assert first["by_symbol"] == second["by_symbol"] == {"OAS/JPY": "250"}
    assert (first["page"], first["page_count"], first["total_rows"]) == (1, 2, 25)
    assert (second["page"], second["page_count"]) == (2, 2)
    assert len(first["rows"]) == 20 and len(second["rows"]) == 5
    assert [row["at"] for row in first["rows"]] == [2024.0 - index for index in range(20)]
    assert [row["at"] for row in second["rows"]] == [2004.0 - index for index in range(5)]
    assert [row["pnl"] for row in first["rows"]] == ["10"] * 20
    assert [row["pnl"] for row in second["rows"]] == ["10"] * 5
    assert last_clamped["page"] == 2
    assert invalid["page"] == 1 and below["page"] == 1


def test_trade_pnl_is_unavailable_when_the_ledger_cannot_be_read(ws):
    ledger = ws.open_ledger(now=NOW)
    ledger.close()

    trade = live_overview(ledger, now=NOW)["trade_pnl"]

    assert trade["available"] is False
    assert trade["total"] is None
    assert trade["by_symbol"] == {}
    assert trade["rows"] == []
    assert trade["complete"] is False
    assert trade["page"] == 1 and trade["page_count"] == 1
