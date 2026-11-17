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
