"""LIVE decisions: signals -> inputs -> books -> sizing -> recorded decisions (L4b-5b-2a).

The ledger, lease, policy storage, mock exchange and executor are the real LIVE pieces
from the shared harness; only the reader hooks (health/reload/balance/rules/books) and the
public market data are faked, as in the cycle tests.  Every number is pinned against the
hand calculation in the task; money is ``Decimal`` and compared with ``==``.  Nothing here
touches the network, and no order is planned, sent or intended: the decision event log is
the only write this module makes.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import decide as decide_module
from docich.trading.live import views
from docich.trading.live.cycle import CycleContext, CycleDeps, CycleOutcome, CycleState
from docich.trading.live.decide import build_decisions, decide_and_act
from docich.trading.live.exchange import MarketRules, OrderBook
from docich.trading.live.executor import OrderExecutor
from docich.trading.live.lease import LeaseLost
from docich.trading.live.ledger import (FillRecord, IntentRequest, LiveLedgerError,
                                        OrderObservation)
from docich.trading.live.policy import build_policy, policy_set_from_policies
from docich.trading.live.safety import SafetyReason, read_safety_state
from docich.trading.live.signals import LiveSignals
from docich.trading.market_data import MarketFrame
from docich.trading.models import Opportunity
from docich.trading.strategy_lab import Condition, Rule, StrategyExperiment

from test_trading_live_support import Harness, Workspace, block_network
import test_trading_live_signals as signals_tests

NOW = 1_700_000_000.0
SRC = Path(decide_module.__file__).parents[3]


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
def spec(experiment_id: str = "exp-1", *, entry_fraction: str = "0.30",
         trigger_fraction: str = "0.05") -> StrategyExperiment:
    """A policy whose fraction is 0.30; its firing entry rule asks for only 0.05."""
    return StrategyExperiment(
        experiment_id=experiment_id,
        name=f"{experiment_id} name",
        thesis=f"{experiment_id} thesis",
        entry_rules=(
            Rule("entry-1", "all",
                 (Condition("return_bps", ">=", D("10000"), 2),), D(entry_fraction)),
            Rule("entry-2", "all",
                 (Condition("return_bps", "<=", D("-100"), 2),), D(trigger_fraction)),
        ),
        exit_rules=(
            Rule("exit-1", "all", (Condition("hold_minutes", ">=", D("100000")),),
                 D("0.10")),
        ),
        max_pair_correlation=D("0.85"),
    )


def policy_set_for(*symbols):
    return policy_set_from_policies(
        {symbol: build_policy(spec(), symbol) for symbol in symbols}
    )


def rule(symbol: str = "OAS/JPY", *, fee: str | None = "0.001", step: str = "0.01",
         min_amount: str = "0.01", tick: str = "1", quote_unit: str = "1",
         base_unit: str = "0.01") -> MarketRules:
    base, quote = symbol.split("/")
    return MarketRules(
        symbol=symbol, base=base, quote=quote, amount_step=D(step),
        min_amount=D(min_amount), price_tick=D(tick), base_unit=D(base_unit),
        quote_unit=D(quote_unit),
        max_fee_rate=None if fee is None else D(fee),
    )


def frame(symbol: str = "OAS/JPY", *, price: str = "100", at: float = NOW,
          timeframe: int = 300) -> MarketFrame:
    return MarketFrame(symbol=symbol, timeframe_seconds=timeframe,
                       timestamps=(float(at),), closes=(D(price),), volumes=(D("1"),))


def book(symbol: str = "OAS/JPY", *, bids=(("100", "1000"),), asks=(("100", "1000"),),
         ts: int | None = None) -> OrderBook:
    return OrderBook(
        symbol=symbol,
        bids=tuple((D(price), D(amount)) for price, amount in bids),
        asks=tuple((D(price), D(amount)) for price, amount in asks),
        exchange_ts_ms=int(NOW * 1000) if ts is None else int(ts),
    )


def opportunity(symbol: str, side: str, *, oid: str = "opp-1", fraction: str = "0.05",
                score: str = "0.5", expires_at: float = NOW + 60) -> Opportunity:
    if side == "buy":
        strategy, reason = "lab:exp-1:entry-2", "entry_signal"
    else:
        strategy, reason = "lab:exp-1:exit-1", "exit_signal"
    return Opportunity(
        opportunity_id=oid,
        strategy_id=strategy,
        symbol=symbol,
        side=side,
        score=D(score),
        expected_edge_bps=D("10"),
        max_notional_fraction=D(fraction),
        expires_at=expires_at,
        reason_code=reason,
    )


@dataclass(frozen=True)
class FakeHealth:
    last_error_kind: str | None = None
    consecutive_failures: int = 0


class DecideReader:
    """The executor's inner reader: test rules/books/health, everything else the mock's."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.rules: dict[str, MarketRules] = {}
        self.health_value = FakeHealth()
        self.health_error: Exception | None = None
        self.reload_error: Exception | None = None
        self.balance_error: Exception | None = None
        self.books: dict[str, object] = {}
        self.book_calls: list[tuple[str, int]] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health(self):
        if self.health_error is not None:
            raise self.health_error
        return self.health_value

    def reload_rules(self) -> None:
        if self.reload_error is not None:
            raise self.reload_error
        inner = getattr(self._inner, "reload_rules", None)
        if callable(inner):
            inner.reload_rules()

    def fetch_balance(self):
        if self.balance_error is not None:
            raise self.balance_error
        return self._inner.fetch_balance()

    def market_rules(self, symbol: str) -> MarketRules:
        return self.rules.get(symbol, self._inner.market_rules(symbol))

    def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook:
        self.book_calls.append((symbol, depth))
        value = self.books.get(symbol)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return self._inner.fetch_order_book(symbol, depth=depth)


