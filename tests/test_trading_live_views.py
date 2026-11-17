"""Read-only views over the LIVE ledger (Issue #3623, L4b).  No network, nothing writes."""
from decimal import Decimal as D

import pytest

from docich.trading.live import views
from docich.trading.live.ledger import FillRecord, OrderObservation
from test_trading_live_support import Harness, Workspace, block_network


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def harnesses(ws):
    made = []

    def make(**kw):
        harness = Harness(ws, **kw)
        made.append(harness)
        return harness

    yield make
    for harness in made:
        harness.close()


@pytest.fixture
def h(harnesses):
    return harnesses()


# ---- helpers: every row is created through the ledger's public methods -----------------------
def _intent(h, *, key, side="buy", symbol="OAS/JPY", amount="10", price="100", now=1000.0):
    return h.ledger.create_intent(
        h.lease, h.req(symbol=symbol, side=side, key=key, amount=amount, price=price), now=now)


def _order(h, intent, *, order_id, amount, price):
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(exchange_order_id=order_id, intent_id=intent.intent_id, symbol=intent.symbol,
                         side=intent.side, order_type="limit", amount=amount, status="FULLY_FILLED",
                         executed_amount=amount, remaining_amount="0", price=price),
        now=1000.0)


def _fill(h, *, fill_id, order_id, symbol, side, amount, price, executed_at, fee_status="known",
          fee_amount=None, fee_currency=None):
    if fee_status == "known":
        fee_amount = "0" if fee_amount is None else fee_amount
        fee_currency = symbol.split("/")[1] if fee_currency is None else fee_currency
    else:
        fee_amount, fee_currency = None, None
    h.ledger.record_fill(
        h.lease,
        FillRecord(fill_id=fill_id, exchange_order_id=order_id, symbol=symbol, side=side, amount=amount,
                   price=price, quote_notional=str(D(amount) * D(price)), executed_at=executed_at,
                   fee_status=fee_status, fee_amount=fee_amount, fee_currency=fee_currency),
        now=executed_at)


def _finish(h, intent, *, state="FILLED", now=1001.0):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state=state, now=now)


# ---- a. intent_by_decision_key ---------------------------------------------------------------
def test_intent_by_decision_key_finds_open_and_terminal_intents_and_missing_is_none(h):
    planned = _intent(h, key="k-open")
    found = views.intent_by_decision_key(h.ledger, "k-open")
    assert found == h.ledger.get_intent(planned.intent_id)
    assert found.decision_key == "k-open" and found.state == "PLANNED"

    h.ledger.transition_intent(h.lease, planned.intent_id, expected_state="PLANNED",
                               to_state="ABANDONED", now=1010.0)
    assert views.intent_by_decision_key(h.ledger, "k-open").state == "ABANDONED"

    rejected = _intent(h, key="k-rejected")
    h.ledger.transition_intent(h.lease, rejected.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1020.0)
    h.ledger.transition_intent(h.lease, rejected.intent_id, expected_state="SUBMITTING",
                               to_state="REJECTED", now=1021.0, reason_code="rejected")
    assert views.intent_by_decision_key(h.ledger, "k-rejected").state == "REJECTED"
    assert views.intent_by_decision_key(h.ledger, "no-such-key") is None


# ---- b. position_cost -----------------------------------------------------------------------
def test_position_cost_buy_with_a_known_quote_fee(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="2", fee_currency="JPY")

    basis = views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    assert basis == views.CostBasis(D("10"), D("1002"), D("100.2"), 1000.0)


def test_position_cost_buy_with_a_known_base_fee_lowers_net_base(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="0.5", fee_currency="OAS")

    basis = views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    assert basis.net_base == D("9.5")
    assert basis.cost == D("1000")
    assert basis.avg_cost == D("105.2631578947368421052631579")
    assert basis.avg_cost == basis.cost / basis.net_base
    assert basis.opened_at == 1000.0


def test_position_cost_a_negative_known_fee_is_a_rebate(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="-1", fee_currency="JPY")

    basis = views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    assert basis.cost == D("999")
    assert basis.cost < D("1000")
    assert basis.net_base == D("10")
    assert basis.avg_cost == D("99.9")


def test_position_cost_an_unknown_fee_uses_max_fee_rate(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_status="unknown")

    basis = views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    assert basis.cost == D("1000") * D("1.0012") == D("1001.2")
    assert basis.net_base == D("10")
    assert basis.avg_cost == D("100.12")


