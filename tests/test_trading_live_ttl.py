"""The order TTL (L4b-5b-2c): expired open orders are withdrawn exactly once.

The ledger, lease, policy storage, mock exchange and executor are the real LIVE pieces from
the shared harness; the cycle rig is the one the act tests use (only the reader hooks and the
public market data are faked).  Every number is pinned against the task's hand calculation;
money is ``Decimal`` and compared with ``==``.  Nothing here touches the network, and no real
sender exists: ``SENDER_KINDS_ALLOWED`` stays mock-only.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import act as act_module
from docich.trading.live import cycle as cycle_module
from docich.trading.live import decide as decide_module
from docich.trading.live import executor as executor_module
from docich.trading.live import safety
from docich.trading.live import ttl
from docich.trading.live import views
from docich.trading.live.cycle import CycleDeps, CycleOutcome, CycleState, run_cycle
from docich.trading.live.enable import EnableResult
from docich.trading.live.executor import OrderExecutor, SubmitOutcome
from docich.trading.live.lease import LeaseLost
from docich.trading.live.signals import LiveSignals
from docich.trading.live.sizing import SizingSettings
from docich.trading.live.ttl import (MAX_CANCELS_PER_CYCLE, ORDER_TTL_S, TtlReport,
                                     cancel_expired)

from test_trading_live_support import Harness, Workspace, block_network
import test_trading_live_act as act_tests
import test_trading_live_decide as decide_tests

NOW = decide_tests.NOW
SRC = Path(ttl.__file__).parents[3]
ALL_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED",
              "FILLED", "CANCELED", "EXPIRED", "REJECTED", "ABANDONED", "NOT_CREATED")


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


# ---- helpers ---------------------------------------------------------------------------
class _NoFrames:
    def fetch_frame(self, symbol, now):
        raise AssertionError("this test never reaches the frame step")


def noop_decide(deps, state, ctx):
    return None


def deps_for(h: Harness, *, executor=None, symbols=None, stop_requested=None,
             decide=None) -> CycleDeps:
    """CycleDeps around an executor over the harness's real ledger/lease/mock."""
    executor = h.ex if executor is None else executor
    symbols = tuple(h.symbols if symbols is None else symbols)
    return CycleDeps(
        ledger=h.ledger, lease=h.lease, executor=executor, reader=h.mock,
        market_data=_NoFrames(), gate=executor.enable_check, policy_set=h.ps,
        symbols=symbols, clock=h.clock.seconds,
        stop_requested=stop_requested or (lambda: False),
        decide=decide or noop_decide,
    )


def resting_buy(h: Harness, *, key="b1", symbol="OAS/JPY", amount="10", price="90"):
    """A buy that the empty mock book leaves UNFILLED (ACKED) and resting."""
    intent, outcome = h.send(symbol=symbol, side="buy", key=key, amount=amount, price=price)
    assert outcome is SubmitOutcome.ACKED
    intent = h.ledger.get_intent(intent.intent_id)      # plan() returns the PLANNED snapshot
    assert intent.state == "ACKED" and intent.exchange_order_id
    return intent


def cancel_rows(ledger) -> list[dict]:
    return [row.payload for row in decide_tests.decision_rows(ledger)
            if row.payload.get("action") == "cancel"]


def mock_order(h: Harness, intent):
    found = [item for item in h.mock.all_orders()
             if item.exchange_order_id == intent.exchange_order_id]
    assert len(found) == 1
    return found[0]


def intent_of(h: Harness, symbol: str = "OAS/JPY") -> list:
    return [item for item in h.ledger.list_intents(states=ALL_STATES)
            if item.symbol == symbol]


def _event_snapshot(ledger) -> list[tuple]:
    return [(event.event_id, event.occurred_at, event.kind, event.entity_type,
             event.entity_id, event.payload) for event in ledger.events(limit=100_000)]


