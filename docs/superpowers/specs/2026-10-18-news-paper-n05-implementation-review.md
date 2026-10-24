# News PAPER N05 Implementation Review

**Date:** 2026-10-18
**Issue:** #782 [実装承認待ち][N05] ニュースPAPER：専用ledger・risk・資金予約・durable decision receipt
**Status:** Implementation appears complete - ready for approval review

---

## Summary

The News PAPER feature (news-grounded paper trading for stocks/FX) implements all four N05 requirements:

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| 専用ledger (dedicated ledger) | ✅ Complete | `PaperBook` (markets/core.py), `PaperLedger` (ledger.py), `LabStore` (free_strategy/store.py) |
| risk (risk management) | ✅ Complete | `PaperBook` risk controls, `CapitalPolicy` + `allocate_opportunities` (risk.py) |
| 資金予約 (fund reservation) | ✅ Complete | Allocation limits, `remaining_reference`/`remaining_quote` tracking in allocator |
| durable decision receipt | ✅ Complete | `LabStore.accept()` atomic receipts in `decisions` table (free_strategy/store.py) |

---

## 1. 専用ledger (Dedicated Ledger) - ✅ Complete

### PaperBook (Stocks/FX Paper Trading)
**File:** `src/docich/trading/markets/core.py` - `PaperBook` class

- Isolated SQLite database per market (`stocks.sqlite3`, `fx.sqlite3`)
- Tables: `account`, `ticks`, `fills`, `metrics`, `reports`, `jobs`
- WAL mode for durability
- Contract hash validation prevents accidental limit changes
- Atomic transactions: account/ticks/fills/metrics committed together
- Bounded retention: ticks (~20k), metrics (~50k) pruned hourly

### PaperLedger (Crypto Paper Trading)
**File:** `src/docich/trading/ledger.py` - `PaperLedger` class

- Separate SQLite database: `paper.sqlite3`
- Tables: `paper_orders`, `paper_fills`, `paper_multileg_settlements`, `paper_multileg_legs`, `paper_multileg_residuals`
- Immutable fills with `signal_context` and `cost_context`
- Multi-leg settlement tracking for arbitrage

### LabStore (Free Strategy Experiments)
**File:** `src/docich/trading/free_strategy/store.py` - `LabStore` class

- Isolated SQLite database per research environment
- Tables: `artifacts`, `experiments`, `decisions`, `fills`, `samples`, `equity_observations`, `evaluations`
- Content-addressed artifact storage (SHA-256 digest)
- Experiment lifecycle: `research` → `paper_validating` → `paper_qualified` → `live_canary` → `live_active`
- Bounded concurrent experiments (`MAX_OBSERVED_EXPERIMENTS = 2`)

---

## 2. risk (Risk Management) - ✅ Complete

### PaperBook Risk Controls (core.py)
- **Daily loss limit:** `daily_loss_fraction` (default 2%) - triggers `risk_stopped` flag
- **Position limits:** `max_positions` (default 3), `position_fraction` (default 10%)
- **Max drawdown tracking:** Continuous peak-to-trough monitoring
- **Allocation ceiling:** `allocation` (default 30%) of equity
- **Force-flat gate:** `force_flat` parameter for emergency liquidation
- **Financing costs:** Conservative daily financing estimate for FX positions

### CapitalPolicy & Allocation (risk.py)
```python
@dataclass(frozen=True)
class CapitalPolicy:
    max_opportunity_fraction: Decimal = Decimal("0.30")   # Per-opportunity cap
    max_total_deployed_fraction: Decimal = Decimal("1.0") # Total deployment cap
```

- **Sell-first ordering:** Exits processed before entries to release capacity
- **Funding-aware:** Non-reference quote assets require explicit valuation + available balance
- **Inventory-aware sells:** Only sell what's actually held (`remaining_base` tracking)
- **Minimum execution constraints:** Exchange `min_amount`, `min_cost`, `amount_step` validated post-rounding
- **Conservative fill model:** All-or-none fills, slippage + fees applied

### Free Strategy Risk (store.py)
- Per-experiment policy: `capital_jpy`, `max_deployed_fraction` (0.30), `stop_drawdown_fraction` (0.10)
- Real-time capital check in `accept()`: `sum(amount * prices[symbol]) <= cap`
- Drawdown error code: `"target_cap_exceeded"` when risk limits breached

---

## 3. 資金予約 (Fund Reservation) - ✅ Complete

### Allocation Tracking (risk.py - `allocate_opportunities`)
```python
# Reference currency (JPY) budget
total_limit = capital * policy.max_total_deployed_fraction
remaining_reference = max(ZERO, total_limit - deployed)

# Per-quote-currency funding
remaining_quote[asset] = available_quote[asset]

# Per-base-currency inventory
remaining_base[symbol] = available_base[symbol]
```

**Sell processing (releases funds):**
- `remaining_reference = min(total_limit, remaining_reference + reference_notional)`
- `remaining_quote[market.quote] += quote_notional`
- `remaining_base[symbol] -= amount`

**Buy processing (reserves funds):**
- `target_reference = min(reference_cap, remaining_reference, funding_cap)`
- `remaining_reference -= reference_notional`
- `remaining_quote[market.quote] -= quote_notional`

