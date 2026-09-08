# Crypto Paper Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a docich-supervised, continuously running paper-only bitbank worker that scans existing strategies, records paper evidence, and emits bounded public status/events without adding live trading capability.

**Architecture:** Add a disabled-by-default `TradingConfig`, a dedicated `trading` tmux component under the existing supervisor, a bounded public event journal, and a `trading.worker` orchestration module that reuses current strategy/risk/ledger/settlement code. Game lifecycle remains independent; the worker uses public bitbank data only and writes paper state under `run/trading`.

**Tech Stack:** Python 3.11+, dataclasses/TOML, sqlite3, JSON/JSONL, tmux supervision, existing CCXT optional public bitbank gateway, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-crypto-paper-worker-design.md`

## Global Constraints

- `paper_worker_enabled` defaults to `false`; deploying code alone must not start polling.
- No bitbank API key, private/balance/order endpoint, leverage, withdrawal, or live execution path.
- Worker interval default is 60 seconds and must be at least 10 seconds.
- Synthetic paper capital default is 10,000 JPY and must be at least 1,000 JPY.
- Existing `CapitalPolicy` 30% per-opportunity / 30% total-deployed limits remain authoritative.
- Existing buy-only strategy behavior is unchanged; no exit strategy is introduced here.
- Public event journal retains at most 500 allowlisted events and uses 0700 directory / 0600 file permissions.
- Worker uses 5m OHLCV / 24 bars, depth 20, 10 bps arbitrage threshold, JPY probes 1,000/3,000/10,000 as code constants.
- Full docich regression and GitHub CI are required before review completion.

---

## File Structure

- Create `src/docich/trading/events.py`: allowlisted bounded JSONL event journal and event builders.
- Create `src/docich/trading/worker.py`: one-cycle orchestration plus continuous loop.
- Modify `src/docich/config.py`: `TradingConfig`, global load/validation.
- Modify `config/docich.toml` and `config/docich.soren-live.toml`: explicit disabled worker config.
- Modify `src/docich/cli.py`: `up`/`run trading` lifecycle wiring.
- Modify `src/docich/status.py`: report `trading` tmux window.
- Modify `src/docich/watchdog.py`: recover enabled missing trading window through `up`.
- Modify `src/docich/trading/status.py`: allowlisted `worker_summary`.
- Modify `src/docich/trading/cli.py`: sanitize `worker_summary`; reuse public settlement observation ID helper.
- Modify `src/docich/trading/settlement.py`: export deterministic observation-ID helper shared by CLI/worker.
- Modify `.github/workflows/ci.yml` and `README.md`: contract coverage and operator docs.
- Create `tests/test_trading_events.py`, `tests/test_trading_worker.py`, `tests/test_trading_worker_lifecycle.py`.
- Modify existing config/status/watchdog/trading CLI tests where additive schema/lifecycle changes require it.

### Task 1: Configuration and docich lifecycle

**Files:**
- Modify: `src/docich/config.py`
- Modify: `config/docich.toml`
- Modify: `config/docich.soren-live.toml`
- Modify: `src/docich/cli.py`
- Modify: `src/docich/status.py`
- Modify: `src/docich/watchdog.py`
- Create: `tests/test_trading_worker_lifecycle.py`
- Modify: `tests/test_config.py`, `tests/test_status.py`, `tests/test_watchdog.py`

**Interfaces:**
- Produces: `TradingConfig(paper_worker_enabled: bool, interval_s: int, paper_capital_jpy: int)` on `GlobalConfig.trading`.
- Produces: internal `docich run trading` component and tmux window name `trading`.

- [ ] **Step 1: Write failing config/lifecycle tests**

```python
def test_trading_config_defaults_disabled(tmp_path):
    g = load_global(tmp_path, config_path=_minimal_config(tmp_path))
    assert g.trading.paper_worker_enabled is False
    assert g.trading.interval_s == 60
    assert g.trading.paper_capital_jpy == 10000


def test_up_starts_enabled_trading_window(fake_global, fake_tmux):
    fake_global.trading.paper_worker_enabled = True
    cmd_up(fake_global)
    assert fake_tmux.new_window_calls["trading"][-2:] == ["run", "trading"]


