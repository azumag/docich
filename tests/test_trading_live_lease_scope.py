"""LIVE writer lease scope (Issue #4588, parent #3623): which writes need the lease.

The ledger has exactly two transaction kinds: ``_tx`` verifies the writer lease
inside ``BEGIN IMMEDIATE`` and refuses ``lease=None``; ``_tx_admin`` is the
management path used only by schema bootstrap, the lease-row claim, a safety
trip (stopping must always be possible, including right after the lease was
lost) and a human-only trip clear.  Every other public writer must refuse a
missing lease before doing any work and must fail closed under a stale,
foreign, released or taken-over lease.  These tests pin that contract with a
whole-database snapshot: a refused call changes nothing at all.

Synthetic data only: the mock exchange stands in for bitbank, there is no
network, no real API key and nothing is ever submitted for real.
"""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from docich.trading.live.enable import EnableResult
from docich.trading.live.lease import LeaseLost, release_writer_lease
from docich.trading.live.ledger import Cashflow, FillRecord, OrderObservation
from docich.trading.live.safety import SafetyReason
from test_trading_live_support import Harness, Workspace, block_network

NOW = 1000.0

_TABLES = ("meta", "events", "policies", "policy_sets", "policy_activations", "positions",
           "intents", "orders", "fills", "cashflows", "writer_lease", "safety_trips")


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


# ---- helpers ---------------------------------------------------------------------
def _snapshot(ledger):
    """Every row of every table, order-insensitive: the no-write oracle."""
    with ledger.read_cursor() as cur:
        return {name: sorted(repr(row) for row in cur.execute(f"SELECT * FROM {name}"))
                for name in _TABLES}


def _enable(h):
    """A gate result that would pass every check except the lease."""
    return EnableResult(True, (), mode="trade", file_sha256="a" * 64,
                        policy_set_digest=h.ps.digest)


def _seed(h):
    """One PLANNED intent (its position) + one order row, through public writers."""
    intent = h.ledger.create_intent(h.lease, h.req(key="seed"), now=NOW)
    obs = OrderObservation(
        exchange_order_id="o-seed", intent_id=intent.intent_id, symbol=intent.symbol,
        side="buy", order_type="limit", amount="10", price="100", status="UNFILLED",
        executed_amount="0", remaining_amount="10")
    h.ledger.record_order_observation(h.lease, obs, now=NOW)
    fill = FillRecord(
        fill_id="f-seed", exchange_order_id="o-seed", symbol="OAS/JPY", side="buy",
        amount="10", price="100", quote_notional="1000", executed_at=NOW,
        fee_status="known", fee_amount="0", fee_currency="JPY")
    flow = Cashflow("cf-seed", "deposit", "JPY", "100", "owner_manual", NOW)
    return intent, obs, fill, flow


def _writers(h, intent, obs, fill, flow):
    """name -> call(lease): every public writer that goes through ``_tx``.

    The arguments are chosen so that only the lease check can refuse the call:
    nothing else about them is invalid.
    """
    ledger, ps, enable = h.ledger, h.ps, _enable(h)
    return {
        "release_lease_row": lambda lease: ledger.release_lease_row(lease, now=NOW),
        "store_policy_set": lambda lease: ledger.store_policy_set(lease, ps, now=NOW),
        "record_owner_activation": lambda lease: ledger.record_owner_activation(
            lease, ps, operator="owner", note="scope test", now=NOW),
        "record_activation": lambda lease: ledger.record_activation(
            lease, ps, reason="rollback", evidence_digest="e" * 64, now=NOW),
        "create_intent": lambda lease: ledger.create_intent(
            lease, h.req(key=f"lease-probe-{NOW}"), now=NOW),
        "mark_submitting": lambda lease: ledger.mark_submitting(
            lease, intent.intent_id, now=NOW),
        "transition_intent": lambda lease: ledger.transition_intent(
            lease, intent.intent_id, expected_state="PLANNED", to_state="ABANDONED", now=NOW),
        "mark_in_flight_unknown": lambda lease: ledger.mark_in_flight_unknown(lease, now=NOW),
        "close_position": lambda lease: ledger.close_position(lease, intent.position_id, now=NOW),
        "mark_residual_inventory": lambda lease: ledger.mark_residual_inventory(
            lease, intent.position_id, now=NOW, min_amount=D("0.0001"), amount_step=D("0.0001")),
        "record_live_event": lambda lease: ledger.record_live_event(
            lease, "live_decision", {"probe": 1}, now=NOW),
        "record_order_observation": lambda lease: ledger.record_order_observation(
            lease, obs, now=NOW),
        "record_fill": lambda lease: ledger.record_fill(lease, fill, now=NOW),
        "record_cashflow": lambda lease: ledger.record_cashflow(lease, flow, now=NOW),
        "record_enable_observation": lambda lease: ledger.record_enable_observation(
            lease, enable, now=NOW),
    }


_WRITER_NAMES = sorted(
    "release_lease_row store_policy_set record_owner_activation record_activation "
    "create_intent mark_submitting transition_intent mark_in_flight_unknown "
    "close_position mark_residual_inventory record_live_event "
    "record_order_observation record_fill record_cashflow record_enable_observation".split())