# ---- t1: the TTL boundary and one row per intent ---------------------------------------
def test_an_expired_acked_order_is_canceled_once(ws, harnesses):
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()},
                             decide=noop_decide)
    rig.state = CycleState()
    intent = resting_buy(h, key="b1")
    assert h.clock.seconds() == NOW

    # 45.0 s old is NOT expired: the comparison is strict (now - created > TTL).
    h.clock.advance(45)
    first = run_cycle(rig.deps, rig.state)
    assert first.outcome is CycleOutcome.CONTINUE
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"
    assert cancel_rows(h.ledger) == []

    # One more second: 46 s old -> canceled, once, with the age pinned.
    h.clock.advance(1)
    assert h.clock.seconds() == NOW + 46
    second = run_cycle(rig.deps, rig.state)
    assert second.outcome is CycleOutcome.CONTINUE
    assert h.ledger.get_intent(intent.intent_id).state == "CANCELED"
    assert mock_order(h, intent).status.value == "CANCELED_UNFILLED"

    rows = [row for row in cancel_rows(h.ledger) if row["intent_id"] == intent.intent_id]
    assert len(rows) == 1                         # exactly one after the two cycles
    payload = rows[0]
    assert payload["reason"] == "ttl_expired"
    assert payload["action"] == "cancel"
    assert payload["side"] == "buy" and payload["symbol"] == "OAS/JPY"
    assert payload["decision_key"] == intent.decision_key
    assert payload["opportunity_id"] == intent.decision_key
    assert payload["cycle"] == "2"
    assert D(payload["age_s"]) == D("46")
    assert all(isinstance(value, str) for value in payload.values())

    # The 45.001 s boundary on a fresh intent: not at exactly 45.0, yes just after it.
    boundary = resting_buy(h, key="b2")
    h.clock.advance(45.0)
    at_boundary = cancel_expired(rig.deps, rig.state, h.clock.seconds())
    assert at_boundary == TtlReport(canceled=(), failed=(), deferred=0)
    assert h.ledger.get_intent(boundary.intent_id).state == "ACKED"
    h.clock.advance(0.001)
    just_over = cancel_expired(rig.deps, rig.state, h.clock.seconds())
    assert just_over.canceled == (boundary.intent_id,)
    assert h.ledger.get_intent(boundary.intent_id).state == "CANCELED"
    boundary_rows = [row for row in cancel_rows(h.ledger)
                     if row["intent_id"] == boundary.intent_id]
    assert len(boundary_rows) == 1
    # The clock is milliseconds, so the real float age is 45.0009999...; the pin is the
    # decimal the task wrote, within the float resolution of a 1.7e9 epoch second.
    assert abs(D(boundary_rows[0]["age_s"]) - D("45.001")) < D("0.000001")


# ---- t2: a partial fill survives the cancel --------------------------------------------
def test_a_partially_filled_expired_order_is_canceled_and_keeps_its_fill(ws, harnesses):
    h = harnesses()
    intent, outcome = h.send(side="buy", key="b1", amount="5", price="90")
    assert outcome is SubmitOutcome.ACKED
    h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="3")
    h.ex.step()
    intent = h.ledger.get_intent(intent.intent_id)
    assert intent.state == "PARTIALLY_FILLED"

    h.clock.advance(46)
    deps = deps_for(h)
    report = cancel_expired(deps, CycleState(cycle_number=1), h.clock.seconds())

    assert report == TtlReport(canceled=(intent.intent_id,), failed=(), deferred=0)
    assert h.ledger.get_intent(intent.intent_id).state == "CANCELED"
    order = h.mock.all_orders()[0]
    assert order.status.value == "CANCELED_PARTIALLY_FILLED"
    assert order.executed_amount == D("3") and order.remaining_amount == D("2")
    fills = views.fills_for_order(h.ledger, intent.exchange_order_id)
    assert [fill.amount for fill in fills] == [D("3")]
    assert views.position_net_base(h.ledger, intent.position_id) == D("3")
    rows = cancel_rows(h.ledger)
    assert len(rows) == 1 and rows[0]["reason"] == "ttl_expired"
    assert D(rows[0]["age_s"]) == D("46")


# ---- t3: other states and young orders are never touched --------------------------------
def test_other_states_and_young_orders_are_left_alone(ws, harnesses):
    symbols = ("OAS/JPY", "XYM/JPY", "ZEC/JPY", "BTC/JPY", "ETH/JPY", "MONA/JPY",
               "DOT/JPY")
    h = harnesses(symbols=symbols)
    now = h.clock.seconds()

    def build(key: str, symbol: str):
        return h.ledger.create_intent(
            h.lease, h.req(symbol=symbol, side="buy", key=key, amount="1", price="10"),
            now=h.clock.seconds())

    # The six non-cancelable states are made OLD below; every one gets its own symbol
    # because the ledger allows one open intent per (symbol, side).
    planned = build("planned", "OAS/JPY")
    assert planned.state == "PLANNED"
    submitting = build("submitting", "XYM/JPY")
    h.ledger.mark_submitting(h.lease, submitting.intent_id, now=h.clock.seconds())
    filled = build("filled", "ZEC/JPY")
    h.ledger.mark_submitting(h.lease, filled.intent_id, now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, filled.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", exchange_order_id="x-filled",
                               now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, filled.intent_id, expected_state="ACKED",
                               to_state="FILLED", now=h.clock.seconds())
    canceled = build("canceled", "BTC/JPY")
    h.ledger.mark_submitting(h.lease, canceled.intent_id, now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, canceled.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", exchange_order_id="x-canceled",
                               now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, canceled.intent_id, expected_state="ACKED",
                               to_state="CANCELED", now=h.clock.seconds())
    rejected = build("rejected", "ETH/JPY")
    h.ledger.mark_submitting(h.lease, rejected.intent_id, now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, rejected.intent_id, expected_state="SUBMITTING",
                               to_state="REJECTED", reason_code="rejected",
                               now=h.clock.seconds())

    h.clock.advance(1000)                          # the five above are far past the TTL
    young = build("young", "MONA/JPY")
    h.ledger.mark_submitting(h.lease, young.intent_id, now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, young.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", exchange_order_id="x-young",
                               now=h.clock.seconds())
    # UNKNOWN_SUBMISSION is built last: it blocks later mark_submitting calls.
    unknown = build("unknown", "DOT/JPY")
    h.ledger.mark_submitting(h.lease, unknown.intent_id, now=h.clock.seconds())
    h.ledger.transition_intent(h.lease, unknown.intent_id, expected_state="SUBMITTING",
                               to_state="UNKNOWN_SUBMISSION",
                               reason_code="send_unknown:TimeoutError",
                               now=h.clock.seconds())

    assert {item.state for item in h.ledger.list_intents(states=ALL_STATES)} == {
        "PLANNED", "SUBMITTING", "ACKED", "FILLED", "CANCELED", "REJECTED",
        "UNKNOWN_SUBMISSION"}

    def forbidden(*args, **kwargs):
        raise AssertionError("cancel must not be called for these states/ages")

    h.ex.cancel = forbidden
    deps = deps_for(h, symbols=symbols)
    expected = {"planned": "PLANNED", "submitting": "SUBMITTING",
                "unknown": "UNKNOWN_SUBMISSION", "filled": "FILLED",
                "canceled": "CANCELED", "rejected": "REJECTED", "young": "ACKED"}

    def states() -> dict:
        return {item.decision_key: item.state
                for item in h.ledger.list_intents(states=ALL_STATES)}

    assert states() == expected
    report = cancel_expired(deps, CycleState(), now + 1010)

    assert report == TtlReport(canceled=(), failed=(), deferred=0)
    assert cancel_rows(h.ledger) == []
    assert states() == expected
    # Only the 10 s old ACKED order is the interesting one, and it is not expired.
    assert h.ledger.get_intent(young.intent_id).created_at == now + 1000
    assert now + 1010 - h.ledger.get_intent(young.intent_id).created_at == 10