def test_position_cost_an_unknown_fee_without_a_max_fee_rate_raises(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_status="unknown")

    with pytest.raises(ValueError, match="max_fee_rate is required"):
        views.position_cost(h.ledger, intent.position_id, None)


def test_position_cost_a_partial_sell_keeps_the_average_price(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="2", fee_currency="JPY")
    sell = _intent(h, key="s1", side="sell", amount="4", price="110", now=1100.0)
    _order(h, sell, order_id="o2", amount="4", price="110")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="sell", amount="4", price="110",
          executed_at=1100.0, fee_amount="1", fee_currency="JPY")

    basis = views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    assert basis.net_base == D("6")
    assert basis.avg_cost == D("100.2")               # exactly the average before the sell
    assert basis.cost == D("100.2") * D("6") == D("601.2")
    assert basis.opened_at == 1000.0


def test_position_cost_a_full_sell_zeroes_the_basis(h):
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    sell = _intent(h, key="s1", side="sell", amount="10", price="110", now=1100.0)
    _order(h, sell, order_id="o2", amount="10", price="110")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="sell", amount="10", price="110",
          executed_at=1100.0)

    basis = views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    assert basis.net_base == D("0") == views.position_net_base(h.ledger, intent.position_id)
    assert basis.cost == D("0")
    assert basis.avg_cost == D("0")
    assert basis.opened_at is None


def test_position_cost_a_second_buy_after_a_full_sell_resets_opened_at(h):
    first = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, first, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, first)

    sell = _intent(h, key="s1", side="sell", amount="10", price="110", now=2000.0)
    _order(h, sell, order_id="o2", amount="10", price="110")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="sell", amount="10", price="110",
          executed_at=2000.0)
    _finish(h, sell)

    second = _intent(h, key="b2", amount="5", price="120", now=3000.0)
    _order(h, second, order_id="o3", amount="5", price="120")
    _fill(h, fill_id="f3", order_id="o3", symbol="OAS/JPY", side="buy", amount="5", price="120",
          executed_at=3000.0)

    basis = views.position_cost(h.ledger, first.position_id, D("0.0012"))
    assert basis.opened_at == 3000.0
    assert basis.net_base == D("5")
    assert basis.cost == D("600")
    assert basis.avg_cost == D("120")


# ---- c. net_base agrees with position_net_base ----------------------------------------------
def test_position_cost_net_base_matches_position_net_base_on_the_same_fills(h):
    buy = _intent(h, key="b1")
    _order(h, buy, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="4", price="100",
          executed_at=1000.0, fee_amount="0.1", fee_currency="OAS")
    _fill(h, fill_id="f2", order_id="o1", symbol="OAS/JPY", side="buy", amount="6", price="102",
          executed_at=1001.0)
    sell = _intent(h, key="s1", side="sell", amount="5", price="110", now=1100.0)
    _order(h, sell, order_id="o2", amount="5", price="110")
    _fill(h, fill_id="f3", order_id="o2", symbol="OAS/JPY", side="sell", amount="5", price="110",
          executed_at=1100.0, fee_amount="0.05", fee_currency="OAS")

    basis = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert basis.net_base == D("4.85") == views.position_net_base(h.ledger, buy.position_id)


