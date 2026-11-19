"""The LIVE worker runner (L4b-6a): startup order, inert starts, exit mapping and the loop.

The ledger, lease, mock exchange, executor and enable gate are the real LIVE pieces from the
shared harness; the reader is a thin test wrapper around the mock (health/balance hooks only),
the market data is injected, and the clock/sleep/stop flag are fakes.  Every number is hand
derived; nothing here touches the network and the default wiring supplies no sender.
"""
from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import act, control as live_control, safety, views
from docich.trading.live import decide as decide_module
from docich.trading.live.cycle import CycleOutcome, CycleState, next_wait, run_cycle
from docich.trading.live.lease import (LeaseHeld, LeaseLost, acquire_writer_lease,
                                       release_writer_lease)
from docich.trading.live.ledger import Cashflow, LiveLedger
from docich.trading.live.signals import LiveSignals
from docich.trading.live_worker import runner as runner_module
from docich.trading.live_worker.status import ALLOWED_KEYS, STATUS_FILENAME
from docich.trading.market_data import MarketFrame

from test_trading_live_support import (Clock, Harness, Workspace, block_network, make_mock,
                                       policy_set)
import test_trading_live_decide as decide_tests
import test_trading_live_ttl as ttl_tests

WORKER_NOW = 1_800_000_000.0
NO_SENDER = "trade_unavailable_no_sender"


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


# ---- fakes -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Health:
    last_error_kind: str | None = None
    consecutive_failures: int = 0


class RunnerReader:
    """The executor's inner reader: the mock plus the two hooks the cycle reads."""

    def __init__(self, inner):
        self._inner = inner
        self.health_value = Health()
        self.health_error = None
        self.on_health = None
        self.balance_error = None
        self.on_balance = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health(self):
        if self.on_health is not None:
            self.on_health()
        if self.health_error is not None:
            raise self.health_error
        return self.health_value

    def fetch_balance(self):
        if self.on_balance is not None:
            self.on_balance()
        if self.balance_error is not None:
            raise self.balance_error
        return self._inner.fetch_balance()


class FakeMarketData:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def fetch_frame(self, symbol: str, now: float) -> MarketFrame:
        self.calls.append((symbol, now))
        return MarketFrame(symbol=symbol, timeframe_seconds=60, timestamps=(float(self.clock()),),
                           closes=(D("100"),), volumes=(D("1"),))


class Sleeps:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.advance(seconds)


@dataclass
class Prepared:
    ws: Workspace
    clock: Clock
    mock: object
    ps: object
    reader: RunnerReader
    mode: str | None


def prepare(ws: Workspace, *, symbols=("OAS/JPY",), mode="trade",
            jpy="10000", coins=None, cashflows=None, activate=True, store=True) -> Prepared:
    """A closed ledger + released lease + active policy set + control file (the runner's inputs)."""
    clock = Clock(start_ms=int(WORKER_NOW * 1000))
    mock, _ = make_mock(clock, symbols=symbols, jpy=jpy)
    for currency, amount in (coins or {}).items():
        mock.adjust_balance(currency, amount)
    ps = policy_set(symbols)
    ledger = ws.open_ledger(now=clock.seconds())
    lease = acquire_writer_lease(ledger, ws.layout, now=clock.seconds())
    if store:
        ledger.store_policy_set(lease, ps, now=clock.seconds())
    if mode is not None:
        ws.write_control(mode=mode)
    if activate:
        ledger.record_owner_activation(lease, ps, operator="owner", note="runner test",
                                       now=clock.seconds())
    for index, (currency, amount) in enumerate((cashflows or {}).items()):
        ledger.record_cashflow(lease, Cashflow(f"cf-{currency}-{index}", "deposit", currency,
                                               str(amount), "owner_manual", clock.seconds()),
                               now=clock.seconds())
    release_writer_lease(ledger, lease, now=clock.seconds())
    ledger.close()
    return Prepared(ws=ws, clock=clock, mock=mock, ps=ps, reader=RunnerReader(mock),
                    mode=mode)


def run(prepared: Prepared, *, stop_requested=None, open_reader=None, open_pair=None,
        make_market_data=None, interval_s=60.0, config=None):
    """Call run_worker with the prepared state dir and the fake clock/sleep."""
    clock = prepared.clock.seconds
    sleeps = Sleeps(prepared.clock)
    kwargs = dict(
        state_dir=prepared.ws.state,
        open_reader=open_reader or (lambda state_dir: prepared.reader),
        make_market_data=make_market_data or (lambda reader: FakeMarketData(clock)),
        clock=clock,
        stop_requested=stop_requested or (lambda: False),
        sleep=sleeps,
        interval_s=interval_s,
    )
    if open_pair is not None:
        kwargs["open_pair"] = open_pair
    if config is not None:
        kwargs["config"] = config
    code = runner_module.run_worker(**kwargs)
    return code, sleeps.calls


def read_status(ws: Workspace) -> dict:
    return json.loads((ws.layout.root / STATUS_FILENAME).read_text(encoding="utf-8"))


def status_text(ws: Workspace) -> str:
    return (ws.layout.root / STATUS_FILENAME).read_text(encoding="utf-8")


def live_events(ws: Workspace) -> list:
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        return ledger.events(limit=100_000)
    finally:
        ledger.close()


def event_kinds(ws: Workspace) -> list[str]:
    return [event.kind for event in live_events(ws)]


def intent_count(ws: Workspace) -> int:
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        with ledger.read_cursor() as cur:
            return cur.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
    finally:
        ledger.close()


def safety_trips(ws: Workspace) -> list[str]:
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        return [trip.reason for trip in safety.read_safety_state(ledger).trips]
    finally:
        ledger.close()


def acquire_again(ws: Workspace) -> None:
    """The lease must be free after every exit: a second acquire succeeds."""
    ledger = LiveLedger.open(ws.layout, paper_dir=ws.paper, now=WORKER_NOW)
    try:
        lease = acquire_writer_lease(ledger, ws.layout, now=WORKER_NOW)
        release_writer_lease(ledger, lease, now=WORKER_NOW)
    finally:
        ledger.close()


def lose_lease(ws: Workspace) -> None:
    """The way the executor tests lose the lease: replace the locked file under the fd."""
    os.rename(ws.layout.lock_path, ws.layout.lock_path.with_name("old.lock"))
    ws.layout.lock_path.write_text("")