class FakeMarketData:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.overrides: dict[str, object] = {}
        self.calls: list[tuple[str, float]] = []

    def fetch_frame(self, symbol: str, now: float) -> MarketFrame:
        self.calls.append((symbol, now))
        value = self.overrides.get(symbol)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return frame(symbol, at=float(self.clock()))


@dataclass
class Rig:
    h: Harness
    ws: Workspace
    ps: object
    rules: dict[str, MarketRules]
    reader: DecideReader
    executor: object
    gate: object
    clock: object
    market: FakeMarketData
    deps: CycleDeps
    deps_kwargs: dict
    state: CycleState


def make_rig(ws: Workspace, h: Harness, *, ps, rules, symbols=("OAS/JPY",),
             book_overrides=None) -> Rig:
    symbols = tuple(symbols)
    clock = h.clock.seconds
    gate = h._gate
    reader = DecideReader(h.mock)
    reader.rules = dict(rules)
    for symbol in symbols:
        reader.books[symbol] = book(symbol)
    if book_overrides:
        reader.books.update(book_overrides)
    executor = OrderExecutor(h.ledger, h.lease, reader=reader, sender=None,
                             enable_check=gate, clock=clock, symbols=symbols,
                             config=h.config)
    market = FakeMarketData(clock)
    kwargs = dict(ledger=h.ledger, lease=h.lease, executor=executor, reader=reader,
                  market_data=market, gate=gate, policy_set=ps, symbols=symbols,
                  clock=clock, stop_requested=lambda: False, decide=decide_and_act)
    return Rig(h=h, ws=ws, ps=ps, rules=rules, reader=reader, executor=executor,
               gate=gate, clock=clock, market=market, deps=CycleDeps(**kwargs),
               deps_kwargs=kwargs, state=CycleState(cycle_number=1))


def make_ctx(rig: Rig, *, mode: str = "shadow", block_reasons=(), frames=None,
             safety=None) -> CycleContext:
    return CycleContext(
        now=rig.clock(),
        mode=mode,
        enable=rig.gate(),
        safety=read_safety_state(rig.h.ledger) if safety is None else safety,
        balance=rig.h.mock.fetch_balance(),
        frames=({symbol: frame(symbol) for symbol in rig.deps.symbols}
                if frames is None else frames),
        stale_symbols=(),
        block_reasons=set(block_reasons),
        can_decide=True,
    )


# ---- ledger builders ------------------------------------------------------------------
def store(h: Harness, ps) -> None:
    h.ledger.store_policy_set(h.lease, ps, now=h.clock.seconds())


def open_buy(h: Harness, ps, *, symbol="OAS/JPY", amount="10", price="100", key="entry-1"):
    return h.ledger.create_intent(
        h.lease,
        IntentRequest(
            decision_key=f"live1:{key}",
            symbol=symbol,
            side="buy",
            order_type="limit",
            amount=amount,
            price=price,
            policy_digest=ps.policy_for(symbol).digest,
            rule_id="entry-1",
        ),
        now=h.clock.seconds(),
    )


def fill_position(h: Harness, ps, *, symbol="OAS/JPY", amount="10", price="100",
                  key="entry-1", fee_amount="0"):
    buy = open_buy(h, ps, symbol=symbol, amount=amount, price=price, key=key)
    order_id = f"o-{key}"
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(
            exchange_order_id=order_id,
            intent_id=buy.intent_id,
            symbol=symbol,
            side="buy",
            order_type="limit",
            amount=amount,
            status="FULLY_FILLED",
            executed_amount=amount,
            remaining_amount="0",
            price=price,
        ),
        now=h.clock.seconds(),
    )
    quote = symbol.split("/")[1]
    h.ledger.record_fill(
        h.lease,
        FillRecord(
            fill_id=f"f-{key}",
            exchange_order_id=order_id,
            symbol=symbol,
            side="buy",
            amount=amount,
            price=price,
            quote_notional=str(D(amount) * D(price)),
            executed_at=h.clock.seconds(),
            fee_status="known",
            fee_amount=fee_amount,
            fee_currency=quote,
        ),
        now=h.clock.seconds(),
    )
    for expected, to_state in (("PLANNED", "SUBMITTING"), ("SUBMITTING", "ACKED"),
                               ("ACKED", "FILLED")):
        h.ledger.transition_intent(h.lease, buy.intent_id, expected_state=expected,
                                   to_state=to_state, now=h.clock.seconds())
    return buy


def intent_count(ledger) -> int:
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT COUNT(*) FROM intents").fetchone()[0]


def position_status(ledger, position_id: str) -> str:
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT status FROM positions WHERE position_id=?",
                           (position_id,)).fetchone()[0]


def decision_rows(ledger) -> list[views.EventRow]:
    return views.events_of_kind(ledger, "live_decision")