# ---- d. deployed_cost -----------------------------------------------------------------------
def test_deployed_cost_sums_only_open_positions(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY", "BTC/JPY"))

    oas = _intent(h, key="oas", symbol="OAS/JPY", amount="10", price="100")
    _order(h, oas, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="2", fee_currency="JPY")

    xym = _intent(h, key="xym", symbol="XYM/JPY", amount="5", price="200", now=2000.0)
    _order(h, xym, order_id="o2", amount="5", price="200")
    _fill(h, fill_id="f2", order_id="o2", symbol="XYM/JPY", side="buy", amount="5", price="200",
          executed_at=2000.0)

    btc = _intent(h, key="btc", symbol="BTC/JPY", amount="1", price="700", now=3000.0)
    _order(h, btc, order_id="o3", amount="1", price="700")
    _fill(h, fill_id="f3", order_id="o3", symbol="BTC/JPY", side="buy", amount="1", price="700",
          executed_at=3000.0)
    _finish(h, btc)
    h.ledger.close_position(h.lease, btc.position_id, now=4000.0)

    assert views.deployed_cost(h.ledger, {"OAS/JPY": D("0.001")}) == D("1002") + D("1000")
    assert views.deployed_cost(h.ledger, {}) == D("2002")   # no unknown fee: the rate is unused


def test_deployed_cost_of_an_empty_ledger_is_zero(harnesses):
    h = harnesses(opening=False)

    assert views.deployed_cost(h.ledger, {}) == D("0")


def test_deployed_cost_treats_a_symbol_missing_from_the_map_as_unknown(harnesses):
    h = harnesses()
    intent = _intent(h, key="b1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_status="unknown")

    with pytest.raises(ValueError, match="max_fee_rate is required"):
        views.deployed_cost(h.ledger, {})
    assert views.deployed_cost(h.ledger, {"OAS/JPY": D("0.0012")}) == D("1001.2")


# ---- e. events_of_kind ----------------------------------------------------------------------
def test_events_of_kind_returns_the_inner_payload_in_order(h):
    intent = _intent(h, key="k1")
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1000.0)
    rows = views.events_of_kind(h.ledger, "intent_state")
    assert len(rows) == 1
    first = rows[0]
    assert isinstance(first, views.EventRow)
    assert first.entity_type == "intent" and first.entity_id == intent.intent_id
    assert first.occurred_at == 1000.0
    assert isinstance(first.event_id, int)
    assert first.payload == {"from": "PLANNED", "to": "SUBMITTING", "reason": None}
    assert "uid" not in first.payload

    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="REJECTED", now=1001.0, reason_code="rejected")
    rows = views.events_of_kind(h.ledger, "intent_state")
    assert [row.payload["to"] for row in rows] == ["SUBMITTING", "REJECTED"]
    assert [row.occurred_at for row in rows] == [1000.0, 1001.0]
    assert [row.event_id for row in rows] == sorted(row.event_id for row in rows)
    assert rows[1].payload == {"from": "SUBMITTING", "to": "REJECTED", "reason": "rejected"}

    assert views.events_of_kind(h.ledger, "intent_state", after_event_id=rows[0].event_id) == [rows[1]]
    assert views.events_of_kind(h.ledger, "intent_state", limit=1) == [rows[0]]
    assert views.events_of_kind(h.ledger, "intent_state", after_event_id=rows[-1].event_id) == []
    assert views.events_of_kind(h.ledger, "no_such_kind") == []
    assert views.events_of_kind(h.ledger, "intent_state' OR '1'='1") == []


# ---- e2. open_positions ---------------------------------------------------------------------
def test_open_positions_lists_only_open_ones_in_opened_at_order(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    xym = _intent(h, key="xym", symbol="XYM/JPY", now=2000.0)
    oas = _intent(h, key="oas", symbol="OAS/JPY", now=1000.0)

    assert views.open_positions(h.ledger) == [(oas.position_id, "OAS/JPY"),
                                              (xym.position_id, "XYM/JPY")]
    assert [pid for pid, _ in views.open_positions(h.ledger)] == views.open_position_ids(h.ledger)

    _finish(h, oas)
    h.ledger.close_position(h.lease, oas.position_id, now=3000.0)
    assert views.open_positions(h.ledger) == [(xym.position_id, "XYM/JPY")]


# ---- f. none of the new views writes --------------------------------------------------------
def test_the_new_views_do_not_write(h):
    intent = _intent(h, key="k1")
    _order(h, intent, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="2", fee_currency="JPY")
    _fill(h, fill_id="f2", order_id="o1", symbol="OAS/JPY", side="buy", amount="2", price="100",
          executed_at=1001.0, fee_status="unknown")

    events_before = len(h.ledger.events(limit=10**6))
    fills_before = views.fill_count(h.ledger)

    views.intent_by_decision_key(h.ledger, "k1")
    views.intent_by_decision_key(h.ledger, "missing")
    views.position_cost(h.ledger, intent.position_id, D("0.0012"))
    views.deployed_cost(h.ledger, {"OAS/JPY": D("0.0012")})
    views.open_positions(h.ledger)
    views.events_of_kind(h.ledger, "intent_state")
    views.events_of_kind(h.ledger, "fill_recorded")

    assert len(h.ledger.events(limit=10**6)) == events_before
    assert views.fill_count(h.ledger) == fills_before


# ---- g. repeated buys and repeated partial sells --------------------------------------------
def test_position_cost_two_buys_at_different_prices_add_both_fees_and_keep_the_first_opened_at(h):
    first = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, first, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="2", fee_currency="JPY")
    _finish(h, first)

    second = _intent(h, key="b2", amount="5", price="130", now=1100.0)
    assert second.position_id == first.position_id
    _order(h, second, order_id="o2", amount="5", price="130")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="buy", amount="5", price="130",
          executed_at=1100.0, fee_amount="1", fee_currency="JPY")

    basis = views.position_cost(h.ledger, first.position_id, D("0.0012"))
    assert basis.net_base == D("15")
    assert basis.cost == D("1653")                    # 1000 + 2 + 650 + 1
    assert basis.avg_cost == D("110.2")               # 1653 / 15
    assert basis.opened_at == 1000.0                  # the first buy's executed_at
    assert basis == views.CostBasis(D("15"), D("1653"), D("110.2"), 1000.0)