def test_watchdog_recovers_missing_enabled_trading(fake_global, fake_tmux):
    fake_global.trading.paper_worker_enabled = True
    fake_tmux.missing.add("trading")
    _check_windows(fake_global, fake_tmux)
    remedy.assert_called_once_with(fake_global, "up")
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_worker_lifecycle.py tests/test_config.py tests/test_status.py tests/test_watchdog.py`

Expected: failures because `GlobalConfig.trading` / `run trading` / `trading` status window do not exist.

- [ ] **Step 3: Implement minimal config/lifecycle wiring**

Add to `config.py`:

```python
@dataclass
class TradingConfig:
    paper_worker_enabled: bool = False
    interval_s: int = 60
    paper_capital_jpy: int = 10000
```

Load with `_filtered(TradingConfig, data.get("trading", {}), "trading")` and validate `interval_s >= 10`, `paper_capital_jpy >= 1000`. Add `trading: TradingConfig` to `GlobalConfig`.

Add `trading` to `run` component choices and dispatch `_run_trading(g)` through `run_callable_loop("trading", g, fn)`. In `cmd_up`, create `trading` window only when enabled. Add `trading` to `STATUS_WINDOWS`. In watchdog `_check_windows`, include missing enabled `trading` in the existing `up` remedy list.

Add explicit disabled tables to both global TOML profiles:

```toml
[trading]
paper_worker_enabled = false
interval_s = 60
paper_capital_jpy = 10000
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_trading_worker_lifecycle.py tests/test_config.py tests/test_status.py tests/test_watchdog.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/docich/config.py config/docich.toml config/docich.soren-live.toml src/docich/cli.py src/docich/status.py src/docich/watchdog.py tests/test_trading_worker_lifecycle.py tests/test_config.py tests/test_status.py tests/test_watchdog.py
git commit -m "feat: add supervised crypto paper worker lifecycle"
```

### Task 2: Bounded public event journal and worker-summary schema

**Files:**
- Create: `src/docich/trading/events.py`
- Modify: `src/docich/trading/status.py`
- Modify: `src/docich/trading/cli.py`
- Create: `tests/test_trading_events.py`
- Modify: `tests/test_trading_paper.py`, `tests/test_trading_cli.py`

**Interfaces:**
- Produces: `append_public_event(path: Path, event: Mapping[str, object], *, max_events: int = 500) -> bool`.
- Produces: `build_fill_event(fill: PaperFill) -> dict[str, object]`.
- Produces: `build_settlement_event(item: RecordedMultiLegSettlement) -> dict[str, object]`.
- Extends: `build_public_status(..., worker_summary: Mapping[str, object] | None = None)`.

- [ ] **Step 1: Write failing event/status tests**

```python
def test_event_journal_is_private_bounded_and_idempotent(tmp_path):
    path = tmp_path / "run" / "trading" / "events.jsonl"
    event = {"schema_version": 1, "event_id": "fill:1", "event_type": "paper_fill", "occurred_at": 1.0, "symbol": "BTC/JPY"}
    assert append_public_event(path, event, max_events=2) is True
    assert append_public_event(path, event, max_events=2) is False
    append_public_event(path, {**event, "event_id": "fill:2"}, max_events=2)
    append_public_event(path, {**event, "event_id": "fill:3"}, max_events=2)
    assert [json.loads(x)["event_id"] for x in path.read_text().splitlines()] == ["fill:2", "fill:3"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_worker_summary_is_allowlisted():
    payload = build_public_status(..., worker_summary={"cycle_index": 2, "api_key": "nope"})
    assert payload["worker_summary"] == {"cycle_index": 2}
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_events.py tests/test_trading_paper.py tests/test_trading_cli.py`

Expected: import/signature failures for event journal and worker summary.

- [ ] **Step 3: Implement event journal and allowlists**

`events.py` must validate non-empty `event_id`, allowed `event_type in {"paper_fill", "multileg_settlement"}`, finite `occurred_at`, and only builder-produced public fields. Rewrite atomically using a temporary file, keep newest `max_events`, chmod directory 0700 and file 0600. Corrupt existing JSONL raises a stable `PublicEventError` rather than overwriting it.

`status.py` adds `_WORKER_SUMMARY_KEYS = {"cycle_index", "last_success_at", "next_cycle_at", "frame_error_count", "arbitrage_candidate_count", "new_fill_count", "new_settlement_count", "error_codes"}` and returns only these keys. `trading/cli.py` sanitizes the nested field in `docich trading status`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_trading_events.py tests/test_trading_paper.py tests/test_trading_cli.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/docich/trading/events.py src/docich/trading/status.py src/docich/trading/cli.py tests/test_trading_events.py tests/test_trading_paper.py tests/test_trading_cli.py
git commit -m "feat: add bounded paper trading event journal"
```

### Task 3: Share settlement identity and implement one worker cycle

**Files:**
- Modify: `src/docich/trading/settlement.py`
- Modify: `src/docich/trading/cli.py`
- Create: `src/docich/trading/worker.py`
- Create: `tests/test_trading_worker.py`
- Modify: `tests/test_trading_settlement_history_cli.py`

**Interfaces:**
- Produces: `settlement_observation_id(route, settlement, depth_books, circuit_statuses, markets) -> str` in `settlement.py`.
- Produces: `run_worker_cycle(g: GlobalConfig, *, gateway: BitbankPublicGateway, cycle_index: int, now: float, last_success_at: float | None = None) -> WorkerCycleResult`.
- Consumes: existing `scan_opportunities`, `scan_relative_value_opportunities`, `select_diversified_opportunities`, `allocate_opportunities`, `PaperLedger`, `PaperBroker`, `build_public_status`, `append_public_event`.

- [ ] **Step 1: Write failing worker-cycle tests**

```python
def test_cycle_isolates_bad_frame_and_reuses_thirty_percent_allocator(tmp_path):
    g = global_with_trading(tmp_path, capital=10000)
    gateway = FakeGateway(one_bad_symbol=True, momentum_symbol="BTC/JPY")
    result = run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW)
    assert result.frame_error_count == 1
    ledger = PaperLedger(g.state_dir / "trading" / "paper.sqlite3")
    assert ledger.deployed_reference() <= Decimal("3000")
    assert result.new_fill_count >= 1


def test_replaying_same_frames_does_not_duplicate_fill_event(tmp_path):
    g = global_with_trading(tmp_path)
    gateway = FakeGateway(momentum_symbol="BTC/JPY")
    run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW)
    run_worker_cycle(g, gateway=gateway, cycle_index=2, now=NOW)
    events = read_events(g.state_dir / "trading" / "events.jsonl")
    assert len([e for e in events if e["event_type"] == "paper_fill"]) == 1
```

- [ ] **Step 2: Run worker tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_worker.py tests/test_trading_settlement_history_cli.py`

Expected: missing `trading.worker` / shared settlement ID helper.

- [ ] **Step 3: Extract deterministic settlement identity**

Move the existing CLI `_settlement_observation_id` algorithm into `settlement.py` as `settlement_observation_id(...)`, preserving `SETTLEMENT_MODEL_VERSION = "multileg-v1"`, market constraints, normalized depth, circuit state, route, start asset and start amount in the hash. Update CLI to import/use the shared helper; existing settlement-history identity tests must remain green.

- [ ] **Step 4: Implement strategy portion of `run_worker_cycle`**

Use constants:

```python
TIMEFRAME = "5m"
HISTORY_LIMIT = 24
BOOK_LIMIT = 20
ARB_MIN_EDGE_BPS = Decimal("10")
ARB_PROBE_JPY = (Decimal("1000"), Decimal("3000"), Decimal("10000"))
```

Open `PaperLedger(g.state_dir / "trading" / "paper.sqlite3")`. Discover markets. Fetch frames one symbol at a time so a failing symbol increments `frame_error_count` and is excluded. Generate/select current opportunities from validated frames. Compute `capital = Decimal(str(g.trading.paper_capital_jpy))`, `deployed = ledger.deployed_reference()`, `available_jpy = max(0, capital - deployed)`, and allocate using JPY reference/funding only. For each decision, call `PaperBroker.fill`; always offer the resulting fill event to the idempotent event journal.

- [ ] **Step 5: Add final status output for strategy cycle**

Write `status.json` with `worker_state="paper_worker_idle"` if no error codes or `paper_worker_degraded` otherwise; include ledger positions/fills/deployment and worker summary counts. Never include raw exception text in status/events.

- [ ] **Step 6: Run worker tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_trading_worker.py tests/test_trading_settlement_history_cli.py tests/test_trading_strategy_cli.py tests/test_trading_risk.py`

Expected: all pass and existing 30% behavior unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/docich/trading/settlement.py src/docich/trading/cli.py src/docich/trading/worker.py tests/test_trading_worker.py tests/test_trading_settlement_history_cli.py
git commit -m "feat: add crypto paper worker cycle"
```

### Task 4: Add arbitrage settlement phase and continuous loop

**Files:**
- Modify: `src/docich/trading/worker.py`
- Modify: `src/docich/cli.py`
- Modify: `tests/test_trading_worker.py`
- Modify: `tests/test_trading_worker_lifecycle.py`

**Interfaces:**
- Extends: `run_worker_cycle(...)` with public triangle/depth/circuit settlement recording.
- Produces: `run_paper_worker(g: GlobalConfig, *, gateway_factory=BitbankPublicGateway, sleep_fn=time.sleep, now_fn=time.time) -> None`.

- [ ] **Step 1: Write failing arbitrage/loop tests**

```python
def test_triangle_cycle_records_settlement_and_one_event(tmp_path):
    g = global_with_trading(tmp_path)
    gateway = FakeTriangleGateway()
    first = run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW)
    second = run_worker_cycle(g, gateway=gateway, cycle_index=2, now=NOW)
    assert first.new_settlement_count >= 1
    assert second.new_settlement_count == 0
    assert len(PaperLedger(...).recent_multileg_settlements(limit=20)) >= 1