class ReaderFailure(Exception):
    def __init__(self, kind=None):
        super().__init__("reader down")
        if kind is not None:
            self.kind = kind


# ---- r1: a clean run of three cycles then a stop ---------------------------------------
def test_r1_three_cycles_then_stop_releases_the_lease_and_writes_exited(ws, monkeypatch):
    prepared = prepare(ws, mode="trade", cashflows={"JPY": "10000"})
    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)
    code, sleeps = run(prepared, stop_requested=lambda: len(completed) >= 3)

    assert code == runner_module.EXIT_OK
    assert len(completed) == 3
    assert [report.cycle_number for report in completed] == [1, 2, 3]
    assert all(report.outcome is CycleOutcome.CONTINUE for report in completed)
    # Two clean waits of 60 s; the stop is seen before the third wait takes a slice.
    assert sleeps == [1.0] * 120
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 0
    assert status["cycle"] == 3
    assert status["outcome"] == "stop_requested"
    assert status["mode"] == "trade"
    assert status["control_state"] == "on" and status["control_mode"] == "trade"
    assert "code_revision" not in status
    assert status["policy_set_digest"] == prepared.ps.digest[:12]

    # The lease is free again and the ledger kept exactly one no-sender notice, no intent.
    acquire_again(ws)
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        decisions = [event.payload for event in views.events_of_kind(ledger, "live_decision")]
    finally:
        ledger.close()
    assert [row["reason"] for row in decisions].count(NO_SENDER) == 1
    assert all(row["reason"] == NO_SENDER for row in decisions)
    assert intent_count(ws) == 0
    assert prepared.mock.submissions == []


# ---- r2: the startup order ---------------------------------------------------------------
def test_r2_startup_order_is_ledger_lease_reader_on_startup_opening_cycle(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    calls = []

    real_open = LiveLedger.open.__func__

    def open_spy(cls, layout, *, paper_dir, now):
        calls.append("ledger")
        return real_open(cls, layout, paper_dir=paper_dir, now=now)

    monkeypatch.setattr(runner_module.LiveLedger, "open", classmethod(open_spy))
    real_acquire = runner_module.acquire_writer_lease

    def acquire_spy(*args, **kwargs):
        calls.append("lease")
        return real_acquire(*args, **kwargs)

    monkeypatch.setattr(runner_module, "acquire_writer_lease", acquire_spy)

    reader = prepared.reader
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        calls.append("on_startup")
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)
    real_opening = runner_module.record_auto_opening

    def opening_spy(*args, **kwargs):
        calls.append("record_auto_opening")
        return real_opening(*args, **kwargs)

    monkeypatch.setattr(runner_module, "record_auto_opening", opening_spy)
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        calls.append("run_cycle")
        return real_cycle(deps, state)

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps = run(
        prepared, stop_requested=lambda: True,
        open_reader=lambda state_dir: (calls.append("reader"), reader)[1])

    assert code == runner_module.EXIT_OK
    # There is no balance precheck any more: on_startup first, then the automatic opening
    # balances, then the first cycle.
    assert calls == ["ledger", "lease", "reader", "on_startup", "record_auto_opening",
                     "run_cycle"]
    assert read_status(ws)["cycle"] == 1


def test_r3_all_watched_currencies_with_a_cashflow_let_the_run_proceed(ws, monkeypatch):
    prepared = prepare(ws, coins={"OAS": "3"},
                       cashflows={"JPY": "10000", "OAS": "3"})
    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert "inert_reason" not in status
    assert status["cycle"] == 1


def test_r3_a_zero_balance_without_a_cashflow_does_not_block(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})   # OAS total is zero, no OAS cashflow
    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    assert read_status(ws)["phase"] == "exited"


# ---- r4: no active policy set ------------------------------------------------------------
def test_r4_no_active_policy_set_is_inert_before_the_executor(ws, monkeypatch):
    prepared = prepare(ws, activate=False, cashflows={"JPY": "10000"})
    startups = []
    monkeypatch.setattr(runner_module.reconcile, "on_startup",
                        lambda executor: startups.append(1))
    before = event_kinds(ws)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == []
    status = read_status(ws)
    assert status["phase"] == "inert"
    assert status["inert_reason"] == "no_active_policy_set"
    assert status["exit_code"] == 0
    assert event_kinds(ws)[len(before):] == ["lease_acquired", "lease_released"]
    assert safety_trips(ws) == []
    assert intent_count(ws) == 0
    acquire_again(ws)


# ---- r5: the exit mapping table ---------------------------------------------------------
@pytest.mark.parametrize("kind,code,reason", [
    ("auth", runner_module.EXIT_AUTH, "auth"),
    ("clock", runner_module.EXIT_CLOCK, "clock"),
    ("config", runner_module.EXIT_CONFIG, "config"),
    ("weird", runner_module.EXIT_RETRYABLE, "reader_open_failed:ReaderFailure"),
    (None, runner_module.EXIT_RETRYABLE, "reader_open_failed:ReaderFailure"),
    ("runtime", runner_module.EXIT_RETRYABLE, "reader_open_failed:RuntimeError"),
])
def test_r5_a_reader_startup_failure_maps_the_exit_code_and_releases_the_lease(
        ws, kind, code, reason):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    def open_reader(state_dir):
        if kind == "runtime":
            raise RuntimeError("reader down")
        raise ReaderFailure(kind)

    got, sleeps = run(prepared, open_reader=open_reader)

    assert got == code
    assert sleeps == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == code
    assert status["reason"] == reason
    acquire_again(ws)