# ---- t4: no sender, no read, no write --------------------------------------------------
def test_nothing_happens_without_a_sender(ws, harnesses):
    h = harnesses()
    intent = resting_buy(h, key="b1")
    read_only = OrderExecutor(h.ledger, h.lease, reader=h.mock, sender=None,
                              enable_check=h.ex.enable_check, clock=h.clock.seconds,
                              symbols=h.symbols, config=h.config)
    deps = deps_for(h, executor=read_only)
    state = CycleState(cycle_number=1)

    def forbidden(*args, **kwargs):
        raise AssertionError("a senderless executor must not cancel")

    read_only.cancel = forbidden
    before = _event_snapshot(h.ledger)
    h.clock.advance(46)
    report = cancel_expired(deps, state, h.clock.seconds())

    assert report == TtlReport(canceled=(), failed=(), deferred=0)
    assert _event_snapshot(h.ledger) == before
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"
    assert cancel_rows(h.ledger) == []
    assert state.dedupe == {}


# ---- t5: a failed cancel is recorded once and retried ----------------------------------
def test_a_failing_cancel_is_recorded_and_retried_next_cycle(ws, harnesses, monkeypatch):
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()},
                             decide=noop_decide)
    rig.state = CycleState()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)

    reports: list[TtlReport] = []
    real = cycle_module.cancel_expired

    def spy(deps, state, now):
        report = real(deps, state, now)
        reports.append(report)
        return report

    monkeypatch.setattr(cycle_module, "cancel_expired", spy)

    def hook(point):
        if point == "before_cancel":
            raise RuntimeError(point)

    def cycle_with(hook_on: bool):
        executor_module.FAULT_HOOK = hook if hook_on else None
        try:
            return run_cycle(rig.deps, rig.state)
        finally:
            executor_module.FAULT_HOOK = None

    first = cycle_with(True)
    assert first.outcome is CycleOutcome.CONTINUE      # not CYCLE_FAILED
    assert reports[-1].failed == ((intent.intent_id, "cancel_failed:RuntimeError"),)
    assert reports[-1].canceled == () and reports[-1].deferred == 0
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"
    failed = [row for row in cancel_rows(h.ledger)
              if row["reason"] == "cancel_failed:RuntimeError"]
    assert len(failed) == 1 and failed[0]["intent_id"] == intent.intent_id

    # The same failure again: still one row (the dedupe key is (cancel, intent, reason)).
    second = cycle_with(True)
    assert second.outcome is CycleOutcome.CONTINUE
    assert reports[-1].failed == ((intent.intent_id, "cancel_failed:RuntimeError"),)
    assert len([row for row in cancel_rows(h.ledger)
                if row["reason"] == "cancel_failed:RuntimeError"]) == 1
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"

    # The next cycle without the fault cancels it and records a DIFFERENT row.
    third = cycle_with(False)
    assert third.outcome is CycleOutcome.CONTINUE
    assert reports[-1].canceled == (intent.intent_id,) and reports[-1].failed == ()
    assert h.ledger.get_intent(intent.intent_id).state == "CANCELED"
    assert sorted(row["reason"] for row in cancel_rows(h.ledger)) == [
        "cancel_failed:RuntimeError", "ttl_expired"]


