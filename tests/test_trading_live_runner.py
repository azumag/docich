"""The LIVE worker runner (L4b-6a): startup order, inert starts, exit mapping and the loop.

The ledger, lease, mock exchange, executor and enable gate are the real LIVE pieces from the
shared harness; the reader is a thin test wrapper around the mock (health/balance hooks only),
the market data is injected, and the clock/sleep/stop flag are fakes.  Every number is hand
derived; nothing here touches the network and no real sender exists.
"""
from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import act, safety, views
from docich.trading.live import decide as decide_module
from docich.trading.live.cycle import CycleOutcome, CycleState, next_wait, run_cycle
from docich.trading.live.lease import (LeaseHeld, LeaseLost, acquire_writer_lease,
                                       release_writer_lease)
from docich.trading.live.ledger import Cashflow, LiveLedger
from docich.trading.live.signals import LiveSignals
from docich.trading.live_owner.revision import RevisionError
from docich.trading.live_worker import runner as runner_module
from docich.trading.live_worker.status import ALLOWED_KEYS, STATUS_FILENAME
from docich.trading.market_data import MarketFrame

from test_trading_live_support import (Clock, Harness, Workspace, block_network, make_mock,
                                       policy_set)
import test_trading_live_decide as decide_tests
import test_trading_live_ttl as ttl_tests

WORKER_NOW = 1_800_000_000.0
REV = "a" * 40
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
    revision: str


def prepare(ws: Workspace, *, symbols=("OAS/JPY",), mode="trade", revision=REV,
            jpy="10000", coins=None, cashflows=None, activate=True, store=True) -> Prepared:
    """A closed ledger + released lease + active policy set + enable file (the runner's inputs)."""
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
        ws.write_enable(ps, mode=mode, revision=revision)
    if activate:
        ledger.record_owner_activation(lease, ps,
                                       enable=ws.enable_result(ps, revision=revision),
                                       operator="owner", note="runner test",
                                       now=clock.seconds())
    for index, (currency, amount) in enumerate((cashflows or {}).items()):
        ledger.record_cashflow(lease, Cashflow(f"cf-{currency}-{index}", "deposit", currency,
                                               str(amount), "owner_manual", clock.seconds()),
                               now=clock.seconds())
    release_writer_lease(ledger, lease, now=clock.seconds())
    ledger.close()
    return Prepared(ws=ws, clock=clock, mock=mock, ps=ps, reader=RunnerReader(mock),
                    mode=mode, revision=revision)


def run(prepared: Prepared, *, stop_requested=None, revision=None, open_reader=None,
        make_market_data=None, interval_s=60.0, revision_check_every_s=10_000.0,
        config=None):
    """Call run_worker with the prepared state dir and the fake clock/sleep."""
    clock = prepared.clock.seconds
    sleeps = Sleeps(prepared.clock)
    times = []

    def rev():
        times.append(float(clock()))
        return prepared.revision if revision is None else revision()

    kwargs = dict(
        state_dir=prepared.ws.state,
        open_reader=open_reader or (lambda state_dir: prepared.reader),
        make_market_data=make_market_data or (lambda reader: FakeMarketData(clock)),
        revision=rev,
        clock=clock,
        stop_requested=stop_requested or (lambda: False),
        sleep=sleeps,
        interval_s=interval_s,
        revision_check_every_s=revision_check_every_s,
    )
    if config is not None:
        kwargs["config"] = config
    code = runner_module.run_worker(**kwargs)
    return code, sleeps.calls, times


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
    code, sleeps, times = run(prepared, stop_requested=lambda: len(completed) >= 3)

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
    assert status["code_revision"] == REV[:12]
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
def test_r2_startup_order_is_ledger_lease_reader_revision_balance_startup_cycle(ws, monkeypatch):
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
    real_fetch = reader.fetch_balance
    seen = {"balance": False}

    def fetch_spy():
        if not seen["balance"]:
            seen["balance"] = True
            calls.append("balance")
        return real_fetch()

    monkeypatch.setattr(reader, "fetch_balance", fetch_spy)
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        calls.append("on_startup")
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        calls.append("run_cycle")
        return real_cycle(deps, state)

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)

    code, sleeps, times = run(
        prepared, stop_requested=lambda: True,
        open_reader=lambda state_dir: (calls.append("reader"), reader)[1],
        revision=lambda: (calls.append("revision"), prepared.revision)[1])

    assert code == runner_module.EXIT_OK
    assert calls == ["ledger", "lease", "reader", "revision", "balance", "on_startup",
                     "run_cycle"]
    assert times == [WORKER_NOW]
    assert read_status(ws)["cycle"] == 1


