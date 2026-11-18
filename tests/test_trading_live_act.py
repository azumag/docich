"""The trade-mode order path (L4b-5b-2b): decided plans -> executor.plan/submit, mock only.

The ledger, lease, policy storage, mock exchange and executor are the real LIVE pieces from
the shared harness (the executor test rig, with the mock as the sender); only the reader
hooks (health/rules/books) and the public market data are faked exactly as in the decide
tests.  Every number is pinned against the hand calculation in the task; money is
``Decimal`` and compared with ``==``.  Nothing here touches the network, and no real sender
exists: ``SENDER_KINDS_ALLOWED`` stays mock-only.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import act
from docich.trading.live import decide as decide_module
from docich.trading.live import executor as executor_module
from docich.trading.live import safety as safety_module
from docich.trading.live import views
from docich.trading.live.act import (NO_SENDER_REASON, PlacementReport, decide_and_act,
                                     place_orders)
from docich.trading.live.cycle import (CycleDeps, CycleOutcome, CycleState, run_cycle)
from docich.trading.live.executor import OrderExecutor, SubmitOutcome
from docich.trading.live.lease import LeaseLost
from docich.trading.live.ledger import LiveLedgerError
from docich.trading.live.signals import LiveSignals

from test_trading_live_support import Harness, Workspace, block_network
import test_trading_live_decide as decide_tests

NOW = decide_tests.NOW
SRC = Path(act.__file__).parents[3]
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


# ---- builders -------------------------------------------------------------------------
@dataclass
class Rig:
    h: Harness
    ws: Workspace
    ps: object
    rules: dict
    reader: object
    executor: object
    gate: object
    clock: object
    market: object
    deps: CycleDeps
    deps_kwargs: dict
    state: CycleState


def make_rig(ws: Workspace, h: Harness, *, ps, rules, symbols=("OAS/JPY",),
             book_overrides=None, stop_requested=None, decide=decide_and_act,
             sender="mock") -> Rig:
    symbols = tuple(symbols)
    clock = h.clock.seconds
    gate = h._gate
    reader = decide_tests.DecideReader(h.mock)
    reader.rules = dict(rules)
    for symbol in symbols:
        reader.books[symbol] = decide_tests.book(symbol)
    if book_overrides:
        reader.books.update(book_overrides)
    executor = OrderExecutor(h.ledger, h.lease, reader=reader,
                             sender=h.mock if sender == "mock" else None,
                             enable_check=gate, clock=clock, symbols=symbols,
                             config=h.config)
    market = decide_tests.FakeMarketData(clock)
    kwargs = dict(ledger=h.ledger, lease=h.lease, executor=executor, reader=reader,
                  market_data=market, gate=gate, policy_set=ps, symbols=symbols,
                  clock=clock, stop_requested=stop_requested or (lambda: False),
                  decide=decide)
    return Rig(h=h, ws=ws, ps=ps, rules=rules, reader=reader, executor=executor,
               gate=gate, clock=clock, market=market, deps=CycleDeps(**kwargs),
               deps_kwargs=kwargs, state=CycleState(cycle_number=1))


def all_intents(ledger) -> list:
    return ledger.list_intents(states=ALL_STATES)


def submit_rows(ledger) -> list[dict]:
    return [row.payload for row in decide_tests.decision_rows(ledger)
            if row.payload["action"] == "submit"]


def skip_rows(ledger, reason: str | None = None) -> list[dict]:
    rows = [row.payload for row in decide_tests.decision_rows(ledger)
            if row.payload["action"] == "skip"]
    if reason is not None:
        rows = [row for row in rows if row["reason"] == reason]
    return rows


def buy_rig(ws, h, *, symbols, ps, oids=("buy-1", "buy-2"), sender="mock",
            stop_requested=None) -> tuple[Rig, tuple]:
    """A two-buy decision set: the first plan is ``oids[0]`` (same score, id order)."""
    rules = {symbol: decide_tests.rule(symbol) for symbol in symbols}
    rig = make_rig(ws, h, ps=ps, rules=rules, symbols=symbols, sender=sender,
                   stop_requested=stop_requested)
    entries = tuple(decide_tests.opportunity(symbols[index], "buy", oid=oid)
                    for index, oid in enumerate(oids))
    return rig, entries


# ---- a1: sells first, then buys --------------------------------------------------------
def test_trade_mode_places_sells_before_buys(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    h.mock.adjust_balance("OAS", "10")
    decide_tests.fill_position(h, ps, amount="10", price="100")
    # The fixture fill was recorded directly in the ledger, so move the mock's JPY by the
    # same 1000 notional: exchange total 9000 + position cost 1000 = capital 10000.
    h.mock.adjust_balance("JPY", "-1000")
    rig = make_rig(ws, h, ps=ps,
                   rules={"OAS/JPY": decide_tests.rule(),
                          "XYM/JPY": decide_tests.rule("XYM/JPY")},
                   symbols=("OAS/JPY", "XYM/JPY"),
                   book_overrides={"OAS/JPY": decide_tests.book("OAS/JPY"),
                                   "XYM/JPY": decide_tests.book("XYM/JPY")})
    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    buy = decide_tests.opportunity("XYM/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (sell,), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")

    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None
    assert len(built.result.sells) == 1 and len(built.result.buys) == 1
    sell_plan = built.result.sells[0]
    buy_plan = built.result.buys[0]
    # Hand numbers: the 10 OAS held are sold at the 100 bid; XYM buys 5 @ 100 because the
    # 0.05 opportunity caps the notional at 500 (capital 10000) and the headroom covers it.
    assert (sell_plan.symbol, sell_plan.side, sell_plan.amount, sell_plan.price) == (
        "OAS/JPY", "sell", D("10"), D("100"))
    assert (buy_plan.symbol, buy_plan.side, buy_plan.amount, buy_plan.price) == (
        "XYM/JPY", "buy", D("5"), D("100"))
    assert buy_plan.required_quote == D("501.5")

    report = place_orders(rig.deps, rig.state, ctx, built)

    assert isinstance(report, PlacementReport)
    assert report.placed == ((sell_plan.decision_key, "acked"),
                             (buy_plan.decision_key, "acked"))
    assert report.skipped == () and report.stopped is None
    # The mock sender saw the sell first, then the buy, with the plan's own numbers.
    assert [item["side"] for item in h.mock.submissions] == ["sell", "buy"]
    assert [item["amount"] for item in h.mock.submissions] == ["10", "5"]
    assert [item["price"] for item in h.mock.submissions] == ["100", "100"]

    intents = all_intents(h.ledger)
    # ``fill_position`` records the position's own seed intent; the two intents this cycle
    # placed are the sell and the buy, so the ledger holds three rows in total.
    assert len(intents) == 3
    placed_intents = [item for item in intents
                      if item.decision_key in (sell_plan.decision_key, buy_plan.decision_key)]
    assert {item.decision_key for item in placed_intents} == {
        sell_plan.decision_key, buy_plan.decision_key}
    by_key = {intent.decision_key: intent for intent in intents}
    assert D(by_key[sell_plan.decision_key].amount) == sell_plan.amount
    assert D(by_key[sell_plan.decision_key].price) == sell_plan.price
    assert D(by_key[buy_plan.decision_key].amount) == buy_plan.amount
    assert D(by_key[buy_plan.decision_key].price) == buy_plan.price
    assert by_key[sell_plan.decision_key].state == "ACKED"
    assert by_key[buy_plan.decision_key].state == "ACKED"

    rows = submit_rows(h.ledger)
    assert [(row["side"], row["symbol"], row["reason"], row["decision_key"])
            for row in rows] == [
        ("sell", "OAS/JPY", "acked", sell_plan.decision_key),
        ("buy", "XYM/JPY", "acked", buy_plan.decision_key)]


# ---- a2: shadow and a senderless trade executor create no intent -----------------------
def test_shadow_and_a_senderless_trade_executor_create_no_intent(ws, harnesses,
                                                                 monkeypatch):
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()}, sender=None)
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    def forbidden(*args, **kwargs):
        raise AssertionError("executor.plan must never be called")

    rig.executor.plan = forbidden

    decide_and_act(rig.deps, rig.state, decide_tests.make_ctx(rig, mode="shadow"))
    assert all_intents(h.ledger) == []

    for cycle in (1, 2, 3):
        rig.state.cycle_number = cycle
        decide_and_act(rig.deps, rig.state, decide_tests.make_ctx(rig, mode="trade"))
        assert all_intents(h.ledger) == []

    rows = skip_rows(h.ledger, NO_SENDER_REASON)
    assert len(rows) == 1                      # one row for the whole process, not per cycle
    payload = rows[0]
    assert payload["action"] == "skip" and payload["reason"] == NO_SENDER_REASON
    assert payload["side"] == "unknown" and payload["symbol"] == "-"
    assert payload["opportunity_id"] == "-" and payload["mode"] == "trade"
    assert payload["cycle"] == "1"
    assert all(isinstance(value, str) for value in payload.values())
    assert ("skip", "-", NO_SENDER_REASON) in rig.state.dedupe
    assert rig.state.dedupe[("skip", "-", NO_SENDER_REASON)] == sys.maxsize


# ---- a3: a stop request after the first order ------------------------------------------
def test_a_stop_request_after_the_first_order_prevents_the_second(ws, harnesses,
                                                                  monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    flag = {"stop": False}
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps,
                           stop_requested=lambda: flag["stop"])
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 2
    first_key = built.result.buys[0].decision_key

    real_submit = rig.executor.submit
    calls: list[str] = []

    def submit_then_stop(intent_id):
        outcome = real_submit(intent_id)
        calls.append(intent_id)
        flag["stop"] = True
        return outcome

    rig.executor.submit = submit_then_stop
    report = place_orders(rig.deps, rig.state, ctx, built)

    assert report.placed == ((first_key, "acked"),)
    assert report.skipped == ()
    assert report.stopped == "stop_requested"
    assert len(calls) == 1
    intents = all_intents(h.ledger)
    assert len(intents) == 1 and intents[0].decision_key == first_key


# ---- a4: exceptions leave no PLANNED / SUBMITTING intent ------------------------------
_FAULT_CASES = (
    # hook point -> state right after act -> state after the next executor.step()
    ("before_mark_submitting", "ABANDONED", "ABANDONED"),
    ("after_send_before_record", "UNKNOWN_SUBMISSION", "ACKED"),
    ("after_order_recorded", "UNKNOWN_SUBMISSION", "ACKED"),
    ("after_ack_transition", "ACKED", "ACKED"),
)


@pytest.mark.parametrize("hook_point,after_act,after_step", _FAULT_CASES)
def test_exceptions_never_leave_planned_or_submitting_behind(ws, harnesses,
                                                             monkeypatch, hook_point,
                                                             after_act, after_step):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 2
    first_key = built.result.buys[0].decision_key
    second_key = built.result.buys[1].decision_key

    def hook(point):
        if point == hook_point:
            raise RuntimeError(point)

    executor_module.FAULT_HOOK = hook
    try:
        report = place_orders(rig.deps, rig.state, ctx, built)
    finally:
        executor_module.FAULT_HOOK = None

    assert report.stopped == "submit_error"
    assert report.placed == ()
    assert report.skipped == ((first_key, "submit_failed:RuntimeError"),)
    assert views.intent_by_decision_key(h.ledger, second_key) is None
    # Only the first plan ever reaches the exchange; before the submitting barrier not even
    # that one leaves.  The post-send cases therefore have the order at the mock.
    expected_submissions = 0 if hook_point == "before_mark_submitting" else 1
    assert len(h.mock.submissions) == expected_submissions
    intents = all_intents(h.ledger)
    assert len(intents) == 1
    assert intents[0].decision_key == first_key
    assert intents[0].state == after_act
    if hook_point == "before_mark_submitting":
        assert intents[0].reason_code == "act_exception:RuntimeError"
    assert not [item for item in intents if item.state in ("PLANNED", "SUBMITTING")]

    # The next step reconciles what the crash left: ACKED stays, UNKNOWN becomes ACKED,
    # an abandoned intent is untouched.  The mock order exists for both post-send cases.
    rig.executor.step()
    intents = all_intents(h.ledger)
    assert len(intents) == 1
    assert intents[0].state == after_step
    assert not [item for item in intents if item.state in ("PLANNED", "SUBMITTING")]
    assert views.intent_by_decision_key(h.ledger, second_key) is None


# ---- a5: outcome handling --------------------------------------------------------------
@pytest.mark.parametrize("case", ["rejected", "local_invalid", "unknown", "blocked"])
def test_outcome_handling(ws, harnesses, monkeypatch, case):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 2
    first_key = built.result.buys[0].decision_key
    second_key = built.result.buys[1].decision_key

    if case == "rejected":
        h.mock.faults.push("submit", "REJECT", nth=1, code="rejected")
    elif case == "local_invalid":
        # The exchange rules the executor reads are stricter than the sizer's market:
        # the amount is below the minimum, so the local check refuses the order.
        rig.reader.rules["OAS/JPY"] = decide_tests.rule(min_amount="1000")
    elif case == "unknown":
        h.mock.faults.push("submit", "LOST_REQUEST", nth=1)
    else:  # blocked: the owner's gate is no longer open when the submit happens
        ws.write_control(mode="shadow")

    report = place_orders(rig.deps, rig.state, ctx, built)
    first = h.ledger.get_intent(
        views.intent_by_decision_key(h.ledger, first_key).intent_id)

    if case == "rejected":
        assert report.placed == ((first_key, "rejected"), (second_key, "acked"))
        assert report.stopped is None
        assert first.state == "REJECTED" and first.reason_code == "rejected"
        assert [item["outcome"] for item in h.mock.submissions] == ["rejected", "created"]
        assert len(all_intents(h.ledger)) == 2
    elif case == "local_invalid":
        assert report.placed == ((first_key, "local_invalid"), (second_key, "acked"))
        assert report.stopped is None
        assert first.state == "ABANDONED" and first.reason_code == "local_invalid"
        assert [item["outcome"] for item in h.mock.submissions] == ["created"]
        assert len(all_intents(h.ledger)) == 2
    elif case == "unknown":
        assert report.placed == ((first_key, "unknown"),)
        assert report.stopped == "unknown"
        assert first.state == "UNKNOWN_SUBMISSION"
        assert h.mock.submissions == []       # LOST_REQUEST never reaches the book
        assert views.intent_by_decision_key(h.ledger, second_key) is None
        assert len(all_intents(h.ledger)) == 1
    else:
        assert report.placed == ((first_key, "blocked"),)
        assert report.stopped == "blocked"
        assert first.state == "ABANDONED" and first.reason_code == "gate_closed"
        assert h.mock.submissions == []
        assert views.intent_by_decision_key(h.ledger, second_key) is None
        assert len(all_intents(h.ledger)) == 1


# ---- a6: plan errors and LeaseLost -----------------------------------------------------
@pytest.mark.parametrize("case", ["ledger", "value", "other"])
def test_plan_errors(ws, harnesses, monkeypatch, case):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 2
    first_key = built.result.buys[0].decision_key
    second_key = built.result.buys[1].decision_key

    error = {"ledger": LiveLedgerError("plan refused"),
             "value": ValueError("bad plan"),
             "other": RuntimeError("plan down")}[case]
    real_plan = rig.executor.plan
    calls: list[int] = []

    def failing_plan(req, _error=error):
        if not calls:
            calls.append(1)
            raise _error
        return real_plan(req)

    rig.executor.plan = failing_plan
    report = place_orders(rig.deps, rig.state, ctx, built)

    if case in ("ledger", "value"):
        name = type(error).__name__
        assert report.skipped == ((first_key, f"plan_failed:{name}"),)
        assert report.placed == ((second_key, "acked"),)
        assert report.stopped is None
        assert len(all_intents(h.ledger)) == 1
        assert views.intent_by_decision_key(h.ledger, second_key) is not None
    else:
        assert report.skipped == ((first_key, "plan_failed:RuntimeError"),)
        assert report.placed == ()
        assert report.stopped == "plan_error"
        assert all_intents(h.ledger) == []
        assert views.intent_by_decision_key(h.ledger, second_key) is None


@pytest.mark.parametrize("source", ["plan", "submit"])
def test_lease_lost_from_plan_or_submit_propagates(ws, harnesses, monkeypatch, source):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 2
    first_key = built.result.buys[0].decision_key
    second_key = built.result.buys[1].decision_key

    if source == "plan":
        rig.executor.plan = lambda req: (_ for _ in ()).throw(LeaseLost("lease gone"))
    else:
        rig.executor.submit = lambda intent_id: (_ for _ in ()).throw(
            LeaseLost("lease gone"))

    with pytest.raises(LeaseLost):
        decide_and_act(rig.deps, rig.state, ctx)

    assert views.intent_by_decision_key(h.ledger, second_key) is None
    intents = all_intents(h.ledger)
    if source == "plan":
        assert intents == []
    else:
        assert len(intents) == 1
        assert intents[0].decision_key == first_key
        assert intents[0].state == "PLANNED"


# ---- a7: buys stop at the cycle budget, sells always go --------------------------------
def test_buys_stop_at_the_cycle_budget_but_sells_always_go(ws, harnesses, monkeypatch):
    symbols = ("OAS/JPY", "XYM/JPY", "ZEC/JPY")
    h = harnesses(symbols=symbols)
    ps = decide_tests.policy_set_for(*symbols)
    decide_tests.store(h, ps)
    h.mock.adjust_balance("OAS", "10")
    decide_tests.fill_position(h, ps, amount="10", price="100")
    rules = {symbol: decide_tests.rule(symbol) for symbol in symbols}
    rig = make_rig(ws, h, ps=ps, rules=rules, symbols=symbols,
                   book_overrides={symbol: decide_tests.book(symbol) for symbol in symbols})
    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    buys = (decide_tests.opportunity("XYM/JPY", "buy", oid="buy-xym"),
            decide_tests.opportunity("ZEC/JPY", "buy", oid="buy-zec"))
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(buys, (sell,), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None
    assert len(built.result.sells) == 1 and len(built.result.buys) == 2
    sell_key = built.result.sells[0].decision_key
    buy_keys = tuple(plan.decision_key for plan in built.result.buys)

    # The budget crosses only after the sell is placed: the sells always go, the buys stop.
    real_submit = rig.executor.submit

    def submit_and_advance(intent_id):
        outcome = real_submit(intent_id)
        h.clock.advance(41)
        return outcome

    rig.executor.submit = submit_and_advance
    report = place_orders(rig.deps, rig.state, ctx, built)

    assert report.placed == ((sell_key, "acked"),)
    assert report.skipped == ((buy_keys[0], "cycle_budget"), (buy_keys[1], "cycle_budget"))
    assert report.stopped == "cycle_budget"
    assert [item["side"] for item in h.mock.submissions] == ["sell"]
    intents = all_intents(h.ledger)
    # The fixture position's seed intent plus the placed sell; no buy plan got an intent.
    assert len(intents) == 2
    assert [item.decision_key for item in intents if item.decision_key == sell_key] == [sell_key]
    assert not [item for item in intents if item.decision_key in buy_keys]
    recorded = skip_rows(h.ledger, "cycle_budget")
    assert [(row["side"], row["symbol"]) for row in recorded] == [
        ("buy", "XYM/JPY"), ("buy", "ZEC/JPY")]


# ---- a8: a book that aged (or is from the future) at placement -------------------------
def test_a_book_that_aged_before_placement_is_not_traded(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()})
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    def forbidden(*args, **kwargs):
        raise AssertionError("executor.plan must not be called for an unusable book")

    rig.executor.plan = forbidden

    # 16 s old at placement: the book was fresh when the decision was built.
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 1
    plan_key = built.result.buys[0].decision_key
    h.clock.advance(16)
    report = place_orders(rig.deps, rig.state, ctx, built)
    assert report.placed == ()
    assert report.skipped == ((plan_key, "stale_book"),)
    rows = skip_rows(h.ledger, "stale_book")
    assert len(rows) == 1 and rows[0]["side"] == "buy"

    # 11 s in the future at placement (the clock was rolled back after the build).
    h.clock.advance(-16)
    assert rig.clock() == NOW
    rig.reader.books["OAS/JPY"] = decide_tests.book("OAS/JPY", ts=int(NOW * 1000))
    ctx2 = decide_tests.make_ctx(rig, mode="trade")
    built2 = decide_module.build_decisions(rig.deps, rig.state, ctx2)
    assert built2.result is not None and len(built2.result.buys) == 1
    h.clock.advance(-11)
    report2 = place_orders(rig.deps, rig.state, ctx2, built2)
    assert report2.placed == ()
    assert report2.skipped == ((built2.result.buys[0].decision_key, "book_from_future"),)
    rows = skip_rows(h.ledger, "book_from_future")
    assert len(rows) == 1 and rows[0]["side"] == "buy"


# ---- a9: a same-symbol sell blocks the buy --------------------------------------------
def test_same_symbol_sell_blocks_the_buy(ws, harnesses, monkeypatch):
    """``scan_live`` itself never emits this pair (an exit suppresses the entry), so the
    signals are hand-built here: the size step plans both, and placement must keep only the
    sell."""
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    h.mock.adjust_balance("OAS", "10")
    decide_tests.fill_position(h, ps, amount="10", price="100")
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()})
    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (sell,), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None
    assert len(built.result.sells) == 1 and len(built.result.buys) == 1
    sell_key = built.result.sells[0].decision_key
    buy_key = built.result.buys[0].decision_key

    report = place_orders(rig.deps, rig.state, ctx, built)

    assert report.placed == ((sell_key, "acked"),)
    assert report.skipped == ((buy_key, "sell_planned_same_symbol"),)
    assert report.stopped is None
    assert [item["side"] for item in h.mock.submissions] == ["sell"]
    intents = all_intents(h.ledger)
    # The fixture position's seed intent plus the placed sell; the same-symbol buy never
    # got an intent.
    assert len(intents) == 2
    assert [item.decision_key for item in intents if item.decision_key == sell_key] == [sell_key]
    assert views.intent_by_decision_key(h.ledger, buy_key) is None
    rows = skip_rows(h.ledger, "sell_planned_same_symbol")
    assert len(rows) == 1
    assert (rows[0]["side"], rows[0]["symbol"]) == ("buy", "OAS/JPY")


# ---- a10: an unrelated account balance difference changes no order decision ----------------
def test_an_unrelated_account_balance_difference_does_not_change_buy_sell_order(
        ws, harnesses, monkeypatch):
    symbols = ("OAS/JPY", "XYM/JPY")
    h = harnesses(symbols=symbols)
    ps = decide_tests.policy_set_for(*symbols)
    decide_tests.store(h, ps)
    rules = {symbol: decide_tests.rule(symbol) for symbol in symbols}
    # The mock validates against its own market rules: give it the same step the sizer and
    # executor read, so a 0.01-step amount such as 4.95 is not rejected by the fixture's
    # default amount_step of 1.
    for symbol in symbols:
        h.mock._markets[symbol] = rules[symbol]
    h.mock.set_book("OAS/JPY", bids=[(D("100"), D("1000"))],
                    asks=[(D("100"), D("1000"))])
    h.mock.set_book("XYM/JPY", bids=[(D("99"), D("1000"))],
                    asks=[(D("100"), D("1000"))])
    rig = make_rig(ws, h, ps=ps, rules=rules, symbols=symbols,
                   book_overrides={symbol: decide_tests.book(symbol) for symbol in symbols})
    rig.state = CycleState()                   # run_cycle numbers the cycles from 1

    # One buy through the mock sender that fills at 100: the ledger and the exchange agree.
    seed = decide_tests.open_buy(h, ps, amount="10", price="100")
    assert rig.executor.submit(seed.intent_id) is SubmitOutcome.ACKED
    assert h.ledger.get_intent(seed.intent_id).state == "FILLED"
    assert h.ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED")) == []

    # Now the exchange's JPY total drifts 100 below the ledger expectation.
    h.mock.adjust_balance("JPY", "-100")

    signals = {"value": LiveSignals((), (), (), {})}
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: signals["value"])

    cycle1 = run_cycle(rig.deps, rig.state)
    assert cycle1.outcome is CycleOutcome.CONTINUE
    assert safety_module.read_safety_state(h.ledger).trips == ()

    h.clock.advance(31)                        # the next cycle
    for symbol in symbols:
        rig.reader.books[symbol] = decide_tests.book(symbol, ts=int(rig.clock() * 1000))
    buy = decide_tests.opportunity("XYM/JPY", "buy", oid="buy-xym")
    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-oas")
    signals["value"] = LiveSignals((buy,), (sell,), (), {})
    submissions_before = len(h.mock.submissions)

    cycle2 = run_cycle(rig.deps, rig.state)

    # The account difference is not judged, warned about, or turned into a safety row; the
    # decisions and their order are unaffected.
    assert rig.state.cycle_number == 2
    assert cycle2.outcome is CycleOutcome.CONTINUE
    assert safety_module.read_safety_state(h.ledger).trips == ()
    assert [reason for reason in h.trips()] == []
    assert views.events_of_kind(h.ledger, "safety_trip") == []

    rows = decide_tests.decision_rows(h.ledger)
    blocked = [row.payload for row in rows
               if row.payload["action"] == "skip"
               and row.payload["reason"] == "buys_blocked_safety"]
    assert blocked == []
    sell_plans = [row.payload for row in rows
                  if row.payload["action"] == "plan" and row.payload["side"] == "sell"]
    buy_plans = [row.payload for row in rows
                 if row.payload["action"] == "plan" and row.payload["side"] == "buy"]
    assert len(sell_plans) == 1 and sell_plans[0]["symbol"] == "OAS/JPY"
    assert len(buy_plans) == 1 and buy_plans[0]["symbol"] == "XYM/JPY"

    # Both the sell and the buy went out through the mock sender in decision order.
    assert [item["side"] for item in h.mock.submissions[submissions_before:]] == [
        "sell", "buy"]
    assert h.mock.submissions[-1]["amount"] == "4.95"
    assert h.mock.submissions[-1]["price"] == "100"
    exit_intent = views.intent_by_decision_key(h.ledger, sell_plans[0]["decision_key"])
    assert exit_intent is not None and exit_intent.side == "sell"
    assert exit_intent.state in ("ACKED", "FILLED")
    buy_intent = views.intent_by_decision_key(h.ledger, buy_plans[0]["decision_key"])
    assert buy_intent is not None and buy_intent.side == "buy"
    assert buy_intent.state in ("ACKED", "FILLED")


# ---- a11: the hook through run_cycle ---------------------------------------------------
def test_the_hook_through_run_cycle(ws, harnesses, monkeypatch):
    h = harnesses(gate_open=False)
    ps = h.ps
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()}, sender="mock")
    rig.state = CycleState()                   # run_cycle numbers the cycles from 1
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    # Shadow: the decision is recorded, no order exists.
    ws.write_control(mode="shadow")
    shadow = run_cycle(rig.deps, rig.state)
    assert shadow.mode == "shadow" and shadow.outcome is CycleOutcome.CONTINUE
    assert all_intents(h.ledger) == []
    assert len(views.events_of_kind(h.ledger, "shadow_fill")) == 1

    # Trade: the hook places the buy through the mock sender.
    ws.write_control(mode="trade")
    trade = run_cycle(rig.deps, rig.state)
    assert trade.mode == "trade"
    intents = all_intents(h.ledger)
    assert len(intents) == 1 and intents[0].side == "buy"
    assert [item["side"] for item in h.mock.submissions] == ["buy"]

    # The default CycleDeps (no decide argument) keeps the no-op placeholder.
    default_deps = CycleDeps(**{key: value for key, value in rig.deps_kwargs.items()
                                if key != "decide"})
    again = run_cycle(default_deps, rig.state)
    assert again.mode == "trade"
    assert len(all_intents(h.ledger)) == 1
    assert [item["side"] for item in h.mock.submissions] == ["buy"]


# ---- a12: imports and static order-call check ------------------------------------------
def test_imports_and_the_only_order_calls(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.act
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

    act_calls, act_exits = calls(Path(act.__file__))
    assert act_exits == []
    assert ".plan" in act_calls and ".submit" in act_calls
    assert ".cancel" not in act_calls
    assert ".create_intent" not in act_calls

    decide_calls, decide_exits = calls(Path(decide_module.__file__))
    assert decide_exits == []
    assert decide_calls & {".plan", ".submit", ".cancel", ".create_intent"} == set()

    # The real sender does not exist yet: production wiring stays mock-only.
    assert executor_module.SENDER_KINDS_ALLOWED == frozenset({"mock"})


# ---- n1: a cycle already over budget still sells ---------------------------------------
def test_sells_are_placed_even_when_the_budget_is_already_spent(ws, harnesses,
                                                                monkeypatch):
    symbols = ("OAS/JPY", "XYM/JPY", "ZEC/JPY")
    h = harnesses(symbols=symbols)
    ps = decide_tests.policy_set_for(*symbols)
    decide_tests.store(h, ps)
    h.mock.adjust_balance("OAS", "10")
    decide_tests.fill_position(h, ps, amount="10", price="100")
    rules = {symbol: decide_tests.rule(symbol) for symbol in symbols}
    rig = make_rig(ws, h, ps=ps, rules=rules, symbols=symbols,
                   book_overrides={symbol: decide_tests.book(symbol) for symbol in symbols})
    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    buys = (decide_tests.opportunity("XYM/JPY", "buy", oid="buy-xym"),
            decide_tests.opportunity("ZEC/JPY", "buy", oid="buy-zec"))
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(buys, (sell,), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None
    assert len(built.result.sells) == 1 and len(built.result.buys) == 2
    assert built.over_budget is False
    sell_key = built.result.sells[0].decision_key
    buy_keys = tuple(plan.decision_key for plan in built.result.buys)

    # The clock is 41 s into the cycle before the first order; every book is stamped at that
    # instant too, so only the budget rule can skip a plan.
    from dataclasses import replace

    h.clock.advance(41)
    assert rig.clock() == ctx.now + 41
    built = replace(built, markets={
        symbol: replace(
            market, book=decide_tests.book(symbol, ts=int(rig.clock() * 1000)))
        for symbol, market in built.markets.items()})

    report = place_orders(rig.deps, rig.state, ctx, built)

    assert report.placed == ((sell_key, "acked"),)
    assert report.skipped == ((buy_keys[0], "cycle_budget"), (buy_keys[1], "cycle_budget"))
    assert report.stopped == "cycle_budget"
    assert [item["side"] for item in h.mock.submissions] == ["sell"]
    intents = all_intents(h.ledger)
    # The fixture position's seed intent plus the placed sell; no buy plan got an intent.
    assert len(intents) == 2
    assert [item.decision_key for item in intents if item.decision_key == sell_key] == [sell_key]
    assert not [item for item in intents if item.decision_key in buy_keys]
    assert [(row["side"], row["symbol"], row["decision_key"])
            for row in submit_rows(h.ledger)] == [("sell", "OAS/JPY", sell_key)]
    recorded = skip_rows(h.ledger, "cycle_budget")
    assert [(row["side"], row["symbol"]) for row in recorded] == [
        ("buy", "XYM/JPY"), ("buy", "ZEC/JPY")]


# ---- n2: the budget boundary is exclusive ----------------------------------------------
def test_a_buy_is_still_placed_at_exactly_the_budget(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()})
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 1
    buy_key = built.result.buys[0].decision_key

    from dataclasses import replace

    def stamped_at(value, at: float):
        """The same decisions with every book stamped at the placement clock."""
        return replace(value, markets={
            symbol: replace(
                market, book=decide_tests.book(symbol, ts=int(at * 1000)))
            for symbol, market in value.markets.items()})

    h.clock.advance(40.0)
    assert rig.clock() == ctx.now + 40.0
    report = place_orders(rig.deps, rig.state, ctx, stamped_at(built, rig.clock()))
    assert report.placed == ((buy_key, "acked"),)
    assert report.skipped == () and report.stopped is None
    assert [item["side"] for item in h.mock.submissions] == ["buy"]

    h.clock.advance(0.001)
    assert rig.clock() - ctx.now == pytest.approx(40.001)
    late = place_orders(rig.deps, rig.state, ctx, stamped_at(built, rig.clock()))
    assert late.placed == ()
    assert late.skipped == ((buy_key, "cycle_budget"),)
    assert late.stopped == "cycle_budget"
    assert [item["side"] for item in h.mock.submissions] == ["buy"]


# ---- n3: a plan without a market is skipped, never planned ------------------------------
def test_a_plan_without_a_market_is_skipped_book_unavailable(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 2
    keys = tuple(plan.decision_key for plan in built.result.buys)
    from dataclasses import replace

    hand_built = replace(built, markets={})

    calls = []

    def forbidden(req):
        calls.append(req)
        raise AssertionError("executor.plan must not be called without a market")

    rig.executor.plan = forbidden
    report = place_orders(rig.deps, rig.state, ctx, hand_built)

    assert calls == []
    assert report.placed == ()
    assert report.skipped == ((keys[0], "book_unavailable"), (keys[1], "book_unavailable"))
    assert report.stopped is None
    assert [item["side"] for item in h.mock.submissions] == []
    assert all_intents(h.ledger) == []
    recorded = skip_rows(h.ledger, "book_unavailable")
    assert [(row["side"], row["symbol"]) for row in recorded] == [
        ("buy", "OAS/JPY"), ("buy", "XYM/JPY")]


# ---- n4: a lost lease while recording stops the loop; other write errors do not ---------
def test_lease_lost_while_recording_propagates(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
    decide_tests.store(h, ps)
    rig, entries = buy_rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"), ps=ps)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    real_record = h.ledger.record_live_event
    attempted: list[str] = []

    def record_lease_lost(lease, kind, payload, *, now):
        if payload.get("action") == "submit":
            attempted.append(payload["decision_key"])
            raise LeaseLost("lease gone")
        return real_record(lease, kind, payload, now=now)

    monkeypatch.setattr(h.ledger, "record_live_event", record_lease_lost)
    with pytest.raises(LeaseLost):
        decide_and_act(rig.deps, rig.state, decide_tests.make_ctx(rig, mode="trade"))

    assert len(attempted) == 1
    # The first buy reached the mock before its record failed; the second never did.
    assert [item["side"] for item in h.mock.submissions] == ["buy"]
    intents = all_intents(h.ledger)
    assert len(intents) == 1
    assert intents[0].decision_key == attempted[0]
    assert intents[0].state == "ACKED"

    # A RuntimeError from the same record is swallowed: the loop reaches the second buy.
    # A second harness needs its own workspace: the writer lease is exclusive per state dir.
    ws2 = Workspace()
    h2 = Harness(ws2, symbols=("OAS/JPY", "XYM/JPY"))
    try:
        ps2 = decide_tests.policy_set_for("OAS/JPY", "XYM/JPY")
        decide_tests.store(h2, ps2)
        rig2, entries2 = buy_rig(ws2, h2, symbols=("OAS/JPY", "XYM/JPY"), ps=ps2)
        monkeypatch.setattr(decide_module, "scan_live",
                            lambda *a, **k: LiveSignals(entries2, (), (), {}))
        real_record2 = h2.ledger.record_live_event
        failed: list[str] = []

        def record_down(lease, kind, payload, *, now):
            if payload.get("action") == "submit" and not failed:
                failed.append(payload["decision_key"])
                raise RuntimeError("record down")
            return real_record2(lease, kind, payload, now=now)

        monkeypatch.setattr(h2.ledger, "record_live_event", record_down)
        ctx2 = decide_tests.make_ctx(rig2, mode="trade")
        built2 = decide_module.build_decisions(rig2.deps, rig2.state, ctx2)
        assert built2.result is not None and len(built2.result.buys) == 2
        keys2 = tuple(plan.decision_key for plan in built2.result.buys)
        report2 = place_orders(rig2.deps, rig2.state, ctx2, built2)

        assert failed == [keys2[0]]
        assert report2.placed == ((keys2[0], "acked"), (keys2[1], "acked"))
        assert report2.skipped == () and report2.stopped is None
        assert [item["side"] for item in h2.mock.submissions] == ["buy", "buy"]
        intents2 = all_intents(h2.ledger)
        assert len(intents2) == 2
        assert {item.decision_key for item in intents2} == set(keys2)
    finally:
        h2.close()
        ws2.cleanup()


# ---- n5: the submit row names the intent -----------------------------------------------
def test_the_submit_row_names_the_intent(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()})
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    ctx = decide_tests.make_ctx(rig, mode="trade")
    built = decide_module.build_decisions(rig.deps, rig.state, ctx)
    assert built.result is not None and len(built.result.buys) == 1
    plan = built.result.buys[0]

    report = place_orders(rig.deps, rig.state, ctx, built)
    assert report.placed == ((plan.decision_key, SubmitOutcome.ACKED.value),)
    rows = submit_rows(h.ledger)
    assert len(rows) == 1
    payload = rows[0]
    intent = views.intent_by_decision_key(h.ledger, plan.decision_key)
    assert intent is not None
    assert payload["intent_id"] == intent.intent_id
    assert payload["intent_id"] != ""
    assert payload["reason"] == SubmitOutcome.ACKED.value
    assert payload["decision_key"] == plan.decision_key


# ---- n6: the no-sender notice survives the dedupe prune --------------------------------
def test_the_no_sender_notice_survives_dedupe_pruning(ws, harnesses, monkeypatch):
    """The notice is pinned: a long run's prune must not let the row be written again."""
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()}, sender=None)
    monkeypatch.setattr(decide_module, "DEDUPE_MAX_ENTRIES", 3)
    monkeypatch.setattr(decide_module, "DEDUPE_KEEP_CYCLES", 2)

    key = ("skip", "-", NO_SENDER_REASON)
    rig.state.cycle_number = 1
    decide_and_act(rig.deps, rig.state, decide_tests.make_ctx(rig, mode="trade"))
    assert len(skip_rows(h.ledger, NO_SENDER_REASON)) == 1
    assert key in rig.state.dedupe

    # Fill the dict past the small cap with low cycle numbers.  These values survive the
    # cutoffs of cycles 2..4 and are pruned at cycle 5, so the prune runs while the notice
    # key is far older than DEDUPE_KEEP_CYCLES.  The notice was recorded at cycle 1.
    for index in range(4):
        rig.state.dedupe[("skip", f"dummy-{index}", "stale_book")] = 2

    for cycle in range(2, 11):
        rig.state.cycle_number = cycle
        decide_and_act(rig.deps, rig.state, decide_tests.make_ctx(rig, mode="trade"))

    assert len(skip_rows(h.ledger, NO_SENDER_REASON)) == 1
    assert key in rig.state.dedupe
    assert rig.state.dedupe[key] == sys.maxsize