def test_loop_catches_cycle_error_and_sleeps_without_exiting(tmp_path):
    calls = []
    run_paper_worker(g, gateway_factory=FlakyFactory, sleep_fn=lambda s: calls.append(s), now_fn=FakeClock(...), max_cycles=2)
    assert calls == [g.trading.interval_s]
    assert json.loads(status_path.read_text())["worker_state"] == "paper_worker_degraded"
```

Use an internal test-only `max_cycles: int | None = None` argument so loop behavior is deterministic without threads; production passes `None`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m pytest -q tests/test_trading_worker.py tests/test_trading_worker_lifecycle.py`

Expected: missing arbitrage phase / loop entrypoint.

- [ ] **Step 3: Implement arbitrage phase**

From discovered markets call `find_triangle_symbols`. If empty, perform no circuit/depth API call. Otherwise fetch complete public circuit/depth sets, create `TopOfBook`s, call `scan_triangular_arbitrage`, and for each route containing JPY run all three `ARB_PROBE_JPY` amounts through `simulate_multileg_settlement`. Compute `settlement_observation_id`, persist with `PaperLedger.record_multileg_settlement`, and offer a `multileg_settlement` event to the idempotent journal. Any exception in this phase appends stable `arbitrage_data_error` and records no partial route settlement for that failing fetch.