# ---- a. lease=None: every writer refuses before doing any work --------------------
@pytest.mark.parametrize("name", _WRITER_NAMES)
def test_every_writer_refuses_a_missing_lease(h, name):
    intent, obs, fill, flow = _seed(h)
    calls = _writers(h, intent, obs, fill, flow)
    before = _snapshot(h.ledger)

    with pytest.raises(ValueError, match="^a writer lease is required$"):
        calls[name](None)

    assert _snapshot(h.ledger) == before


# ---- b. a stale or foreign lease: every writer fails closed -----------------------
@pytest.mark.parametrize("name", _WRITER_NAMES)
@pytest.mark.parametrize("bad", ("epoch", "holder"))
def test_every_writer_refuses_a_stale_or_foreign_lease(h, name, bad):
    intent, obs, fill, flow = _seed(h)
    calls = _writers(h, intent, obs, fill, flow)
    stale = (replace(h.lease, epoch=h.lease.epoch + 1) if bad == "epoch"
             else replace(h.lease, holder_id="f" * 32))
    before = _snapshot(h.ledger)

    with pytest.raises(LeaseLost, match="^writer lease lost$"):
        calls[name](stale)

    assert _snapshot(h.ledger) == before


# ---- c. a released lease ----------------------------------------------------------
@pytest.mark.parametrize("name", _WRITER_NAMES)
def test_every_writer_refuses_a_released_lease(h, name):
    intent, obs, fill, flow = _seed(h)
    calls = _writers(h, intent, obs, fill, flow)
    release_writer_lease(h.ledger, h.lease, now=NOW + 1)
    before = _snapshot(h.ledger)

    with pytest.raises(LeaseLost, match="^writer lease lost$"):
        calls[name](h.lease)

    assert _snapshot(h.ledger) == before


# ---- d. a taken-over lease: a later holder fences the old one ---------------------
def test_a_takeover_fences_the_old_holder_out_of_every_write(h):
    intent, obs, fill, flow = _seed(h)
    calls = _writers(h, intent, obs, fill, flow)
    new_epoch = h.ledger.claim_lease_row(holder_id="takeover", pid=4242, now=NOW + 1)
    assert new_epoch == h.lease.epoch + 1
    before = _snapshot(h.ledger)

    for call in calls.values():
        with pytest.raises(LeaseLost, match="^writer lease lost$"):
            call(h.lease)

    assert _snapshot(h.ledger) == before


# ---- e. the transaction layer itself ----------------------------------------------
def test_tx_refuses_none_and_tx_admin_is_the_management_path(h):
    before = _snapshot(h.ledger)

    with pytest.raises(ValueError, match="^a writer lease is required$"):
        with h.ledger._tx(None):
            pass

    assert _snapshot(h.ledger) == before
    with h.ledger._tx_admin() as cur:   # the management path writes without a lease
        cur.execute("INSERT INTO meta(key, value) VALUES('lease_scope_probe', '1')")
    assert _snapshot(h.ledger)["meta"] != before["meta"]


# ---- f. the lease-free set is exactly the four management operations --------------
def test_trip_safety_and_clear_safety_need_no_lease(h):
    before = _snapshot(h.ledger)
    assert h.ledger.trip_safety(SafetyReason.DATA_ANOMALY, detail="probe", now=NOW) is True
    tripped = _snapshot(h.ledger)
    assert len(tripped["safety_trips"]) == len(before["safety_trips"]) + 1

    h.ledger.clear_safety(SafetyReason.DATA_ANOMALY, operator="owner", note="checked",
                          evidence="ticket", now=NOW + 1)
    cleared = _snapshot(h.ledger)
    assert cleared["safety_trips"] != tripped["safety_trips"]
    with h.ledger.read_cursor() as cur:
        row = cur.execute("SELECT cleared_by FROM safety_trips").fetchone()
    assert row[0] == "owner"


def test_claim_lease_row_is_the_lease_bootstrap_and_needs_no_lease(ws):
    ledger = ws.open_ledger()
    try:
        epoch = ledger.claim_lease_row(holder_id="first", pid=111, now=NOW)
        assert epoch == 1
        epoch = ledger.claim_lease_row(holder_id="second", pid=222, now=NOW)
        assert epoch == 2
        with ledger.read_cursor() as cur:
            assert cur.execute("SELECT holder_id, epoch FROM writer_lease").fetchone() == ("second", 2)
    finally:
        ledger.close()


def test_the_real_lease_still_carries_every_write(h):
    """Sanity: the same writers, under the live lease, do write (no regression)."""
    intent, obs, fill, flow = _seed(h)
    before = _snapshot(h.ledger)

    h.ledger.mark_submitting(h.lease, intent.intent_id, now=NOW + 1)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=NOW + 2)
    h.ledger.record_order_observation(h.lease, obs, now=NOW + 3)  # last_seen_at moves
    h.ledger.record_fill(h.lease, fill, now=NOW)
    h.ledger.record_cashflow(h.lease, flow, now=NOW)
    h.ledger.record_enable_observation(h.lease, _enable(h), now=NOW)
    h.ledger.record_live_event(h.lease, "live_decision", {"probe": 1}, now=NOW)
    h.ledger.store_policy_set(h.lease, h.ps, now=NOW)

    after = _snapshot(h.ledger)
    for name in ("intents", "orders", "fills", "cashflows", "events"):
        assert after[name] != before[name], name
    assert h.ledger.verify_event_chain()
