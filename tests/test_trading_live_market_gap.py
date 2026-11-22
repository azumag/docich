"""Issue #4586: the public-frame gap tracker, the status view and the exit-8 condition.

A single stale symbol is a PARTIAL gap the worker observes and retries forever; every
configured symbol stale is an ALL-symbol gap the private reader's own health cannot see
(the balance read keeps succeeding); a symbol that fetches a fresh frame again is a
recovery.  When the all-symbol gap outlasts ``CycleConfig.market_gap_exit_s`` the cycle
reports ``EXIT_MARKET_DATA`` and the runner exits 8 (retryable).

Everything is synthetic: the shared LIVE harness, scripted readers and fake public
frames.  No network, no real clock, no files outside the workspace state dir.
"""
from __future__ import annotations

import json

import pytest

from docich.trading.live import views
from docich.trading.live.cycle import (MARKET_DATA_GAP, CycleConfig, CycleOutcome,
                                       run_cycle)
from docich.trading.live_worker import runner as runner_module
from docich.trading.live_worker.status import STATUS_FILENAME, write_status

import test_trading_live_runner as runner_tests
from test_trading_live_cycle import market_frame, make_rig
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


def _read_status(ws) -> dict:
    return json.loads((ws.layout.root / STATUS_FILENAME).read_text(encoding="utf-8"))