- [ ] **Step 4: Implement continuous loop**

`run_paper_worker` keeps `cycle_index` and `last_success_at`, attempts to create/reuse a public gateway, executes cycles, catches unexpected per-cycle exceptions into a degraded public status, prints an internal warning, and sleeps `max(0, interval_s - elapsed)`. It must not return in production. `_run_trading(g)` in `cli.py` calls it under `run_callable_loop("trading", g, fn)`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python3 -m pytest -q tests/test_trading_worker.py tests/test_trading_worker_lifecycle.py`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/docich/trading/worker.py src/docich/cli.py tests/test_trading_worker.py tests/test_trading_worker_lifecycle.py
git commit -m "feat: run continuous crypto paper worker"
```

### Task 5: Contract/docs verification and stacked PR

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:** none; freezes operator contract and CI scope.

- [ ] **Step 1: Add worker/event tests to Crypto trading CI contract**

Append `tests/test_trading_events.py tests/test_trading_worker.py tests/test_trading_worker_lifecycle.py` to the existing Crypto trading contracts command.

- [ ] **Step 2: Document disabled-by-default worker**

README must show:

```toml
[trading]
paper_worker_enabled = false
interval_s = 60
paper_capital_jpy = 10000
```

and explain `docich up` starts a `trading` window only when enabled, status/event/ledger paths, 500-event retention, game-switch independence, and current buy-only/30%-cap limitation.

- [ ] **Step 3: Run complete verification**

Run:

```bash
python3 -m pytest -q tests/test_trading_*.py
python3 -m compileall -q src tests
git diff --check
python3 -m pytest -q tests
```

Expected: zero failures. Also run a production-source scan proving no new live/private/order/API-key path and a credential-free public bitbank smoke with worker disabled by default.

- [ ] **Step 4: Commit docs/CI**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "docs: document continuous crypto paper worker"
```

- [ ] **Step 5: Rebase/retarget stack if parents moved**

Fetch `main`, #147 branch, and #148 branch. If #147/#148 merged, rebase this branch onto latest main. If #148 remains open, keep this PR stacked on `codex/crypto-settlement-ledger-20260908`. Re-run the complete verification after any rebase.

- [ ] **Step 6: Push a Draft PR and verify GitHub**

Create a Draft PR titled `feat: add continuous crypto paper worker`, explicitly state its parent/base and paper-only boundary, then verify latest-head GitHub CI, VM operations CI, mergeability, and unresolved review threads. Do not merge or deploy in this slice.