# ---- g1: the buy hand calculation (real scan_live) -------------------------------------
def test_a_buy_opportunity_is_sized_and_recorded_as_a_plan(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    # The signals tests' own frame builder: ten bars ending at 100, two bars after a 110
    # print, so the real scan_live fires the 0.05 entry rule.
    monkeypatch.setattr(signals_tests, "NOW", NOW)
    ctx = make_ctx(rig, frames={
        "OAS/JPY": signals_tests.frame("OAS/JPY", [100, 100, 100, 100, 100, 100, 100,
                                                   110, 105, 100])})

    built = build_decisions(rig.deps, rig.state, ctx)

    # The real scan_live finds exactly one entry, from the 0.05 rule, under a 0.30 policy.
    assert len(built.signals.entries) == 1
    entry = built.signals.entries[0]
    assert entry.symbol == "OAS/JPY"
    assert entry.max_notional_fraction == D("0.05")
    assert ps.policy_for("OAS/JPY").max_opportunity_fraction == D("0.30")
    assert built.signals.exits == ()

    assert built.result is not None
    assert built.result.sells == ()
    assert len(built.result.buys) == 1
    plan = built.result.buys[0]
    # cap 500; amount 5.00 at 100; required_quote 5*100*1.001+1
    assert plan.amount == D("5")
    assert plan.price == D("100")
    assert plan.required_quote == D("501.5")
    assert plan.side == "buy" and plan.symbol == "OAS/JPY"
    assert built.buys_blocked is False and built.block_causes == ()
    assert built.over_budget is False

    rows = decision_rows(h.ledger)
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["action"] == "plan" and payload["side"] == "buy"
    assert D(payload["amount"]) == D("5")
    assert D(payload["price"]) == D("100")
    assert D(payload["required_quote"]) == D("501.5")
    assert payload["blocks"] == ""
    assert payload["cycle"] == "1"
    assert intent_count(h.ledger) == 0


# ---- g2: a sell under the position's entry digest ---------------------------------------
def test_a_sell_is_planned_for_a_held_position_under_its_entry_digest(ws, harnesses,
                                                                     monkeypatch):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    h.mock.adjust_balance("OAS", "10")
    bought = fill_position(h, ps, amount="10", price="100")
    digest = ps.policy_for("OAS/JPY").digest
    assert bought.policy_digest == digest

    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    exit_opportunity = opportunity("OAS/JPY", "sell", oid="exit-1")
    signals = LiveSignals((), (exit_opportunity,), (), {})
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: signals)

    built = build_decisions(rig.deps, rig.state, make_ctx(rig))

    assert built.result is not None
    assert built.result.buys == ()
    assert len(built.result.sells) == 1
    plan = built.result.sells[0]
    assert plan.amount == D("10")
    assert plan.price == D("100")
    assert plan.policy_digest == digest
    assert plan.position_id == bought.position_id
    assert plan.decision_key.endswith(":a0")

    rows = decision_rows(h.ledger)
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["action"] == "plan" and payload["side"] == "sell"
    assert payload["decision_key"].endswith(":a0")
    assert payload["digest"] == digest[:16]


# ---- g3: dedupe and pruning ------------------------------------------------------------
def test_dedupe_records_each_decision_once_per_reason(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})

    buy = opportunity("OAS/JPY", "buy", oid="opp-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    rig.reader.books["OAS/JPY"] = book(ts=int(NOW * 1000) - 16_000)

    first = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert first.result is None
    assert first.signals.entries == ()
    rows = decision_rows(h.ledger)
    assert [row.payload["reason"] for row in rows] == ["stale_book"]
    first_key = ("skip", "opp-1", "stale_book")
    assert first_key in rig.state.dedupe

    # The same cycle again: the stale_book decision is already present.
    second = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert second.result is None
    assert len(decision_rows(h.ledger)) == 1

    # The same opportunity with a different reason (fresh book -> plan) is one new row.
    rig.reader.books["OAS/JPY"] = book()
    third = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert third.result is not None and len(third.result.buys) == 1
    rows = decision_rows(h.ledger)
    assert len(rows) == 2
    assert rows[1].payload["action"] == "plan"
    plan_key = ("plan", third.result.buys[0].decision_key, "entry_signal")
    assert plan_key in rig.state.dedupe

    # Pruning: a small cap and cycle numbers far apart delete the old keys, keep the new one.
    monkeypatch.setattr(decide_module, "DEDUPE_MAX_ENTRIES", 2)
    rig.state.cycle_number = 5000
    later = opportunity("OAS/JPY", "buy", oid="opp-2")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((later,), (), (), {}))
    fourth = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert fourth.result is not None and len(fourth.result.buys) == 1

    assert first_key not in rig.state.dedupe
    assert plan_key not in rig.state.dedupe
    new_key = ("plan", fourth.result.buys[0].decision_key, "entry_signal")
    assert rig.state.dedupe[new_key] == 5000
    assert len(decision_rows(h.ledger)) == 3


