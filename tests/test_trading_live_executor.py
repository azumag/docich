"""The LIVE order executor against the mock exchange (Issue #3623, L2b). No network."""
import os
from decimal import Decimal as D

import pytest

from docich.trading.live import executor as live_executor
from docich.trading.live import safety as sf
from docich.trading.live import views
from docich.trading.live.exchange import (Capabilities, ExchangeRejected, FeeStatus, OrderNotFound, OrderSnapshot,
                                          OrderStatus)
from docich.trading.live.executor import SubmitOutcome as S
from docich.trading.live.ingest import ExecutorConfig
from docich.trading.live.lease import LeaseLost
from docich.trading.live.mock_exchange import MockFees
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
def h(ws):
    harness = Harness(ws)
    harness.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
    yield harness
    harness.close()


def _buy(h, key="b1", amount="10", price="100"):
    intent, outcome = h.send(side="buy", key=key, amount=amount, price=price)
    return intent, outcome


# ---- the happy path ------------------------------------------------------------------------
def test_buy_that_crosses_the_book_is_filled_and_recorded(h):
    intent, outcome = _buy(h)
    assert outcome is S.ACKED and h.state(intent.intent_id) == "FILLED"
    fills = views.fills_for_order(h.ledger, h.ledger.get_intent(intent.intent_id).exchange_order_id)
    assert len(fills) == 1 and fills[0].amount == 10 and fills[0].fee_status == "known"
    h.ex.step()
    assert h.trips() == []
    assert views.position_net_base(h.ledger, intent.position_id) == 10


def test_resting_order_becomes_partially_filled_then_filled(h):
    intent, _ = _buy(h, price="90")
    assert h.state(intent.intent_id) == "ACKED"
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
    h.ex.step()
    assert h.state(intent.intent_id) == "PARTIALLY_FILLED"
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="6")
    h.ex.step()
    assert h.state(intent.intent_id) == "FILLED"
    assert sum(f.amount for f in views.fills_for_order(
        h.ledger, h.ledger.get_intent(intent.intent_id).exchange_order_id)) == 10
    h.ex.step()
    assert h.trips() == []


def test_sell_after_buy_closes_the_position(h):
    buy, _ = _buy(h)
    sell, outcome = h.send(side="sell", key="s1", amount="10", price="99")
    assert outcome is S.ACKED and h.state(sell.intent_id) == "FILLED"
    with h.ledger.read_cursor() as cur:
        assert cur.execute("SELECT status FROM positions WHERE position_id=?", (buy.position_id,)).fetchone()[0] == "closed"
    h.ex.step()
    assert h.trips() == []


# ---- gates and local checks ----------------------------------------------------------------
def test_closed_gate_sends_nothing(ws):
    h = Harness(ws, gate_open=False)
    try:
        intent, outcome = h.send()
        assert outcome is S.BLOCKED and h.state(intent.intent_id) == "ABANDONED" and h.mock.submissions == []
    finally:
        h.close()


def test_shadow_mode_never_sends(ws):
    h = Harness(ws, gate_open=False)
    ws.write_control(mode="shadow")
    try:
        intent, outcome = h.send()
        assert outcome is S.BLOCKED and h.mock.submissions == []
    finally:
        h.close()


def test_local_checks_stop_bad_orders_before_the_exchange_sees_them(h):
    for kwargs, key in ((dict(amount="1.5"), "k1"), (dict(amount="0.5"), "k2"), (dict(price="100.0005"), "k3")):
        intent, outcome = h.send(key=key, **kwargs)
        assert outcome is S.LOCAL_INVALID and h.state(intent.intent_id) == "ABANDONED"
    sell, outcome = h.send(side="sell", key="k4", amount="5")   # nothing held
    assert outcome is S.LOCAL_INVALID
    assert h.mock.submissions == []


def test_exchange_rejection_is_not_created_for_sure(h):
    h.mock.faults.push("submit", "REJECT", code="system_busy")
    intent, outcome = _buy(h)
    assert outcome is S.REJECTED
    assert h.ledger.get_intent(intent.intent_id).state == "REJECTED"
    assert h.ledger.get_intent(intent.intent_id).reason_code == "system_busy"
    assert sf.read_safety_state(h.ledger).block_new_buys is False


