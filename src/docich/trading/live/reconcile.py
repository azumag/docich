"""Restart reconciliation and resolution of UNKNOWN_SUBMISSION (Issue #3623, L2).

Time never proves anything by itself.  An unknown submission becomes NOT_CREATED only when
(1) enough time has passed that a late request would be visible, (2) the exchange's own answers
(order lookup by client id, or the complete open-order and fill lists) show no such order, and
(3) the balance shows no unexplained change in the currencies the order could have touched.
A sell needs only the base currency (so a stuck JPY check never pins an exit).  Anything less stays
UNKNOWN and keeps new buys stopped.
"""
from __future__ import annotations

from decimal import Decimal

from . import views
from .balance import stable_check
from .canonical import canonical_json
from .exchange import (ExchangeAmbiguous, ExchangeDataError, OrderNotFound, OrderPage, OrderSnapshot)
from .ingest import observation_from
from .ledger import Intent


def _link(ex, intent: Intent, snap: OrderSnapshot, how: str) -> str:
    if not ex.ingest.sync_order(intent, snap):
        return "anomaly"
    acked = ex.ledger.transition_intent(ex.lease, intent.intent_id, expected_state="UNKNOWN_SUBMISSION",
                                        to_state="ACKED", now=ex.clock(), exchange_order_id=snap.exchange_order_id,
                                        reason_code=f"found_by_{how}")
    ex.ingest.refresh(acked)
    return "acked"


def _matches(intent: Intent, snap: OrderSnapshot, lo_ms: int, hi_ms: int) -> bool:
    if (snap.symbol, snap.side, snap.order_type) != (intent.symbol, intent.side, intent.order_type):
        return False
    if snap.amount != Decimal(intent.amount):
        return False
    if intent.order_type == "limit" and snap.price != Decimal(intent.price):
        return False
    return snap.ordered_at_ms is None or lo_ms <= snap.ordered_at_ms <= hi_ms


def try_resolve(ex, intent: Intent) -> str:
    """One attempt.  Returns "acked", "not_created", "waiting" or "anomaly"."""
    ledger, reader, cfg = ex.ledger, ex.reader, ex.config
    now = ex.clock()
    try:
        linked = views.order_for_intent(ledger, intent.intent_id)
        if linked is not None:  # the ack was recorded, then we died before the state moved
            return _link(ex, intent, reader.fetch_order(intent.symbol, linked.exchange_order_id), "ledger_link")
    except ExchangeDataError as exc:
        ex.ingest.anomaly(f"resolve {intent.intent_id}: {exc}")
        return "waiting"
    except (ExchangeAmbiguous, OrderNotFound):
        return "waiting"

    caps = reader.capabilities
    by_client_id = bool(caps.client_order_id and caps.fetch_by_client_id and intent.client_order_id)
    submitted_at = views.submitting_time(ledger, intent.intent_id) or intent.updated_at
    margin_ms = int(cfg.skew_margin_s * 1000)
    lo_ms, hi_ms = int(submitted_at * 1000) - margin_ms, int(now * 1000) + margin_ms
    box: dict[str, object] = {}

    def lists() -> None:
        if by_client_id:
            box["by_client_id"] = reader.fetch_order_by_client_id(intent.symbol, intent.client_order_id)
        else:
            box["open"] = reader.fetch_open_orders(intent.symbol)
            box["fills"] = reader.fetch_fills(intent.symbol, since_ms=max(0, lo_ms))

    currencies = {ex.rules()[intent.symbol].base}
    if intent.side == "buy":
        currencies.add(ex.rules()[intent.symbol].quote)
    try:
        result, stable = stable_check(ledger, reader, ex.rules(), currencies=currencies, between=lists)
    except ExchangeAmbiguous:
        return "waiting"
    except (ExchangeDataError, OrderNotFound) as exc:
        ex.ingest.anomaly(f"resolve {intent.intent_id}: {exc}")
        return "waiting"
    if not box:
        return "waiting"

    # --- positive evidence first: it never needs the settle time
    if by_client_id:
        found = box["by_client_id"]
        if found is not None:
            return _link(ex, intent, found, "client_id")
        complete = True
        ex.none_seen.setdefault(intent.intent_id, now)
    else:
        page: OrderPage = box["open"]
        fills = box["fills"]
        known = views.known_order_ids(ledger)
        candidates: dict[str, OrderSnapshot | None] = {o.exchange_order_id: o for o in page.orders
                                                       if o.exchange_order_id not in known}
        for fill in fills.fills:
            if fill.symbol == intent.symbol and fill.exchange_order_id not in known:
                candidates.setdefault(fill.exchange_order_id, None)
        resolved: list[OrderSnapshot] = []
        for eid, snap in candidates.items():
            if snap is None:
                try:
                    snap = reader.fetch_order(intent.symbol, eid)
                except OrderNotFound:
                    return "waiting"  # a fill with an order we cannot see yet: not provable either way
                except ExchangeDataError as exc:
                    ex.ingest.anomaly(f"resolve {intent.intent_id}: {exc}")
                    return "waiting"
                except ExchangeAmbiguous:
                    return "waiting"
            resolved.append(snap)
        matching = [s for s in resolved if _matches(intent, s, lo_ms, hi_ms)]
        if len(matching) > 1:
            ex.ingest.anomaly(f"resolve {intent.intent_id}: {len(matching)} exchange orders match")
            return "anomaly"
        if matching:
            return _link(ex, intent, matching[0], "list_match")
        complete = page.complete and fills.complete

    # --- negative conclusion: time + complete answers + balance
    if not complete or not stable or result is None or not result.proven(currencies):
        return "waiting"
    if now - intent.updated_at < cfg.settle_s:
        return "waiting"
    if by_client_id and now - ex.none_seen.get(intent.intent_id, now) < cfg.settle_s:
        return "waiting"  # two empty answers, settle_s apart
    evidence = canonical_json({
        "method": "client_id" if by_client_id else "list_match", "settle_s": str(cfg.settle_s),
        "checked_at": str(now), "currencies": sorted(currencies),
        "balance": {c.currency: {"expected": str(c.expected), "actual": str(c.actual), "tolerance": str(c.tolerance)}
                    for c in result.checks}}).decode("utf-8")
    done = ex.ledger.transition_intent(ex.lease, intent.intent_id, expected_state="UNKNOWN_SUBMISSION",
                                       to_state="NOT_CREATED", now=now, evidence=evidence)
    ex.ingest._close_if_flat(done)
    ex.none_seen.pop(intent.intent_id, None)
    return "not_created"