# ---- g4: blocks stop buys but never sells ----------------------------------------------
@pytest.mark.parametrize("case,cause", [
    ("safety", "safety"),
    ("fee", "fee_rate_unknown"),
    ("rules", "rules_unavailable"),
])
def test_blocks_stop_buys_but_not_sells(ws, harnesses, monkeypatch, case, cause):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    h.mock.adjust_balance("OAS", "10")
    fill_position(h, ps, amount="10", price="100")

    fee = None if case == "fee" else "0.001"
    rules = {"OAS/JPY": rule(fee=fee)}
    if case == "fee":
        # An open buy whose symbol has no fee bound makes build_cycle_inputs raise
        # open_buy_exposure's "fee rate unknown" ValueError, so inputs.block_buys is set.
        open_buy(h, ps, key="open-buy", amount="1", price="100")
    if case == "safety":
        h.ledger.trip_safety(SafetyReason.UNKNOWN_ORDER, detail="test trip",
                             now=h.clock.seconds())

    rig = make_rig(ws, h, ps=ps, rules=rules)
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    sell = opportunity("OAS/JPY", "sell", oid="sell-1")
    signals = LiveSignals((buy,), (sell,), (), {})
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: signals)
    block_reasons = {"rules_unavailable"} if case == "rules" else set()

    built = build_decisions(rig.deps, rig.state,
                            make_ctx(rig, block_reasons=block_reasons))

    assert built.buys_blocked is True
    assert built.block_causes == (cause,)
    assert built.result is not None
    assert built.result.buys == ()
    assert len(built.result.sells) == 1
    assert built.result.sells[0].symbol == "OAS/JPY"
    assert any(str(skip.reason_code) == "buys_blocked_safety"
               and skip.opportunity_id == "buy-1" for skip in built.result.skipped)

    rows = decision_rows(h.ledger)
    buy_skips = [row.payload for row in rows
                 if row.payload["action"] == "skip" and row.payload["side"] == "buy"]
    assert len(buy_skips) == 1
    assert buy_skips[0]["reason"] == "buys_blocked_safety"
    assert cause in buy_skips[0]["blocks"]
    sell_plans = [row.payload for row in rows
                  if row.payload["action"] == "plan" and row.payload["side"] == "sell"]
    assert len(sell_plans) == 1


# ---- g5: stale/missing books drop the symbol before sizing ------------------------------
def test_a_stale_or_missing_book_drops_the_symbol_before_sizing(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    sell = opportunity("OAS/JPY", "sell", oid="sell-1")
    both = LiveSignals((buy,), (sell,), (), {})
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: both)

    # 16 s old: both the entry and the exit of the symbol are dropped before sizing.
    rig.reader.books["OAS/JPY"] = book(ts=int(NOW * 1000) - 16_000)
    real_size = decide_module.size_orders
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("a fully dropped symbol must never reach size_orders")

    monkeypatch.setattr(decide_module, "size_orders", forbidden)
    built = build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))
    assert built.result is None
    assert calls == []
    assert built.signals.entries == () and built.signals.exits == ()
    rows = decision_rows(h.ledger)
    assert [(row.payload["side"], row.payload["reason"]) for row in rows] == [
        ("buy", "stale_book"), ("sell", "stale_book")]
    monkeypatch.setattr(decide_module, "size_orders", real_size)

    # A fetch that raises is book_unavailable for both sides.
    rig.reader.books["OAS/JPY"] = RuntimeError("book down")
    built = build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))
    assert built.result is None
    rows = decision_rows(h.ledger)
    assert [(row.payload["side"], row.payload["reason"]) for row in rows[-2:]] == [
        ("buy", "book_unavailable"), ("sell", "book_unavailable")]

    # A book for another symbol is book_unavailable too.
    rig.reader.books["OAS/JPY"] = book(symbol="XYM/JPY")
    built = build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))
    assert built.result is None
    assert [row.payload["reason"] for row in decision_rows(h.ledger)][-2:] == [
        "book_unavailable", "book_unavailable"]

    # Exactly 15 s old is not stale (the boundary is exclusive).
    rig.reader.books["OAS/JPY"] = book(ts=int(NOW * 1000) - 15_000)
    fresh_only = LiveSignals((buy,), (), (), {})
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: fresh_only)
    built = build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))
    assert built.result is not None and len(built.result.buys) == 1


# ---- g6: one invalid market must not stop the others -----------------------------------
def test_one_invalid_market_does_not_stop_the_other_symbols(ws, harnesses, monkeypatch):
    """XYM/JPY has price_tick 0, which ``sizing._rules_values`` really refuses with a
    ValueError; no size_orders monkeypatch is needed to make that market invalid."""
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = policy_set_for("OAS/JPY", "XYM/JPY")
    store(h, ps)
    rules = {"OAS/JPY": rule(), "XYM/JPY": rule("XYM/JPY", tick="0")}
    rig = make_rig(ws, h, ps=ps, rules=rules, symbols=("OAS/JPY", "XYM/JPY"),
                   book_overrides={"OAS/JPY": book("OAS/JPY"),
                                   "XYM/JPY": book("XYM/JPY")})
    entries = (opportunity("OAS/JPY", "buy", oid="buy-oas"),
               opportunity("XYM/JPY", "buy", oid="buy-xym"))
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))

    built = build_decisions(rig.deps, rig.state, make_ctx(rig))

    assert built.result is not None
    assert len(built.result.buys) == 1
    assert built.result.buys[0].symbol == "OAS/JPY"
    assert {item.symbol for item in built.signals.entries} == {"OAS/JPY"}

    rows = decision_rows(h.ledger)
    invalid = [row.payload for row in rows if row.payload["reason"] == "market_invalid"]
    assert len(invalid) == 1
    assert invalid[0]["symbol"] == "XYM/JPY"
    plans = [row.payload for row in rows if row.payload["action"] == "plan"]
    assert len(plans) == 1 and plans[0]["symbol"] == "OAS/JPY"