def test_only_the_mock_sender_is_accepted(h):
    class Real:
        kind = "bitbank"

        def submit_order(self, req): raise AssertionError

        def cancel_order(self, s, e): raise AssertionError

    with pytest.raises(ValueError):
        live_executor.OrderExecutor(h.ledger, h.lease, reader=h.mock, sender=Real(), enable_check=h._gate,
                                    clock=h.clock.seconds, symbols=h.symbols)


# ---- safety stop: buys stop, sells go ------------------------------------------------------
def test_during_a_safety_stop_a_buy_is_abandoned_but_the_sell_goes_out(h):
    buy, _ = _buy(h)
    sf.trip(h.ledger, sf.SafetyReason.DATA_ANOMALY, detail="x", now=h.clock.seconds())
    blocked, outcome = h.send(side="buy", key="b2")
    assert outcome is S.BLOCKED and h.state(blocked.intent_id) == "ABANDONED"
    sell, outcome = h.send(side="sell", key="s1", amount="10", price="99")
    assert outcome is S.ACKED and h.state(sell.intent_id) == "FILLED"


# ---- lost request / lost response ---------------------------------------------------------
def test_lost_response_is_unknown_then_found_at_once_by_client_id(h):
    h.mock.faults.push("submit", "LOST_RESPONSE")
    intent, outcome = _buy(h)
    assert outcome is S.UNKNOWN and h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"
    assert sf.read_safety_state(h.ledger).block_new_buys
    report = h.ex.step()  # no waiting needed: finding the order is positive evidence
    assert h.state(intent.intent_id) == "FILLED" and report.resolved
    assert len(h.mock.submissions) == 1 and not sf.read_safety_state(h.ledger).block_new_buys


def test_lost_response_without_client_ids_is_found_by_matching_the_order_lists(ws):
    h = Harness(ws, caps=Capabilities(False, False, True))
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))])
    try:
        h.mock.faults.push("submit", "LOST_RESPONSE")
        intent, outcome = _buy(h)
        assert outcome is S.UNKNOWN
        h.ex.step()
        assert h.state(intent.intent_id) == "FILLED" and len(h.mock.submissions) == 1 and h.trips() == []
    finally:
        h.close()


def test_lost_request_stays_unknown_until_settled_then_not_created_with_evidence(h):
    h.mock.faults.push("submit", "LOST_REQUEST")
    intent, outcome = _buy(h)
    assert outcome is S.UNKNOWN
    h.ex.step()                                   # first empty answer (noted)
    assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"
    h.advance(60)
    h.ex.step()
    assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"          # settle time has not passed
    h.advance(61)
    h.ex.step()                                   # second empty answer, settle_s after the first
    after = h.ledger.get_intent(intent.intent_id)
    assert after.state == "NOT_CREATED" and h.mock.submissions == []
    with h.ledger.read_cursor() as cur:
        evidence = cur.execute("SELECT evidence FROM intents WHERE intent_id=?", (intent.intent_id,)).fetchone()[0]
    assert '"method":"client_id"' in evidence and "balance" in evidence
    assert not sf.read_safety_state(h.ledger).block_new_buys


def test_one_empty_answer_after_the_settle_time_is_still_not_enough(h):
    h.mock.faults.push("submit", "LOST_REQUEST")
    intent, _ = _buy(h)
    h.advance(300)                       # long past settle_s, but nobody has looked yet
    h.ex.step()
    assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"      # the first empty answer is only noted
    h.advance(121)
    h.ex.step()
    assert h.state(intent.intent_id) == "NOT_CREATED"