# ---- t6: the per-cycle cap -------------------------------------------------------------
def test_the_cap_per_cycle(ws, harnesses):
    symbols = ("OAS/JPY", "XYM/JPY", "ZEC/JPY", "BTC/JPY", "ETH/JPY", "MONA/JPY",
               "DOT/JPY")
    h = harnesses(symbols=symbols)
    intents = []
    for index, symbol in enumerate(symbols):
        intent, outcome = h.send(symbol=symbol, side="buy", key=f"b{index}",
                                 amount="1", price="10")
        assert outcome is SubmitOutcome.ACKED
        intents.append(intent)
        h.clock.advance(1)                         # distinct created_at: list order is age order
    h.clock.advance(53)
    now = h.clock.seconds()
    assert [item.created_at for item in h.ledger.list_intents(
        states=("ACKED", "PARTIALLY_FILLED"))] == [item.created_at for item in intents]

    deps = deps_for(h, symbols=symbols)
    state = CycleState(cycle_number=1)
    first = cancel_expired(deps, state, now)
    assert first.canceled == tuple(item.intent_id for item in intents[:MAX_CANCELS_PER_CYCLE])
    assert first.failed == () and first.deferred == 2
    assert [h.ledger.get_intent(item.intent_id).state for item in intents] == [
        "CANCELED", "CANCELED", "CANCELED", "CANCELED", "CANCELED", "ACKED", "ACKED"]

    second = cancel_expired(deps, state, now)
    assert second.canceled == tuple(item.intent_id for item in intents[5:])
    assert second.failed == () and second.deferred == 0
    assert all(h.ledger.get_intent(item.intent_id).state == "CANCELED" for item in intents)
    assert [order.status.value for order in h.mock.all_orders()] == [
        "CANCELED_UNFILLED"] * 7


# ---- t7: an ambiguous cancel is retried by the next cycle -------------------------------
def test_an_ambiguous_cancel_is_retried(ws, harnesses):
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()},
                             decide=noop_decide)
    rig.state = CycleState()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)
    h.mock.faults.push("cancel", "LOST_REQUEST")   # the request never reaches the exchange

    first = run_cycle(rig.deps, rig.state)

    assert first.outcome is CycleOutcome.CONTINUE
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"   # the order is still there
    assert [order.status.value for order in h.mock.all_orders()] == ["UNFILLED"]
    assert [row for row in cancel_rows(h.ledger)
            if row["reason"].startswith("cancel_failed")] == []
    ttl_rows = [row for row in cancel_rows(h.ledger) if row["reason"] == "ttl_expired"]
    assert len(ttl_rows) == 1                     # the cancel call itself returned without error

    second = run_cycle(rig.deps, rig.state)

    assert second.outcome is CycleOutcome.CONTINUE
    assert h.ledger.get_intent(intent.intent_id).state == "CANCELED"
    assert [order.status.value for order in h.mock.all_orders()] == ["CANCELED_UNFILLED"]
    assert len([row for row in cancel_rows(h.ledger)
                if row["reason"] == "ttl_expired"]) == 1                # no duplicate row


# ---- t8: LeaseLost while canceling ends the cycle --------------------------------------
def test_lease_lost_while_canceling_exits_the_cycle(ws, harnesses):
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()},
                             decide=noop_decide)
    rig.state = CycleState()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)
    steps: list[int] = []
    real_step = rig.executor.step

    def step_spy():
        steps.append(1)
        return real_step()

    rig.executor.step = step_spy
    rig.executor.cancel = lambda intent_id: (_ for _ in ()).throw(LeaseLost("lease gone"))

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.EXIT_LEASE_LOST
    assert report.reason == "lease_lost"
    assert steps == []                            # nothing runs after the lost lease
    assert views.events_of_kind(h.ledger, "live_decision") == []
    assert views.events_of_kind(h.ledger, "worker_state") == []
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"
    assert [order.status.value for order in h.mock.all_orders()] == ["UNFILLED"]
    assert [trip.reason for trip in safety.read_safety_state(h.ledger).trips] == [
        "lease_lost"]