def test_position_cost_a_buy_after_a_partial_sell_adds_to_the_remaining_cost(h):
    first = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, first, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="2", fee_currency="JPY")
    _finish(h, first)

    before_sell = views.position_cost(h.ledger, first.position_id, D("0.0012"))
    assert before_sell == views.CostBasis(D("10"), D("1002"), D("100.2"), 1000.0)

    sell = _intent(h, key="s1", side="sell", amount="4", price="110", now=1100.0)
    _order(h, sell, order_id="o2", amount="4", price="110")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="sell", amount="4", price="110",
          executed_at=1100.0, fee_amount="1", fee_currency="JPY")
    _finish(h, sell, now=1101.0)

    after_sell = views.position_cost(h.ledger, first.position_id, D("0.0012"))
    assert after_sell.net_base == D("6")
    assert after_sell.cost == D("601.2")              # 100.2 * 6, the quote fee is ignored
    assert after_sell.avg_cost == D("100.2")
    assert after_sell.opened_at == 1000.0
    assert after_sell == views.CostBasis(D("6"), D("601.2"), D("100.2"), 1000.0)

    second = _intent(h, key="b2", amount="4", price="120", now=1200.0)
    assert second.position_id == first.position_id
    _order(h, second, order_id="o3", amount="4", price="120")
    _fill(h, fill_id="f3", order_id="o3", symbol="OAS/JPY", side="buy", amount="4", price="120",
          executed_at=1200.0)

    basis = views.position_cost(h.ledger, first.position_id, D("0.0012"))
    assert basis.net_base == D("10")
    assert basis.cost == D("1081.2")                  # 601.2 + 4 * 120
    assert basis.avg_cost == D("108.12")              # 1081.2 / 10
    assert basis.opened_at == 1000.0                  # still the first buy's executed_at
    assert basis == views.CostBasis(D("10"), D("1081.2"), D("108.12"), 1000.0)


def test_position_cost_two_partial_sells_keep_the_non_terminating_average(h):
    buy = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, buy, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="0.5", fee_currency="OAS")
    _finish(h, buy)

    before_first = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert before_first.net_base == D("9.5")
    assert before_first.cost == D("1000")             # the base fee lowers only net_base
    assert before_first.avg_cost == D("105.2631578947368421052631579")   # 1000 / 9.5
    assert before_first.opened_at == 1000.0

    first_sell = _intent(h, key="s1", side="sell", amount="3", price="110", now=1100.0)
    _order(h, first_sell, order_id="o2", amount="3", price="110")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="sell", amount="3", price="110",
          executed_at=1100.0, fee_amount="1", fee_currency="JPY")
    _finish(h, first_sell, now=1101.0)

    after_first = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert after_first.net_base == D("6.5")
    assert after_first.avg_cost == before_first.avg_cost == D("105.2631578947368421052631579")
    assert after_first.cost == after_first.avg_cost * after_first.net_base
    assert after_first.cost == D("684.2105263157894736842105264")
    assert after_first.opened_at == 1000.0

    before_second = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert before_second == after_first

    second_sell = _intent(h, key="s2", side="sell", amount="2", price="110", now=1200.0)
    _order(h, second_sell, order_id="o3", amount="2", price="110")
    _fill(h, fill_id="f3", order_id="o3", symbol="OAS/JPY", side="sell", amount="2", price="110",
          executed_at=1200.0, fee_amount="1", fee_currency="JPY")

    after_second = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert after_second.net_base == D("4.5")
    assert after_second.avg_cost == before_second.avg_cost == D("105.2631578947368421052631579")
    assert after_second.cost == after_second.avg_cost * after_second.net_base
    assert after_second.cost == D("473.6842105263157894736842106")
    assert after_second.opened_at == 1000.0