def test_lost_request_without_client_ids_needs_complete_lists(ws):
    h = Harness(ws, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = _buy(h)
        h.advance(121)
        h.mock.faults.push("fetch_open_orders", "TRUNCATED_PAGE")
        h.ex.step()
        assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"        # a cut-off list proves nothing
        h.ex.step()
        assert h.state(intent.intent_id) == "NOT_CREATED"
    finally:
        h.close()


def test_an_order_that_is_invisible_for_longer_than_the_settle_protocol_is_caught_by_the_sweep(h):
    """Residual risk, pinned: a RESTING order (no balance change) that stays invisible for the whole
    protocol is declared NOT_CREATED; when it becomes visible the sweep trips UNKNOWN_ORDER (buys stop)."""
    h.mock.faults.push("submit", "VISIBILITY_LAG", n=6)
    h.mock.faults.push("submit", "LOST_RESPONSE")
    intent, outcome = _buy(h, price="90")
    assert outcome is S.UNKNOWN and len(h.mock.submissions) == 1
    h.ex.step()
    h.advance(121)
    h.ex.step()
    assert h.state(intent.intent_id) == "NOT_CREATED"
    for _ in range(3):
        h.ex.step()
    assert "unknown_order" in h.trips() and sf.read_safety_state(h.ledger).block_new_buys


def test_a_filled_order_that_is_still_invisible_cannot_be_declared_not_created(h):
    """A fill changes the balance, so the balance proof refuses even when every list is empty."""
    h.mock.faults.push("submit", "VISIBILITY_LAG", n=6)
    h.mock.faults.push("submit", "LOST_RESPONSE")
    intent, outcome = _buy(h, price="100")
    assert outcome is S.UNKNOWN
    for _ in range(3):
        h.ex.step()
        h.advance(121)
    assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"
    for _ in range(4):
        h.ex.step()
    assert h.state(intent.intent_id) == "FILLED" and h.trips() == []


def test_unknown_sell_on_a_stuck_symbol_cannot_be_followed_by_a_second_sell(h):
    buy, _ = _buy(h)
    h.mock.faults.push("submit", "LOST_REQUEST")
    first, outcome = h.send(side="sell", key="s1", amount="10", price="99")
    assert outcome is S.UNKNOWN
    with pytest.raises(Exception):
        h.ex.plan(h.req(side="sell", key="s2", amount="10", price="99"))


@pytest.mark.parametrize("fee_currency", ["quote", "base"])
def test_a_missing_fee_never_pins_an_exit(ws, fee_currency):
    h = Harness(ws, fees=MockFees(maker_rate=D("0"), taker_rate=D("0.001"), currency=fee_currency))
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
    try:
        h.mock.faults.push("fill", "FEE_MISSING")
        buy, _ = _buy(h, amount="20")
        for _ in range(6):
            h.ex.step()
        assert h.state(buy.intent_id) == "FILLED" and "fee_missing" in h.trips()   # buys are now stopped
        # the sell request is lost; the owner wants out
        held = int(h.mock.fetch_balance().assets["OAS"].free)   # what the exchange really has (L4 sizes sells so)
        h.mock.faults.push("submit", "LOST_REQUEST")
        sell, outcome = h.send(side="sell", key="s1", amount=str(held), price="99")
        assert outcome is S.UNKNOWN
        for _ in range(3):
            h.advance(121)
            h.ex.step()
        assert h.state(sell.intent_id) == "NOT_CREATED"
        again, outcome = h.send(side="sell", key="s2", amount=str(held), price="99")   # a new attempt goes out
        assert outcome is S.ACKED and h.state(again.intent_id) == "FILLED"
    finally:
        h.close()


# ---- fills: late, duplicated, conflicting, fees ---------------------------------------------
def test_fills_that_show_up_late_keep_the_intent_acked_until_they_do(h):
    h.mock.faults.push("fill", "FILL_DELAY", n=2)
    intent, _ = _buy(h)
    assert h.state(intent.intent_id) == "ACKED"
    h.ex.step()
    assert h.state(intent.intent_id) == "ACKED"
    h.ex.step()
    assert h.state(intent.intent_id) == "FILLED" and h.trips() == []


def test_fills_that_never_show_up_trip_a_data_anomaly(ws):
    h = Harness(ws, config=ExecutorConfig(settle_s=120.0, sweep_every=1, fill_lag_polls=3))
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))])
    try:
        h.mock.faults.push("fill", "FILL_DELAY", n=50)
        intent, _ = _buy(h)
        for _ in range(5):
            h.ex.step()
        assert h.state(intent.intent_id) == "ACKED" and "data_anomaly" in h.trips()
    finally:
        h.close()