def sweep(ex) -> bool:
    """Look for orders the ledger does not know (owner's manual orders, a lost link).  True if it ran."""
    ledger, reader, cfg = ex.ledger, ex.reader, ex.config
    unknown_symbols = {i.symbol for i in ledger.list_intents(states=("UNKNOWN_SUBMISSION",))}
    now = ex.clock()
    margin_ms = int(cfg.skew_margin_s * 1000)
    if ex.sweep_since_ms is None:
        created = [i.created_at for i in ledger.list_intents(states=("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION",
                                                                     "ACKED", "PARTIALLY_FILLED", "FILLED",
                                                                     "CANCELED", "ABANDONED", "NOT_CREATED",
                                                                     "REJECTED", "EXPIRED"))]
        ex.sweep_since_ms = int(min(created + [now]) * 1000) - margin_ms
    ran = False
    complete_all = True
    for symbol in ex.symbols:
        if symbol in unknown_symbols:
            continue  # resolving the unknown submission accounts for orders on this symbol
        try:
            page = reader.fetch_open_orders(symbol)
            fills = reader.fetch_fills(symbol, since_ms=max(0, ex.sweep_since_ms))
            snaps = {o.exchange_order_id: o for o in page.orders}
            for fill in fills.fills:
                if fill.exchange_order_id not in snaps and views.order_row(ledger, fill.exchange_order_id) is None:
                    snaps[fill.exchange_order_id] = reader.fetch_order(symbol, fill.exchange_order_id)
        except ExchangeDataError as exc:
            ex.ingest.anomaly(f"sweep {symbol}: {exc}")
            complete_all = False
            continue
        except (ExchangeAmbiguous, OrderNotFound):   # unavailable or not visible: skip this symbol for now
            complete_all = False
            continue
        complete_all = complete_all and page.complete and fills.complete
        known = views.known_order_ids(ledger)
        for eid, snap in snaps.items():
            if views.order_row(ledger, eid) is not None:
                continue
            ledger.record_order_observation(ex.lease, observation_from(snap, known.get(eid)), now=now)
        ran = True
    if ran and complete_all:
        ex.sweep_since_ms = int(now * 1000) - margin_ms
    return ran


def on_startup(ex) -> dict[str, int]:
    """Bring the ledger and the exchange back in line after a (re)start.  Safe to run again."""
    ledger = ex.ledger
    counts = {"abandoned": 0, "unknown": 0, "acked": 0, "not_created": 0}
    for intent in ledger.list_intents(states=("PLANNED",)):
        ledger.transition_intent(ex.lease, intent.intent_id, expected_state="PLANNED", to_state="ABANDONED",
                                 now=ex.clock(), reason_code="restart_stale_plan")
        counts["abandoned"] += 1
    counts["unknown"] = len(ledger.mark_in_flight_unknown(ex.lease, now=ex.clock()))
    for intent in ledger.list_intents(states=("UNKNOWN_SUBMISSION",)):
        outcome = try_resolve(ex, intent)
        if outcome in counts:
            counts[outcome] += 1
    for intent in ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED")):
        ex.ingest.refresh(intent)
    sweep(ex)
    ex.check_balance()
    return counts