# ---- t9: cancel runs before the step, in every mode, never on a stop -------------------
@pytest.mark.parametrize("mode", ["monitor", "shadow", "trade"])
def test_cancel_runs_before_the_step_and_in_every_mode(ws, harnesses, mode):
    # The harness starts with the trade enable so that a real resting order can be created.
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()},
                             decide=noop_decide)
    rig.state = CycleState()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)

    # The owner's enable file has no "monitor" mode; a hand gate pins the monitor mode so
    # the mode-independence is tested, not only the closed-gate path.
    deps = rig.deps
    if mode == "monitor":
        def monitor_gate():
            return EnableResult(enabled=True, reasons=(), mode="monitor")

        rig.executor.enable_check = monitor_gate
        deps = replace(rig.deps, gate=monitor_gate)
    else:
        ws.write_enable(h.ps, mode=mode)

    calls: list[str] = []
    real_cancel, real_step = rig.executor.cancel, rig.executor.step

    def cancel_spy(intent_id):
        calls.append("cancel")
        return real_cancel(intent_id)

    def step_spy():
        calls.append("step")
        return real_step()

    rig.executor.cancel, rig.executor.step = cancel_spy, step_spy
    report = run_cycle(deps, rig.state)

    assert report.outcome is CycleOutcome.CONTINUE
    assert report.mode == mode
    assert "cancel" in calls and "step" in calls
    assert calls.index("cancel") < calls.index("step")
    assert h.ledger.get_intent(intent.intent_id).state == "CANCELED"

    # A stop request before step 0 returns before the TTL step: nothing is canceled.
    if mode == "shadow":
        ws.write_enable(h.ps, mode="trade")        # create the order through the open gate
    second = resting_buy(h, key="b2")
    if mode == "shadow":
        ws.write_enable(h.ps, mode="shadow")
    h.clock.advance(46)
    seen = list(calls)

    stopped = run_cycle(replace(deps, stop_requested=lambda: True), rig.state)
    assert stopped.outcome is CycleOutcome.STOP_REQUESTED
    assert calls == seen
    assert h.ledger.get_intent(second.intent_id).state == "ACKED"
    assert [row["intent_id"] for row in cancel_rows(h.ledger)] == [intent.intent_id]


# ---- t10: a canceled sell comes back as attempt a1 in the same cycle --------------------
def test_a_canceled_sell_comes_back_as_attempt_a1(ws, harnesses, monkeypatch):
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()})
    rig.state = CycleState()

    # A real filled buy through the mock: ledger and exchange agree, and the position's
    # entry digest is the current policy's.
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))])
    bought, outcome = h.send(side="buy", key="entry-1", amount="10", price="100")
    assert outcome is SubmitOutcome.ACKED
    bought = h.ledger.get_intent(bought.intent_id)
    assert bought.state == "FILLED"
    assert views.position_net_base(h.ledger, bought.position_id) == D("10")

    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((), (sell,), (), {}))

    first = run_cycle(rig.deps, rig.state)
    assert first.outcome is CycleOutcome.CONTINUE
    sells = [item for item in intent_of(h) if item.side == "sell"]
    assert len(sells) == 1
    a0 = sells[0]
    assert a0.decision_key.endswith(":a0")
    assert a0.state == "ACKED"
    assert mock_order(h, a0).status.value == "UNFILLED"

    # The book must be fresh again in cycle 2, and the a0 sell is 46 s old.
    h.clock.advance(46)
    rig.reader.books["OAS/JPY"] = decide_tests.book(
        "OAS/JPY", ts=int(rig.clock() * 1000))
    second = run_cycle(rig.deps, rig.state)

    assert second.outcome is CycleOutcome.CONTINUE
    assert h.ledger.get_intent(a0.intent_id).state == "CANCELED"
    sells = [item for item in intent_of(h) if item.side == "sell"]
    assert len(sells) == 2
    by_key = {item.decision_key: item for item in sells}
    a1 = [item for item in sells if item.decision_key.endswith(":a1")]
    assert len(a1) == 1
    assert a1[0].state == "ACKED"
    assert a1[0].intent_id != a0.intent_id
    assert a1[0].amount == a0.amount == "10"
    assert mock_order(h, a0).status.value == "CANCELED_UNFILLED"
    assert mock_order(h, a1[0]).status.value == "UNFILLED"
    assert views.position_net_base(h.ledger, bought.position_id) == D("10")

    # The real order of the decision rows: plan/submit a0, the TTL cancel, plan/submit a1.
    sequence = [(row.payload["action"], row.payload.get("decision_key"),
                 row.payload["reason"]) for row in decide_tests.decision_rows(h.ledger)]
    assert [item[0] for item in sequence] == ["plan", "submit", "cancel", "plan", "submit"]
    assert sequence[0][1] == a0.decision_key and sequence[1][1] == a0.decision_key
    assert sequence[2] == ("cancel", a0.decision_key, "ttl_expired")
    assert sequence[3][1] == a1[0].decision_key and sequence[4][1] == a1[0].decision_key
    assert by_key[a0.decision_key].state == "CANCELED"