def test_duplicate_fill_delivery_is_recorded_once(h):
    h.mock.faults.push("fetch_fills", "DUPLICATE_FILL", nth=2)
    intent, _ = _buy(h)
    h.mock.faults.push("fetch_fills", "DUPLICATE_FILL")
    h.ex.step()
    assert len(views.all_fills(h.ledger)) == 1 and h.trips() == []


def test_conflicting_fill_delivery_trips_data_anomaly_and_keeps_the_first(h):
    intent, _ = _buy(h, price="90")
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
    h.mock.faults.push("fetch_fills", "CONFLICTING_FILL")
    h.ex.step()
    assert "data_anomaly" in h.trips()
    assert [f.price for f in views.all_fills(h.ledger)] == [D("90")]


def test_unknown_fee_is_held_back_and_recorded_when_it_appears(h):
    h.mock.faults.push("fill", "FEE_MISSING", backfill_after=1)
    intent, _ = _buy(h)
    assert h.state(intent.intent_id) == "ACKED" and views.all_fills(h.ledger) == []
    h.ex.step()
    assert h.state(intent.intent_id) == "FILLED"
    assert views.all_fills(h.ledger)[0].fee_status == "known" and h.trips() == []


def test_fee_that_never_appears_is_recorded_unknown_after_the_grace_and_trips_fee_missing(h):
    h.mock.faults.push("fill", "FEE_MISSING")
    intent, _ = _buy(h)
    for _ in range(5):
        h.ex.step()
    assert views.all_fills(h.ledger)[0].fee_status == "unknown"
    assert h.state(intent.intent_id) == "FILLED" and h.trips() == ["fee_missing"]


def test_a_fee_that_arrives_after_an_unknown_fee_was_recorded_is_not_written(h):
    h.mock.faults.push("fill", "FEE_MISSING", backfill_after=6)
    intent, _ = _buy(h)
    for _ in range(10):
        h.ex.step()
    row = views.all_fills(h.ledger)[0]
    assert row.fee_status == "unknown" and h.trips() == ["fee_missing"]   # no conflict, no data_anomaly


# ---- order anomalies --------------------------------------------------------------------
def test_an_ack_that_differs_from_the_intent_is_recorded_but_trips_data_anomaly(h):
    real = h.mock.submit_order

    def wrong(req):
        snap = real(req)
        return OrderSnapshot(**{**snap.__dict__, "amount": snap.amount + 1, "remaining_amount": snap.remaining_amount + 1})

    h.mock.submit_order = wrong
    intent, outcome = _buy(h)
    assert "data_anomaly" in h.trips()
    assert views.order_for_intent(h.ledger, intent.intent_id) is not None


def test_an_order_that_goes_missing_after_the_ack_trips_data_anomaly(ws):
    h = Harness(ws, config=ExecutorConfig(settle_s=120.0, sweep_every=1, fill_lag_polls=2))
    try:
        intent, _ = _buy(h, price="90")
        h.mock.fetch_order = lambda symbol, eid: (_ for _ in ()).throw(OrderNotFound(eid))
        for _ in range(4):
            h.ex.step()
        assert "data_anomaly" in h.trips() and h.state(intent.intent_id) == "ACKED"
    finally:
        h.close()


def test_the_owners_manual_order_is_an_unknown_order_and_stops_buys_only(h):
    from docich.trading.live.exchange import OrderRequest
    h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", D("5"), D("50"), None))
    h.ex.step()
    assert h.trips() == ["unknown_order"] and sf.read_safety_state(h.ledger).block_new_buys


# ---- cancel ----------------------------------------------------------------------------
def test_cancel_a_resting_order(h):
    intent, _ = _buy(h, price="90")
    h.ex.cancel(intent.intent_id)
    assert h.state(intent.intent_id) == "CANCELED"
    h.ex.step()
    assert h.trips() == [] and h.mock.fetch_balance().assets["JPY"].locked == 0


def test_cancel_after_a_partial_fill_keeps_the_filled_part(h):
    intent, _ = _buy(h, price="90")
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
    h.ex.step()
    h.ex.cancel(intent.intent_id)
    assert h.state(intent.intent_id) == "CANCELED"
    assert views.position_net_base(h.ledger, intent.position_id) == 4