# ---- h. fills at one executed_at: numeric trade ids, then text ids ---------------------------
def test_position_cost_orders_same_timestamp_fills_by_numeric_trade_id(h):
    first = _intent(h, key="b1", amount="10", price="100", now=999.0)
    _order(h, first, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="OAS/JPY:8", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=999.0)
    _finish(h, first)

    second = _intent(h, key="b2", amount="10", price="200", now=1000.0)
    assert second.position_id == first.position_id
    _order(h, second, order_id="o2", amount="10", price="200")
    _fill(h, fill_id="OAS/JPY:9", order_id="o2", symbol="OAS/JPY", side="buy", amount="10", price="200",
          executed_at=1000.0)
    _finish(h, second)

    sell = _intent(h, key="s1", side="sell", amount="10", price="150", now=1001.0)
    _order(h, sell, order_id="o3", amount="10", price="150")
    _fill(h, fill_id="OAS/JPY:10", order_id="o3", symbol="OAS/JPY", side="sell", amount="10", price="150",
          executed_at=1000.0)

    basis = views.position_cost(h.ledger, first.position_id, D("0.0012"))
    assert (basis.net_base, basis.cost, basis.avg_cost, basis.opened_at) == (
        D("10"), D("1500"), D("150"), 999.0)


def test_position_cost_orders_a_99_to_100_crossing_numerically(h):
    buy = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, buy, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="OAS/JPY:99", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, buy)

    sell = _intent(h, key="s1", side="sell", amount="4", price="120", now=1001.0)
    _order(h, sell, order_id="o2", amount="4", price="120")
    _fill(h, fill_id="OAS/JPY:100", order_id="o2", symbol="OAS/JPY", side="sell", amount="4", price="120",
          executed_at=1000.0)

    basis = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert (basis.net_base, basis.cost, basis.avg_cost, basis.opened_at) == (
        D("6"), D("600"), D("100"), 1000.0)


def test_position_cost_orders_ids_without_a_numeric_tail_by_text(h):
    buy = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, buy, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="fa", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, buy)

    sell = _intent(h, key="s1", side="sell", amount="4", price="120", now=1001.0)
    _order(h, sell, order_id="o2", amount="4", price="120")
    _fill(h, fill_id="fb", order_id="o2", symbol="OAS/JPY", side="sell", amount="4", price="120",
          executed_at=1000.0)

    basis = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert (basis.net_base, basis.cost, basis.avg_cost, basis.opened_at) == (
        D("6"), D("600"), D("100"), 1000.0)


def test_position_cost_orders_ids_without_a_numeric_tail_even_when_the_sell_is_first(h):
    buy = _intent(h, key="b1", amount="10", price="100", now=1000.0)
    _order(h, buy, order_id="o1", amount="10", price="100")
    _fill(h, fill_id="fb", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, buy)

    sell = _intent(h, key="s1", side="sell", amount="4", price="120", now=1001.0)
    _order(h, sell, order_id="o2", amount="4", price="120")
    _fill(h, fill_id="fa", order_id="o2", symbol="OAS/JPY", side="sell", amount="4", price="120",
          executed_at=1000.0)

    basis = views.position_cost(h.ledger, buy.position_id, D("0.0012"))
    assert (basis.net_base, basis.cost, basis.avg_cost, basis.opened_at) == (
        D("6"), D("1000"), D("166.6666666666666666666666667"), 1000.0)


# ---- L4b-5: sizing/reconcile views ----------------------------------------------------------
def _to_acked(h, intent, *, now=1001.0, order_id=None):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=now)
    return h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                                      to_state="ACKED", now=now, exchange_order_id=order_id)


def _rules(symbol="OAS/JPY", *, fee="0.001", quote_unit="1"):
    from test_trading_live_support import market_rules
    return market_rules(symbol, max_fee_rate=None if fee is None else D(fee), quote_unit=D(quote_unit))


def _observe(h, intent, *, order_id, amount, price, status="FULLY_FILLED", executed="0"):
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(exchange_order_id=order_id, intent_id=intent.intent_id, symbol=intent.symbol,
                         side=intent.side, order_type="limit", amount=amount, status=status,
                         executed_amount=executed, remaining_amount=str(D(amount) - D(executed)),
                         price=price),
        now=1000.0)