# ---- g7: the cycle budget --------------------------------------------------------------
def test_the_cycle_budget(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = policy_set_for("OAS/JPY", "XYM/JPY")
    store(h, ps)
    h.mock.adjust_balance("XYM", "10")
    fill_position(h, ps, symbol="XYM/JPY", amount="10", price="100", key="entry-xym")
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule(), "XYM/JPY": rule("XYM/JPY")},
                   symbols=("OAS/JPY", "XYM/JPY"))
    rig.reader.books["XYM/JPY"] = book("XYM/JPY", ts=int((NOW + 41) * 1000))  # fetch-time clock is NOW+41
    entry = opportunity("OAS/JPY", "buy", oid="buy-1")
    exit_opportunity = opportunity("XYM/JPY", "sell", oid="exit-1")
    signals = LiveSignals((entry,), (exit_opportunity,), (), {})
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: signals)

    ctx = make_ctx(rig)
    h.clock.advance(41)

    # (a) an entry-only symbol is not even fetched; a symbol with an exit still is.
    first = build_decisions(rig.deps, rig.state, ctx)
    assert rig.reader.book_calls == [("XYM/JPY", 20)]
    assert first.result is not None
    assert first.result.buys == ()
    assert len(first.result.sells) == 1 and first.result.sells[0].symbol == "XYM/JPY"
    assert first.signals.entries == ()
    assert first.over_budget is True
    rows = decision_rows(h.ledger)
    assert any(row.payload["action"] == "skip"
               and row.payload["reason"] == "cycle_budget"
               and row.payload["symbol"] == "OAS/JPY" for row in rows)

    # (b) the clock crosses the budget only after the real sizing call: buys are dropped,
    # sells stay.  The dry run is call 1, the real call is call 2.
    h.clock.advance(-41)
    assert rig.clock() == NOW
    buy = opportunity("XYM/JPY", "buy", oid="buy-2")
    sell = opportunity("XYM/JPY", "sell", oid="exit-2")
    both = LiveSignals((buy,), (sell,), (), {})
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: both)
    real_size = decide_module.size_orders
    calls: list[int] = []
    sized_buys: list[int] = []

    def wrapper(signals_arg, policy_set_arg, markets_arg, held_arg, funds_arg, **kwargs):
        out = real_size(signals_arg, policy_set_arg, markets_arg, held_arg, funds_arg,
                        **kwargs)
        calls.append(1)
        sized_buys.append(len(out.buys))
        if len(calls) == 2:
            h.clock.advance(41)
        return out

    monkeypatch.setattr(decide_module, "size_orders", wrapper)
    state = CycleState(cycle_number=1)
    second = build_decisions(rig.deps, state, make_ctx(rig))

    assert len(calls) == 2
    assert sized_buys[-1] == 1                 # the real call did plan the buy
    assert second.over_budget is True
    assert second.result is not None
    assert second.result.buys == ()
    assert len(second.result.sells) == 1
    payloads = [row.payload for row in decision_rows(h.ledger)]
    assert any(payload["action"] == "skip" and payload["reason"] == "cycle_budget"
               and payload["side"] == "buy" and payload["cycle"] == "1"
               for payload in payloads)
    assert any(payload["action"] == "plan" and payload["side"] == "sell"
               for payload in payloads)


# ---- g8: dust positions become residual inventory ---------------------------------------
def test_dust_positions_are_marked_residual(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = policy_set_for("OAS/JPY", "XYM/JPY")
    store(h, ps)
    rules = {"OAS/JPY": rule(), "XYM/JPY": rule("XYM/JPY")}
    h.mock.adjust_balance("OAS", "0.005")
    dust = fill_position(h, ps, amount="0.005", price="100", key="dust-oas")
    rig = make_rig(ws, h, ps=ps, rules=rules, symbols=("OAS/JPY", "XYM/JPY"))
    exit_opportunity = opportunity("OAS/JPY", "sell", oid="exit-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((), (exit_opportunity,), (), {}))

    built = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert built.result is not None
    assert built.result.sells == ()
    assert built.result.residual_position_ids == (dust.position_id,)
    assert views.open_position_rows(h.ledger) == []
    assert position_status(h.ledger, dust.position_id) == "residual_inventory"

    # A second cycle over the same (now residual) position does not fail.
    rig.state.cycle_number += 1
    again = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert again.result is not None
    assert again.result.sells == ()

    # A mark that the ledger refuses becomes a skip, never an exception.
    h.mock.adjust_balance("XYM", "0.005")
    dust2 = fill_position(h, ps, symbol="XYM/JPY", amount="0.005", price="100",
                          key="dust-xym")
    exit2 = opportunity("XYM/JPY", "sell", oid="exit-2")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((), (exit2,), (), {}))

    def refuse_mark(*args, **kwargs):
        raise LiveLedgerError("mark refused")

    monkeypatch.setattr(h.ledger, "mark_residual_inventory", refuse_mark)
    rig.state.cycle_number += 1
    failed = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert failed.result is not None
    assert failed.result.residual_position_ids == (dust2.position_id,)
    assert position_status(h.ledger, dust2.position_id) == "open"
    residual_skips = [row.payload for row in decision_rows(h.ledger)
                      if row.payload["reason"] == "residual_mark_failed"]
    assert len(residual_skips) == 1
    assert residual_skips[0]["symbol"] == "XYM/JPY"


# ---- g9: shadow and trade record the same decisions -------------------------------------
def test_shadow_and_trade_modes_record_the_same_decisions(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    def forbidden(*args, **kwargs):
        raise AssertionError("decide must not plan, submit or cancel an order")

    rig.executor.plan = forbidden
    rig.executor.submit = forbidden
    rig.executor.cancel = forbidden

    shadow = build_decisions(rig.deps, CycleState(cycle_number=1),
                             make_ctx(rig, mode="shadow"))
    trade = build_decisions(rig.deps, CycleState(cycle_number=1),
                            make_ctx(rig, mode="trade"))

    assert shadow.result is not None and len(shadow.result.buys) == 1
    assert trade.result is not None and len(trade.result.buys) == 1
    rows = decision_rows(h.ledger)
    assert len(rows) == 2
    keys = [(row.payload["action"], row.payload["reason"], row.payload["symbol"],
             row.payload["side"]) for row in rows]
    assert keys[0] == keys[1]
    assert rows[0].payload["mode"] == "shadow"
    assert rows[1].payload["mode"] == "trade"
    assert intent_count(h.ledger) == 0


# ---- g10: through run_cycle ------------------------------------------------------------
def test_decide_and_act_through_run_cycle(ws, harnesses, monkeypatch):
    from docich.trading.live.cycle import run_cycle

    h = harnesses(gate_open=False)
    ps = h.ps
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()},
                   book_overrides={"OAS/JPY": book()})
    buy = opportunity("OAS/JPY", "buy", oid="decide-buy")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    # monitor: no enable file, no decide, no decision row
    monitor = run_cycle(rig.deps, rig.state)
    assert monitor.outcome is CycleOutcome.CONTINUE and monitor.mode is None
    assert decision_rows(h.ledger) == []

    # shadow: decide_and_act records one plan
    ws.write_enable(ps, mode="shadow")
    shadow = run_cycle(rig.deps, rig.state)
    assert shadow.mode == "shadow"
    assert len(decision_rows(h.ledger)) == 1

    # the default CycleDeps (no decide argument) keeps the no-op placeholder
    default_deps = CycleDeps(**{key: value for key, value in rig.deps_kwargs.items()
                                if key != "decide"})
    still_shadow = run_cycle(default_deps, rig.state)
    assert still_shadow.mode == "shadow"
    assert len(decision_rows(h.ledger)) == 1

    # a disabled gate (expired enable file) never reaches decide
    ws.write_enable(ps, mode="shadow", created="2000-01-01T00:00:00+09:00",
                    expires="2000-01-02T00:00:00+09:00")
    disabled = run_cycle(rig.deps, rig.state)
    assert disabled.mode is None
    assert len(decision_rows(h.ledger)) == 1


