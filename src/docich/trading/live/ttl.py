"""The order TTL: withdraw stale open orders so a limit order cannot hang (L4b-5b-2c).

A live limit order that nobody fills would otherwise sit on the book forever.  Every cycle,
one step before the executor's own poll, this module withdraws the orders whose intent is
older than ``ORDER_TTL_S`` (the same TTL the sizer stamps on its plans).  Withdrawing is not
a decision: the next cycle re-decides with a fresh book, and a canceled sell comes back as
attempt ``a1`` through ``sell_attempts`` (the attempt number is part of the decision key,
which the ledger remembers).

This module calls only ``executor.cancel``; it never calls ``plan`` or ``submit`` and creates
no intent.  It is mode-independent: a cancel reduces risk, so it runs in monitor, shadow and
trade alike -- whenever the executor HAS a sender.  With ``sender is None`` it does nothing
(the read-only executor refuses every cancel anyway, so no ledger read is even needed).

The per-cycle cap (``MAX_CANCELS_PER_CYCLE``) keeps one bad cycle from spending the whole
budget on cancels; expired orders beyond it are reported as ``deferred``.  To keep a cancel
that never lands from holding the cap forever, the selection is ordered by the last cycle
each intent was tried (``("cancel_try", intent_id)`` in ``state.dedupe``): never-tried orders
first, then the least recently tried.  A cancel that returns without an error is a success;
``ValueError`` means the order became uncancelable between the read and the call (a fill won
the race), which is silently ignored; any other exception is reported as
``cancel_failed:<Class>`` and retried on the next cycle.  ``LeaseLost`` always propagates.

Every success and failure writes one ``live_decision`` row through
``record_live_event`` with string-only payloads, deduped by ``(action, intent_id, reason)``
exactly as ``decide``/``act`` do.  A failure to write the row (other than ``LeaseLost``) is
swallowed: the cancel already happened, and losing its record must not fail the cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from .lease import LeaseLost
from .sizing import SizingSettings

if TYPE_CHECKING:  # annotations only: ttl.py must not import the cycle at runtime (circular)
    from .cycle import CycleDeps, CycleState
    from .ledger import Intent

ORDER_TTL_S = SizingSettings().order_ttl_s      # 45: the same TTL the sizer stamps on its plans
MAX_CANCELS_PER_CYCLE = 5


@dataclass(frozen=True)
class TtlReport:
    canceled: tuple[str, ...]                  # intent ids whose cancel call returned without error
    failed: tuple[tuple[str, str], ...]        # (intent_id, "cancel_failed:<Class>")
    deferred: int                              # expired orders left for the next cycle by the per-cycle cap


def _payload(intent: "Intent", reason: str, state: "CycleState", now: float) -> dict:
    """The live_decision payload, every value a string (the ledger refuses floats)."""
    age_s = format(Decimal(str(now - intent.created_at)), "f")
    return {
        "action": "cancel",
        "side": intent.side,
        "symbol": intent.symbol,
        "opportunity_id": intent.decision_key,
        "decision_key": intent.decision_key,
        "intent_id": intent.intent_id,
        "reason": reason,
        "age_s": age_s,
        "cycle": str(state.cycle_number),
    }


def _record(deps: "CycleDeps", state: "CycleState", intent: "Intent", reason: str, now: float) -> None:
    """Write one row once per (action, intent, reason); swallow write failures, not LeaseLost."""
    key = ("cancel", intent.intent_id, reason)
    if key in state.dedupe:
        return
    try:
        deps.ledger.record_live_event(deps.lease, "live_decision",
                                      _payload(intent, reason, state, now), now=now)
    except LeaseLost:
        raise
    except Exception:
        return
    state.dedupe[key] = state.cycle_number


def _forget_try_if_closed(deps: "CycleDeps", state: "CycleState", intent: "Intent") -> None:
    """Drop a try marker once the order can no longer be listed as expired.

    The marker only orders the next selection, and a closed order never enters that list.
    A cancel that returned without error but left the intent open (OrderNotFound, an
    ambiguous outcome) keeps its marker, so it stays behind the never-tried orders.
    """
    try:
        current = deps.ledger.get_intent(intent.intent_id)
    except LeaseLost:
        raise
    except Exception:
        return                                   # keep the marker on doubt: it is harmless
    if current is None or current.state not in ("ACKED", "PARTIALLY_FILLED"):
        state.dedupe.pop(("cancel_try", intent.intent_id), None)


def cancel_expired(deps: "CycleDeps", state: "CycleState", now: float) -> TtlReport:
    """Cancel up to ``MAX_CANCELS_PER_CYCLE`` open orders older than ``ORDER_TTL_S``."""
    if deps.executor.sender is None:
        return TtlReport(canceled=(), failed=(), deferred=0)
    expired = [intent for intent in deps.ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED"))
               if intent.exchange_order_id and now - intent.created_at > ORDER_TTL_S]
    # A cancel that stays ACKED must not keep its created_at slot forever: the orders that
    # were never tried go first, then the ones tried longest ago (original order among
    # equals).  With no attempt remembered, this is the plain oldest-first list.
    ordered = sorted(
        enumerate(expired),
        key=lambda pair: (state.dedupe.get(("cancel_try", pair[1].intent_id), 0), pair[0]),
    )
    taken = [intent for _, intent in ordered[:MAX_CANCELS_PER_CYCLE]]
    deferred = len(expired) - len(taken)
    canceled: list[str] = []
    failed: list[tuple[str, str]] = []
    for intent in taken:
        state.dedupe[("cancel_try", intent.intent_id)] = state.cycle_number
        try:
            deps.executor.cancel(intent.intent_id)
        except LeaseLost:
            raise
        except ValueError:
            # not cancelable any more: a fill (or another terminal state) won the race
            continue
        except Exception as exc:
            reason = f"cancel_failed:{type(exc).__name__}"
            failed.append((intent.intent_id, reason))
            _record(deps, state, intent, reason, now)
        else:
            canceled.append(intent.intent_id)
            _record(deps, state, intent, "ttl_expired", now)
            _forget_try_if_closed(deps, state, intent)
    return TtlReport(canceled=tuple(canceled), failed=tuple(failed), deferred=deferred)
