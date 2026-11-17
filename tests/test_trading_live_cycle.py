"""The per-cycle LIVE skeleton (Issue #3623, L4b-5b-1): steps 0-9 and 14.  No network.

The executor, ledger, lease, mock exchange and enable gate are the real ones from the
LIVE test harness; only the reader's health/rules hooks and the public market data are
faked, because the mock exchange has neither.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import cycle as cycle_module
from docich.trading.live import executor as live_executor
from docich.trading.live import ingest as live_ingest
from docich.trading.live import safety as live_safety
from docich.trading.live import views
from docich.trading.live.cycle import (CycleConfig, CycleDeps, CycleOutcome, CycleState,
                                       next_wait, run_cycle)
from docich.trading.live.exchange import BalanceSnapshot
from docich.trading.live.lease import LeaseLost
from docich.trading.live.policy import build_policy_set
from docich.trading.live.safety import SafetyState
from docich.trading.market_data import MarketFrame
from docich.trading.strategy_lab import experiment_from_mapping

from test_trading_live_support import Harness, SimulatedCrash, Workspace, block_network, synthetic_mapping

SRC = Path(cycle_module.__file__).parents[3]


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


# ---- fakes: the reader hooks and the public market data ---------------------------------------
@dataclass(frozen=True)
class FakeHealth:
    last_error_kind: str | None = None
    consecutive_failures: int = 0
    last_ok_at: float | None = None


class FakeReader:
    """The executor's inner reader: health/reload_rules from the test, everything else the mock's."""

    def __init__(self, inner):
        self._inner = inner
        self.health_value = FakeHealth()
        self.health_error = None
        self.reload_calls = 0
        self.reload_error = None
        self.balance_error = None
        self.balance_calls = 0
        self.on_health = None
        self.on_reload = None
        self.on_balance = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health(self):
        if self.on_health is not None:
            self.on_health()
        if self.health_error is not None:
            raise self.health_error
        return self.health_value

    def reload_rules(self) -> None:
        self.reload_calls += 1
        if self.on_reload is not None:
            self.on_reload()
        if self.reload_error is not None:
            raise self.reload_error
        inner = getattr(self._inner, "reload_rules", None)
        if callable(inner):
            inner.reload_rules()

    def fetch_balance(self):
        self.balance_calls += 1
        if self.on_balance is not None:
            self.on_balance()
        if self.balance_error is not None:
            raise self.balance_error
        return self._inner.fetch_balance()


def market_frame(symbol: str, *, at: float, timeframe: int = 60, price: str = "100") -> MarketFrame:
    return MarketFrame(symbol=symbol, timeframe_seconds=timeframe, timestamps=(float(at),),
                       closes=(D(price),), volumes=(D("1"),))


class FakeMarketData:
    """One frame per symbol; an Exception value raises, a missing symbol is a fresh default."""

    def __init__(self, clock):
        self.clock = clock
        self.overrides: dict[str, object] = {}
        self.calls: list[tuple[str, float]] = []
        self.on_fetch = None

    def fetch_frame(self, symbol: str, now: float) -> MarketFrame:
        self.calls.append((symbol, now))
        if self.on_fetch is not None:
            self.on_fetch(symbol)
        value = self.overrides.get(symbol)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return market_frame(symbol, at=float(self.clock()))


@dataclass
class Rig:
    h: Harness
    ws: Workspace
    reader: FakeReader
    executor: object
    gate: object
    clock: object
    market: FakeMarketData
    deps: CycleDeps
    state: CycleState
    hook_calls: list


def make_rig(ws, h, *, symbols=None, config=None, stop_requested=None) -> Rig:
    symbols = tuple(symbols or h.symbols)
    gate = h._gate
    clock = h.clock.seconds
    reader = FakeReader(h.mock)
    executor = live_executor.OrderExecutor(h.ledger, h.lease, reader=reader, sender=None,
                                           enable_check=gate, clock=clock, symbols=symbols,
                                           config=h.config)
    market = FakeMarketData(clock)
    hook_calls: list = []

    def decide(deps, state, ctx):
        hook_calls.append(ctx)

    deps = CycleDeps(ledger=h.ledger, lease=h.lease, executor=executor, reader=reader,
                     market_data=market, gate=gate, policy_set=h.ps, symbols=symbols, clock=clock,
                     stop_requested=stop_requested or (lambda: False), decide=decide,
                     config=config or CycleConfig())
    return Rig(h=h, ws=ws, reader=reader, executor=executor, gate=gate, clock=clock,
               market=market, deps=deps, state=CycleState(), hook_calls=hook_calls)


def _event_count(ledger) -> int:
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def _trip_reasons(ledger) -> list[str]:
    return [trip.reason for trip in live_safety.read_safety_state(ledger).trips]


def _lose_lease(ws) -> None:
    """The way the executor tests lose the lease: replace the locked file under the fd."""
    os.rename(ws.layout.lock_path, ws.layout.lock_path.with_name("old.lock"))
    ws.layout.lock_path.write_text("")


# ---- c1. the steps run in order --------------------------------------------------------------
def test_a_normal_monitor_cycle_runs_the_steps_in_order(ws, harnesses, monkeypatch):
    h = harnesses(gate_open=False)
    calls: list[str] = []
    base_gate = h._gate

    def gate():
        calls.append("gate")
        return base_gate()

    h._gate = gate
    rig = make_rig(ws, h)
    rig.executor.step = _call_logger(calls, "step", rig.executor.step)
    rig.reader.on_health = lambda: calls.append("health")
    rig.reader.on_balance = lambda: calls.append("balance")
    rig.market.on_fetch = lambda symbol: calls.append(f"frame:{symbol}")

    real_fence = cycle_module.fence

    class FenceSpy:
        def check_lease(self, ledger, lease):
            calls.append("lease")
            return real_fence.check_lease(ledger, lease)

        def on_lease_lost(self, *args, **kwargs):
            return real_fence.on_lease_lost(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "fence", FenceSpy())

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.CONTINUE
    assert report.mode is None
    assert report.fresh_symbols == ("OAS/JPY",) and report.stale_symbols == ()
    assert report.block_reasons == ()
    assert rig.hook_calls == []                      # monitor mode never decides

    expected = ["gate", "lease", "health", "step", "balance", "frame:OAS/JPY"]
    first = [calls.index(item) for item in expected]
    assert first == sorted(first), calls            # every step first appears in that order
    assert len(set(first)) == len(expected), calls
    assert calls[-1] == "health"                     # the post-cycle health read is the last step


def _call_logger(calls, label, real):
    def call():
        calls.append(label)
        return real()

    return call


# ---- c2. the hook: monitor never, shadow/trade once, no balance no hook ----------------------
def test_the_hook_is_never_reached_in_monitor_mode_and_is_reached_in_shadow_and_trade(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    monitor = run_cycle(rig.deps, rig.state)
    assert monitor.outcome is CycleOutcome.CONTINUE and monitor.mode is None
    assert rig.hook_calls == []

    ws.write_enable(h.ps, mode="shadow")
    shadow = run_cycle(rig.deps, rig.state)
    assert shadow.mode == "shadow" and len(rig.hook_calls) == 1
    ctx = rig.hook_calls[0]
    assert ctx.mode == "shadow"
    assert set(ctx.frames) == {"OAS/JPY"}
    assert isinstance(ctx.balance, BalanceSnapshot)
    assert isinstance(ctx.safety, SafetyState)
    assert ctx.can_decide is True

    ws.write_enable(h.ps, mode="trade")
    trade = run_cycle(rig.deps, rig.state)
    assert trade.mode == "trade" and len(rig.hook_calls) == 2
    assert rig.hook_calls[1].mode == "trade"

    rig.reader.balance_error = RuntimeError("balance down")
    blind = run_cycle(rig.deps, rig.state)
    assert len(rig.hook_calls) == 2                 # no balance: no decision
    assert "balance_unavailable" in blind.block_reasons


# ---- c3. stop wins ---------------------------------------------------------------------------
def test_stop_requested_does_nothing(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h, stop_requested=lambda: True)
    rig.executor.step = lambda: (_ for _ in ()).throw(AssertionError("step must not run"))
    before = _event_count(h.ledger)

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.STOP_REQUESTED
    assert report.cycle_number == 1
    assert _event_count(h.ledger) == before
    assert rig.hook_calls == []


# ---- c4. auth and clock exit without a trip and without counting -----------------------------
def test_auth_and_clock_exit_without_a_trip_and_without_counting(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)
    steps: list[int] = []
    real_step = rig.executor.step
    rig.executor.step = lambda: (steps.append(1), real_step())[1]

    rig.reader.health_value = FakeHealth("auth", 0)
    auth = run_cycle(rig.deps, rig.state)
    assert auth.outcome is CycleOutcome.EXIT_AUTH and auth.reason == "auth"
    assert live_safety.read_safety_state(h.ledger).trips == ()
    assert rig.state.consecutive_failures == 0
    assert steps == []

    rig.reader.health_value = FakeHealth("clock", 0)
    clock = run_cycle(rig.deps, rig.state)
    assert clock.outcome is CycleOutcome.EXIT_CLOCK and clock.reason == "clock"
    assert live_safety.read_safety_state(h.ledger).trips == ()
    assert rig.state.consecutive_failures == 0
    assert steps == []

    rig.reader.health_value = FakeHealth("network", 1)
    network = run_cycle(rig.deps, rig.state)
    assert network.outcome is CycleOutcome.CONTINUE
    assert live_safety.read_safety_state(h.ledger).trips == ()
    assert rig.state.consecutive_failures == 0
    assert steps == [1]

    rig.reader.health_value = FakeHealth("rate", 1)
    rate = run_cycle(rig.deps, rig.state)
    assert rate.outcome is CycleOutcome.CONTINUE
    assert steps == [1, 1]


# ---- c5. three consecutive reader failures fail the cycle, success resets --------------------
def test_reader_unhealthy_after_three_failures_is_a_failed_cycle(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    rig.reader.health_value = FakeHealth("network", 3)
    failed = run_cycle(rig.deps, rig.state)
    assert failed.outcome is CycleOutcome.CYCLE_FAILED
    assert failed.reason == "reader_unhealthy"
    assert rig.state.consecutive_failures == 1

    rig.reader.health_value = FakeHealth(None, 0)
    healthy = run_cycle(rig.deps, rig.state)
    assert healthy.outcome is CycleOutcome.CONTINUE
    assert rig.state.consecutive_failures == 0


# ---- c6. the lease check at the top of the cycle -------------------------------------------
def test_lease_lost_at_the_check_exits_with_a_trip(ws, harnesses, monkeypatch):
    h = harnesses()                                   # enabled: the observation write is real
    rig = make_rig(ws, h)
    real_note = cycle_module.note_enable_result

    def note_then_lose(*args, **kwargs):
        real_note(*args, **kwargs)                    # step 1 completes its write...
        _lose_lease(ws)                               # ...then the lease goes away

    monkeypatch.setattr(cycle_module, "note_enable_result", note_then_lose)
    report = run_cycle(rig.deps, rig.state)
    assert report.outcome is CycleOutcome.EXIT_LEASE_LOST
    assert report.reason == "lease_lost" and report.mode == "trade"
    assert _trip_reasons(h.ledger) == ["lease_lost"]
    assert rig.hook_calls == []

    # the next cycle cannot even write the enable observation; that is the same exit, not a raise
    again = run_cycle(rig.deps, rig.state)
    assert again.outcome is CycleOutcome.EXIT_LEASE_LOST
    assert _trip_reasons(h.ledger) == ["lease_lost"]


# ---- c7. a lease lost inside step is not a failed cycle -------------------------------------
def test_lease_lost_inside_step_is_exit_lease_lost_not_cycle_failed(ws, harnesses, monkeypatch):
    h = harnesses()
    rig = make_rig(ws, h)

    def boom():
        raise RuntimeError("the message must never leak into the reason")

    monkeypatch.setattr(rig.executor, "step", boom)
    failed = run_cycle(rig.deps, rig.state)
    assert failed.outcome is CycleOutcome.CYCLE_FAILED
    assert failed.reason == "step_failed:RuntimeError"
    assert "message" not in (failed.reason or "")
    assert rig.state.consecutive_failures == 1

    raised: list[BaseException] = []

    def step_after_loss():
        _lose_lease(ws)
        try:
            rig.executor._guard(rig.executor._step)   # the real guard path: trip + re-raise
        except BaseException as exc:
            raised.append(exc)
            raise

    monkeypatch.setattr(rig.executor, "step", step_after_loss)
    lost = run_cycle(rig.deps, rig.state)
    assert lost.outcome is CycleOutcome.EXIT_LEASE_LOST and lost.reason == "lease_lost"
    assert [type(exc) for exc in raised] == [LeaseLost]
    assert _trip_reasons(h.ledger) == ["lease_lost"]


# ---- c8. the gate and the reader must be the executor's own ---------------------------------
def test_constructor_rejects_a_gate_or_reader_that_is_not_the_executors(ws, harnesses):
    h = harnesses()
    rig = make_rig(ws, h)
    base = dict(ledger=h.ledger, lease=h.lease, executor=rig.executor, reader=rig.reader,
                market_data=rig.market, gate=rig.gate, policy_set=h.ps, symbols=h.symbols,
                clock=rig.clock, stop_requested=lambda: False)

    with pytest.raises(ValueError):
        CycleDeps(**{**base, "gate": lambda: h._gate()})
    with pytest.raises(ValueError):
        CycleDeps(**{**base, "reader": FakeReader(h.mock)})
    assert CycleDeps(**base).gate is rig.gate             # the correct pair is accepted


# ---- c9. rules reload cadence and failure ---------------------------------------------------
def test_rules_reload_cadence_and_failure(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h, config=CycleConfig(rules_reload_every=3))
    resets: list[int] = []
    real_reset = rig.executor.reset_rules

    def reset():
        resets.append(1)
        real_reset()

    rig.executor.reset_rules = reset
    rules_before = rig.executor.rules()

    counts = []
    for _ in range(3):
        run_cycle(rig.deps, rig.state)
        counts.append(rig.reader.reload_calls)
    assert counts == [0, 0, 0]                    # never on the first cycles

    rig.reader.reload_error = RuntimeError("rules down")
    failed = run_cycle(rig.deps, rig.state)       # cycle 4: the first reload
    assert rig.reader.reload_calls == 1
    assert "rules_unavailable" in failed.block_reasons
    assert resets == []                           # a failed reload keeps the cached rules
    assert rig.executor.rules() == rules_before

    recovered = run_cycle(rig.deps, rig.state)    # cycle 5: no reload, no stale reason
    assert rig.reader.reload_calls == 1
    assert "rules_unavailable" not in recovered.block_reasons
    assert resets == []

    run_cycle(rig.deps, rig.state)                # cycle 6
    assert rig.reader.reload_calls == 1

    rig.reader.reload_error = None
    run_cycle(rig.deps, rig.state)                # cycle 7: the second reload succeeds
    assert rig.reader.reload_calls == 2
    assert resets == [1]
    assert rig.executor.rules() == rules_before

    with pytest.raises(ValueError):
        CycleConfig(rules_reload_every=0)


# ---- c10. fresh and stale frames are split --------------------------------------------------
def test_frames_fresh_and_stale_are_split(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "OMG/JPY", "XYM/JPY"))
    rig = make_rig(ws, h)
    now = h.clock.seconds()
    rig.market.overrides = {
        "OAS/JPY": market_frame("OAS/JPY", at=now),
        "OMG/JPY": market_frame("OMG/JPY", at=now - 10_000),      # far older than 2 timeframes
        "XYM/JPY": RuntimeError("public fetch down"),
    }

    report = run_cycle(rig.deps, rig.state)

    assert set(report.fresh_symbols) == {"OAS/JPY"}
    assert report.stale_symbols == ("OMG/JPY", "XYM/JPY")
    assert len(rig.hook_calls) == 1
    assert set(rig.hook_calls[0].frames) == {"OAS/JPY"}
    assert rig.hook_calls[0].stale_symbols == ("OMG/JPY", "XYM/JPY")


# ---- c11. worker_state only on change -------------------------------------------------------
def test_worker_state_is_recorded_only_on_change(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    for _ in range(3):
        assert run_cycle(rig.deps, rig.state).outcome is CycleOutcome.CONTINUE
    events = views.events_of_kind(h.ledger, "worker_state")
    assert len(events) == 1
    first = events[0].payload
    assert set(first) == {"mode", "outcome", "blocks", "reason", "cycle"}
    assert all(isinstance(value, str) for value in first.values())
    assert first == {"mode": "none", "outcome": "continue", "blocks": "", "reason": "", "cycle": "1"}

    ws.write_enable(h.ps, mode="shadow")
    run_cycle(rig.deps, rig.state)
    events = views.events_of_kind(h.ledger, "worker_state")
    assert len(events) == 2
    assert events[1].payload["mode"] == "shadow" and events[1].payload["cycle"] == "4"
    assert all(isinstance(value, str) for value in events[1].payload.values())


# ---- c12. the pure backoff helper -----------------------------------------------------------
def test_next_wait():
    assert next_wait(30, 0) == 30
    assert next_wait(30, 1) == 30
    assert next_wait(30, 2) == 60
    assert next_wait(30, 3) == 120
    assert next_wait(30, 4) == 240
    assert next_wait(30, 5) == 300
    assert next_wait(30, 10) == 300
    assert next_wait(60, 1) == 60
    assert next_wait(400, 3) == 400
    with pytest.raises(ValueError):
        next_wait(10, 0)


# ---- c13. a flat position left open by a crash is healed by the cycle's step ----------------
def test_a_flat_open_position_is_closed_by_the_cycle_step_and_stops_being_legacy(ws, harnesses):
    h = harnesses()
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])

    # a position entered under another policy digest: the current set calls that "legacy"
    old_mapping = dict(synthetic_mapping(), experiment_id="synthetic_old", name="synthetic old")
    old_ps = build_policy_set(experiment_from_mapping(old_mapping), ("OAS/JPY",))
    h.ledger.store_policy_set(h.lease, old_ps, now=h.clock.seconds())
    old_digest = old_ps.policy_for("OAS/JPY").digest

    buy_req = replace(h.req(side="buy", key="old-buy", amount="10", price="100"),
                      policy_digest=old_digest)
    buy = h.ex.plan(buy_req)
    h.ex.submit(buy.intent_id)
    assert views.position_net_base(h.ledger, buy.position_id) == 10

    def crash(point):
        if point == "before_close_position":
            raise SimulatedCrash(point)

    live_ingest.FAULT_HOOK = crash
    try:
        sell_req = replace(h.req(side="sell", key="old-sell", amount="10", price="99"),
                           policy_digest=old_digest)
        sell = h.ex.plan(sell_req)
        with pytest.raises(SimulatedCrash):
            h.ex.submit(sell.intent_id)      # filled, then the crash before closing the position
    finally:
        live_ingest.FAULT_HOOK = None

    with h.ledger.read_cursor() as cur:
        status = cur.execute("SELECT status FROM positions WHERE position_id=?",
                             (buy.position_id,)).fetchone()[0]
    assert status == "open"
    assert views.position_net_base(h.ledger, buy.position_id) == 0
    assert not views.has_open_intent(h.ledger, buy.position_id)

    balance = h.mock.fetch_balance()
    rules = h.ex.rules()
    from docich.trading.live.inputs import build_cycle_inputs
    inputs_before = build_cycle_inputs(h.ledger, rules=rules, balance=balance, policy_set=h.ps)
    assert inputs_before.legacy_position_symbols == frozenset({"OAS/JPY"})

    rig = make_rig(ws, h)
    run_cycle(rig.deps, rig.state)

    with h.ledger.read_cursor() as cur:
        status = cur.execute("SELECT status FROM positions WHERE position_id=?",
                             (buy.position_id,)).fetchone()[0]
    assert status == "closed"
    inputs_after = build_cycle_inputs(h.ledger, rules=rules, balance=h.mock.fetch_balance(),
                                      policy_set=h.ps)
    assert inputs_after.legacy_position_symbols == frozenset()


# ---- c14. no order method is ever called ----------------------------------------------------
def test_sender_is_none_and_no_order_method_is_called(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    def forbidden(*args, **kwargs):
        raise AssertionError("the cycle must not plan, submit or cancel in this task")

    rig.executor.plan = forbidden
    rig.executor.submit = forbidden
    rig.executor.cancel = forbidden

    assert run_cycle(rig.deps, rig.state).outcome is CycleOutcome.CONTINUE   # monitor
    ws.write_enable(h.ps, mode="shadow")
    assert run_cycle(rig.deps, rig.state).outcome is CycleOutcome.CONTINUE
    ws.write_enable(h.ps, mode="trade")
    assert run_cycle(rig.deps, rig.state).outcome is CycleOutcome.CONTINUE
    assert len(rig.hook_calls) == 2                     # shadow and trade reached the no-op hook


# ---- c15. a fresh interpreter: no network modules, no exchange/runner modules, no files -----
def test_cycle_import_without_network_or_side_effects(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.cycle
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests", "socket")
               if m in sys.modules]
        assert not bad, bad
        exchanges = [m for m in sys.modules if m == "docich.trading.exchanges"
                     or m.startswith("docich.trading.exchanges.")]
        assert not exchanges, exchanges
        for name in ("docich.trading.worker", "docich.trading.slot_runtime", "docich.trading.cli"):
            assert name not in sys.modules, name
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert out.stdout.strip() == "clean", out.stderr


# ---- c16. no exit calls in the module -------------------------------------------------------
def test_cycle_module_has_no_process_exit_call():
    tree = ast.parse(Path(cycle_module.__file__).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"exit", "quit"}:
            bad.append(func.id)
        elif isinstance(func, ast.Attribute) and func.attr in {"exit", "_exit"}:
            bad.append(func.attr)
    assert bad == []


# ---- d1. a gate that raises must not escape run_cycle ---------------------------------------
def test_a_gate_that_raises_becomes_a_failed_cycle(ws, harnesses):
    h = harnesses(gate_open=False)
    base_gate = h._gate
    failing = {"on": True}

    def gate():
        if failing["on"]:
            raise RuntimeError("gate down")
        return base_gate()

    h._gate = gate
    rig = make_rig(ws, h)
    assert rig.executor.enable_check is rig.gate          # the very callable the executor uses

    failed = run_cycle(rig.deps, rig.state)
    assert failed.outcome is CycleOutcome.CYCLE_FAILED
    assert failed.reason == "cycle_error:RuntimeError"
    assert rig.state.consecutive_failures == 1

    failing["on"] = False
    resumed = run_cycle(rig.deps, rig.state)
    assert resumed.outcome is CycleOutcome.CONTINUE
    assert rig.state.consecutive_failures == 0


# ---- d2. any other exception is a failed cycle, not a raise ---------------------------------
def test_read_safety_state_failure_is_a_failed_cycle(ws, harnesses, monkeypatch):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    def boom(ledger):
        raise ValueError("safety read down")

    monkeypatch.setattr(cycle_module, "read_safety_state", boom)
    failed = run_cycle(rig.deps, rig.state)

    assert failed.outcome is CycleOutcome.CYCLE_FAILED
    assert failed.reason == "cycle_error:ValueError"
    assert rig.state.consecutive_failures == 1


# ---- d3. a LeaseLost escaping a step is still the lease exit --------------------------------
def test_lease_lost_raised_anywhere_is_exit_lease_lost(ws, harnesses, monkeypatch):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    def boom(ledger):
        raise LeaseLost("the safety read lost the lease")

    monkeypatch.setattr(cycle_module, "read_safety_state", boom)
    lost = run_cycle(rig.deps, rig.state)

    assert lost.outcome is CycleOutcome.EXIT_LEASE_LOST
    assert lost.reason == "lease_lost"
    assert _trip_reasons(h.ledger) == ["lease_lost"]


# ---- d4. every non-stop, non-lease exit records worker_state on change only -----------------
def test_failure_and_exit_paths_record_worker_state_on_change_only(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)

    def states():
        return [event.payload for event in views.events_of_kind(h.ledger, "worker_state")]

    assert run_cycle(rig.deps, rig.state).outcome is CycleOutcome.CONTINUE
    assert states() == [{"mode": "none", "outcome": "continue", "blocks": "", "reason": "",
                         "cycle": "1"}]

    real_step = rig.executor.step

    def step_down():
        raise RuntimeError("step down")

    rig.executor.step = step_down
    for _ in range(2):                                    # the same failure twice is one change
        failed = run_cycle(rig.deps, rig.state)
        assert failed.outcome is CycleOutcome.CYCLE_FAILED
        assert failed.reason == "step_failed:RuntimeError"
    assert states() == [
        {"mode": "none", "outcome": "continue", "blocks": "", "reason": "", "cycle": "1"},
        {"mode": "none", "outcome": "cycle_failed", "blocks": "",
         "reason": "step_failed:RuntimeError", "cycle": "2"},
    ]

    rig.executor.step = real_step
    assert run_cycle(rig.deps, rig.state).outcome is CycleOutcome.CONTINUE
    assert len(states()) == 3
    assert states()[-1] == {"mode": "none", "outcome": "continue", "blocks": "", "reason": "",
                            "cycle": "4"}

    rig.reader.health_value = FakeHealth("auth", 0)       # the early exit records too
    auth = run_cycle(rig.deps, rig.state)
    assert auth.outcome is CycleOutcome.EXIT_AUTH
    assert len(states()) == 4
    assert states()[-1] == {"mode": "none", "outcome": "exit_auth", "blocks": "", "reason": "auth",
                            "cycle": "5"}

    rig.reader.health_value = FakeHealth()
    ws.write_enable(h.ps, mode="shadow")

    def decide_down(deps, state, ctx):
        raise ValueError("decide down")

    rig.deps = replace(rig.deps, decide=decide_down)      # shadow mode reaches the hook
    decided = run_cycle(rig.deps, rig.state)
    assert decided.outcome is CycleOutcome.CYCLE_FAILED
    assert len(states()) == 5
    assert states()[-1] == {"mode": "shadow", "outcome": "cycle_failed", "blocks": "",
                            "reason": "decide_failed:ValueError", "cycle": "6"}


# ---- d5. stop and lease lost write no worker_state ------------------------------------------
def test_stop_requested_and_lease_lost_do_not_record_worker_state(ws, harnesses, monkeypatch):
    h = harnesses()                                       # enabled: the observation write is real
    stopped = make_rig(ws, h, stop_requested=lambda: True)

    assert run_cycle(stopped.deps, stopped.state).outcome is CycleOutcome.STOP_REQUESTED
    assert views.events_of_kind(h.ledger, "worker_state") == []

    rig = make_rig(ws, h)
    real_note = cycle_module.note_enable_result

    def note_then_lose(*args, **kwargs):
        real_note(*args, **kwargs)                        # step 1 completes its write...
        _lose_lease(ws)                                   # ...then the lease goes away

    monkeypatch.setattr(cycle_module, "note_enable_result", note_then_lose)
    lost = run_cycle(rig.deps, rig.state)
    assert lost.outcome is CycleOutcome.EXIT_LEASE_LOST
    assert views.events_of_kind(h.ledger, "worker_state") == []


# ---- d6. a disabled gate result never gets a mode and never builds a context ----------------
def test_a_disabled_gate_result_never_gets_a_mode(ws, harnesses, monkeypatch):
    from docich.trading.live.enable import EnableReason, EnableResult

    h = harnesses(gate_open=False)
    disabled = EnableResult(enabled=False, reasons=(EnableReason.EXPIRED,), mode="trade")

    def gate():
        return disabled

    h._gate = gate
    rig = make_rig(ws, h)
    built = []
    real_context = cycle_module.CycleContext

    def spy(*args, **kwargs):
        built.append((args, kwargs))
        return real_context(*args, **kwargs)

    monkeypatch.setattr(cycle_module, "CycleContext", spy)
    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.CONTINUE
    assert report.mode is None
    assert rig.hook_calls == []
    assert built == []


# ---- d7. a frame for another symbol is stale -------------------------------------------------
def test_a_frame_for_another_symbol_is_stale(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h)
    ws.write_enable(h.ps, mode="shadow")
    rig.market.overrides = {"OAS/JPY": market_frame("OMG/JPY", at=h.clock.seconds())}

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.CONTINUE
    assert report.fresh_symbols == ()
    assert report.stale_symbols == ("OAS/JPY",)
    assert len(rig.hook_calls) == 1
    assert rig.hook_calls[0].frames == {}
    assert rig.hook_calls[0].stale_symbols == ("OAS/JPY",)


# ---- e1. a stop that arrives during steps 6-8 skips the decide hook --------------------------
def test_stop_flipping_during_the_frame_fetch_skips_the_hook(ws, harnesses):
    h = harnesses(gate_open=False)
    calls: list[int] = []

    def stop_requested() -> bool:
        calls.append(1)
        return len(calls) > 1                 # step 0 sees False, everything after sees True

    rig = make_rig(ws, h, stop_requested=stop_requested)
    ws.write_enable(h.ps, mode="shadow")      # a mode that would otherwise reach the hook
    steps: list[int] = []
    real_step = rig.executor.step

    def step():
        steps.append(1)
        return real_step()

    rig.executor.step = step
    before = len(views.events_of_kind(h.ledger, "worker_state"))

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.STOP_REQUESTED
    assert report.reason == "stop_requested"
    assert report.mode == "shadow"
    assert rig.hook_calls == []                           # the hook was skipped
    assert steps == [1]                                   # the stop arrived after step 6
    assert len(calls) == 2                                # step 0, then the last look before the hook
    assert report.fresh_symbols == ("OAS/JPY",)           # the report keeps the step-8 work
    assert report.block_reasons == ()
    assert len(views.events_of_kind(h.ledger, "worker_state")) == before


# ---- e2. a stop set before step 0 keeps its reason (c3 covers the rest) ---------------------
def test_stop_flag_set_before_step_0_reports_stop_requested(ws, harnesses):
    h = harnesses(gate_open=False)
    rig = make_rig(ws, h, stop_requested=lambda: True)
    rig.executor.step = lambda: (_ for _ in ()).throw(AssertionError("step must not run"))

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.STOP_REQUESTED
    assert report.reason == "stop_requested"
    assert rig.hook_calls == []


# ---- e3. the real reader resets its counters on the step-7 balance read ---------------------
def test_the_real_bitbank_reader_health_returns_to_continue_after_failures(ws, harnesses):
    from bitbank_live_support import FakeTransport, assets_payload, default_script
    from docich.trading.exchanges.bitbank_live import errors as bitbank_errors
    from docich.trading.exchanges.bitbank_live import reader as bitbank_reader

    symbols = ("OAS/JPY",)
    h = harnesses(gate_open=False, symbols=symbols)       # monitor: the gate is closed
    clock = h.clock.seconds
    gate = h._gate

    transport = FakeTransport(default_script())           # the reader tests' scripted transport
    reader = bitbank_reader.BitbankReader(transport, symbols=symbols, clock=clock)
    assert reader.health().consecutive_failures == 0

    network = bitbank_errors.classify(endpoint="assets", http_status=503)
    assert network.health_kind == "network"
    transport.script["assets"] = [network, network, network, assets_payload()]

    executor = live_executor.OrderExecutor(h.ledger, h.lease, reader=reader, sender=None,
                                           enable_check=gate, clock=clock, symbols=symbols,
                                           config=h.config)
    hook: list = []

    def decide(deps, state, ctx):
        hook.append(ctx)

    deps = CycleDeps(ledger=h.ledger, lease=h.lease, executor=executor, reader=reader,
                     market_data=FakeMarketData(clock), gate=gate, policy_set=h.ps,
                     symbols=symbols, clock=clock, stop_requested=lambda: False, decide=decide)
    state = CycleState()

    table = []
    for _ in range(10):
        report = run_cycle(deps, state)
        table.append((report.cycle_number, report.outcome, report.reason,
                      tuple(report.block_reasons), reader.health().consecutive_failures,
                      state.consecutive_failures))

    # the scripted balance failures are a block, never a terminal outcome
    assert [row[0] for row in table] == list(range(1, 11))
    assert [row[1] for row in table] == [CycleOutcome.CONTINUE] * 10
    assert [(row[0], row[3]) for row in table if row[3]] == [(1, ("balance_unavailable",))]
    assert table[0][4] >= 1                               # the failed reads were actually counted
    assert hook == []                                     # monitor mode never decides

    # cycle 1 could not read the balance; the first balance read of cycle 2 fails too, then step 7's
    # read succeeds and resets the reader, so the first clean report is cycle 2
    recovered = next(row for row in table if row[0] > 1 and not row[3])
    assert recovered[0] == 2
    assert recovered[1] is CycleOutcome.CONTINUE
    assert recovered[5] == 0
    assert all(row[5] == 0 for row in table)
    assert state.consecutive_failures == 0

    assert reader.health().consecutive_failures == 0
    assert reader.health().last_error_kind is None