# ---- g11: recording failures -----------------------------------------------------------
def test_recording_failures(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    real_record = h.ledger.record_live_event

    def record_down(*args, **kwargs):
        raise RuntimeError("record down")

    monkeypatch.setattr(h.ledger, "record_live_event", record_down)
    built = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert built.result is not None and len(built.result.buys) == 1
    assert decision_rows(h.ledger) == []
    assert rig.state.dedupe == {}

    # The key was not stored, so the same decision is retried on the next cycle.
    monkeypatch.setattr(h.ledger, "record_live_event", real_record)
    rig.state.cycle_number += 1
    retried = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert retried.result is not None and len(retried.result.buys) == 1
    assert len(decision_rows(h.ledger)) == 1

    def record_lost(*args, **kwargs):
        raise LeaseLost("lease gone")

    monkeypatch.setattr(h.ledger, "record_live_event", record_lost)
    with pytest.raises(LeaseLost):
        build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))


# ---- g12: payload shape ----------------------------------------------------------------
def test_payloads_are_strings_and_small(ws, harnesses, monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = policy_set_for("OAS/JPY", "XYM/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps,
                   rules={"OAS/JPY": rule(), "XYM/JPY": rule("XYM/JPY")},
                   symbols=("OAS/JPY", "XYM/JPY"),
                   book_overrides={"OAS/JPY": book("OAS/JPY"),
                                   "XYM/JPY": book("XYM/JPY",
                                                   ts=int(NOW * 1000) - 16_000)})
    entries = (opportunity("OAS/JPY", "buy", oid="buy-oas"),
               opportunity("XYM/JPY", "buy", oid="buy-xym"))
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))

    built = build_decisions(rig.deps, rig.state, make_ctx(rig))
    assert built.result is not None and len(built.result.buys) == 1
    rows = decision_rows(h.ledger)
    assert {row.payload["action"] for row in rows} == {"plan", "skip"}

    for row in rows:
        payload = row.payload
        assert all(isinstance(value, str) for value in payload.values())
        assert all(not isinstance(value, (dict, list)) for value in payload.values())
        assert len(json.dumps(payload).encode("utf-8")) < 16_384

    with h.ledger.read_cursor() as cur:
        kinds = [row[0] for row in cur.execute(
            "SELECT kind FROM events WHERE kind='live_decision'")]
    assert kinds and set(kinds) == {"live_decision"}


# ---- g13: imports and static order-call check ------------------------------------------
def test_import_leaves_no_network_modules_and_no_order_calls(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.decide
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests",
                           "socket") if m in sys.modules]
        assert not bad, bad
        exchanges = [m for m in sys.modules if m == "docich.trading.exchanges"
                     or m.startswith("docich.trading.exchanges.")]
        assert not exchanges, exchanges
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

    tree = ast.parse(Path(decide_module.__file__).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"exit", "quit"}:
            bad.append(func.id)
        elif isinstance(func, ast.Attribute):
            if func.attr in {"exit", "_exit"}:
                bad.append(func.attr)
            if func.attr in {"plan", "submit", "cancel", "create_intent"}:
                bad.append("." + func.attr)
    assert bad == []