def test_open_position_rows_lists_open_positions_with_entry_digests(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY", "BTC/JPY", "DDD/JPY"))
    xym = _intent(h, key="xym", symbol="XYM/JPY", now=2000.0)
    oas = _intent(h, key="oas", symbol="OAS/JPY", now=1000.0)
    btc = _intent(h, key="btc", symbol="BTC/JPY", now=1500.0)
    _finish(h, btc)
    h.ledger.close_position(h.lease, btc.position_id, now=2500.0)

    dust = _intent(h, key="dust", symbol="DDD/JPY", amount="0.5", price="100", now=1200.0)
    _observe(h, dust, order_id="o-dust", amount="0.5", price="100", executed="0.5")
    _fill(h, fill_id="f-dust", order_id="o-dust", symbol="DDD/JPY", side="buy", amount="0.5",
          price="100", executed_at=1200.0)
    _finish(h, dust)
    h.ledger.mark_residual_inventory(h.lease, dust.position_id, now=2600.0,
                                     min_amount=D("1"), amount_step=D("1"))

    rows = views.open_position_rows(h.ledger)
    assert rows == [
        views.PositionRow(oas.position_id, "OAS/JPY", h.ps.policy_for("OAS/JPY").digest, 1000.0),
        views.PositionRow(xym.position_id, "XYM/JPY", h.ps.policy_for("XYM/JPY").digest, 2000.0),
    ]
    assert rows[0].entry_policy_digest != rows[1].entry_policy_digest
    assert [row.position_id for row in rows] == views.open_position_ids(h.ledger)


def test_open_intent_symbols_only_open_intents_of_that_side(harnesses):
    h = harnesses(symbols=("AAA/JPY", "BBB/JPY", "CCC/JPY", "DDD/JPY", "EEE/JPY"))
    _intent(h, key="a", symbol="AAA/JPY")
    _to_acked(h, _intent(h, key="b", symbol="BBB/JPY"))
    _finish(h, _intent(h, key="c", symbol="CCC/JPY"))

    d_buy = _intent(h, key="d-buy", symbol="DDD/JPY")
    _finish(h, d_buy)
    _to_acked(h, _intent(h, key="d-sell", side="sell", symbol="DDD/JPY", now=1100.0), now=1101.0)

    e_buy = _intent(h, key="e-buy", symbol="EEE/JPY")
    _finish(h, e_buy)
    e_sell = _to_acked(h, _intent(h, key="e-sell", side="sell", symbol="EEE/JPY", now=1100.0),
                       now=1101.0)
    h.ledger.transition_intent(h.lease, e_sell.intent_id, expected_state="ACKED",
                               to_state="CANCELED", now=1102.0)

    assert views.open_intent_symbols(h.ledger, "buy") == frozenset({"AAA/JPY", "BBB/JPY"})
    assert views.open_intent_symbols(h.ledger, "sell") == frozenset({"DDD/JPY"})
    with pytest.raises(ValueError):
        views.open_intent_symbols(h.ledger, "hold")


def test_decision_keys_uses_an_exact_prefix_not_like(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY", "BTC/JPY"))
    _intent(h, key="live1:snapback_harvest_v25:x", symbol="OAS/JPY")
    _intent(h, key="live1:snapbackXharvest_v25:y", symbol="XYM/JPY")
    _intent(h, key="live1:other:z", symbol="BTC/JPY")

    assert views.decision_keys(h.ledger, "live1:snapback_harvest") == [
        "live1:snapback_harvest_v25:x"
    ]
    assert views.decision_keys(h.ledger, "live1:%") == []
    with pytest.raises(ValueError):
        views.decision_keys(h.ledger, "")
    everything = views.decision_keys(h.ledger, "live1:")
    assert everything == [
        "live1:other:z",
        "live1:snapbackXharvest_v25:y",
        "live1:snapback_harvest_v25:x",
    ]
    assert everything == sorted(everything)


def test_capital_quote_sums_deposits_withdrawals_and_signed_adjustments(harnesses):
    from docich.trading.live.ledger import Cashflow

    h = harnesses(opening=False)
    assert views.capital_quote(h.ledger) == D("0")
    assert views.capital_quote(h.ledger, "BTC") == D("0")

    h.deposit("JPY", "1000")
    h.deposit("BTC", "5")
    h.ledger.record_cashflow(
        h.lease, Cashflow("w1", "withdrawal", "JPY", "200", "owner_manual", 1001.0), now=1001.0)
    h.ledger.record_cashflow(
        h.lease, Cashflow("a1", "adjustment", "JPY", "-50", "owner_manual", 1002.0), now=1002.0)
    h.ledger.record_cashflow(
        h.lease, Cashflow("a2", "adjustment", "JPY", "10", "owner_manual", 1003.0), now=1003.0)

    assert views.capital_quote(h.ledger) == D("760")
    assert views.capital_quote(h.ledger, "JPY") == D("760")
    assert views.capital_quote(h.ledger, "BTC") == D("5")
    assert views.capital_quote(h.ledger, "ETH") == D("0")


def test_open_buy_exposure_acked_buy_without_fills(h):
    intent = _intent(h, key="acked")
    _to_acked(h, intent)

    assert views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules()}) == views.OpenBuyExposure(
        D("1000"), D("1002.000"))


