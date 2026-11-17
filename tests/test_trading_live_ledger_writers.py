"""L4b writers: residual inventory and live events (Issue #3623).  No network."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from docich.trading.live import views
from docich.trading.live.canonical import canonical_json
from docich.trading.live.ledger import (
    LIVE_EVENT_KINDS,
    FillRecord,
    LiveLedgerError,
    OrderObservation,
)
from docich.trading.live.lease import LeaseLost
from test_trading_live_support import Harness, Workspace, block_network


NOW = 1000.0


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
def _intent(h, *, key, side="buy", symbol="OAS/JPY", amount="10", price="100", now=NOW):
    return h.ledger.create_intent(
        h.lease, h.req(symbol=symbol, side=side, key=key, amount=amount, price=price), now=now)


def _order(h, intent, *, order_id, amount, price):
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(exchange_order_id=order_id, intent_id=intent.intent_id, symbol=intent.symbol,
                         side=intent.side, order_type="limit", amount=amount, status="FULLY_FILLED",
                         executed_amount=amount, remaining_amount="0", price=price),
        now=NOW)


def _fill(h, *, fill_id, order_id, symbol, side, amount, price, executed_at=NOW, fee_status="known",
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


def _finish(h, intent, *, state="FILLED", now=NOW + 1):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state=state, now=now)


def _dust_position(h, *, key="dust", amount="0.0001", fee="0.00005", fee_currency="OAS"):
    intent = _intent(h, key=key, amount=amount, price="100")
    _order(h, intent, order_id=f"o-{key}", amount=amount, price="100")
    _fill(h, fill_id=f"f-{key}", order_id=f"o-{key}", symbol=intent.symbol, side="buy", amount=amount,
          price="100", fee_amount=fee, fee_currency=fee_currency)
    _finish(h, intent)
    return intent


def _round_trip(h, *, key, buy_amount, sell_amount):
    buy = _intent(h, key=f"{key}-buy", amount=buy_amount, now=NOW)
    _order(h, buy, order_id=f"o-{key}-buy", amount=buy_amount, price="100")
    _fill(h, fill_id=f"f-{key}-buy", order_id=f"o-{key}-buy", symbol=buy.symbol, side="buy",
          amount=buy_amount, price="100")
    _finish(h, buy, now=NOW + 1)

    sell = _intent(h, key=f"{key}-sell", side="sell", amount=sell_amount, now=NOW + 100)
    _order(h, sell, order_id=f"o-{key}-sell", amount=sell_amount, price="100")
    _fill(h, fill_id=f"f-{key}-sell", order_id=f"o-{key}-sell", symbol=sell.symbol, side="sell",
          amount=sell_amount, price="100", executed_at=NOW + 100)
    _finish(h, sell, now=NOW + 101)
    return buy


def _position(ledger, position_id):
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT status, closed_at FROM positions WHERE position_id=?",
                           (position_id,)).fetchone()


def _event_count(ledger):
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def _mark(h, position_id, *, now=1200.0, min_amount=D("0.0001"), amount_step=D("0.0001"), lease=None):
    h.ledger.mark_residual_inventory(
        h.lease if lease is None else lease, position_id, now=now,
        min_amount=min_amount, amount_step=amount_step)


# ---- a. mark_residual_inventory ---------------------------------------------------------------
def test_mark_residual_inventory_marks_dust_and_emits_the_exact_event(h):
    intent = _dust_position(h)
    assert views.position_net_base(h.ledger, intent.position_id) == D("0.00005")
    before = _event_count(h.ledger)

    _mark(h, intent.position_id, now=1234.5)

    assert _position(h.ledger, intent.position_id) == ("residual_inventory", 1234.5)
    rows = views.events_of_kind(h.ledger, "position_residual")
    assert len(rows) == 1
    assert rows[0].entity_type == "position"
    assert rows[0].entity_id == intent.position_id
    assert rows[0].occurred_at == 1234.5
    assert rows[0].payload == {"net_base": "0.00005"}
    assert _event_count(h.ledger) == before + 1
    assert h.ledger.verify_event_chain()


def test_mark_residual_inventory_refuses_a_net_exactly_at_the_minimum(h):
    intent = _dust_position(h, fee="0", fee_currency="OAS")
    assert views.position_net_base(h.ledger, intent.position_id) == D("0.0001")
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    with pytest.raises(LiveLedgerError, match="^position is not residual inventory$"):
        _mark(h, intent.position_id)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_a_zero_net(h):
    intent = _round_trip(h, key="zero", buy_amount="10", sell_amount="10")
    assert views.position_net_base(h.ledger, intent.position_id) == D("0")
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    with pytest.raises(LiveLedgerError, match="^position is not residual inventory$"):
        _mark(h, intent.position_id)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_a_negative_net(h):
    intent = _round_trip(h, key="negative", buy_amount="5", sell_amount="6")
    assert views.position_net_base(h.ledger, intent.position_id) == D("-1")
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    with pytest.raises(LiveLedgerError, match="^position is not residual inventory$"):
        _mark(h, intent.position_id)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_a_position_with_an_open_intent(h):
    intent = _intent(h, key="still-open")
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    with pytest.raises(LiveLedgerError, match="^position still has open intents$"):
        _mark(h, intent.position_id)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_a_closed_position(h):
    intent = _dust_position(h)
    h.ledger.close_position(h.lease, intent.position_id, now=1100.0)
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)
    assert status_before == ("closed", 1100.0)

    with pytest.raises(LiveLedgerError, match="^position is not open$"):
        _mark(h, intent.position_id)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_an_already_residual_position(h):
    intent = _dust_position(h)
    _mark(h, intent.position_id, now=1100.0)
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    with pytest.raises(LiveLedgerError, match="^position is not open$"):
        _mark(h, intent.position_id, now=1200.0)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_an_unknown_position(h):
    events_before = _event_count(h.ledger)

    with pytest.raises(LiveLedgerError, match="^position is not open$"):
        _mark(h, "no-such-position")

    assert _event_count(h.ledger) == events_before


def test_mark_residual_inventory_refuses_a_stale_or_foreign_lease(h):
    intent = _dust_position(h)
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    for bad in (replace(h.lease, epoch=h.lease.epoch + 1),
                replace(h.lease, holder_id="f" * 32)):
        with pytest.raises(LeaseLost, match="^writer lease lost$"):
            _mark(h, intent.position_id, lease=bad)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


@pytest.mark.parametrize("min_amount, amount_step", [
    (D("0"), D("0.0001")),
    (D("-0.0001"), D("0.0001")),
    (D("0.0001"), D("0")),
    (D("0.0001"), D("-0.0001")),
    (D("NaN"), D("0.0001")),
    (D("Infinity"), D("0.0001")),
    (0.0001, D("0.0001")),
    (D("0.0001"), 0.0001),
    ("0.0001", D("0.0001")),
])
def test_mark_residual_inventory_refuses_invalid_min_amount_and_step(h, min_amount, amount_step):
    intent = _dust_position(h)
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    with pytest.raises(ValueError, match="^min_amount and amount_step must be positive decimals$"):
        _mark(h, intent.position_id, min_amount=min_amount, amount_step=amount_step)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before


# ---- b. a residual position no longer occupies the open slot ---------------------------------
def test_a_residual_position_frees_the_one_open_position_slot(h):
    dust = _dust_position(h)
    assert views.open_position_ids(h.ledger) == [dust.position_id]

    _mark(h, dust.position_id)
    assert views.open_position_ids(h.ledger) == []

    new = _intent(h, key="after-residual", amount="1", now=NOW + 200)
    assert new.position_id != dust.position_id
    assert views.open_position_ids(h.ledger) == [new.position_id]
    with h.ledger.read_cursor() as cur:
        rows = cur.execute("SELECT position_id, status FROM positions ORDER BY opened_at, position_id").fetchall()
    assert rows == [(dust.position_id, "residual_inventory"), (new.position_id, "open")]


# ---- c. record_live_event ---------------------------------------------------------------------
def test_live_event_kinds_include_the_three_design_kinds():
    assert LIVE_EVENT_KINDS >= frozenset({"live_decision", "shadow_fill", "worker_state"})


@pytest.mark.parametrize("kind", sorted(LIVE_EVENT_KINDS))
def test_each_allowed_live_event_kind_is_recorded(h, kind):
    payload = {"decision": "hold", "score": 7, "enabled": True,
               "detail": {"levels": [1, 2, None, False], "note": "n"}}
    before = _event_count(h.ledger)

    h.ledger.record_live_event(h.lease, kind, payload, now=1500.25)

    rows = views.events_of_kind(h.ledger, kind)
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_type == "live"
    assert row.entity_id is None
    assert row.occurred_at == 1500.25
    assert row.payload == payload
    assert _event_count(h.ledger) == before + 1
    assert h.ledger.verify_event_chain()


@pytest.mark.parametrize("kind", ["intent_state", "fill_recorded", "safety_trip", "position_closed",
                                  "position_residual", ""])
def test_record_live_event_refuses_a_forbidden_kind(h, kind):
    before = _event_count(h.ledger)

    with pytest.raises(ValueError, match="^kind is not an allowed live event kind$"):
        h.ledger.record_live_event(h.lease, kind, {"a": "b"}, now=NOW)

    assert _event_count(h.ledger) == before
    assert h.ledger.verify_event_chain()


@pytest.mark.parametrize("payload", [
    {"value": 1.5},
    {"value": D("1.5")},
    {"nested": {"value": 1.5}},
    {"nested": {"value": D("1.5")}},
    {"nested": [1, 2.5]},
    {"nested": [D("2.5")]},
])
def test_record_live_event_refuses_float_and_decimal_values(h, payload):
    before = _event_count(h.ledger)

    with pytest.raises(ValueError, match="^numbers must be passed as strings or integers$"):
        h.ledger.record_live_event(h.lease, "live_decision", payload, now=NOW)

    assert _event_count(h.ledger) == before


def test_record_live_event_refuses_a_payload_over_16384_bytes(h):
    fixed = len(canonical_json({"text": ""}))
    at_limit = {"text": "a" * (16384 - fixed)}
    over = {"text": "a" * (16384 - fixed + 1)}
    assert len(canonical_json(at_limit)) == 16384
    assert len(canonical_json(over)) == 16385
    before = _event_count(h.ledger)

    h.ledger.record_live_event(h.lease, "worker_state", at_limit, now=NOW)
    assert _event_count(h.ledger) == before + 1
    with pytest.raises(ValueError, match="^payload exceeds 16384 bytes$"):
        h.ledger.record_live_event(h.lease, "worker_state", over, now=NOW)

    assert _event_count(h.ledger) == before + 1
    assert views.events_of_kind(h.ledger, "worker_state")[0].payload == at_limit
    assert h.ledger.verify_event_chain()


@pytest.mark.parametrize("payload", [{1: "x"}, {"nested": {1: "x"}}])
def test_record_live_event_refuses_a_non_str_key(h, payload):
    before = _event_count(h.ledger)

    with pytest.raises(ValueError, match="^payload keys must be strings$"):
        h.ledger.record_live_event(h.lease, "live_decision", payload, now=NOW)

    assert _event_count(h.ledger) == before


@pytest.mark.parametrize("payload", [None, [], "x", 1])
def test_record_live_event_refuses_a_non_dict_payload(h, payload):
    before = _event_count(h.ledger)

    with pytest.raises(ValueError, match="^payload must be a dict$"):
        h.ledger.record_live_event(h.lease, "live_decision", payload, now=NOW)

    assert _event_count(h.ledger) == before


def test_record_live_event_refuses_an_unsupported_value_type(h):
    before = _event_count(h.ledger)

    with pytest.raises(ValueError, match="^payload values must be strings, integers, null, "
                                         "dictionaries or lists$"):
        h.ledger.record_live_event(h.lease, "live_decision", {"value": (1, 2)}, now=NOW)

    assert _event_count(h.ledger) == before


def test_record_live_event_refuses_a_stale_lease(h):
    before = _event_count(h.ledger)

    with pytest.raises(LeaseLost, match="^writer lease lost$"):
        h.ledger.record_live_event(replace(h.lease, epoch=h.lease.epoch + 1), "live_decision",
                                   {"a": 1}, now=NOW)

    assert _event_count(h.ledger) == before


# ---- d. no writer lease, no write -------------------------------------------------------------
def test_both_writers_refuse_a_none_lease_without_writing(h):
    intent = _dust_position(h)
    status_before, events_before = _position(h.ledger, intent.position_id), _event_count(h.ledger)

    # The lease check comes first: the other arguments are deliberately invalid here.
    with pytest.raises(ValueError, match="^a writer lease is required$"):
        h.ledger.mark_residual_inventory(None, intent.position_id, now=NOW,
                                         min_amount=D("0"), amount_step=D("0"))
    with pytest.raises(ValueError, match="^a writer lease is required$"):
        h.ledger.record_live_event(None, "not-a-live-kind", {"value": 1.5}, now=NOW)

    assert _position(h.ledger, intent.position_id) == status_before
    assert _event_count(h.ledger) == events_before