# ---- r3: the opening-balance precheck ---------------------------------------------------
def test_r3_a_nonzero_opening_balance_without_a_cashflow_is_inert(ws, monkeypatch):
    prepared = prepare(ws, coins={"OAS": "3"},
                       cashflows={"JPY": "10000"})       # OAS has no cashflow row of any kind
    before = event_kinds(ws)
    startups = []
    monkeypatch.setattr(runner_module.reconcile, "on_startup",
                        lambda executor: startups.append(1))

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == []
    assert sleeps == []
    status = read_status(ws)
    assert status["phase"] == "inert"
    assert status["inert_reason"] == "opening_balance_missing:OAS"
    assert status["exit_code"] == 0
    # Only the lease bookkeeping is appended: no cycle, no decision, no safety write.
    assert event_kinds(ws)[len(before):] == ["lease_acquired", "lease_released"]
    assert safety_trips(ws) == []
    assert intent_count(ws) == 0
    assert prepared.mock.submissions == []
    assert prepared.mock.all_orders() == []
    acquire_again(ws)


def test_r3_all_watched_currencies_with_a_cashflow_let_the_run_proceed(ws, monkeypatch):
    prepared = prepare(ws, coins={"OAS": "3"},
                       cashflows={"JPY": "10000", "OAS": "3"})
    startups = []
    real_startup = runner_module.reconcile.on_startup

    def startup_spy(executor):
        startups.append(1)
        return real_startup(executor)

    monkeypatch.setattr(runner_module.reconcile, "on_startup", startup_spy)

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    assert read_status(ws)["phase"] == "exited"


# ---- r4: no active policy set ------------------------------------------------------------
def test_r4_no_active_policy_set_is_inert_and_never_reaches_the_revision(ws, monkeypatch):
    prepared = prepare(ws, activate=False, cashflows={"JPY": "10000"})
    startups = []
    monkeypatch.setattr(runner_module.reconcile, "on_startup",
                        lambda executor: startups.append(1))
    before = event_kinds(ws)

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert times == []                       # step 4 is before step 5
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

    got, sleeps, times = run(prepared, open_reader=open_reader)

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
        code, sleeps, times = run(prepared, stop_requested=lambda: True)
        assert code == runner_module.EXIT_LEASE_HELD
        assert not (ws.layout.root / STATUS_FILENAME).exists()
    finally:
        release_writer_lease(ledger, lease, now=WORKER_NOW)
        ledger.close()


@pytest.mark.parametrize("exc", [
    pytest.param(RevisionError("the tracked tree is dirty"), id="RevisionError"),
    pytest.param(RuntimeError("git rev-parse failed"), id="RuntimeError"),
    pytest.param(OSError("git is not installed"), id="OSError"),
])
def test_r5_any_unreadable_revision_is_exit_2(ws, exc):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    def revision():
        raise exc

    code, sleeps, times = run(prepared, revision=revision)

    assert code == runner_module.EXIT_CONFIG
    assert times == [WORKER_NOW]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_CONFIG
    assert status["reason"] == f"revision_error:{type(exc).__name__}"
    acquire_again(ws)