def test_open_buy_exposure_partially_filled_buy_uses_the_remaining_amount(h):
    intent = _intent(h, key="partial")
    _observe(h, intent, order_id="o1", amount="10", price="100", status="PARTIALLY_FILLED",
             executed="4")
    _to_acked(h, intent, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="4", price="100",
          executed_at=1000.0)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state="PARTIALLY_FILLED", now=1002.0)

    assert h.ledger.get_intent(intent.intent_id).exchange_order_id == "o1"
    assert views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules()}) == views.OpenBuyExposure(
        D("600"), D("601.6"))


def test_open_buy_exposure_planned_buy_reserves_the_full_amount(h):
    _intent(h, key="planned")

    assert views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules()}) == views.OpenBuyExposure(
        D("1000"), D("1002"))


def test_open_buy_exposure_sums_open_buys_on_two_symbols(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    oas = _intent(h, key="oas", symbol="OAS/JPY")
    _to_acked(h, oas)
    _intent(h, key="xym", symbol="XYM/JPY")

    rules = {"OAS/JPY": _rules("OAS/JPY"),
             "XYM/JPY": _rules("XYM/JPY", fee="0.002", quote_unit="2")}
    assert views.open_buy_exposure(h.ledger, rules) == views.OpenBuyExposure(
        D("2000"), D("2006"))


def test_open_buy_exposure_ignores_terminal_and_sell_intents(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY", "BTC/JPY"))
    _finish(h, _intent(h, key="filled-buy", symbol="OAS/JPY"))

    canceled = _intent(h, key="canceled-buy", symbol="XYM/JPY")
    _to_acked(h, canceled)
    h.ledger.transition_intent(h.lease, canceled.intent_id, expected_state="ACKED",
                               to_state="CANCELED", now=1002.0)

    btc_buy = _intent(h, key="btc-buy", symbol="BTC/JPY")
    _finish(h, btc_buy)
    _to_acked(h, _intent(h, key="btc-sell", side="sell", symbol="BTC/JPY", now=1100.0),
              now=1101.0)

    rules = {symbol: _rules(symbol) for symbol in ("OAS/JPY", "XYM/JPY", "BTC/JPY")}
    assert views.open_buy_exposure(h.ledger, rules) == views.OpenBuyExposure(D("0"), D("0"))


def test_open_buy_exposure_requires_a_known_fee_rate(h):
    _to_acked(h, _intent(h, key="acked"))

    with pytest.raises(ValueError, match="fee rate unknown for OAS/JPY"):
        views.open_buy_exposure(h.ledger, {})
    with pytest.raises(ValueError, match="fee rate unknown for OAS/JPY"):
        views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules(fee=None)})


def test_open_buy_exposure_of_an_empty_ledger_is_zero(h):
    assert views.open_buy_exposure(h.ledger, {}) == views.OpenBuyExposure(D("0"), D("0"))