# ---- t11: the pinned TTL, a clean import, and the call-direction checks ----------------
def test_ttl_matches_the_sizer_ttl_and_modules_stay_clean(tmp_path):
    assert ttl.ORDER_TTL_S == 45 == SizingSettings().order_ttl_s
    assert ORDER_TTL_S == ttl.ORDER_TTL_S
    assert MAX_CANCELS_PER_CYCLE == 5

    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.ttl
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests",
                           "socket") if m in sys.modules]
        assert not bad, bad
        exchanges = [m for m in sys.modules if m == "docich.trading.exchanges"
                     or m.startswith("docich.trading.exchanges.")]
        assert not exchanges, exchanges
        live_exchanges = [m for m in sys.modules if "bitbank_live" in m]
        assert not live_exchanges, live_exchanges
        for name in ("docich.trading.worker", "docich.trading.slot_runtime",
                     "docich.trading.cli"):
            assert name not in sys.modules, name
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                         text=True, cwd=tmp_path,
                         env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert out.stdout.strip() == "clean", out.stderr

    def calls(path: Path) -> tuple[set[str], list[str]]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        attrs: set[str] = set()
        exits: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"exit", "quit"}:
                exits.append(func.id)
            elif isinstance(func, ast.Attribute):
                if func.attr in {"exit", "_exit"}:
                    exits.append(func.attr)
                attrs.add("." + func.attr)
        return attrs, exits

    ttl_calls, ttl_exits = calls(Path(ttl.__file__))
    assert ttl_exits == []
    assert ".cancel" in ttl_calls
    assert ttl_calls & {".plan", ".submit", ".create_intent"} == set()
    for module in (act_module, decide_module):
        module_calls, module_exits = calls(Path(module.__file__))
        assert module_exits == []
        assert ".cancel" not in module_calls

    # The real sender does not exist yet: production wiring stays mock-only.
    assert executor_module.SENDER_KINDS_ALLOWED == frozenset({"mock"})


# ---- p1: a fill that wins the cancel race is not a failed cancel ------------------------
def test_a_valueerror_from_cancel_is_a_race_not_a_failure(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    rig = act_tests.make_rig(
        ws, h, ps=h.ps, symbols=h.symbols,
        rules={symbol: decide_tests.rule(symbol) for symbol in h.symbols},
        decide=noop_decide)
    rig.state = CycleState()
    lost_race = resting_buy(h, symbol="OAS/JPY", key="b1")
    normal = resting_buy(h, symbol="XYM/JPY", key="b2")
    h.clock.advance(46)

    reports: list[TtlReport] = []
    real_cancel_expired = cycle_module.cancel_expired

    def cancel_expired_spy(deps, state, now):
        report = real_cancel_expired(deps, state, now)
        reports.append(report)
        return report

    monkeypatch.setattr(cycle_module, "cancel_expired", cancel_expired_spy)

    real_cancel = rig.executor.cancel
    cancel_calls: list[str] = []

    def cancel_spy(intent_id):
        cancel_calls.append(intent_id)
        if intent_id == lost_race.intent_id:
            raise ValueError("a fill won the race before the cancel")
        return real_cancel(intent_id)

    rig.executor.cancel = cancel_spy

    steps: list[int] = []
    real_step = rig.executor.step

    def step_spy():
        steps.append(1)
        return real_step()

    rig.executor.step = step_spy

    cycle_report = run_cycle(rig.deps, rig.state)

    assert cycle_report.outcome is CycleOutcome.CONTINUE
    assert steps == [1]                           # the cycle went on after the ValueError
    assert sorted(cancel_calls) == sorted((lost_race.intent_id, normal.intent_id))
    assert len(reports) == 1
    report = reports[0]
    assert report.failed == ()                    # a ValueError is a race, not a cancel failure
    assert report.canceled == (normal.intent_id,)
    assert report.deferred == 0

    assert h.ledger.get_intent(lost_race.intent_id).state == "ACKED"
    assert h.ledger.get_intent(normal.intent_id).state == "CANCELED"
    assert mock_order(h, lost_race).status.value == "UNFILLED"
    assert mock_order(h, normal).status.value == "CANCELED_UNFILLED"
    assert [row for row in cancel_rows(h.ledger)
            if row["intent_id"] == lost_race.intent_id] == []
    rows = [row for row in cancel_rows(h.ledger) if row["intent_id"] == normal.intent_id]
    assert len(rows) == 1 and rows[0]["reason"] == "ttl_expired"


# ---- p2: a lease lost while writing the cancel row is not swallowed ---------------------
def test_lease_lost_while_recording_a_cancel_propagates(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    deps = deps_for(h, symbols=h.symbols)
    first = resting_buy(h, symbol="OAS/JPY", key="b1")
    second = resting_buy(h, symbol="XYM/JPY", key="b2")
    h.clock.advance(46)

    real_record = h.ledger.record_live_event

    def flaky_record(lease, kind, payload, *, now):
        if payload.get("intent_id") == first.intent_id:
            raise RuntimeError("the cancel row could not be written")
        return real_record(lease, kind, payload, now=now)

    monkeypatch.setattr(h.ledger, "record_live_event", flaky_record)

    state = CycleState(cycle_number=1)
    report = cancel_expired(deps, state, h.clock.seconds())

    # A RuntimeError from the row write is swallowed: both cancels happened and the loop went on.
    assert set(report.canceled) == {first.intent_id, second.intent_id}
    assert report.failed == () and report.deferred == 0
    assert h.ledger.get_intent(first.intent_id).state == "CANCELED"
    assert h.ledger.get_intent(second.intent_id).state == "CANCELED"
    assert [row for row in cancel_rows(h.ledger)
            if row["intent_id"] == first.intent_id] == []
    assert [row["reason"] for row in cancel_rows(h.ledger)
            if row["intent_id"] == second.intent_id] == ["ttl_expired"]
    assert ("cancel", first.intent_id, "ttl_expired") not in state.dedupe
    assert state.dedupe == {("cancel", second.intent_id, "ttl_expired"): 1}

    # A LeaseLost from the row write is NOT swallowed: cancel_expired raises it to the cycle.
    third = resting_buy(h, symbol="OAS/JPY", key="b3")
    h.clock.advance(46)

    def lease_lost_record(lease, kind, payload, *, now):
        if payload.get("intent_id") == third.intent_id:
            raise LeaseLost("the writer lease is gone")
        return real_record(lease, kind, payload, now=now)

    monkeypatch.setattr(h.ledger, "record_live_event", lease_lost_record)
    lost_state = CycleState(cycle_number=2)

    with pytest.raises(LeaseLost) as lost:
        cancel_expired(deps, lost_state, h.clock.seconds())

    assert str(lost.value) == "the writer lease is gone"
    assert h.ledger.get_intent(third.intent_id).state == "CANCELED"
    assert [row for row in cancel_rows(h.ledger) if row["intent_id"] == third.intent_id] == []
    assert ("cancel", third.intent_id, "ttl_expired") not in lost_state.dedupe


# ---- p3: a cancel that loses the lease trips the latch exactly once ----------------------
def test_a_lease_lost_cancel_trips_the_latch_exactly_once(ws, harnesses):
    h = harnesses()
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={"OAS/JPY": decide_tests.rule()},
                             decide=noop_decide)
    rig.state = CycleState()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)

    steps: list[int] = []
    real_step = rig.executor.step

    def step_spy():
        steps.append(1)
        return real_step()

    rig.executor.step = step_spy
    rig.executor.cancel = lambda intent_id: (_ for _ in ()).throw(
        LeaseLost("lease gone during the cancel"))

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.EXIT_LEASE_LOST
    assert report.reason == "lease_lost"
    # The step-5 handler carries the mode; the outer run_cycle wrapper reports mode=None.
    # This is the observable difference between the handler and the wrapper for this mutant.
    assert report.mode == "trade"
    assert report.cycle_number == 1
    assert steps == []                            # nothing runs after the lost lease
    assert views.events_of_kind(h.ledger, "live_decision") == []
    assert views.events_of_kind(h.ledger, "worker_state") == []

    # Exactly one LEASE_LOST row, counted both in the persisted latch and in the event log.
    assert [trip.reason for trip in safety.read_safety_state(h.ledger).trips] == ["lease_lost"]
    trip_events = [event for event in views.events_of_kind(h.ledger, "safety_trip")
                   if event.entity_id == "lease_lost"]
    assert len(trip_events) == 1
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"
    assert [order.status.value for order in h.mock.all_orders()] == ["UNFILLED"]


