# Crypto Paper Worker Design

**Date:** 2026-09-08
**Status:** Approved by the user's 2026-09-08 "すすめよ" continuation of the previously proposed continuous paper-worker slice.
**Parent work:** #147 constrained multi-leg settlement, #148 persistent settlement ledger.

## Goal

Add a continuously supervised, paper-only crypto trading worker to `azumag/docich` that stays alive across game switches, periodically scans bitbank public markets, evaluates existing single-market and cross-market strategies, records deterministic paper results, and publishes a secret-free status/event surface for later stream narration.

## Non-goals

- No bitbank API key, private endpoint, balance endpoint, order endpoint, leverage, withdrawal, or live execution.
- No new profitable-strategy claim and no LLM-directed order authority.
- No automatic exit strategy for the existing buy-only single-market paper positions in this slice.
- No VOICEVOX/overlay narration consumer in this slice; only public-safe status/event artifacts are produced.
- No websocket/order-book streaming optimization; this first worker uses periodic public REST reads.

## Lifecycle architecture

The worker is a docich-owned tmux component named `trading`, not a game/agent child and not part of the watchdog process. `docich up` starts the `trading` window only when `[trading].paper_worker_enabled=true`; `docich run trading` runs it under `supervise.run_callable_loop`. `switch`/`rotate` do not stop it. `docich down` kills the shared docich tmux session, so it stops with the rest of docich. If watchdog recovery is enabled, a missing enabled trading window is recovered via the existing idempotent `docich up` remedy.

The global status window map includes `trading` so operators can distinguish enabled-but-missing from healthy tmux state.

## Configuration

Add a global `TradingConfig` with exactly these first-slice fields:

- `paper_worker_enabled: bool = false`
- `interval_s: int = 60`, valid range `>= 10`
- `paper_capital_jpy: int = 10000`, valid range `>= 1000`

The default profile stays disabled. The worker therefore cannot start merely because code is deployed. Existing 30% per-opportunity / 30% total-deployed `CapitalPolicy` remains authoritative.

Worker market-data constants remain code constants in this slice: 5-minute OHLCV, 24 bars, depth limit 20, arbitrage minimum top-of-book edge 10 bps, and JPY probe sizes 1,000 / 3,000 / 10,000. They can become config only after evidence shows a need.

## One paper-worker cycle

1. Mark public status `paper_worker_running` with a cycle start timestamp and prior ledger summary.
2. Discover order-eligible public bitbank spot markets through `BitbankPublicGateway`.
3. Fetch validated 5-minute/24-bar OHLCV per market. A malformed or unavailable market is excluded from this cycle and counted as a stable `frame_fetch_error`; one bad market does not make valid unrelated markets tradable on unvalidated data.
4. Run the existing `momentum-v1`, `mean-reversion-v1`, and `relative-value-v1` generators on validated frames, then the existing diversification filter.
5. Allocate selected buy opportunities through the existing `CapitalPolicy`. Funding is synthetic paper JPY only: reference rate JPY=1, available JPY is configured paper capital minus existing paper deployment. Non-JPY quote opportunities fail closed through existing funding/valuation gates.
6. Persist only newly created synthetic single-market paper fills. Because exits are not implemented yet, the existing 30% total-deployed cap eventually prevents more buy fills; scanning continues.
7. If the eligible market graph contains a real triangle, fetch public circuit status and depth, scan fee-aware routes, simulate the configured JPY probe sizes with the `multileg-v1` constrained settlement model, and persist deterministic observations through the #148 ledger. If any required arbitrage public data fails, skip the arbitrage phase for that cycle; do not fabricate partial route data.
8. Publish new paper-fill and new multi-leg-settlement events to the bounded public event journal.
9. Write final public status as `paper_worker_idle` or `paper_worker_degraded`, with candidate/selection/error/new-event counts and ledger-derived paper positions/deployment.

## Error and freshness rules

All execution remains fail-closed. Public data exceptions never trigger synthetic fills based on stale cached values. Frame failures are isolated per symbol. Arbitrage requires a complete fresh route and existing circuit/depth gates. Unexpected cycle exceptions are caught inside the worker loop, produce a stable degraded status/error code, and the worker sleeps until the next interval instead of exiting the tmux component. If the worker itself escapes unexpectedly, `run_callable_loop` supplies the existing exponential restart backoff.

Public artifacts never include raw exception payloads, request headers, URLs containing credentials, environment variables, or API keys. Logs may contain internal exception text because this worker uses only public unauthenticated endpoints, but public status/event JSON uses stable error codes only.

## Public status and events

Extend `status.json` additively with an allowlisted `worker_summary` containing only:

- `cycle_index`
- `last_success_at`
- `next_cycle_at`
- `frame_error_count`
- `arbitrage_candidate_count`
- `new_fill_count`
- `new_settlement_count`
- `error_codes`

Keep existing fills, positions, signal summary, and capital fields. `docich trading status` must continue stripping unknown fields and must explicitly allow the new worker summary keys.

Create `run/trading/events.jsonl` only when a new public event exists. Event types in this slice are `paper_fill` and `multileg_settlement`. Events contain allowlisted identifiers, symbols/route, paper amounts/edge/completion/failure code, and timestamps only. The journal is mode 0600 in a 0700 directory and retains at most the newest 500 events by atomic rewrite, bounding disk growth.

## Idempotency and persistence

Single-market fill idempotency remains keyed by opportunity ID. Multi-leg observation idempotency remains the #148 deterministic `multileg-v1:<hash>` identity. Event IDs derive from the durable fill/settlement IDs, so a replay does not emit another public event. Worker restart reads the ledger and journal instead of assuming in-memory state is authoritative.

## Observability

- tmux window: `trading`
- supervisor log: `run/logs/trading.log`
- private paper ledger: `run/trading/paper.sqlite3`
- public-safe current status: `run/trading/status.json`
- bounded public-safe event journal: `run/trading/events.jsonl`
- `docich status` exposes whether the trading tmux window exists.
- `docich trading settlement-history` continues to expose allowlisted multi-leg history.

## Security boundary

The worker constructs only `BitbankPublicGateway`. There is no broker interface with order methods in this slice. Production-source scans must continue to find no `create_order`, `cancel_order`, `fetch_balance`, `fetch_orders`, private endpoint, API-key, secret, or authorization path added by this feature. Enabling the worker changes only public-data polling and paper-state writes.

## Testing requirements

TDD must cover configuration validation, `up`/`run` lifecycle wiring, watchdog recovery, status-window reporting, per-symbol frame failure isolation, 30% allocator reuse, no duplicate fill/settlement events on replay, bounded event retention and file permissions, degraded status on public-data errors, no DB/event creation when there are no new records, and continued absence of live/private exchange methods. Full docich regression and GitHub CI are required before the stacked Draft PR is considered ready for review.