def test_the_l4b5_views_do_not_write(h):
    intent = _intent(h, key="snapback_harvest_v25:k1")
    _observe(h, intent, order_id="o1", amount="10", price="100", status="PARTIALLY_FILLED",
             executed="4")
    _to_acked(h, intent, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="4", price="100",
          executed_at=1000.0)

    events_before = h.ledger.events(limit=10**6)
    fills_before = views.fill_count(h.ledger)
    intent_before = views.intent_by_decision_key(h.ledger, intent.decision_key)

    rows = views.open_position_rows(h.ledger)
    assert rows and rows[0].position_id == intent.position_id
    assert views.open_intent_symbols(h.ledger, "buy") == frozenset({"OAS/JPY"})
    assert views.decision_keys(h.ledger, "snapback_harvest_v25") == ["snapback_harvest_v25:k1"]
    assert views.capital_quote(h.ledger) == D("10000")
    assert views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules()}) == views.OpenBuyExposure(
        D("600"), D("601.6"))

    assert h.ledger.events(limit=10**6) == events_before
    assert views.fill_count(h.ledger) == fills_before
    assert views.intent_by_decision_key(h.ledger, intent.decision_key) == intent_before


# ---- L4b-5 mutation gaps: unknown submission, negative fee rate, opened_at order -------------
def test_open_intent_symbols_and_exposure_count_an_unknown_submission(harnesses):
    h = harnesses(symbols=("AAA/JPY", "BBB/JPY", "CCC/JPY"))
    rules = {symbol: _rules(symbol) for symbol in ("AAA/JPY", "BBB/JPY", "CCC/JPY")}

    unknown = _intent(h, key="unknown-buy", symbol="AAA/JPY")
    h.ledger.transition_intent(h.lease, unknown.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1001.0)
    h.ledger.transition_intent(h.lease, unknown.intent_id, expected_state="SUBMITTING",
                               to_state="UNKNOWN_SUBMISSION", now=1002.0)

    assert h.ledger.get_intent(unknown.intent_id).state == "UNKNOWN_SUBMISSION"
    assert "AAA/JPY" in views.open_intent_symbols(h.ledger, "buy")
    assert views.open_buy_exposure(h.ledger, rules) == views.OpenBuyExposure(D("1000"), D("1002"))

    opened = _intent(h, key="opened-buy", symbol="BBB/JPY")
    _finish(h, opened)
    sell = _intent(h, key="unknown-sell", side="sell", symbol="BBB/JPY", now=1100.0)
    h.ledger.transition_intent(h.lease, sell.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1101.0)
    h.ledger.transition_intent(h.lease, sell.intent_id, expected_state="SUBMITTING",
                               to_state="UNKNOWN_SUBMISSION", now=1102.0)

    assert h.ledger.get_intent(sell.intent_id).state == "UNKNOWN_SUBMISSION"
    assert views.open_intent_symbols(h.ledger, "sell") == frozenset({"BBB/JPY"})
    assert views.open_buy_exposure(h.ledger, rules) == views.OpenBuyExposure(D("1000"), D("1002"))

    submitting = _intent(h, key="submitting-buy", symbol="CCC/JPY", now=1200.0)
    h.ledger.transition_intent(h.lease, submitting.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=1201.0)

    assert views.open_intent_symbols(h.ledger, "buy") == frozenset({"AAA/JPY", "CCC/JPY"})
    assert views.open_buy_exposure(h.ledger, rules) == views.OpenBuyExposure(D("2000"), D("2004"))


def test_open_buy_exposure_uses_the_absolute_value_of_a_negative_fee_rate(h):
    _to_acked(h, _intent(h, key="acked"))

    negative = views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules(fee="-0.001", quote_unit="1")})
    assert negative == views.OpenBuyExposure(D("1000"), D("1002"))
    assert negative == views.open_buy_exposure(h.ledger, {"OAS/JPY": _rules(fee="0.001")})


def test_open_position_rows_are_ordered_by_opened_at_not_by_id(harnesses):
    symbols = ("AAA/JPY", "BBB/JPY", "CCC/JPY", "DDD/JPY",
               "EEE/JPY", "FFF/JPY", "GGG/JPY", "HHH/JPY")
    h = harnesses(symbols=symbols)

    created = []
    for index, symbol in enumerate(symbols):
        now = 1008.0 - index                      # strictly descending in creation order
        created.append((_intent(h, key=f"p{index}", symbol=symbol, now=now), symbol, now))

    rows = views.open_position_rows(h.ledger)
    assert rows == [views.PositionRow(intent.position_id, symbol, h.ps.policy_for(symbol).digest, now)
                    for intent, symbol, now in reversed(created)]
    assert [row.opened_at for row in rows] == [1001.0, 1002.0, 1003.0, 1004.0,
                                               1005.0, 1006.0, 1007.0, 1008.0]
    assert [row.symbol for row in rows] == list(reversed(symbols))