# ---- h1: the book age is measured from the fetch clock, not the cycle start --------------
def test_book_age_is_measured_at_the_fetch_clock(ws, harnesses, monkeypatch):
    """A book fetched 30 s after the cycle start is filtered by its age AT THE FETCH."""
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    sell = opportunity("OAS/JPY", "sell", oid="sell-1")
    both = LiveSignals((buy,), (sell,), (), {})

    real_fetch = rig.reader.fetch_order_book
    fetched: list[float] = []

    def fetch_and_advance(symbol, *, depth=20):
        value = real_fetch(symbol, depth=depth)
        h.clock.advance(30)
        fetched.append(float(rig.clock()))
        return value

    monkeypatch.setattr(rig.reader, "fetch_order_book", fetch_and_advance)
    real_size = decide_module.size_orders
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("a stale book must never reach size_orders")

    # T = NOW is the cycle start; the fetch happens 30 s later.  The (T+10) book is 20 s
    # old at the fetch, so both the entry and the exit must be skipped as stale_book.
    rig.reader.books["OAS/JPY"] = book(ts=int((NOW + 10) * 1000))
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: both)
    monkeypatch.setattr(decide_module, "size_orders", forbidden)
    ctx = make_ctx(rig)
    assert ctx.now == NOW
    built = build_decisions(rig.deps, CycleState(cycle_number=1), ctx)
    assert fetched == [NOW + 30]
    assert calls == []
    assert built.result is None
    assert built.signals.entries == () and built.signals.exits == ()
    rows = decision_rows(h.ledger)
    assert [(row.payload["side"], row.payload["reason"]) for row in rows] == [
        ("buy", "stale_book"), ("sell", "stale_book")]
    assert not any(row.payload["action"] == "plan" for row in rows)

    # The boundary is measured at the fetch too.  (T+16) is 14 s old at T+30: NOT stale.
    h.clock.advance(-30)
    rig.reader.books["OAS/JPY"] = book(ts=int((NOW + 16) * 1000))
    monkeypatch.setattr(decide_module, "size_orders", real_size)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    before = len(decision_rows(h.ledger))
    fresh = build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))
    assert fetched[-1] == NOW + 30
    assert fresh.result is not None and len(fresh.result.buys) == 1
    assert [row.payload["action"] for row in decision_rows(h.ledger)[before:]] == ["plan"]

    # Exactly 15 s old at the fetch (T+15 against the T+30 fetch clock): NOT stale.
    h.clock.advance(-30)
    rig.reader.books["OAS/JPY"] = book(ts=int((NOW + 15) * 1000))
    before = len(decision_rows(h.ledger))
    edge = build_decisions(rig.deps, CycleState(cycle_number=1), make_ctx(rig))
    assert fetched[-1] == NOW + 30
    assert edge.result is not None and len(edge.result.buys) == 1
    assert [row.payload["action"] for row in decision_rows(h.ledger)[before:]] == ["plan"]


# ---- h2: recording failures are counted, not just swallowed ------------------------------
def test_record_failures_are_counted(ws, harnesses, monkeypatch):
    """One plan and one skip are two writes; every swallowed failure is counted."""
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = policy_set_for("OAS/JPY", "XYM/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps,
                   rules={"OAS/JPY": rule(), "XYM/JPY": rule("XYM/JPY")},
                   symbols=("OAS/JPY", "XYM/JPY"),
                   book_overrides={"OAS/JPY": book("OAS/JPY"),
                                   "XYM/JPY": book("XYM/JPY",
                                                   ts=int(NOW * 1000) - 16_000)})
    entries = (opportunity("OAS/JPY", "buy", oid="buy-oas"),
               opportunity("XYM/JPY", "buy", oid="buy-xym"))
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    state = CycleState(cycle_number=1)

    real_record = h.ledger.record_live_event
    attempts: list[str] = []

    def record_down(lease, kind, payload, *, now):
        attempts.append(payload["action"])
        raise RuntimeError("record down")

    # A failing write is swallowed, but every attempted write is counted: one plan from
    # OAS/JPY and one stale_book skip from XYM/JPY make exactly two.
    monkeypatch.setattr(h.ledger, "record_live_event", record_down)
    failed = build_decisions(rig.deps, state, make_ctx(rig))
    assert failed.result is not None and len(failed.result.buys) == 1
    assert attempts == ["plan", "skip"]
    assert failed.record_failures == 2
    assert decision_rows(h.ledger) == []
    assert state.dedupe == {}

    # The same decisions with a working writer: both are recorded and nothing is counted.
    monkeypatch.setattr(h.ledger, "record_live_event", real_record)
    state.cycle_number += 1
    clean = build_decisions(rig.deps, state, make_ctx(rig))
    assert clean.record_failures == 0
    rows = decision_rows(h.ledger)
    assert [row.payload["action"] for row in rows] == ["plan", "skip"]
    assert len(state.dedupe) == 2

    # The clean run stored the keys: the next cycle attempts no write at all, so a reader
    # that fails every write still reports zero failures.
    attempts.clear()
    monkeypatch.setattr(h.ledger, "record_live_event", record_down)
    state.cycle_number += 1
    repeated = build_decisions(rig.deps, state, make_ctx(rig))
    assert repeated.record_failures == 0
    assert attempts == []
    assert len(decision_rows(h.ledger)) == 2