# ---- p4: step 5 runs with a REAL closed gate (no mode check guards it) ------------------
def test_open_orders_are_withdrawn_when_the_gate_is_closed(ws, harnesses):
    """An expired enable file (the real closed gate of the decide g10 test) still cancels.

    The order is placed while the owner's gate is open; the enable file is then expired, so
    the step-1 gate read reports mode None.  Closing a gate that was once open also arms
    ENABLE_MISSING (the measured step-1 behavior) -- and step 5 still withdraws the order:
    the intent is CANCELED, the mock order is CANCELED_UNFILLED, exactly one ttl_expired row
    is written, the outcome is CONTINUE and report.mode stays None.
    """
    h = harnesses()
    intent = resting_buy(h, key="b1")
    assert h.clock.seconds() == NOW
    h.clock.advance(46)

    ws.write_enable(h.ps, mode="trade", created="2000-01-01T00:00:00+09:00",
                    expires="2000-01-02T00:00:00+09:00")
    closed = h._gate()
    assert closed.enabled is False and closed.mode is None      # a real closed gate

    report = run_cycle(deps_for(h), CycleState())

    assert report.outcome is CycleOutcome.CONTINUE
    assert report.mode is None and report.reason is None
    assert h.ledger.get_intent(intent.intent_id).state == "CANCELED"
    assert mock_order(h, intent).status.value == "CANCELED_UNFILLED"
    rows = [row for row in cancel_rows(h.ledger) if row["intent_id"] == intent.intent_id]
    assert len(rows) == 1
    assert rows[0]["action"] == "cancel" and rows[0]["reason"] == "ttl_expired"
    assert D(rows[0]["age_s"]) == D("46") and rows[0]["cycle"] == "1"
    # The closed gate armed the latch at step 1; the risk-reducing withdraw runs anyway.
    assert [trip.reason for trip in safety.read_safety_state(h.ledger).trips] == [
        "enable_missing"]