def test_r5_a_held_lease_returns_5_and_writes_no_status(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    ledger = LiveLedger.open(ws.layout, paper_dir=ws.paper, now=WORKER_NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=WORKER_NOW)
    try:
        code, sleeps = run(prepared, stop_requested=lambda: True)
        assert code == runner_module.EXIT_LEASE_HELD
        assert not (ws.layout.root / STATUS_FILENAME).exists()
    finally:
        release_writer_lease(ledger, lease, now=WORKER_NOW)
        ledger.close()


def test_r5_an_on_startup_failure_is_retryable_and_no_cycle_runs(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    cycles = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        cycles.append(1)
        return real_cycle(deps, state)

    def startup(executor):
        raise RuntimeError("reconcile down")

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup)
    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_RETRYABLE
    assert cycles == []
    assert sleeps == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_RETRYABLE
    assert status["reason"] == "on_startup_failed:RuntimeError"
    assert safety_trips(ws) == []
    assert intent_count(ws) == 0
    assert prepared.mock.all_orders() == []
    acquire_again(ws)


# ---- r6: mid-run exits ------------------------------------------------------------------
@pytest.mark.parametrize("kind,code", [("auth", runner_module.EXIT_AUTH),
                                      ("clock", runner_module.EXIT_CLOCK)])
def test_r6_reader_health_auth_and_clock_exit(ws, kind, code):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    prepared.reader.health_value = Health(kind, 0)

    got, sleeps = run(prepared, stop_requested=lambda: False)

    assert got == code
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == code
    assert status["cycle"] == 1
    assert status["reason"] == kind
    assert status["outcome"] == f"exit_{kind}"
    acquire_again(ws)


def test_r6_a_lost_lease_during_a_cycle_is_exit_7(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    prepared.reader.on_health = lambda: lose_lease(ws)

    got, sleeps = run(prepared, stop_requested=lambda: False)

    assert got == runner_module.EXIT_LEASE_LOST
    # A worker that lost the lease writes no status at all: the file still holds the
    # "starting" write made right after the lease was acquired, with no exit fields.
    status = read_status(ws)
    assert status["phase"] == "starting"
    assert "exit_code" not in status
    assert "reason" not in status
    assert "cycle" not in status
    assert "lease_lost" in safety_trips(ws)
    # The lock was replaced; the best-effort release must not have raised out of the runner.


# ---- r7: stop handling ------------------------------------------------------------------
def test_r7_a_stop_during_the_wait_ends_within_one_slice(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    def stop_requested():
        return prepared.clock.seconds() >= WORKER_NOW + 1.0

    code, sleeps = run(prepared, stop_requested=stop_requested)

    assert code == runner_module.EXIT_OK
    assert sleeps == [1.0]                       # one 1.0 s slice, no full interval
    assert sum(sleeps) == 1.0
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 0
    assert status["cycle"] == 1
    assert status["outcome"] == "stop_requested"
    acquire_again(ws)


def test_r7_a_stop_before_the_first_cycle_exits_0_without_orders(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert sleeps == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 0
    assert status["cycle"] == 1
    assert status["outcome"] == "stop_requested"
    assert prepared.mock.submissions == []
    assert prepared.mock.all_orders() == []
    assert intent_count(ws) == 0


def test_the_stop_flag_set_before_startup_work_still_releases_everything(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    # The flag is already true before run_worker does any startup work.
    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 0
    assert status["outcome"] == "stop_requested"
    assert prepared.mock.submissions == []
    assert prepared.mock.all_orders() == []
    assert intent_count(ws) == 0
    acquire_again(ws)


# ---- r8: next_wait backoff ---------------------------------------------------------------
def test_r8_consecutive_failures_lengthen_the_exact_waits(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    prepared.reader.health_value = Health("network", 3)   # >= max_consecutive_reader_failures

    def stop_requested():
        return prepared.clock.seconds() >= WORKER_NOW + 181.0

    code, sleeps = run(prepared, stop_requested=stop_requested, interval_s=60.0)

    # Hand derivation: failures 1,2,3 -> next_wait(60,1)=60, next_wait(60,2)=120, then one
    # 1.0 s slice of next_wait(60,3)=240 before the stop is seen.  All slices are <= 1.0 s.
    assert [next_wait(60.0, n) for n in (1, 2, 3)] == [60.0, 120.0, 240.0]
    assert len(sleeps) == 60 + 120 + 1
    assert set(sleeps) == {1.0}
    assert sum(sleeps) == 181.0
    assert code == runner_module.EXIT_OK
    assert read_status(ws)["cycle"] == 3


# ---- r9: production wiring pins ---------------------------------------------------------
def test_r9_the_runner_has_one_executor_and_defaults_open_pair_to_none():
    tree = ast.parse(Path(runner_module.__file__).read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "OrderExecutor":
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            calls.append(keywords)
    assert len(calls) == 1
    assert isinstance(calls[0]["sender"], ast.Name) and calls[0]["sender"].id == "sender"

    (run,) = [node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "run_worker"]
    defaults = dict(zip([arg.arg for arg in run.args.kwonlyargs], run.args.kw_defaults))
    assert isinstance(defaults["open_pair"], ast.Constant) and defaults["open_pair"].value is None


class PairedSender:
    """The explicit paired sender: enough shape for the executor; it must not be called here."""

    kind = "bitbank_spot"

    def submit_order(self, request):
        raise AssertionError("this test must not place an order")

    def cancel_order(self, symbol, exchange_order_id):
        raise AssertionError("this test must not cancel an order")


def test_r9_an_injected_pair_passes_its_sender_and_keeps_the_existing_gate(ws, monkeypatch):
    prepared = prepare(ws, mode="trade", cashflows={"JPY": "10000"})
    sender = PairedSender()
    opened = []
    captured = {}
    real_cycle = runner_module.run_cycle

    # Keep the trade mode and the gate real, but make the decide hook a no-op so this test is
    # about wiring, not about a synthetic signal.
    monkeypatch.setattr(runner_module.act, "decide_and_act", lambda deps, state, ctx: None)

    def cycle_spy(deps, state):
        captured["deps"] = deps
        return real_cycle(deps, state)

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    def open_pair(state_dir):
        opened.append(state_dir)
        return prepared.reader, sender

    code, sleeps = run(prepared, open_pair=open_pair, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    deps = captured["deps"]
    assert deps.executor.sender is not None and deps.executor.sender._inner is sender
    assert deps.gate is deps.executor.enable_check
    assert deps.gate().allows_orders is True               # on+trade is still the runtime switch
    assert opened == [prepared.ws.state]
    assert prepared.mock.submissions == []


@pytest.mark.parametrize("kind,code,reason", [
    ("config", runner_module.EXIT_CONFIG, "config"),
    ("auth", runner_module.EXIT_AUTH, "auth"),
    ("clock", runner_module.EXIT_CLOCK, "clock"),
    (None, runner_module.EXIT_RETRYABLE, "reader_open_failed:ReaderFailure"),
])
def test_r9_a_broken_pair_uses_the_reader_exit_path_and_never_falls_back(
        ws, kind, code, reason):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    reader_calls = []

    def open_reader(state_dir):
        reader_calls.append(state_dir)
        return prepared.reader

    def open_pair(state_dir):
        raise ReaderFailure(kind)

    got, sleeps = run(prepared, open_pair=open_pair, open_reader=open_reader)

    assert got == code
    assert sleeps == []
    assert reader_calls == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == code
    assert status["reason"] == reason
    acquire_again(ws)


def test_r9_a_malformed_pair_is_config_and_never_falls_back(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    reader_calls = []

    def open_reader(state_dir):
        reader_calls.append(state_dir)
        return prepared.reader

    got, sleeps = run(prepared, open_pair=lambda _state_dir: (prepared.reader, None),
                       open_reader=open_reader)

    assert got == runner_module.EXIT_CONFIG
    assert reader_calls == []
    assert read_status(ws)["reason"] == "config"
    acquire_again(ws)


def test_r9_a_pair_with_an_unrelated_sender_kind_is_config(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    unrelated = type("Unrelated", (), {"kind": "bitbank_margin"})()

    got, sleeps = run(prepared, open_pair=lambda _state_dir: (prepared.reader, unrelated))

    assert got == runner_module.EXIT_CONFIG
    assert read_status(ws)["reason"] == "config"
    acquire_again(ws)


FORBIDDEN_MODULES = frozenset({
    "ccxt", "socket", "ssl", "http", "urllib", "requests", "subprocess", "importlib",
    "asyncio", "signal", "threading",
})
FORBIDDEN_PATHS = ("docich.trading.exchanges", "docich.trading.live_owner.cli",
                   "docich.trading.worker", "docich.trading.slot_runtime")

# ``__main__.py`` is the one process entry point: it may install the stop handlers and reach the
# real (read-only) exchange layer.  Every other live_worker module keeps the full ban above.
MAIN_ALLOWED_IMPORTS = frozenset({
    "signal", "time",
    "docich.trading.exchanges.bitbank_live.factory",
    "docich.trading.exchanges.bitbank_spot_session",
    "docich.trading.exchanges.bitbank_ccxt",
    "docich.trading.market_data",
    "docich.trading.live.policy",
})


def _main_allowed(name: str) -> bool:
    return any(name == allowed or name.startswith(allowed + ".")
               for allowed in MAIN_ALLOWED_IMPORTS)


def _forbidden_for(filename: str, name: str) -> bool:
    """``_forbidden`` with the entry point's narrow import exception."""
    if not _forbidden(name):
        return False
    return not (filename == "__main__.py" and _main_allowed(name))


def _resolved_import(module: str | None, level: int) -> str:
    """Resolve a relative import against ``docich.trading.live_worker`` (the module's package)."""
    if not level:
        return module or ""
    base = ["docich", "trading", "live_worker"][: 3 - (level - 1)]
    return ".".join(base + ([module] if module else []))


def test_r9_no_live_worker_module_imports_a_forbidden_module_or_exits():
    directory = Path(runner_module.__file__).parent
    offenders = []
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    offenders.extend(
                        (path.name, name) for name in [alias.name]
                        if _forbidden_for(path.name, name))
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolved_import(node.module, node.level)
                if _forbidden_for(path.name, resolved):
                    offenders.append((path.name, resolved))
                for alias in node.names:
                    joined = f"{resolved}.{alias.name}" if resolved else alias.name
                    if _forbidden_for(path.name, joined):
                        offenders.append((path.name, joined))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in {"exit", "quit"}:
                    offenders.append((path.name, func.id))
                elif isinstance(func, ast.Attribute):
                    owner = getattr(func.value, "id", None)
                    # The entry point must exit with the runner's code: sys.exit is allowed in
                    # __main__.py only.  A bare exit()/quit() and os._exit() stay forbidden.
                    entrypoint_exit = (path.name == "__main__.py" and owner == "sys"
                                       and func.attr == "exit")
                    if (owner, func.attr) in {("sys", "exit"), ("os", "_exit")} and not entrypoint_exit:
                        offenders.append((path.name, f"{owner}.{func.attr}"))
    assert offenders == []


def _forbidden(name: str) -> bool:
    parts = name.split(".")
    if any(part in FORBIDDEN_MODULES for part in parts):
        return True
    if "mock_exchange" in parts:
        return True
    if any(name == path or name.startswith(path + ".") for path in FORBIDDEN_PATHS):
        return True
    return False


def _imported_names(path: Path) -> list[str]:
    """Every import target in ``path``: resolved relative imports and from-import members."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import(node.module, node.level)
            names.append(resolved)
            names.extend(f"{resolved}.{alias.name}" if resolved else alias.name
                         for alias in node.names)
    return names


def test_r9_main_imports_only_the_allowed_modules_and_no_other_module_imports_signal():
    directory = Path(runner_module.__file__).parent
    main_imports = _imported_names(directory / "__main__.py")
    assert [name for name in main_imports if _forbidden(name) and not _main_allowed(name)] == []
    for path in sorted(directory.glob("*.py")):
        if path.name == "__main__.py":
            continue
        imports = _imported_names(path)
        assert not [name for name in imports if "signal" in name.split(".")], path.name


def test_r9_the_cycle_deps_carry_the_real_decide_hook_and_the_policy_symbols(ws, monkeypatch):
    prepared = prepare(ws, symbols=("OAS/JPY", "XYM/JPY"),
                       cashflows={"JPY": "10000"})
    captured = {}
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        captured["deps"] = deps
        captured["state"] = state
        return real_cycle(deps, state)

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)
    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    deps = captured["deps"]
    assert deps.decide is act.decide_and_act
    assert deps.executor.sender is None
    assert deps.reader is prepared.reader
    assert deps.executor.reader._inner is prepared.reader
    assert deps.gate is deps.executor.enable_check
    assert deps.symbols == tuple(sorted(prepared.ps.policies))
    assert deps.symbols == ("OAS/JPY", "XYM/JPY")
    assert isinstance(captured["state"], CycleState)


def test_r9_the_status_has_only_the_allow_listed_keys_and_no_balance(ws):
    prepared = prepare(ws, jpy="987654321", cashflows={"JPY": "987654321"})

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    text = status_text(ws)
    status = read_status(ws)
    assert set(status) <= ALLOWED_KEYS
    assert "balance" not in status and "amount" not in status
    assert "api_key" not in status and "prices" not in status
    assert "987654321" not in text
    assert status["phase"] == "exited" and status["cycle"] == 1


def test_r9_the_status_records_the_actual_interval_in_whole_milliseconds(ws, monkeypatch):
    prepared = prepare(ws, jpy="10000", cashflows={"JPY": "10000"})
    calls = _status_spy(monkeypatch)

    # 600.5 s keeps its half second as an int: 600_500 ms, never a float in the status.
    code, sleeps = run(prepared, stop_requested=lambda: True, interval_s=600.5)

    assert code == runner_module.EXIT_OK
    assert calls
    assert all(fields.get("interval_ms") == 600_500 for fields in calls)
    assert read_status(ws)["interval_ms"] == 600_500


# ---- r10: the per-cycle fields added to cycle.py / act.py --------------------------------
def test_r10_ttl_fields_are_reset_and_populated_from_the_ttl_report(ws, harnesses):
    symbols = ("OAS/JPY", "XYM/JPY", "ZEC/JPY", "BTC/JPY", "ETH/JPY", "MONA/JPY",
               "DOT/JPY")
    h = harnesses(symbols=symbols)
    intents = []
    for index, symbol in enumerate(symbols):
        intents.append(ttl_tests.resting_buy(h, symbol=symbol, key=f"b{index}"))
        h.clock.advance(1)                       # distinct created_at: the list is oldest first
    h.clock.advance(53)                          # every order is 54..60 s old: all expired
    deps = ttl_tests.deps_for(h, symbols=symbols)
    state = CycleState(ttl_deferred=9, ttl_failed=9, record_failures=9)

    real_cancel = h.ex.cancel
    failing = {intents[0].intent_id, intents[1].intent_id}

    def cancel_spy(intent_id):
        if intent_id in failing:
            raise RuntimeError("this cancel never lands")
        return real_cancel(intent_id)

    h.ex.cancel = cancel_spy
    report = run_cycle(deps, state)

    # 7 expired, cap 5: 3 canceled, 2 failed, 2 deferred.  All three fields are fresh facts.
    assert report.outcome is CycleOutcome.CONTINUE
    assert state.ttl_deferred == 2
    assert state.ttl_failed == 2
    assert state.record_failures == 0
    assert [h.ledger.get_intent(item.intent_id).state for item in intents] == [
        "ACKED", "ACKED", "CANCELED", "CANCELED", "CANCELED", "ACKED", "ACKED"]

    # A second cycle retries the two stuck cancels; they land this time, so both TTL facts
    # are 0 again (per-cycle, never stale).
    h.ex.cancel = real_cancel
    assert run_cycle(deps, state).outcome is CycleOutcome.CONTINUE
    assert state.ttl_deferred == 0 and state.ttl_failed == 0
    assert all(h.ledger.get_intent(item.intent_id).state == "CANCELED"
               for item in intents)


def test_r10_record_failures_are_populated_by_decide_and_act(ws, harnesses, monkeypatch):
    h = harnesses()
    ps = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, ps)
    rig = decide_tests.make_rig(ws, h, ps=ps, rules={"OAS/JPY": decide_tests.rule()})
    rig.reader.books["OAS/JPY"] = RuntimeError("book down")
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *args, **kwargs: LiveSignals((buy,), (), (), {}))
    ws.write_control(mode="shadow")

    def record_down(lease, kind, payload, *, now):
        raise RuntimeError("record down")

    monkeypatch.setattr(h.ledger, "record_live_event", record_down)
    state = CycleState(ttl_deferred=7, ttl_failed=7, record_failures=7)
    deps = replace(rig.deps, decide=act.decide_and_act)   # the runner's real hook

    report = run_cycle(deps, state)

    assert report.outcome is CycleOutcome.CONTINUE
    # One buy, its book is unavailable -> exactly one skip write was attempted and lost.
    assert state.record_failures == 1
    assert state.ttl_deferred == 0 and state.ttl_failed == 0


# ---- r11: mutation-proof additions -------------------------------------------------------
def test_exit_code_numbers_are_part_of_the_contract():
    # The numbers are the process contract (systemd and operators key off them): pin the
    # literals, not the module constants, so moving a constant cannot move a number.
    assert runner_module.EXIT_OK == 0
    assert runner_module.EXIT_CONFIG == 2
    assert runner_module.EXIT_AUTH == 3
    assert runner_module.EXIT_CLOCK == 4
    assert runner_module.EXIT_LEASE_HELD == 5
    assert not hasattr(runner_module, "EXIT_CODE_CHANGED")   # 6 stays unused
    assert runner_module.EXIT_LEASE_LOST == 7
    assert runner_module.EXIT_RETRYABLE == 8


def test_a_held_lease_returns_the_literal_5(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    ledger = LiveLedger.open(ws.layout, paper_dir=ws.paper, now=WORKER_NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=WORKER_NOW)
    try:
        code, sleeps = run(prepared, stop_requested=lambda: True)
        assert code == 5
    finally:
        release_writer_lease(ledger, lease, now=WORKER_NOW)
        ledger.close()


def test_a_watched_currency_missing_from_the_balance_counts_as_zero(ws, monkeypatch):
    # The exchange omits an asset it does not hold.  A watched currency with no entry at all
    # is a zero balance, not an opening-balance omission: the run must not go inert.
    from docich.trading.live.exchange import AssetBalance, BalanceSnapshot

    prepared = prepare(ws, cashflows={"JPY": "10000"})
    snapshot = BalanceSnapshot(
        assets={"JPY": AssetBalance(total=D("10000"), free=D("10000"), locked=D("0"))},
        exchange_ts_ms=int(WORKER_NOW * 1000), fetched_at=WORKER_NOW)
    monkeypatch.setattr(prepared.reader, "fetch_balance", lambda: snapshot)
    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert "inert_reason" not in status
    assert status["cycle"] == 1


def test_the_ledger_is_closed_on_every_exit_path(monkeypatch):
    real_close = LiveLedger.close
    closes = []

    def close_spy(self):
        closes.append(self)
        return real_close(self)

    monkeypatch.setattr(LiveLedger, "close", close_spy)

    def one_run(build, **kwargs):
        workspace = Workspace()
        try:
            prepared = build(workspace)
            closes.clear()                      # prepare() opened and closed its own ledger
            code = run(prepared, **kwargs)[0]
            return code, len(closes)
        finally:
            workspace.cleanup()

    # a clean stop
    code, count = one_run(lambda w: prepare(w, cashflows={"JPY": "10000"}),
                          stop_requested=lambda: True)
    assert (code, count) == (runner_module.EXIT_OK, 1)

    # an inert start (no policy): the ledger is still closed
    code, count = one_run(lambda w: prepare(w, activate=False, cashflows={"JPY": "10000"}),
                          stop_requested=lambda: True)
    assert (code, count) == (runner_module.EXIT_OK, 1)

    # a reader failure
    def reader_fails(state_dir):
        raise ReaderFailure("auth")

    code, count = one_run(lambda w: prepare(w, cashflows={"JPY": "10000"}),
                          open_reader=reader_fails)
    assert (code, count) == (runner_module.EXIT_AUTH, 1)

    # a lease-held return: the runner's ledger is closed even though no cycle ran
    workspace = Workspace()
    try:
        prepared = prepare(workspace, cashflows={"JPY": "10000"})
        holding = LiveLedger.open(workspace.layout, paper_dir=workspace.paper, now=WORKER_NOW)
        lease = acquire_writer_lease(holding, workspace.layout, now=WORKER_NOW)
        try:
            closes.clear()
            code = run(prepared, stop_requested=lambda: True)[0]
            assert (code, len(closes)) == (runner_module.EXIT_LEASE_HELD, 1)
        finally:
            release_writer_lease(holding, lease, now=WORKER_NOW)
            real_close(holding)
    finally:
        workspace.cleanup()

    # an on_startup failure
    workspace = Workspace()
    original_startup = runner_module.reconcile.on_startup
    try:
        prepared = prepare(workspace, cashflows={"JPY": "10000"})

        def startup_fails(executor):
            raise RuntimeError("reconcile down")

        runner_module.reconcile.on_startup = startup_fails
        closes.clear()
        code = run(prepared, stop_requested=lambda: True)[0]
        assert (code, len(closes)) == (runner_module.EXIT_RETRYABLE, 1)
    finally:
        runner_module.reconcile.on_startup = original_startup
        workspace.cleanup()


# ---- h2/h3: fills and fee currencies are opening-balance history --------------------------
def record_mock_buy(ws, prepared, *, amount="2", price="100", symbol="OAS/JPY", key="h2-buy"):
    """A real small mock-exchange buy recorded in the prepared ledger (the lease is free)."""
    from docich.trading.live.executor import OrderExecutor, SubmitOutcome
    from docich.trading.live.ingest import ExecutorConfig
    from docich.trading.live.ledger import IntentRequest

    clock = prepared.clock.seconds
    prepared.mock.set_book(symbol, asks=[(D(price), D("1000"))],
                           bids=[(D(price) - D("1"), D("1000"))])
    ledger = ws.open_ledger(now=clock())
    lease = acquire_writer_lease(ledger, ws.layout, now=clock())
    try:
        executor = OrderExecutor(
            ledger, lease, reader=prepared.mock, sender=prepared.mock,
            enable_check=lambda: live_control.read_control(ws.state, now=clock()),
            clock=clock, symbols=(symbol,), config=ExecutorConfig())
        intent = executor.plan(IntentRequest(
            decision_key=key, symbol=symbol, side="buy", order_type="limit", amount=amount,
            price=price, policy_digest=prepared.ps.policy_for(symbol).digest,
            rule_id="entry-1"))
        outcome = executor.submit(intent.intent_id)
    finally:
        release_writer_lease(ledger, lease, now=clock())
        ledger.close()
    assert outcome is SubmitOutcome.ACKED


def record_fill_with_fee_in(ws, prepared, *, fee_currency, fee_amount="0.5",
                            amount="2", price="100", symbol="OAS/JPY", key="h3-buy"):
    """A recorded buy in ``symbol`` charged a known fee in another currency."""
    from docich.trading.live.ledger import FillRecord, IntentRequest, OrderObservation

    ledger = ws.open_ledger(now=WORKER_NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=WORKER_NOW)
    try:
        intent = ledger.create_intent(lease, IntentRequest(
            decision_key=key, symbol=symbol, side="buy", order_type="limit", amount=amount,
            price=price, policy_digest=prepared.ps.policy_for(symbol).digest,
            rule_id="entry-1"), now=WORKER_NOW)
        ledger.record_order_observation(lease, OrderObservation(
            exchange_order_id=f"{key}-order", intent_id=intent.intent_id, symbol=symbol,
            side="buy", order_type="limit", amount=amount, status="FULLY_FILLED",
            executed_amount=amount, remaining_amount="0", price=price), now=WORKER_NOW)
        ledger.record_fill(lease, FillRecord(
            fill_id=f"{key}-fill", exchange_order_id=f"{key}-order", symbol=symbol, side="buy",
            amount=amount, price=price, quote_notional=str(D(amount) * D(price)),
            executed_at=WORKER_NOW, fee_status="known", fee_amount=fee_amount,
            fee_currency=fee_currency), now=WORKER_NOW)
    finally:
        release_writer_lease(ledger, lease, now=WORKER_NOW)
        ledger.close()
    # The mock is the exchange view of the same facts: align it so reconciliation is clean.
    base, quote = symbol.split("/")
    prepared.mock.adjust_balance(base, D(amount))
    prepared.mock.adjust_balance(quote, -(D(amount) * D(price)))
    prepared.mock.adjust_balance(fee_currency, -D(fee_amount))


def test_h2_a_recorded_buy_fill_is_history_so_the_run_proceeds(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})    # no OAS cashflow of any kind
    record_mock_buy(ws, prepared)

    # The premise: OAS is non-zero on the exchange and its only history is the buy fill.
    assert prepared.reader.fetch_balance().assets["OAS"].total == D("2")
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        assert [fill.symbol for fill in views.all_fills(ledger)] == ["OAS/JPY"]
        with ledger.read_cursor() as cur:
            assert cur.execute("SELECT COUNT(*) FROM cashflows WHERE currency='OAS'").fetchone()[0] == 0
    finally:
        ledger.close()

    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["cycle"] == 1
    assert "inert_reason" not in status


def test_h3_a_fee_currency_is_history_so_the_run_proceeds(ws, monkeypatch):
    # XRP is watched (its own policy symbol) and non-zero; its only history is the fee of a
    # buy in another symbol, not a cashflow and not a fill base/quote.
    prepared = prepare(ws, symbols=("OAS/JPY", "XRP/JPY"), coins={"XRP": "3"},
                       cashflows={"JPY": "10000"})
    record_fill_with_fee_in(ws, prepared, fee_currency="XRP", fee_amount="0.5")

    assert prepared.reader.fetch_balance().assets["XRP"].total == D("2.5")
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        with ledger.read_cursor() as cur:
            assert cur.execute("SELECT COUNT(*) FROM cashflows WHERE currency='XRP'").fetchone()[0] == 0
            assert cur.execute("SELECT fee_currency FROM fills").fetchone()[0] == "XRP"
    finally:
        ledger.close()

    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["cycle"] == 1
    assert "inert_reason" not in status


# ---- h6: the first status after the lease is starting ---------------------------------------
def test_h6_the_first_status_after_the_lease_is_starting(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    phases = []
    real_write = runner_module.write_status

    def spy(state_dir, fields, *, now):
        phases.append(fields.get("phase"))
        return real_write(state_dir, fields, now=now)

    monkeypatch.setattr(runner_module, "write_status", spy)

    def open_reader(state_dir):
        raise ReaderFailure("auth")

    code, sleeps = run(prepared, open_reader=open_reader)

    assert code == runner_module.EXIT_AUTH
    assert phases == ["starting", "exited"]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["reason"] == "auth"
    acquire_again(ws)


# ---- F1: an intent that may have reached the exchange is opening-balance history -----------
def record_unknown_submission(ws, prepared, *, symbol="OAS/JPY", key="f1-unknown"):
    """The ledger a crash between send and record leaves: a SUBMITTING intent moved to UNKNOWN."""
    from docich.trading.live.ledger import IntentRequest

    ledger = ws.open_ledger(now=WORKER_NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=WORKER_NOW)
    try:
        intent = ledger.create_intent(lease, IntentRequest(
            decision_key=key, symbol=symbol, side="buy", order_type="limit", amount="3",
            price="100", policy_digest=prepared.ps.policy_for(symbol).digest,
            rule_id="entry-1"), now=WORKER_NOW)
        ledger.transition_intent(lease, intent.intent_id, expected_state="PLANNED",
                                 to_state="SUBMITTING", now=WORKER_NOW)
        ledger.transition_intent(lease, intent.intent_id, expected_state="SUBMITTING",
                                 to_state="UNKNOWN_SUBMISSION", now=WORKER_NOW)
    finally:
        release_writer_lease(ledger, lease, now=WORKER_NOW)
        ledger.close()
    return intent


def test_f1_an_unknown_submission_intent_is_opening_balance_history(ws, monkeypatch):
    # The exchange holds 3 OAS from the send, but the ledger has no fill: only the intent.
    prepared = prepare(ws, coins={"OAS": "3"}, cashflows={"JPY": "10000"})
    record_unknown_submission(ws, prepared)

    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        assert views.currencies_with_history(ledger) == frozenset({"OAS", "JPY"})
    finally:
        ledger.close()
    assert prepared.reader.fetch_balance().assets["OAS"].total == D("3")

    startups = []
    monkeypatch.setattr(runner_module.reconcile, "on_startup",
                        lambda executor: startups.append(1))

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]                    # history existed: reconcile was reached, no inert
    assert sleeps == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 0
    assert status["cycle"] == 1
    assert "inert_reason" not in status
    assert intent_count(ws) == 1


# ---- F2: a market-data factory failure is retryable, not a runner error ---------------------
def test_f2_a_market_data_failure_is_retryable_and_skips_the_cycle(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    cycles = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        cycles.append(1)
        return real_cycle(deps, state)

    def make_market_data(reader):
        raise OSError("market data down")

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps = run(prepared, make_market_data=make_market_data,
                              stop_requested=lambda: True)

    assert code == runner_module.EXIT_RETRYABLE
    assert cycles == []
    assert sleeps == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 8
    assert status["reason"] == "market_data_failed:OSError"
    assert intent_count(ws) == 0
    assert prepared.mock.submissions == []
    acquire_again(ws)


# ---- F4: a worker that lost the lease writes no status --------------------------------------
def _status_spy(monkeypatch):
    calls = []
    real_write = runner_module.write_status

    def spy(state_dir, fields, *, now):
        calls.append(dict(fields))
        return real_write(state_dir, fields, now=now)

    monkeypatch.setattr(runner_module, "write_status", spy)
    return calls


def test_f4_a_lost_lease_during_a_cycle_writes_no_running_or_exited_status(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    calls = _status_spy(monkeypatch)
    cycles = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        cycles.append(1)
        if len(cycles) == 2:                  # cycle 1 completed and wrote its "running" status
            lose_lease(ws)
        return real_cycle(deps, state)

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    got, sleeps = run(prepared, stop_requested=lambda: False)

    assert got == runner_module.EXIT_LEASE_LOST
    assert len(cycles) == 2
    assert [fields["phase"] for fields in calls] == ["starting", "running"]
    assert calls[-1]["phase"] == "running"
    assert all(fields["phase"] != "exited" for fields in calls)
    assert all(fields.get("outcome") != "exit_lease_lost" for fields in calls)


def test_f4_a_lost_lease_during_on_startup_writes_no_exited_status(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    calls = _status_spy(monkeypatch)

    def startup(executor):
        raise LeaseLost("lease gone")

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup)

    got, sleeps = run(prepared, stop_requested=lambda: True)

    assert got == runner_module.EXIT_LEASE_LOST
    assert [fields["phase"] for fields in calls] == ["starting"]
    assert all(fields["phase"] != "exited" for fields in calls)
    assert all(fields.get("outcome") != "exit_lease_lost" for fields in calls)


# ---- F5: a swallowed no-sender notice is counted in record_failures -------------------------
def _capture_cycle_states(monkeypatch):
    states = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        states.append(state)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)
    return states


def _live_decision_rows(ws):
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        return [event.payload for event in views.events_of_kind(ledger, "live_decision")]
    finally:
        ledger.close()


def test_f5_a_swallowed_no_sender_write_counts_one_record_failure(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    states = _capture_cycle_states(monkeypatch)
    real_record = LiveLedger.record_live_event
    attempts = []

    def record(self, lease, kind, payload, *, now):
        if payload.get("reason") == NO_SENDER:
            attempts.append(payload)
            raise RuntimeError("record down")
        return real_record(self, lease, kind, payload, now=now)

    monkeypatch.setattr(LiveLedger, "record_live_event", record)

    # Cycle 1 fails the notice write; cycle 2 tries again (a failed write is not deduped).
    code, sleeps = run(prepared, stop_requested=lambda: len(states) >= 2)

    assert code == runner_module.EXIT_OK
    assert len(states) == 2
    assert states[-1].record_failures == 1
    assert len(attempts) == 2
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["record_failures"] == 1
    assert status["cycle"] == 2
    assert [row for row in _live_decision_rows(ws) if row.get("reason") == NO_SENDER] == []


def test_f5_a_written_no_sender_notice_counts_no_record_failure(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    states = _capture_cycle_states(monkeypatch)

    code, sleeps = run(prepared, stop_requested=lambda: len(states) >= 1)

    assert code == runner_module.EXIT_OK
    assert len(states) == 1
    assert states[-1].record_failures == 0
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["record_failures"] == 0
    assert status["cycle"] == 1
    rows = _live_decision_rows(ws)
    assert [row["reason"] for row in rows if row.get("reason") == NO_SENDER] == [NO_SENDER]


# ---- control switch through the runner ---------------------------------------------------
def test_run_worker_has_no_revision_parameters():
    import inspect
    parameters = inspect.signature(runner_module.run_worker).parameters
    assert "revision" not in parameters
    assert "revision_check_every_s" not in parameters


def test_control_switched_off_mid_run_keeps_cycling_and_reports_off(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})       # on/trade
    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        if len(completed) == 1:                              # the owner switches off after cycle 1
            ws.write_control(state="off", mode="trade")
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)
    code, sleeps = run(prepared, stop_requested=lambda: len(completed) >= 3)

    assert code == runner_module.EXIT_OK
    assert [report.control_state for report in completed] == ["on", "off", "off"]
    assert [report.mode for report in completed] == ["trade", None, None]
    status = read_status(ws)
    assert status["phase"] == "exited" and status["exit_code"] == 0
    assert status["control_state"] == "off" and status["control_mode"] == "trade"
    assert "code_revision" not in status
    assert safety_trips(ws) == []
    assert prepared.mock.submissions == []
    acquire_again(ws)


def test_a_missing_control_file_cycles_with_control_unknown_and_no_trip(ws):
    prepared = prepare(ws, mode=None, cashflows={"JPY": "10000"})  # no control file at all
    assert not ws.layout.control_path.exists()

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    status = read_status(ws)
    assert status["phase"] == "exited" and status["cycle"] == 1
    assert status["control_state"] == "unknown" and status["control_mode"] is None
    assert status["mode"] is None
    assert safety_trips(ws) == []
    assert prepared.mock.submissions == []
    acquire_again(ws)


# ---- automatic opening balances (no precheck, no inert) ------------------------------------
def auto_opening_rows(ws) -> list[tuple]:
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        with ledger.read_cursor() as cur:
            return cur.execute(
                "SELECT currency, amount, source FROM cashflows "
                "WHERE cashflow_id LIKE 'auto_opening:%' ORDER BY currency").fetchall()
    finally:
        ledger.close()


def test_a_start_with_coin_and_jpy_balances_and_no_history_now_runs(ws, monkeypatch):
    # The ledger has an active policy set but no cashflow/fill/intent of any kind.
    prepared = prepare(ws, coins={"OAS": "3"}, cashflows=None)
    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps = run(prepared, stop_requested=lambda: bool(completed))

    assert code == runner_module.EXIT_OK
    assert len(completed) == 1 and completed[0].outcome is CycleOutcome.CONTINUE
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 0
    assert status["cycle"] == 1
    assert "inert_reason" not in status
    assert auto_opening_rows(ws) == [("JPY", "10000", "exchange_history"),
                                     ("OAS", "3", "exchange_history")]
    assert safety_trips(ws) == []
    assert prepared.mock.submissions == []
    acquire_again(ws)


def test_a_start_with_a_dust_balance_also_runs_and_records_the_dust(ws, monkeypatch):
    prepared = prepare(ws, coins={"OAS": "0.00000001"}, cashflows=None)
    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)
    code, sleeps = run(prepared, stop_requested=lambda: bool(completed))

    assert code == runner_module.EXIT_OK
    assert len(completed) == 1
    assert "inert_reason" not in read_status(ws)
    assert ("OAS", "0.00000001", "exchange_history") in auto_opening_rows(ws)


def test_an_unknown_submission_is_ingested_before_any_auto_opening(ws, monkeypatch):
    prepared = prepare(ws, coins={"OAS": "3"}, cashflows={"JPY": "10000"})
    record_unknown_submission(ws, prepared)            # the send reached the exchange; no fill
    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    assert read_status(ws)["phase"] == "exited"
    # The intent is history for OAS, so the coin never gets an auto_opening cashflow.
    assert auto_opening_rows(ws) == []
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        assert views.currencies_with_history(ledger) == frozenset({"OAS", "JPY"})
    finally:
        ledger.close()


def test_a_failed_opening_attempt_is_retried_once_before_the_next_cycle(ws, monkeypatch):
    prepared = prepare(ws, coins={"OAS": "3"}, cashflows=None)
    real_opening = runner_module.record_auto_opening
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the first attempt could not read the balance")
        return real_opening(*args, **kwargs)

    monkeypatch.setattr(runner_module, "record_auto_opening", flaky)
    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps = run(prepared, stop_requested=lambda: bool(completed))

    assert code == runner_module.EXIT_OK
    assert len(calls) == 2                       # startup attempt + one retry before cycle 1
    assert len(completed) == 1
    assert auto_opening_rows(ws) == [("JPY", "10000", "exchange_history"),
                                     ("OAS", "3", "exchange_history")]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert "inert_reason" not in status


def test_an_account_balance_difference_is_not_reported_in_the_status(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    prepared.mock.adjust_balance("JPY", "-100")     # expected 10000, the exchange says 9900
    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps = run(prepared, stop_requested=lambda: len(completed) >= 2)

    assert code == runner_module.EXIT_OK
    assert len(completed) == 2
    assert all(not hasattr(report, "balance_warnings") for report in completed)
    assert safety_trips(ws) == []
    assert "safety_trip" not in event_kinds(ws)
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert "balance_warnings" not in status
    assert "balance" not in status and "amount" not in status
    acquire_again(ws)