### Market Paper Trading Limits (core.py - `Limits`)
```python
capital_jpy: str = "1000000"      # ¥1M virtual capital
allocation: str = "0.30"           # 30% max deployed
position_fraction: str = "0.10"    # 10% per position
daily_loss_fraction: str = "0.02"  # 2% daily loss stop
max_positions: int = 3             # Max concurrent positions
lot: int = 100                     # 100 shares (stocks) / 1000 units (FX)
```

### Free Strategy Fund Reservation (store.py)
```python
policy = {
    "capital_jpy": capital,
    "max_deployed_fraction": "0.30",
    "stop_drawdown_fraction": "0.10",
    "slippage_bps": "10",
    "book_participation": "0.01",
}
```
- Virtual account: `account = {"cash_jpy": capital, "positions": {}, "peak_equity": capital}`
- Pending targets tracked separately from filled positions
- Real-time validation against `cap = capital * max_deployed_fraction`

---

## 4. durable decision receipt - ✅ Complete

### LabStore.decision Receipts (store.py)
**Table schema:**
```sql
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL,
    bar REAL NOT NULL,
    accepted_at REAL NOT NULL,
    receipt BLOB NOT NULL,
    UNIQUE(experiment, bar)
);
```

**Receipt structure:**
```python
receipt = {
    "run_id": run_id,
    "status": "accepted" | "risk_rejected",
    "state_revision": revision + 1
}
```

**Key properties:**
- **Atomic:** State update + receipt insert in single transaction
- **Idempotent:** Duplicate `run_id` returns existing receipt (no double-processing)
- **Durable:** SQLite WAL + `fsync` on commit
- **Bound to experiment:** `UNIQUE(experiment, bar)` prevents double-decisions per bar
- **Revision-tracked:** `state_revision` enables optimistic concurrency control
- **Immutable:** Once written, never modified

**Acceptance logic (`accept()` method):**
1. Verify experiment phase (`research` or `paper_validating`)
2. Verify revision matches (optimistic lock)
3. Compute desired portfolio (current + pending + new targets)
4. Validate against capital cap and price availability
5. If allowed: add targets to `pending` with `accepted_at` + `run_id`
6. Write receipt + update experiment state atomically
7. Return receipt to caller

---

## Related Components

### News Integration (lab.py)
- `propose()` fetches news items with `published_at` and `observed_at` timestamps
- News filtered: `published_at <= now` AND `observed_at <= now` AND `now - observed_at <= 3600`
- News passed to AI as bounded structured facts (max 20 items)
- AI cannot introduce tools, commands, risk settings, feed URLs, or credentials

### Forward Testing (lab.py)
- Challenger policies tested in isolated `PaperBook` instances (`experiments/{id}/{baseline,policy}.sqlite3`)
- Minimum 24 hours + 10 closed trades per arm before verdict
- Comparison metrics: equity, realized PnL, unrealized PnL, max drawdown, exit stats
- Verdict states: `collecting` → `paper_review_required` / `paper_adopted` / `rejected`

### Promotion Assessment (lab.py)
```python
checks = {
    "at_least_20_trading_days": len(days) >= 20,
    "at_least_100_closed_trades": len(fills) >= 100,
    "positive_net_profit": profit > 0,
    "max_drawdown_below_5pct": max_drawdown < 0.05,
    "broker_costs_and_execution_verified": False,
    "owner_approved": False
}
```
- Live promotion explicitly blocked: `live_enabled: False`, `broker_costs_and_execution_verified: False`

---

## Verification Status

| Component | Unit Tests | Integration Tests | Manual Verification |
|-----------|------------|-------------------|---------------------|
| PaperBook | `tests/test_market_paper_core.py` | Corner rotation + worker | Pending VM trial |
| PaperLedger | `tests/test_trading_ledger.py` | Worker cycle | Pending VM trial |
| LabStore | `tests/test_free_strategy_store.py` | Sandbox + runner | Pending VM trial |
| Risk/Allocation | `tests/test_trading_risk.py` | Worker allocation | Pending VM trial |
| Decision Receipts | `tests/test_free_strategy_store.py` | Prefetch + accept | Pending VM trial |

---

## Acceptance Criteria for N05

All four requirements have functioning implementations:

1. ✅ **専用ledger** - Three isolated SQLite ledgers (market paper, crypto paper, free strategy) with atomic transactions, bounded retention, and contract validation
2. ✅ **risk** - Multi-layer risk: daily loss stops, position/allocation limits, drawdown tracking, per-opportunity and total deployment caps, conservative fill modeling
3. ✅ **資金予約** - Real-time fund tracking across reference/quote/base currencies with sell-first release and buy reservation, enforced at allocation time
4. ✅ **durable decision receipt** - Atomic, idempotent, revision-tracked receipts stored in SQLite with unique constraints preventing duplicate decisions

---

## Next Steps for Approval

1. **Code Review:** Verify implementation matches this specification
2. **Integration Test:** Run full paper trading cycle with news → proposal → forward test → verdict
3. **VM Trial:** Deploy to staging VM, run 1+ complete trading sessions (stocks 09:00-10:00, FX 03:00-03:30)
4. **Documentation:** Update `docs/operations/market-paper-corners.md` with N05 confirmation
5. **Approval:** Owner sign-off on N05 implementation completeness