# ---- p5: a fill that wins the cancel race is benign through run_cycle -------------------
def test_a_cancel_race_with_a_fill_is_benign(ws, harnesses, monkeypatch):
    """CANCEL_RACE fills the whole order; the cycle records it and trips nothing.

    The mock fills the resting order while the cancel is processed, so cancel_order returns
    ALREADY_CLOSED and executor.cancel's own refresh records the fill and moves the intent
    to FILLED.  cancel_expired sees a cancel call without an error: its TtlReport has
    canceled=(intent,) and failed=(), and one ttl_expired row is written (the row records
    the request, not what the exchange did).  Measured: no active safety trip at all (in
    particular no DATA_ANOMALY), one fill of 10 @ 90 with fee 0, position net_base 10, mock
    status FULLY_FILLED, and the cycle is CONTINUE in mode trade.
    """
    h = harnesses()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)
    h.mock.faults.push("cancel", "CANCEL_RACE")

    reports: list[TtlReport] = []
    real_cancel_expired = cycle_module.cancel_expired

    def cancel_expired_spy(deps, state, now):
        report = real_cancel_expired(deps, state, now)
        reports.append(report)
        return report

    monkeypatch.setattr(cycle_module, "cancel_expired", cancel_expired_spy)

    cycle_report = run_cycle(deps_for(h), CycleState())

    assert cycle_report.outcome is CycleOutcome.CONTINUE
    assert cycle_report.mode == "trade" and cycle_report.reason is None
    assert reports == [TtlReport(canceled=(intent.intent_id,), failed=(), deferred=0)]

    assert h.ledger.get_intent(intent.intent_id).state == "FILLED"
    order = mock_order(h, intent)
    assert order.status.value == "FULLY_FILLED"
    assert order.executed_amount == D("10") and order.remaining_amount == D("0")

    fills = views.fills_for_order(h.ledger, intent.exchange_order_id)
    assert len(fills) == 1
    assert (fills[0].amount, fills[0].price, fills[0].fee_amount) == (
        D("10"), D("90"), D("0"))
    assert views.position_net_base(h.ledger, intent.position_id) == D("10")

    state = safety.read_safety_state(h.ledger)
    assert state.trips == ()
    assert state.block_new_buys is False
    assert state.unresolved_unknown_submissions == 0 and state.ledger_readable is True

    rows = [row for row in cancel_rows(h.ledger) if row["intent_id"] == intent.intent_id]
    assert len(rows) == 1
    assert (rows[0]["action"], rows[0]["reason"], rows[0]["cycle"]) == (
        "cancel", "ttl_expired", "1")
    assert D(rows[0]["age_s"]) == D("46")


# ---- p6: the documented contrast -- an order the exchange does not know ------------------
def test_an_order_unknown_to_the_exchange_trips_data_anomaly(ws, harnesses, monkeypatch):
    """OrderNotFound from cancel trips DATA_ANOMALY; the intent stays ACKED and the cycle runs on.

    The acknowledged order is removed from the mock (the exchange no longer knows it at
    all).  cancel_order raises OrderNotFound; executor.cancel catches it, calls
    ingest.anomaly (one active DATA_ANOMALY), and its refresh also gets OrderNotFound (a
    "missing" poll, not a second anomaly).  Because cancel returned without raising,
    cancel_expired counts the intent as canceled and writes a ttl_expired row even though
    nothing was withdrawn.  Measured and asserted as such: the intent is still ACKED, the
    mock has no order and there are no fills or position, block_new_buys is True, the TTL
    report has failed=(), and the cycle still reports CONTINUE in mode trade.
    """
    h = harnesses()
    intent = resting_buy(h, key="b1")
    h.clock.advance(46)
    h.mock._orders.pop(intent.exchange_order_id)       # the exchange lost the order entirely

    reports: list[TtlReport] = []
    real_cancel_expired = cycle_module.cancel_expired

    def cancel_expired_spy(deps, state, now):
        report = real_cancel_expired(deps, state, now)
        reports.append(report)
        return report

    monkeypatch.setattr(cycle_module, "cancel_expired", cancel_expired_spy)

    cycle_report = run_cycle(deps_for(h), CycleState())

    assert cycle_report.outcome is CycleOutcome.CONTINUE
    assert cycle_report.mode == "trade" and cycle_report.reason is None
    assert reports == [TtlReport(canceled=(intent.intent_id,), failed=(), deferred=0)]

    # The real state: the cancel did nothing at the exchange and the intent never advanced.
    assert h.ledger.get_intent(intent.intent_id).state == "ACKED"
    assert h.mock.all_orders() == []
    assert views.fills_for_order(h.ledger, intent.exchange_order_id) == []
    assert views.position_net_base(h.ledger, intent.position_id) == D("0")

    state = safety.read_safety_state(h.ledger)
    assert [trip.reason for trip in state.trips] == ["data_anomaly"]
    assert state.trips[0].detail == (
        f"cancel: order {intent.exchange_order_id} of intent {intent.intent_id} not found")
    assert state.block_new_buys is True
    assert state.unresolved_unknown_submissions == 0 and state.ledger_readable is True

    # The row records the request, not a completed exchange action: still exactly one.
    rows = [row for row in cancel_rows(h.ledger) if row["intent_id"] == intent.intent_id]
    assert len(rows) == 1
    assert (rows[0]["action"], rows[0]["reason"], rows[0]["cycle"]) == (
        "cancel", "ttl_expired", "1")
    assert D(rows[0]["age_s"]) == D("46")
