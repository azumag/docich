# Crypto Trading Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a safe paper-only multi-market crypto trading foundation to docich, with dynamic bitbank market discovery, 30% capital allocation, idempotent paper fills, and safe CLI status.

**Architecture:** Trading lives under `src/docich/trading/` and is not a normal game adapter. Domain/risk/ledger code remains stdlib-only; CCXT is isolated behind an optional read-only market gateway. The first slice never calls private/order endpoints and exposes only paper state to the rest of docich.

**Tech Stack:** Python 3.11+, stdlib dataclasses/sqlite3/argparse/json; pytest/unittest; optional `ccxt==4.5.78` for public bitbank discovery.

**Spec:** `docs/superpowers/specs/2026-09-08-crypto-trading-foundation-design.md`

## Global Constraints

- Default and only supported execution mode in this slice is `paper`.
- No API key, secret, private endpoint, order endpoint, leverage, short sale, or production VM deployment.
- `max_opportunity_fraction` and `max_total_deployed_fraction` both default to `0.30` and cannot exceed `1.0`.
- Markets and opportunities must not be hard-coded to BTC/JPY or JPY quote.
- An opportunity whose quote asset cannot be valued in the configured reference currency is skipped, never guessed.
- Runtime files live under `run/trading/` and are not committed.

---
### Task 1: Trading domain and capital allocator

**Files:**
- Create: `src/docich/trading/__init__.py`
- Create: `src/docich/trading/models.py`
- Create: `src/docich/trading/risk.py`
- Test: `tests/test_trading_risk.py`

**Interfaces:**
- Produces `MarketInfo`, `Opportunity`, `AllocationDecision`, and `CapitalPolicy`.
- Produces `allocate_opportunities(opportunities, markets, prices, quote_to_reference, capital_reference, deployed_reference, policy)`.

- [x] **Step 1: Write failing allocator tests** covering 30% per-opportunity, 30% total, score ordering, non-JPY quote valuation, minimum order rejection, expired opportunity rejection, and unknown quote valuation rejection.
- [x] **Step 2: Run `python3 -m pytest -q tests/test_trading_risk.py`** and verify failures are caused by missing trading modules.
- [x] **Step 3: Implement immutable dataclasses with finite-number/range validation and a deterministic allocator**. Allocation converts quote notional to the configured reference currency, rounds down by market amount precision, and never rounds a too-small order upward.
- [x] **Step 4: Run `python3 -m pytest -q tests/test_trading_risk.py`** and require all tests to pass.
- [x] **Step 5: Commit domain/risk changes** with `feat: add crypto trading allocation core`.

### Task 2: Idempotent paper ledger and public status

**Files:**
- Create: `src/docich/trading/ledger.py`
- Create: `src/docich/trading/paper.py`
- Create: `src/docich/trading/status.py`
- Test: `tests/test_trading_paper.py`

**Interfaces:**
- Produces `PaperLedger(path)`, `PaperBroker(ledger)`, and `write_public_status(path, snapshot)`.
- `PaperBroker.fill(decision, price, timestamp)` returns one `PaperFill` or the existing fill for the same opportunity ID.
- [x] **Step 1: Write failing paper tests** covering SQLite creation, private file permissions, duplicate-opportunity idempotency, position aggregation, recent fills, and safe public status fields.
- [x] **Step 2: Run `python3 -m pytest -q tests/test_trading_paper.py`** and verify the expected RED failures.
- [x] **Step 3: Implement SQLite schema and deterministic paper fills**. Store only order/fill/account facts needed for replay; never store credentials or raw exchange payloads.
- [x] **Step 4: Implement atomic public status writes** using a temporary file + `os.replace`, with an allowlisted schema and 0600 permissions.
- [x] **Step 5: Run `python3 -m pytest -q tests/test_trading_paper.py`** and require all tests to pass.
- [x] **Step 6: Commit paper ledger/status changes** with `feat: add paper crypto ledger`.

### Task 3: Optional read-only CCXT bitbank market discovery

**Files:**
- Create: `src/docich/trading/exchanges/__init__.py`
- Create: `src/docich/trading/exchanges/bitbank_ccxt.py`
- Create: `requirements-trading.txt`
- Test: `tests/test_trading_bitbank.py`

**Interfaces:**
- Produces `CCXTUnavailableError` and `BitbankPublicGateway(exchange=None)`.
- Produces `BitbankPublicGateway.discover_markets() -> dict[str, MarketInfo]`.

- [x] **Step 1: Write failing discovery tests** using a fake CCXT exchange object. Cover spot filtering, `active=false`, malformed markets, bitbank disabled/order-stop metadata, and absence of the optional ccxt package.
- [x] **Step 2: Run `python3 -m pytest -q tests/test_trading_bitbank.py`** and verify the expected RED failures.
- [x] **Step 3: Implement the gateway** so the default constructor imports CCXT lazily, creates `ccxt.bitbank({'enableRateLimit': True})`, and exposes only `load_markets()`-based public discovery.
- [x] **Step 4: Add `ccxt==4.5.78` to `requirements-trading.txt`** without adding it to the base/test requirements.
- [x] **Step 5: Run `python3 -m pytest -q tests/test_trading_bitbank.py`** and require all tests to pass.
- [x] **Step 6: Commit exchange discovery changes** with `feat: add bitbank market discovery`.
### Task 4: docich trading CLI and deterministic paper cycle

**Files:**
- Create: `src/docich/trading/cli.py`
- Modify: `src/docich/cli.py`
- Modify: `README.md`
- Test: `tests/test_trading_cli.py`

**Interfaces:**
- Adds `docich trading status`, `docich trading discover`, and `docich trading paper-cycle --snapshot PATH`.
- Snapshot schema contains `capital_reference`, `deployed_reference`, `quote_to_reference`, `markets`, `prices`, and `opportunities`; it contains no credentials.

- [x] **Step 1: Write failing CLI tests** for absent status, deterministic JSON status, discover without CCXT, paper-cycle allocation/fill, duplicate replay, malformed snapshot rejection, and rejection of `mode=live`.
- [x] **Step 2: Run `python3 -m pytest -q tests/test_trading_cli.py`** and verify expected RED failures.
- [x] **Step 3: Wire the `trading` argparse subtree** to `src/docich/trading/cli.py`; keep existing top-level commands behavior unchanged.
- [x] **Step 4: Implement paper-cycle snapshot parsing and atomic status generation**. Use allocator + paper broker, reject unknown fields needed for live execution, and print only safe JSON.
- [x] **Step 5: Document optional setup and safe example commands** in README; explicitly state that live orders are unavailable in this slice.
- [x] **Step 6: Run targeted trading tests**: `python3 -m pytest -q tests/test_trading_risk.py tests/test_trading_paper.py tests/test_trading_bitbank.py tests/test_trading_cli.py`.
- [x] **Step 7: Run regression checks**: `python3 -m compileall -q src tests`, `git diff --check`, and `python3 -m pytest -q` after submodules are initialized.
- [x] **Step 8: Commit CLI/docs changes** with `feat: add paper crypto trading CLI`.

### Task 5: Review and PR

**Files:**
- Review all changes in this branch.

- [ ] **Step 1: Inspect `git diff origin/main...HEAD`** for secrets, live-order code, accidental submodule movement, and unrelated changes.
- [ ] **Step 2: Run the targeted and full verification commands again immediately before reporting completion.**
- [ ] **Step 3: Push `codex/crypto-trading-foundation-20260908` and open a PR** linking the Bitcoin corner in #113 and marking the implementation paper-only.
- [ ] **Step 4: Do not merge or deploy this slice unless separately requested after review.**