# ---- g1: a single stale symbol is a partial gap, retried forever, never an exit ---------
def test_a_single_symbol_gap_is_partial_and_never_exits(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY", "OMG/JPY"), gate_open=False)
    rig = make_rig(ws, h, config=CycleConfig(market_gap_exit_s=60))
    rig.market.overrides = {"OAS/JPY": RuntimeError("feed down")}

    t0 = h.clock.seconds()
    first = run_cycle(rig.deps, rig.state)
    assert first.outcome is CycleOutcome.CONTINUE
    gap = first.market_gap
    assert gap.scope == "partial" and gap.stale_count == 1
    assert gap.gap_since == t0
    assert gap.all_stale_since is None and gap.all_stale_cycles == 0

    h.advance(600)                                    # ten times the exit threshold
    later = run_cycle(rig.deps, rig.state)
    assert later.outcome is CycleOutcome.CONTINUE     # a partial gap NEVER exits
    assert later.market_gap.scope == "partial"
    assert later.market_gap.gap_since == t0           # the staleness is continuous
    assert later.fresh_symbols == ("OMG/JPY", "XYM/JPY")
    assert later.stale_symbols == ("OAS/JPY",)


# ---- g2: an all-symbol gap counts cycles and exits once it outlasts the threshold ------
def test_an_all_symbol_gap_exits_after_the_threshold(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"), gate_open=False)
    rig = make_rig(ws, h, config=CycleConfig(market_gap_exit_s=120))
    rig.market.overrides = {symbol: RuntimeError("feed down") for symbol in h.symbols}

    t0 = h.clock.seconds()
    first = run_cycle(rig.deps, rig.state)
    assert first.outcome is CycleOutcome.CONTINUE     # the gap just started: retry
    assert first.market_gap.scope == "all"
    assert first.market_gap.stale_count == 2
    assert first.market_gap.gap_since == t0
    assert first.market_gap.all_stale_since == t0
    assert first.market_gap.all_stale_cycles == 1

    h.advance(60)
    second = run_cycle(rig.deps, rig.state)
    assert second.outcome is CycleOutcome.CONTINUE     # 60 s < 120 s: still retrying
    assert second.market_gap.all_stale_since == t0
    assert second.market_gap.all_stale_cycles == 2

    h.advance(60)
    third = run_cycle(rig.deps, rig.state)
    assert third.outcome is CycleOutcome.EXIT_MARKET_DATA
    assert third.reason == MARKET_DATA_GAP == "market_data_gap"
    assert third.market_gap.scope == "all"             # the exit report keeps the view
    assert third.market_gap.all_stale_since == t0
    assert third.market_gap.all_stale_cycles == 3
    assert rig.state.consecutive_failures == 0         # the reader stayed healthy

    # the ledger keeps one worker_state row for the change into the exit outcome
    events = views.events_of_kind(h.ledger, "worker_state")
    assert events[-1].payload["outcome"] == "exit_market_data"
    assert events[-1].payload["reason"] == "market_data_gap"


def test_a_disabled_exit_retries_the_all_symbol_gap_forever(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY",), gate_open=False)
    rig = make_rig(ws, h, config=CycleConfig(market_gap_exit_s=None))
    rig.market.overrides = {"OAS/JPY": RuntimeError("feed down")}

    t0 = h.clock.seconds()
    first = run_cycle(rig.deps, rig.state)
    assert first.outcome is CycleOutcome.CONTINUE     # the gap just started: retry
    h.advance(10_000)                                  # far past any sane threshold
    report = run_cycle(rig.deps, rig.state)
    assert report.outcome is CycleOutcome.CONTINUE     # exit disabled: observe and retry
    assert report.market_gap.scope == "all"
    assert report.market_gap.all_stale_since == t0
    assert report.market_gap.all_stale_cycles == 2


def test_the_exit_threshold_must_be_positive_or_none():
    with pytest.raises(ValueError):
        CycleConfig(market_gap_exit_s=0)
    with pytest.raises(ValueError):
        CycleConfig(market_gap_exit_s=-5)
    with pytest.raises(ValueError):
        CycleConfig(market_gap_exit_s=float("nan"))
    assert CycleConfig(market_gap_exit_s=None).market_gap_exit_s is None


# ---- g3: recovery -- a fresh frame again clears the symbol, partial after all ----------
def test_a_recovered_symbol_leaves_the_gap_and_resets_the_all_timer(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"), gate_open=False)
    rig = make_rig(ws, h, config=CycleConfig(market_gap_exit_s=60))
    rig.market.overrides = {symbol: RuntimeError("feed down") for symbol in h.symbols}

    t0 = h.clock.seconds()
    first = run_cycle(rig.deps, rig.state)
    assert first.market_gap.scope == "all"

    del rig.market.overrides["OAS/JPY"]               # OAS fetches a fresh frame again
    h.advance(60)
    second = run_cycle(rig.deps, rig.state)
    assert second.outcome is CycleOutcome.CONTINUE     # recovery, not an exit
    assert second.market_gap.scope == "partial"
    assert second.market_gap.stale_count == 1
    assert second.market_gap.gap_since == t0           # XYM has been stale since t0
    assert second.market_gap.all_stale_since is None
    assert second.market_gap.all_stale_cycles == 0
    assert rig.state.market_gap.stale_since == {"XYM/JPY": t0}

    del rig.market.overrides["XYM/JPY"]
    third = run_cycle(rig.deps, rig.state)
    assert third.market_gap.scope == "none"
    assert third.market_gap.stale_count == 0
    assert third.market_gap.gap_since is None
    assert rig.state.market_gap.stale_since == {}


def test_a_partial_gap_becoming_all_starts_the_all_timer_at_the_transition(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"), gate_open=False)
    rig = make_rig(ws, h, config=CycleConfig(market_gap_exit_s=30))
    rig.market.overrides = {"OAS/JPY": RuntimeError("feed down")}

    t0 = h.clock.seconds()
    first = run_cycle(rig.deps, rig.state)
    assert first.market_gap.scope == "partial" and first.market_gap.gap_since == t0

    h.advance(60)                                     # past the threshold, but partial
    rig.market.overrides["XYM/JPY"] = RuntimeError("feed down")
    t1 = h.clock.seconds()
    second = run_cycle(rig.deps, rig.state)
    assert second.outcome is CycleOutcome.CONTINUE     # the all-gap just began: retry
    assert second.market_gap.scope == "all"
    assert second.market_gap.all_stale_since == t1     # the transition, not t0
    assert second.market_gap.gap_since == t0           # the oldest staleness is still OAS
    assert second.market_gap.all_stale_cycles == 1


# ---- g4: stale data counts the same as a fetch exception --------------------------------
def test_stale_quality_frames_count_toward_the_gap_like_exceptions(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY",), gate_open=False)
    rig = make_rig(ws, h)
    now = h.clock.seconds()
    rig.market.overrides = {"OAS/JPY": market_frame("OAS/JPY", at=now - 10_000)}

    report = run_cycle(rig.deps, rig.state)

    assert report.outcome is CycleOutcome.CONTINUE
    assert report.stale_symbols == ("OAS/JPY",)
    assert report.market_gap.scope == "all"            # unusable data is a gap too
    assert report.market_gap.all_stale_cycles == 1


# ---- g5: a cycle that never reaches the fetch step changes nothing ----------------------
def test_a_failed_cycle_neither_confirms_nor_clears_the_gap(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"), gate_open=False)
    rig = make_rig(ws, h)
    rig.market.overrides = {symbol: RuntimeError("feed down") for symbol in h.symbols}

    t0 = h.clock.seconds()
    run_cycle(rig.deps, rig.state)
    assert rig.state.market_gap.all_stale_cycles == 1

    real_step = rig.executor.step
    rig.executor.step = lambda: (_ for _ in ()).throw(RuntimeError("step down"))
    h.advance(60)
    failed = run_cycle(rig.deps, rig.state)
    assert failed.outcome is CycleOutcome.CYCLE_FAILED
    assert failed.reason == "step_failed:RuntimeError"
    # step 8 never ran: the report still carries the last known gap, uncleared
    assert failed.market_gap.scope == "all"
    assert failed.market_gap.all_stale_since == t0
    assert failed.market_gap.all_stale_cycles == 1     # unchanged, not reset

    rig.executor.step = real_step
    h.advance(60)
    resumed = run_cycle(rig.deps, rig.state)
    assert resumed.market_gap.all_stale_cycles == 2    # the count continues, not restarts
    assert resumed.market_gap.all_stale_since == t0


def test_a_stop_reports_the_last_known_gap_without_touching_it(ws, harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"), gate_open=False)
    stopped = make_rig(ws, h, stop_requested=lambda: True)
    t0 = h.clock.seconds()
    stopped.state.market_gap.update(symbols=h.symbols, stale=list(h.symbols), now=t0)

    report = run_cycle(stopped.deps, stopped.state)

    assert report.outcome is CycleOutcome.STOP_REQUESTED
    assert report.market_gap.scope == "all"            # the last known state is reported
    assert report.market_gap.all_stale_since == t0
    assert report.market_gap.all_stale_cycles == 1


# ---- g6: the runner maps EXIT_MARKET_DATA to exit code 8 and the status shows the gap ---
def test_the_runner_exits_8_and_the_status_shows_scope_and_duration(ws, monkeypatch):
    prepared = runner_tests.prepare(ws, cashflows={"JPY": "10000"})

    class Down(runner_tests.FakeMarketData):
        def fetch_frame(self, symbol, now):
            raise RuntimeError("feed down")

    code, sleeps = runner_tests.run(
        prepared,
        make_market_data=lambda reader: Down(prepared.clock.seconds),
        config=CycleConfig(market_gap_exit_s=120))

    assert code == runner_module.EXIT_RETRYABLE == 8
    status = runner_tests.read_status(ws)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 8
    assert status["reason"] == "market_data_gap"
    assert status["outcome"] == "exit_market_data"
    assert status["cycle"] == 3                        # t0, +60, +120: the exit cycle
    assert status["market_gap_scope"] == "all"
    assert status["market_stale_count"] == 1
    assert status["market_all_stale_cycles"] == 3
    assert status["market_gap_since"] == status["market_all_stale_since"]
    assert status["market_gap_seconds"] == 120
    assert status["market_all_stale_seconds"] == 120
    runner_tests.acquire_again(ws)


def test_a_partial_gap_is_visible_in_the_status_while_the_run_goes_on(ws, monkeypatch):
    prepared = runner_tests.prepare(ws, symbols=("OAS/JPY", "XYM/JPY"),
                                    cashflows={"JPY": "10000"})

    class Partial(runner_tests.FakeMarketData):
        def fetch_frame(self, symbol, now):
            if symbol == "OAS/JPY":
                raise RuntimeError("feed down")
            return super().fetch_frame(symbol, now)

    completed = []
    real_cycle = runner_module.run_cycle

    def cycle_spy(deps, state):
        report = real_cycle(deps, state)
        completed.append(report)
        return report

    monkeypatch.setattr(runner_module, "run_cycle", cycle_spy)
    code, sleeps = runner_tests.run(
        prepared,
        make_market_data=lambda reader: Partial(prepared.clock.seconds),
        stop_requested=lambda: len(completed) >= 2)

    assert code == runner_module.EXIT_OK
    assert [report.market_gap.scope for report in completed] == ["partial", "partial"]
    status = runner_tests.read_status(ws)
    assert status["phase"] == "exited"                 # stopped, not a gap exit
    assert status["market_gap_scope"] == "partial"
    assert status["market_stale_count"] == 1
    assert status["market_gap_since"] is not None
    assert status["market_gap_seconds"] is not None
    assert status["market_all_stale_since"] is None
    assert status["market_all_stale_seconds"] is None
    assert status["market_all_stale_cycles"] == 0
    runner_tests.acquire_again(ws)


def test_a_clean_run_reports_scope_none_and_null_gap_fields(ws, monkeypatch):
    prepared = runner_tests.prepare(ws, cashflows={"JPY": "10000"})

    code, sleeps = runner_tests.run(prepared, stop_requested=lambda: True)

    assert code == runner_module.EXIT_OK
    status = runner_tests.read_status(ws)
    assert status["market_gap_scope"] == "none"
    assert status["market_stale_count"] == 0
    assert status["market_gap_since"] is None
    assert status["market_gap_seconds"] is None
    assert status["market_all_stale_since"] is None
    assert status["market_all_stale_cycles"] == 0


# ---- g7: the status allow-list carries and cleans the gap fields ------------------------
def test_the_status_allow_list_carries_the_gap_fields_and_drops_bad_values(ws):
    ws.layout.root.mkdir(mode=0o700)

    assert write_status(ws.state, {
        "phase": "running",
        "market_gap_scope": "partial",
        "market_stale_count": 2,
        "market_gap_since": 1_800_000_000,
        "market_gap_seconds": 60,
        "market_all_stale_since": None,
        "market_all_stale_seconds": None,
        "market_all_stale_cycles": 0,
    }, now=1_800_000_060.0) is True
    data = _read_status(ws)
    assert data["market_gap_scope"] == "partial"
    assert data["market_stale_count"] == 2
    assert data["market_gap_since"] == 1_800_000_000
    assert data["market_gap_seconds"] == 60
    assert data["market_all_stale_since"] is None
    assert data["market_all_stale_cycles"] == 0

    # a bogus scope and negative counters are dropped like every other bad value
    assert write_status(ws.state, {
        "phase": "running",
        "market_gap_scope": "bogus",
        "market_stale_count": -1,
        "market_gap_seconds": -5,
        "market_all_stale_cycles": -2,
    }, now=1_800_000_060.0) is True
    data = _read_status(ws)
    assert "market_gap_scope" not in data
    assert "market_stale_count" not in data
    assert "market_gap_seconds" not in data
    assert "market_all_stale_cycles" not in data
