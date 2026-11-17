"""The trade-mode order path: sized plans become intents through the executor (L4b-5b-2b).

``decide_and_act`` is the hook the runner will pass to ``cycle.run_cycle``: it builds and
records the cycle's decisions (``decide.build_decisions``) and, in TRADE mode only, places
the sized plans through ``OrderExecutor.plan``/``submit``.  This is the only module that
calls ``executor.plan``/``executor.submit``; shadow and monitor never reach it.  With
``deps.executor.sender is None`` nothing is planned: one ``trade_unavailable_no_sender``
decision is recorded per process and the cycle moves on.  There is no real sender yet:
``executor.SENDER_KINDS_ALLOWED`` is mock-only.

Known consequence for the owner (to be confirmed before phase E): sell proceeds raise the
investment headroom but never the free quote. If a limit sell does not fill, the buys that
used its headroom stay placed, so deployed can exceed C by that sell's notional until a
later exit fills. Buys are still capped by the free quote (no buy can spend JPY that is not
in the account), and the signed headroom blocks every new buy while deployed is above C. A
TTL cancel of the unfilled sell (T5b-2c) withdraws the sell but does NOT end the overshoot.

Placement order is fixed: every sell, then every buy (never interleaved).  A buy whose
symbol has a sell plan in the same cycle is never placed.  A buy is also dropped once the
cycle has spent ``decide.CYCLE_BUDGET_S``, and both sides are re-checked for book freshness
at the placement instant (the books may have aged since ``build_decisions``).

Every write goes through ``record_live_event`` with string-only payloads and the same
dedupe rule as ``decide``: a recorded ``(action, identity, reason)`` is not written again
while its key stays in ``state.dedupe``.  A failed write is swallowed (``LeaseLost`` is
not).  Nothing here cancels an order, creates an intent outside ``executor.plan``, or ends
the process.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .decide import CYCLE_BUDGET_S, DecisionSet, book_age_problem, build_decisions
from .executor import SubmitOutcome
from .lease import LeaseLost
from .ledger import LiveLedgerError

if TYPE_CHECKING:  # annotations only: keeps this module independent of the runner
    from .cycle import CycleContext, CycleDeps, CycleState
    from .sizing import OrderPlan

NO_SENDER_REASON = "trade_unavailable_no_sender"
SELL_PLANNED_SAME_SYMBOL = "sell_planned_same_symbol"
CYCLE_BUDGET = "cycle_budget"
BOOK_UNAVAILABLE = "book_unavailable"


@dataclass(frozen=True)
class PlacementReport:
    placed: tuple[tuple[str, str], ...]     # (decision_key, SubmitOutcome value) in order
    skipped: tuple[tuple[str, str], ...]    # (decision_key, reason)
    stopped: str | None                      # why the loop stopped early (None = ran through)


def _record(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext", action: str,
            identity: str, reason: str, payload: dict, *, pinned: bool = False) -> None:
    """Write one live_decision once per dedupe key; a failure is swallowed, LeaseLost is not.

    A ``pinned`` key is stored as ``sys.maxsize`` so the runner's dedupe pruning (which
    deletes values below the cutoff) can never make it recordable again.
    """
    key = (action, identity, reason)
    if key in state.dedupe:
        return
    try:
        deps.ledger.record_live_event(deps.lease, "live_decision", payload, now=ctx.now)
    except LeaseLost:
        raise
    except Exception:
        return
    state.dedupe[key] = sys.maxsize if pinned else state.cycle_number


def _skip_payload(plan: "OrderPlan", reason: str, mode_text: str, cycle_text: str,
                  blocks_text: str) -> dict:
    return {
        "action": "skip",
        "side": plan.side,
        "symbol": plan.symbol,
        "opportunity_id": plan.opportunity_id,
        "reason": reason,
        "mode": mode_text,
        "cycle": cycle_text,
        "blocks": blocks_text,
    }


def _settle_after_exception(deps: "CycleDeps", intent_id: str, exc: BaseException,
                            now: float) -> None:
    """Never leave a PLANNED or SUBMITTING intent behind when plan/submit raised.

    A PLANNED intent provably never left: abandon it.  A SUBMITTING intent may already be
    at the exchange: mark it UNKNOWN_SUBMISSION so the next ``executor.step()`` reconciles
    it (the settlement instant is the clock, the same one ``now`` was read from).
    """
    try:
        intent = deps.ledger.get_intent(intent_id)
    except LeaseLost:
        raise
    except Exception:
        return
    if intent is None:
        return
    if intent.state == "PLANNED":
        deps.ledger.transition_intent(deps.lease, intent_id, expected_state="PLANNED",
                                      to_state="ABANDONED", now=deps.clock(),
                                      reason_code=f"act_exception:{type(exc).__name__}")
    elif intent.state == "SUBMITTING":
        deps.ledger.transition_intent(deps.lease, intent_id, expected_state="SUBMITTING",
                                      to_state="UNKNOWN_SUBMISSION", now=deps.clock(),
                                      reason_code=f"act_exception:{type(exc).__name__}")


def place_orders(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext",
                 decisions: DecisionSet) -> PlacementReport:
    """Place ``decisions.result`` (sells, then buys) through the executor, in order."""
    result = decisions.result
    if result is None:
        return PlacementReport(placed=(), skipped=(), stopped=None)

    mode_text = ctx.mode or ""
    cycle_text = str(state.cycle_number)
    blocks_text = ",".join(decisions.block_causes)
    sell_symbols = {plan.symbol for plan in result.sells}
    placed: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    stopped: str | None = None
    budget_hit = False

    def skip(plan: "OrderPlan", reason: str) -> None:
        skipped.append((plan.decision_key, reason))
        _record(deps, state, ctx, "skip", plan.opportunity_id, reason,
                _skip_payload(plan, reason, mode_text, cycle_text, blocks_text))

    for plan in result.sells + result.buys:
        # 1. the stop flag wins over every remaining plan; nothing more is written
        if deps.stop_requested():
            stopped = "stop_requested"
            break

        # 2. once the budget is spent, every remaining buy is skipped (sells never get here)
        if plan.side == "buy" and (budget_hit or deps.clock() - ctx.now > CYCLE_BUDGET_S):
            budget_hit = True
            stopped = CYCLE_BUDGET
            skip(plan, CYCLE_BUDGET)
            continue

        # 3. a buy under a same-cycle sell must not fight the exit for the same coin
        if plan.side == "buy" and plan.symbol in sell_symbols:
            skip(plan, SELL_PLANNED_SAME_SYMBOL)
            continue

        # 4. freshness at the placement instant, not only when the plans were built
        market = decisions.markets.get(plan.symbol)
        if market is None:
            skip(plan, BOOK_UNAVAILABLE)
            continue
        problem = book_age_problem(deps.clock(), market.book.exchange_ts_ms)
        if problem is not None:
            skip(plan, problem)
            continue

        # 5. plan: a known ledger/validation failure skips this plan, anything else stops
        try:
            intent = deps.executor.plan(plan.intent_request())
        except LeaseLost:
            raise
        except (LiveLedgerError, ValueError) as exc:
            skip(plan, f"plan_failed:{type(exc).__name__}")
            continue
        except Exception as exc:
            skip(plan, f"plan_failed:{type(exc).__name__}")
            stopped = "plan_error"
            break

        # 6. submit: settle the intent, then stop; the next step() reconciles an unknown
        try:
            outcome = deps.executor.submit(intent.intent_id)
        except LeaseLost:
            raise
        except Exception as exc:
            _settle_after_exception(deps, intent.intent_id, exc, ctx.now)
            skip(plan, f"submit_failed:{type(exc).__name__}")
            stopped = "submit_error"
            break

        # 7. record the outcome and keep going only when the exchange answered clearly
        _record(deps, state, ctx, "submit", plan.decision_key, outcome.value, {
            "action": "submit",
            "side": plan.side,
            "symbol": plan.symbol,
            "opportunity_id": plan.opportunity_id,
            "reason": outcome.value,
            "decision_key": plan.decision_key,
            "intent_id": intent.intent_id,
            "mode": mode_text,
            "cycle": cycle_text,
            "blocks": blocks_text,
        })
        placed.append((plan.decision_key, outcome.value))
        if outcome in (SubmitOutcome.UNKNOWN, SubmitOutcome.BLOCKED):
            stopped = outcome.value
            break

    return PlacementReport(placed=tuple(placed), skipped=tuple(skipped), stopped=stopped)


def decide_and_act(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext") -> None:
    """The cycle hook: build (and record) the decisions, then place them in trade mode."""
    decisions = build_decisions(deps, state, ctx)
    if ctx.mode != "trade":
        return None
    if deps.executor.sender is None:
        # There is no real sender yet.  One row for the whole process: the dedupe key is
        # pinned (sys.maxsize, never pruned) so the runner's dedupe pruning cannot delete it
        # and make the notice recordable again on a later cycle.
        _record(deps, state, ctx, "skip", "-", NO_SENDER_REASON, {
            "action": "skip",
            "side": "unknown",
            "symbol": "-",
            "opportunity_id": "-",
            "reason": NO_SENDER_REASON,
            "mode": ctx.mode or "",
            "cycle": str(state.cycle_number),
            "blocks": ",".join(decisions.block_causes),
        }, pinned=True)
        return None
    place_orders(deps, state, ctx, decisions)
    return None