def test_cancel_race_the_order_filled_just_before(h):
    intent, _ = _buy(h, price="90")
    h.mock.faults.push("cancel", "CANCEL_RACE")
    h.ex.cancel(intent.intent_id)
    assert h.state(intent.intent_id) == "FILLED"


def test_cancel_whose_response_is_lost_is_settled_by_the_next_poll(h):
    intent, _ = _buy(h, price="90")
    h.mock.faults.push("cancel", "LOST_RESPONSE")
    h.ex.cancel(intent.intent_id)
    assert h.state(intent.intent_id) == "CANCELED"   # the refresh inside cancel already sees it


# ---- the writer lease ------------------------------------------------------------------
def test_losing_the_lease_stops_sends_and_leaves_a_trip(h, ws):
    os.rename(ws.layout.lock_path, ws.layout.lock_path.with_name("old.lock"))
    ws.layout.lock_path.write_text("")
    with pytest.raises(LeaseLost):
        h.send()
    assert "lease_lost" in h.trips() and h.mock.submissions == []


def test_lease_lost_between_mark_submitting_and_the_send_prevents_the_send(h, ws):
    def hook(point):
        if point == "after_submitting_commit":
            os.rename(ws.layout.lock_path, ws.layout.lock_path.with_name("old.lock"))
            ws.layout.lock_path.write_text("")

    from docich.trading.live import ledger as lg
    lg.FAULT_HOOK = hook
    with pytest.raises(LeaseLost):
        h.send()
    assert h.mock.submissions == [] and "lease_lost" in h.trips()


def test_a_crash_before_the_cancel_request_leaves_the_order_alive_and_acked(h):
    from test_trading_live_support import SimulatedCrash
    intent, _ = _buy(h, price="90")

    def hook(point):
        if point == "before_cancel":
            raise SimulatedCrash(point)

    live_executor.FAULT_HOOK = hook
    with pytest.raises(SimulatedCrash):
        h.ex.cancel(intent.intent_id)
    live_executor.FAULT_HOOK = None
    assert h.state(intent.intent_id) == "ACKED"
    assert [o.status for o in h.mock.all_orders()] == [OrderStatus.UNFILLED]


def test_malformed_replies_are_anomalies_not_crashes_of_the_polling_loop(h):
    intent, _ = _buy(h, price="90")
    other = None
    h.mock.faults.push("fetch_order", "MALFORMED")
    h.ex.step()                                    # must not raise
    assert "data_anomaly" in h.trips() and h.state(intent.intent_id) == "ACKED"


def test_an_unclassified_exception_from_the_exchange_means_outcome_unknown(h):
    intent, _ = _buy(h, price="90")
    h.mock.fetch_order = lambda symbol, eid: (_ for _ in ()).throw(RuntimeError("boom"))
    h.ex.step()                                    # polled again later, nothing crashes, nothing is declared
    assert h.state(intent.intent_id) == "ACKED" and h.trips() == []


def test_an_unclassified_exception_on_send_leaves_the_intent_unknown(h):
    h.mock.submit_order = lambda req: (_ for _ in ()).throw(RuntimeError("boom"))
    sent, outcome = h.send(key="b2", price="90", amount="3", side="buy")
    assert outcome is S.UNKNOWN and h.state(sent.intent_id) == "UNKNOWN_SUBMISSION"


def test_known_limit_an_unprovable_lost_sell_pins_the_exit_until_a_human_acts(h):
    """Pinned limitation (to be closed before phase E): when the base balance cannot be proven, a lost
    sell stays UNKNOWN, and the one-open-intent rule then blocks further sells on that symbol."""
    buy, _ = _buy(h)
    h.mock.faults.push("submit", "LOST_REQUEST")
    sell, outcome = h.send(side="sell", key="s1", amount="10", price="99")
    assert outcome is S.UNKNOWN
    h.mock.adjust_balance("OAS", "-2")             # an unexplained base discrepancy exists
    for _ in range(4):
        h.advance(121)
        h.ex.step()
    assert h.state(sell.intent_id) == "UNKNOWN_SUBMISSION"
    with pytest.raises(Exception):
        h.ex.plan(h.req(side="sell", key="s2", amount="8", price="99"))