def test_r5_a_balance_failure_at_the_precheck_is_retryable_and_skips_startup(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    prepared.reader.balance_error = RuntimeError("balance down")
    startups = []
    before = event_kinds(ws)
    monkeypatch.setattr(runner_module.reconcile, "on_startup",
                        lambda executor: startups.append(1))

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_RETRYABLE
    assert sleeps == []
    assert startups == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_RETRYABLE
    assert status["reason"] == "balance_unavailable_at_start"
    assert safety_trips(ws) == []
    assert intent_count(ws) == 0
    assert prepared.mock.submissions == []
    assert prepared.mock.all_orders() == []
    assert event_kinds(ws)[len(before):] == ["lease_acquired", "lease_released"]
    acquire_again(ws)


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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

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

    got, sleeps, times = run(prepared, stop_requested=lambda: False)

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

    got, sleeps, times = run(prepared, stop_requested=lambda: False)

    assert got == runner_module.EXIT_LEASE_LOST
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_LEASE_LOST
    assert status["reason"] == "lease_lost"
    assert "lease_lost" in safety_trips(ws)
    # The lock was replaced; the best-effort release must not have raised out of the runner.


def test_r6_a_changed_revision_exits_6_only_at_the_check_interval(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    def revision():
        return "b" * 40 if prepared.clock.seconds() >= WORKER_NOW + 60 else prepared.revision

    code, sleeps, times = run(prepared, stop_requested=lambda: False,
                              revision=revision, revision_check_every_s=60.0)

    assert code == runner_module.EXIT_CODE_CHANGED
    # Called once at startup, then once at exactly t0+60 -- never on every loop turn.
    assert times == [WORKER_NOW, WORKER_NOW + 60]
    assert sum(sleeps) == 60.0 and set(sleeps) == {1.0}
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_CODE_CHANGED
    assert status["reason"] == "code_revision_changed"
    assert status["cycle"] == 1
    acquire_again(ws)


@pytest.mark.parametrize("exc", [
    pytest.param(RevisionError("the tracked tree is dirty"), id="RevisionError"),
    pytest.param(RuntimeError("git rev-parse failed"), id="RuntimeError"),
])
def test_r6_an_unreadable_revision_exits_6_at_the_check_interval(ws, exc):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    calls = []

    def revision():
        calls.append(1)
        if len(calls) == 1:               # startup reads the revision; the periodic read fails
            return prepared.revision
        raise exc

    code, sleeps, times = run(prepared, stop_requested=lambda: False,
                              revision=revision, revision_check_every_s=60.0)

    assert code == runner_module.EXIT_CODE_CHANGED
    # One startup call plus exactly one periodic call: the failure ends the worker at t0+60.
    assert times == [WORKER_NOW, WORKER_NOW + 60]
    assert len(calls) == 2
    assert sleeps == [1.0] * 60
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_CODE_CHANGED
    assert status["reason"] == f"revision_unreadable:{type(exc).__name__}"
    assert status["cycle"] == 1
    acquire_again(ws)


# ---- r7: stop handling ------------------------------------------------------------------
def test_r7_a_stop_during_the_wait_ends_within_one_slice(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    def stop_requested():
        return prepared.clock.seconds() >= WORKER_NOW + 1.0

    code, sleeps, times = run(prepared, stop_requested=stop_requested)

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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

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
    code, sleeps, times = run(prepared, stop_requested=lambda: True)

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

    code, sleeps, times = run(prepared, stop_requested=stop_requested,
                              interval_s=60.0, revision_check_every_s=10_000.0)

    # Hand derivation: failures 1,2,3 -> next_wait(60,1)=60, next_wait(60,2)=120, then one
    # 1.0 s slice of next_wait(60,3)=240 before the stop is seen.  All slices are <= 1.0 s.
    assert [next_wait(60.0, n) for n in (1, 2, 3)] == [60.0, 120.0, 240.0]
    assert len(sleeps) == 60 + 120 + 1
    assert set(sleeps) == {1.0}
    assert sum(sleeps) == 181.0
    assert code == runner_module.EXIT_OK
    assert read_status(ws)["cycle"] == 3


# ---- r9: production wiring pins ---------------------------------------------------------
def test_r9_the_runner_constructs_the_executor_with_a_literal_none_sender():
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
    assert "sender" in calls[0]
    assert isinstance(calls[0]["sender"], ast.Constant)
    assert calls[0]["sender"].value is None


FORBIDDEN_MODULES = frozenset({
    "ccxt", "socket", "ssl", "http", "urllib", "requests", "subprocess", "importlib",
    "asyncio", "signal", "threading",
})
FORBIDDEN_PATHS = ("docich.trading.exchanges", "docich.trading.live_owner.cli",
                   "docich.trading.worker", "docich.trading.slot_runtime")


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
                        if _forbidden(name))
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolved_import(node.module, node.level)
                if _forbidden(resolved):
                    offenders.append((path.name, resolved))
                for alias in node.names:
                    joined = f"{resolved}.{alias.name}" if resolved else alias.name
                    if _forbidden(joined):
                        offenders.append((path.name, joined))
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in {"exit", "quit"}:
                    offenders.append((path.name, func.id))
                elif isinstance(func, ast.Attribute):
                    owner = getattr(func.value, "id", None)
                    if (owner, func.attr) in {("sys", "exit"), ("os", "_exit")}:
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
    code, sleeps, times = run(prepared, stop_requested=lambda: True)

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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    text = status_text(ws)
    status = read_status(ws)
    assert set(status) <= ALLOWED_KEYS
    assert "balance" not in status and "amount" not in status
    assert "api_key" not in status and "prices" not in status
    assert "987654321" not in text
    assert status["phase"] == "exited" and status["cycle"] == 1


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
    ws.write_enable(h.ps, mode="shadow")

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
    assert runner_module.EXIT_CODE_CHANGED == 6
    assert runner_module.EXIT_LEASE_LOST == 7
    assert runner_module.EXIT_RETRYABLE == 8


def test_a_held_lease_returns_the_literal_5(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    ledger = LiveLedger.open(ws.layout, paper_dir=ws.paper, now=WORKER_NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=WORKER_NOW)
    try:
        code, sleeps, times = run(prepared, stop_requested=lambda: True)
        assert code == 5
    finally:
        release_writer_lease(ledger, lease, now=WORKER_NOW)
        ledger.close()


def test_a_changed_revision_returns_the_literal_6(ws):
    prepared = prepare(ws, cashflows={"JPY": "10000"})

    def revision():
        return "b" * 40 if prepared.clock.seconds() >= WORKER_NOW + 60 else prepared.revision

    code, sleeps, times = run(prepared, stop_requested=lambda: False,
                              revision=revision, revision_check_every_s=60.0)

    assert code == 6


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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert "inert_reason" not in status
    assert status["cycle"] == 1


def test_a_locked_only_watched_currency_without_a_cashflow_is_inert(ws, monkeypatch):
    # The converse of the missing-entry rule: present with free 0 but a non-zero locked total
    # and no cashflow of any kind is still an opening balance this worker cannot account for.
    from docich.trading.live.exchange import AssetBalance, BalanceSnapshot

    prepared = prepare(ws, cashflows={"JPY": "10000"})
    snapshot = BalanceSnapshot(
        assets={"JPY": AssetBalance(total=D("10000"), free=D("10000"), locked=D("0")),
                "OAS": AssetBalance(total=D("2"), free=D("0"), locked=D("2"))},
        exchange_ts_ms=int(WORKER_NOW * 1000), fetched_at=WORKER_NOW)
    monkeypatch.setattr(prepared.reader, "fetch_balance", lambda: snapshot)

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    status = read_status(ws)
    assert status["phase"] == "inert"
    assert status["inert_reason"] == "opening_balance_missing:OAS"
    assert status["exit_code"] == 0


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
            enable_check=lambda: ws.enable_result(prepared.ps, revision=prepared.revision),
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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

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

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    assert startups == [1]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["cycle"] == 1
    assert "inert_reason" not in status


# ---- h5/h6: the precheck's try boundary and the starting status ---------------------------
def test_h5_a_watched_currencies_failure_is_retryable_not_runner_error(ws, monkeypatch):
    prepared = prepare(ws, cashflows={"JPY": "10000"})
    startups = []
    monkeypatch.setattr(runner_module.reconcile, "on_startup",
                        lambda executor: startups.append(1))

    def boom(self):
        raise RuntimeError("rules down")

    monkeypatch.setattr(runner_module.OrderExecutor, "watched_currencies", boom)
    before = event_kinds(ws)

    code, sleeps, times = run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_RETRYABLE
    assert sleeps == []
    assert startups == []
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == runner_module.EXIT_RETRYABLE
    assert status["reason"] == "balance_unavailable_at_start"
    assert safety_trips(ws) == []
    assert intent_count(ws) == 0
    assert prepared.mock.submissions == []
    assert prepared.mock.all_orders() == []
    assert event_kinds(ws)[len(before):] == ["lease_acquired", "lease_released"]
    acquire_again(ws)


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

    code, sleeps, times = run(prepared, open_reader=open_reader)

    assert code == runner_module.EXIT_AUTH
    assert phases == ["starting", "exited"]
    status = read_status(ws)
    assert status["phase"] == "exited"
    assert status["reason"] == "auth"
    acquire_again(ws)