# ---- k1: the book loop keeps an entry-only symbol AT the budget -------------------------
def test_an_entry_only_symbol_is_still_fetched_at_exactly_the_budget(ws, harnesses,
                                                                    monkeypatch):
    """Exactly CYCLE_BUDGET_S is NOT over budget: the entry-only book is fetched and sized."""
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    # Fresh against the fetch clock (NOW+40), which is also the cycle-budget boundary.
    rig.reader.books["OAS/JPY"] = book(ts=int((NOW + 40) * 1000))

    ctx = make_ctx(rig)
    assert ctx.now == NOW
    h.clock.advance(40)
    assert rig.clock() - ctx.now == decide_module.CYCLE_BUDGET_S == 40.0

    at_budget = build_decisions(rig.deps, rig.state, ctx)

    # The entry-only symbol IS fetched and its buy IS planned at exactly the budget.
    assert rig.reader.book_calls == [("OAS/JPY", 20)]
    assert at_budget.result is not None
    assert len(at_budget.result.buys) == 1
    assert at_budget.result.buys[0].symbol == "OAS/JPY"
    assert at_budget.over_budget is False
    assert not any(row.payload["reason"] == "cycle_budget"
                   for row in decision_rows(h.ledger))

    # One millisecond later the same entry-only symbol is not fetched at all: cycle_budget.
    h.clock.advance(-40)
    assert rig.clock() == NOW
    rig.reader.book_calls.clear()
    late_ctx = make_ctx(rig)
    assert late_ctx.now == NOW
    h.clock.advance(40.001)
    assert rig.clock() - late_ctx.now > decide_module.CYCLE_BUDGET_S
    before = len(decision_rows(h.ledger))

    over = build_decisions(rig.deps, CycleState(cycle_number=1), late_ctx)

    assert rig.reader.book_calls == []
    assert over.result is None
    assert over.signals.entries == ()
    rows = decision_rows(h.ledger)
    assert len(rows) == before + 1
    assert rows[-1].payload["action"] == "skip"
    assert rows[-1].payload["reason"] == "cycle_budget"
    assert rows[-1].payload["symbol"] == "OAS/JPY"
    assert rows[-1].payload["side"] == "buy"


# ---- k2: buys survive a sizing call that ends exactly at the budget ---------------------
def test_buys_are_kept_at_exactly_the_budget_after_sizing(ws, harnesses, monkeypatch):
    """A clock that reaches CYCLE_BUDGET_S inside the real sizing call keeps the buys."""
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    real_size = decide_module.size_orders

    def run(offset: float, state: CycleState):
        calls: list[int] = []
        sized_buys: list[int] = []

        def wrapper(signals_arg, policy_set_arg, markets_arg, held_arg, funds_arg,
                    **kwargs):
            out = real_size(signals_arg, policy_set_arg, markets_arg, held_arg, funds_arg,
                            **kwargs)
            calls.append(1)
            sized_buys.append(len(out.buys))
            if len(calls) == 2:      # the dry run is call 1, the real call is call 2
                h.clock.advance(offset)
            return out

        monkeypatch.setattr(decide_module, "size_orders", wrapper)
        ctx = make_ctx(rig)
        assert ctx.now == NOW and rig.clock() == NOW
        return build_decisions(rig.deps, state, ctx), calls, sized_buys

    # Exactly the budget after sizing: the planned buy is not dropped, over_budget is False.
    at_budget, calls, sized_buys = run(40.0, CycleState(cycle_number=1))
    assert len(calls) == 2
    assert sized_buys[-1] == 1
    assert rig.clock() - NOW == decide_module.CYCLE_BUDGET_S == 40.0
    assert at_budget.over_budget is False
    assert at_budget.result is not None and len(at_budget.result.buys) == 1
    assert at_budget.result.buys[0].symbol == "OAS/JPY"

    # One millisecond later the same real call plans the buy and the budget drops it.
    h.clock.advance(-40)
    assert rig.clock() == NOW
    over, calls, sized_buys = run(40.001, CycleState(cycle_number=1))
    assert len(calls) == 2
    assert sized_buys[-1] == 1
    assert rig.clock() - NOW > decide_module.CYCLE_BUDGET_S
    assert over.over_budget is True
    assert over.result is not None and over.result.buys == ()
    payloads = [row.payload for row in decision_rows(h.ledger)]
    assert any(payload["action"] == "skip" and payload["reason"] == "cycle_budget"
               and payload["side"] == "buy" and payload["symbol"] == "OAS/JPY"
               for payload in payloads)


# ---- k3: the dedupe prune keeps the cutoff cycle, drops only what is below it -----------
def test_dedupe_prune_keeps_the_entry_at_exactly_the_cutoff(ws, harnesses, monkeypatch):
    """The prune cutoff is exclusive: value == cutoff survives, cutoff - 1 is deleted."""
    h = harnesses()
    ps = policy_set_for("OAS/JPY")
    store(h, ps)
    rig = make_rig(ws, h, ps=ps, rules={"OAS/JPY": rule()})
    buy = opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    monkeypatch.setattr(decide_module, "DEDUPE_MAX_ENTRIES", 2)

    state = rig.state
    state.cycle_number = 5000
    cutoff = state.cycle_number - decide_module.DEDUPE_KEEP_CYCLES
    keep_key = ("skip", "old-at-cutoff", "stale_book")
    drop_key = ("skip", "old-below-cutoff", "stale_book")
    state.dedupe[keep_key] = cutoff
    state.dedupe[drop_key] = cutoff - 1

    built = build_decisions(rig.deps, state, make_ctx(rig))

    assert built.result is not None and len(built.result.buys) == 1
    new_key = ("plan", built.result.buys[0].decision_key, "entry_signal")
    # The prune ran (three keys against a cap of two) and kept exactly the cutoff value.
    assert keep_key in state.dedupe
    assert state.dedupe[keep_key] == cutoff
    assert drop_key not in state.dedupe
    assert state.dedupe[new_key] == 5000
    assert len(state.dedupe) == 2