def test_a_duplicated_unknown_fee_fill_ticks_the_grace_counter_once_per_poll(h):
    h.mock.faults.push("fill", "FEE_MISSING", backfill_after=2)
    h.mock.faults.push("fetch_fills", "DUPLICATE_FILL")
    h.mock.faults.push("fetch_fills", "DUPLICATE_FILL")
    intent, _ = _buy(h)
    for _ in range(3):
        h.ex.step()
    assert h.state(intent.intent_id) == "FILLED"
    assert views.all_fills(h.ledger)[0].fee_status == "known" and h.trips() == []


def test_each_regular_step_increments_the_missing_order_counter_once(ws):
    h = Harness(ws, config=ExecutorConfig(settle_s=120.0, sweep_every=1, fill_lag_polls=2))
    try:
        intent, _ = _buy(h, price="90")
        h.mock.fetch_order = lambda symbol, eid: (_ for _ in ()).throw(OrderNotFound(eid))
        for _ in range(2):
            h.ex.step()
        assert "data_anomaly" not in h.trips()          # 2 polls missing: still within fill_lag_polls=2
        h.ex.step()
        assert "data_anomaly" in h.trips()              # the 3rd poll is the first one past it
    finally:
        h.close()


# ---- a read-only executor (no sender) ------------------------------------------------------
def _read_only(h):
    return live_executor.OrderExecutor(h.ledger, h.lease, reader=h.mock, sender=None, enable_check=h._gate,
                                       clock=h.clock.seconds, symbols=h.symbols, config=h.config)


def test_an_executor_without_a_sender_abandons_the_intent_instead_of_sending_it(h):
    ro = _read_only(h)
    intent = h.ex.plan(h.req(key="ro1"))
    assert ro.submit(intent.intent_id) is S.BLOCKED
    after = h.ledger.get_intent(intent.intent_id)
    assert after.state == "ABANDONED" and after.reason_code == "no_sender"
    assert h.mock.submissions == []


def test_a_read_only_executor_refuses_to_cancel_and_still_polls_the_orders_it_did_not_send(h):
    intent, _ = _buy(h, price="90")                    # resting: recorded and ACKED
    ro = _read_only(h)
    sent = list(h.mock.submissions)
    with pytest.raises(ValueError):
        ro.cancel(intent.intent_id)
    assert h.mock.submissions == sent and h.state(intent.intent_id) == "ACKED"
    assert [o.status for o in h.mock.all_orders()] == [OrderStatus.UNFILLED]
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
    ro.step()                                           # polls through the reader alone
    assert h.state(intent.intent_id) == "PARTIALLY_FILLED"
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="6")
    ro.step()
    assert h.state(intent.intent_id) == "FILLED"


def test_a_sender_that_is_not_the_mock_is_still_refused(h):
    class Other:
        kind = "ccxt"

        def submit_order(self, req): raise AssertionError("must not be called")

        def cancel_order(self, symbol, exchange_order_id): raise AssertionError("must not be called")

    with pytest.raises(ValueError):
        live_executor.OrderExecutor(h.ledger, h.lease, reader=h.mock, sender=Other(), enable_check=h._gate,
                                    clock=h.clock.seconds, symbols=h.symbols, config=h.config)
    with pytest.raises(ValueError):                    # no kind at all is refused too
        live_executor.OrderExecutor(h.ledger, h.lease, reader=h.mock, sender=object(), enable_check=h._gate,
                                    clock=h.clock.seconds, symbols=h.symbols, config=h.config)


def test_reset_rules_makes_the_next_rules_call_read_the_market_again(h):
    real, calls = h.mock.market_rules, []

    def counted(symbol):
        calls.append(symbol)
        return real(symbol)

    h.mock.market_rules = counted
    assert h.ex.rules()["OAS/JPY"].symbol == "OAS/JPY" and calls == ["OAS/JPY"]
    assert h.ex.rules()["OAS/JPY"].symbol == "OAS/JPY" and calls == ["OAS/JPY"]   # served from the cache
    h.ex.reset_rules()
    assert h.ex.rules()["OAS/JPY"].symbol == "OAS/JPY" and calls == ["OAS/JPY"] * 2
