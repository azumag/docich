"""The per-cycle LIVE decisions: signals -> inputs -> books -> sizing -> recorded decisions.

``build_decisions`` reads the ledger inputs, scans the fresh frames for entry/exit
candidates, fetches one order book per involved symbol, builds the sizing markets, runs
the pure sizer and records one ``live_decision`` event per plan and per skip.  It records
every decision; it plans NOTHING and sends NOTHING in this task (a later task adds the
trade-mode order placement after ``build_decisions``).  In particular nothing here calls
``OrderExecutor.plan``/``submit``/``cancel`` or ``LiveLedger.create_intent``.

The order books are freshness-filtered here (``BOOK_MAX_AGE_S``) because the sizer trusts
its caller for that.  A cycle that has already spent ``CYCLE_BUDGET_S`` stops fetching
books for entry-only symbols, and buys planned after that mark are dropped: a sell must
never be starved by the budget.

Every decision is written through ``record_live_event`` with string-only payloads.  A
decision that was recorded once is not recorded again while its ``(action, id, reason)``
key stays in ``state.dedupe``; the runner keeps that dict for at most
``DEDUPE_KEEP_CYCLES`` cycles (pruned once it grows past ``DEDUPE_MAX_ENTRIES``).  Losing
the dedupe dictionary on a restart is accepted: the first cycle after a restart may record
a decision that was already recorded before it.

A failure to record a decision (other than ``LeaseLost``) is swallowed and counted in
``DecisionSet.record_failures``: a decision that could not be written must not stop the
cycle, but the caller must be able to see how many writes were lost.  ``LeaseLost`` always
propagates and is not counted.

This module remains the real-input decision builder.  Shadow mode calls it with injected
simulated inputs, a separate dedupe map and ``mark_residual=False`` from
``shadow.decide_and_act``; it never reads real intents for simulated positions or
decision keys and never marks a real position residual.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from ..liquidity import entry_block
from ..models import SkipDecision
from .exchange import OrderBook
from .inputs import CycleInputs, build_cycle_inputs, sell_attempts_for
from .lease import LeaseLost
from .ledger import LiveLedgerError
from .signals import LiveSignals, scan_live
from .sizing import SizingResult, SymbolMarket, size_orders

if TYPE_CHECKING:  # annotations only: keeps this module independent of the runner
    from .cycle import CycleContext, CycleDeps, CycleState

BOOK_MAX_AGE_S = 15.0        # a book older than this is stale
BOOK_FUTURE_TOLERANCE_S = 10.0  # legal clock skew: the signed-request window is 10 s
CYCLE_BUDGET_S = 40.0        # buys are dropped when the cycle has run longer than this
DEDUPE_KEEP_CYCLES = 200
DEDUPE_MAX_ENTRIES = 5000


def book_age_problem(fetched_at: float, exchange_ts_ms: int) -> str | None:
    """Why a book cannot be used at ``fetched_at``, or None when its age is legal.

    The age is signed: a book stamped in the future is legal up to the 10 s request
    window the exchange signs for, beyond that it is ``book_from_future`` (clock skew).
    """
    age_ms = fetched_at * 1000 - exchange_ts_ms
    if age_ms > BOOK_MAX_AGE_S * 1000:
        return "stale_book"
    if age_ms < -BOOK_FUTURE_TOLERANCE_S * 1000:
        return "book_from_future"
    return None


@dataclass(frozen=True)
class DecisionSet:
    inputs: CycleInputs
    signals: LiveSignals            # AFTER the book/market filters below
    result: SizingResult | None     # None when sizing was not run (nothing to size)
    buys_blocked: bool
    block_causes: tuple[str, ...]   # sorted causes of buys_blocked
    over_budget: bool
    record_failures: int            # record_live_event writes swallowed in this call
    markets: Mapping[str, SymbolMarket]  # the FINAL markets the real sizing call used


def _drop_symbol(signals: LiveSignals, symbol: str,
                 reason: str) -> tuple[LiveSignals, list[SkipDecision]]:
    """Remove every entry/exit of ``symbol`` and return them as skips (side preserved)."""
    dropped: list[SkipDecision] = []
    entries = []
    for opportunity in signals.entries:
        if opportunity.symbol == symbol:
            dropped.append(SkipDecision(opportunity.opportunity_id, opportunity.symbol,
                                        reason, side=opportunity.side))
        else:
            entries.append(opportunity)
    exits = []
    for opportunity in signals.exits:
        if opportunity.symbol == symbol:
            dropped.append(SkipDecision(opportunity.opportunity_id, opportunity.symbol,
                                        reason, side=opportunity.side))
        else:
            exits.append(opportunity)
    filtered = dataclasses.replace(signals, entries=tuple(entries), exits=tuple(exits))
    return filtered, dropped


def _drop_entries(signals: LiveSignals, symbol: str,
                  reason: str) -> tuple[LiveSignals, list[SkipDecision]]:
    """Remove only the BUY entries of ``symbol``; its exits stay untouched."""
    dropped: list[SkipDecision] = []
    entries = []
    for opportunity in signals.entries:
        if opportunity.symbol == symbol:
            dropped.append(SkipDecision(opportunity.opportunity_id, opportunity.symbol,
                                        reason, side=opportunity.side))
        else:
            entries.append(opportunity)
    filtered = dataclasses.replace(signals, entries=tuple(entries))
    return filtered, dropped


def _restrict(signals: LiveSignals, symbol: str) -> LiveSignals:
    """The same signals with everything outside ``symbol`` removed (for one-market dry runs)."""
    return LiveSignals(
        tuple(opportunity for opportunity in signals.entries if opportunity.symbol == symbol),
        tuple(opportunity for opportunity in signals.exits if opportunity.symbol == symbol),
        signals.skipped,
        signals.reason_contexts,
    )


def _payload(plan, mode_text: str, cycle_text: str, blocks_text: str) -> dict:
    return {
        "action": "plan",
        "side": plan.side,
        "symbol": plan.symbol,
        "opportunity_id": plan.opportunity_id,
        "reason": plan.reason_code,
        "decision_key": plan.decision_key,
        "position_id": plan.position_id or "",
        "policy_digest": plan.policy_digest,
        "rule_id": plan.rule_id,
        "amount": format(plan.amount, "f"),
        "price": format(plan.price, "f"),
        "required_quote": format(plan.required_quote, "f"),
        "notes": ",".join(plan.notes),
        "digest": plan.policy_digest[:16],
        "mode": mode_text,
        "cycle": cycle_text,
        "blocks": blocks_text,
    }


def entry_exit_policies(ledger, held, policy_set) -> dict:
    """The entry policy of each held position whose digest is not the active one.

    After a promotion the active policy of a symbol changes while an older position is
    still open.  That position keeps the exit rules it was entered under (Issue #3623
    §2.6-5); buys stay blocked by ``legacy_position_open`` until it is flat.  A digest the
    ledger cannot load is left out, so the sell falls back to the active exit rules: the
    sell still carries the entry digest (the ledger trigger), only the rule set differs.
    """
    policies: dict = {}
    for symbol, position in (held or {}).items():
        digest = getattr(position, "entry_policy_digest", None)
        active = policy_set.policies.get(symbol)
        if not digest or active is None or digest == active.digest:
            continue
        try:
            policies[symbol] = ledger.load_policy(digest)
        except Exception:
            continue
    return policies


def build_decisions(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext", *,
                    inputs: CycleInputs | None = None, dedupe: dict | None = None,
                    decision_prefix: str = "live1",
                    mark_residual: bool = True,
                    sell_attempts: Mapping[str, int] | None = None) -> DecisionSet:
    """Build, filter, size and record one cycle's decisions; never plans or sends an order.

    The default arguments are the real trade path: inputs from the LIVE ledger, the
    ``live1:`` decision namespace, and residual marking of real dust.  Shadow mode passes
    its own simulated inputs/book, a process-local dedupe map and ``mark_residual=False``;
    this is the only way a shadow projection reaches the pure scan/size code.
    """
    rules = deps.executor.rules()
    if inputs is None:
        inputs = build_cycle_inputs(deps.ledger, rules=rules, balance=ctx.balance,
                                    policy_set=deps.policy_set,
                                    free_digests=deps.ledger.free_policy_digests())
    keys = state.dedupe if dedupe is None else dedupe

    def attempts_for(exits):
        if sell_attempts is not None:
            return sell_attempts
        return sell_attempts_for(deps.ledger, exits, inputs.held, deps.policy_set,
                                 decision_prefix=decision_prefix)

    signals = scan_live(ctx.frames, inputs.positions, deps.policy_set, now=ctx.now,
                        exit_policies=entry_exit_policies(deps.ledger, inputs.held,
                                                          deps.policy_set))

    skip_decisions: list[SkipDecision] = list(signals.skipped)

    # ---- books: symbols with an entry or an exit, in sorted order ---------------------
    current = signals
    books: dict[str, OrderBook] = {}
    symbols = sorted({item.symbol for item in current.entries}
                     | {item.symbol for item in current.exits})
    for symbol in symbols:
        has_exit = any(item.symbol == symbol for item in current.exits)
        if not has_exit and deps.clock() - ctx.now > CYCLE_BUDGET_S:
            # An entry-only symbol is not worth a book once the cycle is over budget; an
            # exit is always fetched (a sell must not be starved by the budget).
            current, dropped = _drop_symbol(current, symbol, "cycle_budget")
            skip_decisions.extend(dropped)
            continue
        try:
            book = deps.reader.fetch_order_book(symbol, depth=20)
        except Exception:
            current, dropped = _drop_symbol(current, symbol, "book_unavailable")
            skip_decisions.extend(dropped)
            continue
        fetched_at = deps.clock()  # the book is read now, not at the cycle start
        problem = book_age_problem(fetched_at, book.exchange_ts_ms)
        if problem is not None:
            current, dropped = _drop_symbol(current, symbol, problem)
            skip_decisions.extend(dropped)
            continue
        if book.symbol != symbol:
            current, dropped = _drop_symbol(current, symbol, "book_unavailable")
            skip_decisions.extend(dropped)
            continue
        books[symbol] = book

    # ---- a book can age while the later books are fetched serially --------------------
    # The per-fetch check above cannot see a book that was fresh then and is old by the
    # time the whole loop (up to 10 s per fetch) has finished: filter once more on the
    # clock the sizing call will use.  Dropping the book drops the symbol's entries and
    # exits exactly as the first check does.
    sized_at = deps.clock()
    for symbol in sorted(tuple(books)):
        problem = book_age_problem(sized_at, books[symbol].exchange_ts_ms)
        if problem is not None:
            current, dropped = _drop_symbol(current, symbol, problem)
            skip_decisions.extend(dropped)
            del books[symbol]

    # ---- liquidity gate: NEW BUYS only ------------------------------------------------
    # A wide spread or a mostly empty recent history blocks entries, never exits:
    # closing a position must stay possible even when the market is thin.
    for symbol in sorted(tuple(books)):
        if not any(item.symbol == symbol for item in current.entries):
            continue
        reason = entry_block(books[symbol], ctx.frames.get(symbol))
        if reason is None:
            continue
        current, dropped = _drop_entries(current, symbol, reason)
        skip_decisions.extend(dropped)
        if not any(item.symbol == symbol for item in current.entries + current.exits):
            del books[symbol]

    # ---- markets: rules + a fresh frame + a good book ---------------------------------
    markets: dict[str, SymbolMarket] = {}
    for symbol in sorted(books):
        rule = rules.get(symbol)
        frame = ctx.frames.get(symbol)
        if rule is None or frame is None:
            continue
        markets[symbol] = SymbolMarket(rule, books[symbol], frame.last_price)

    # ---- buys blocked (computed before the dry runs so every call gets the same args) --
    causes: set[str] = set()
    if ctx.safety.block_new_buys:
        causes.add("safety")
    causes.update(inputs.block_buys)
    causes.update(ctx.block_reasons)
    block_causes = tuple(sorted(causes))
    buys_blocked = bool(block_causes)
    mode_text = ctx.mode or ""
    cycle_text = str(state.cycle_number)
    blocks_text = ",".join(block_causes)

    def call_size(signals_for_call: LiveSignals, markets_for_call: dict[str, SymbolMarket],
                  attempts: dict[str, int]):
        return size_orders(
            signals_for_call,
            deps.policy_set,
            markets_for_call,
            inputs.held,
            inputs.funds,
            now=ctx.now,
            decided_keys=inputs.decided_keys,
            open_buy_symbols=inputs.open_buy_symbols,
            open_sell_symbols=inputs.open_sell_symbols,
            legacy_position_symbols=inputs.legacy_position_symbols,
            ambiguous_position_symbols=inputs.ambiguous_position_symbols,
            sell_attempts=attempts,
            buys_blocked=buys_blocked,
            decision_prefix=decision_prefix,
        )

    # ---- bad-market pre-filter: one invalid market makes the whole call raise ---------
    attempts = attempts_for(current.exits)
    for symbol in sorted(tuple(markets)):
        try:
            # The dry run only validates this symbol's market inputs.  It records nothing.
            call_size(_restrict(current, symbol), {symbol: markets[symbol]}, attempts)
        except ValueError:
            current, dropped = _drop_symbol(current, symbol, "market_invalid")
            skip_decisions.extend(dropped)
            del markets[symbol]

    # ---- the real sizing call ----------------------------------------------------------
    if current.entries or current.exits:
        attempts = attempts_for(current.exits)
        result = call_size(current, markets, attempts)
    else:
        result = None

    # ---- the budget after sizing: buys are dropped, sells are kept ---------------------
    over_budget = deps.clock() - ctx.now > CYCLE_BUDGET_S
    if over_budget and result is not None and result.buys:
        for plan in result.buys:
            skip_decisions.append(SkipDecision(plan.opportunity_id, plan.symbol,
                                               "cycle_budget", side="buy"))
        # Re-size without the buy opportunities: dropping only the plan tuples would leave
        # free_quote_left/headroom_left (and the residual ids) reflecting buys that are not
        # kept.  The sells are the same, and the first result's skips are kept as-is.
        rerun_attempts = attempts_for(current.exits)
        rerun = call_size(dataclasses.replace(current, entries=()), markets,
                          rerun_attempts)
        result = dataclasses.replace(rerun, skipped=result.skipped)

    # ---- dust positions become residual inventory (never for a simulated book) ---------
    if mark_residual and result is not None:
        held_by_position = {position.position_id: symbol
                            for symbol, position in inputs.held.items()}
        for position_id in result.residual_position_ids:
            symbol = held_by_position.get(position_id)
            if symbol is None:
                continue
            rule = rules.get(symbol)
            if rule is None:
                skip_decisions.append(SkipDecision(position_id, symbol,
                                                   "residual_mark_failed", side="sell"))
                continue
            try:
                # The sizer's own min_lot: min_amount 0 still reports dust against the
                # amount_step, so the ledger must see the same effective minimum.
                deps.ledger.mark_residual_inventory(
                    deps.lease, position_id, now=ctx.now,
                    min_amount=max(rule.min_amount, rule.amount_step),
                    amount_step=rule.amount_step,
                )
            except (LiveLedgerError, ValueError):
                skip_decisions.append(SkipDecision(position_id, symbol,
                                                   "residual_mark_failed", side="sell"))

    # ---- recording ---------------------------------------------------------------------
    record_failures = 0

    def record(action: str, identity: str, reason: str, payload: dict) -> None:
        nonlocal record_failures
        key = (action, identity, reason)
        if key in keys:
            return
        try:
            deps.ledger.record_live_event(deps.lease, "live_decision", payload, now=ctx.now)
        except LeaseLost:
            raise
        except Exception:
            # a decision that could not be written does not stop the cycle, but is counted
            record_failures += 1
            return
        keys[key] = state.cycle_number

    plans = () if result is None else result.sells + result.buys
    for plan in plans:
        record("plan", plan.decision_key, plan.reason_code,
               _payload(plan, mode_text, cycle_text, blocks_text))

    recorded_skips = list(skip_decisions)
    if result is not None:
        recorded_skips.extend(result.skipped)
    for skip in recorded_skips:
        reason = str(skip.reason_code)
        record("skip", skip.opportunity_id, reason, {
            "action": "skip",
            "side": str(skip.side),
            "symbol": skip.symbol,
            "opportunity_id": skip.opportunity_id,
            "reason": reason,
            "mode": mode_text,
            "cycle": cycle_text,
            "blocks": blocks_text,
        })

    if len(keys) > DEDUPE_MAX_ENTRIES:
        cutoff = state.cycle_number - DEDUPE_KEEP_CYCLES
        for key in [key for key, value in keys.items() if value < cutoff]:
            del keys[key]

    return DecisionSet(
        inputs=inputs,
        signals=current,
        result=result,
        buys_blocked=buys_blocked,
        block_causes=block_causes,
        over_budget=over_budget,
        record_failures=record_failures,
        markets=markets,
    )


def decide_and_act(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext") -> None:
    """Build and record the decisions; the normal shadow branch lives in ``act``."""
    build_decisions(deps, state, ctx)
    return None
