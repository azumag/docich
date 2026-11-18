"""Read-only queries over the LIVE ledger used by the executor, reconcile and balance code."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping

from .ledger import Intent

if TYPE_CHECKING:  # imported for annotations only: keeps this module light at import time
    from .exchange import MarketRules


@dataclass(frozen=True)
class OrderRow:
    exchange_order_id: str
    intent_id: str | None
    symbol: str
    side: str
    order_type: str
    amount: Decimal
    price: Decimal | None
    status: str
    executed_amount: Decimal
    remaining_amount: Decimal


@dataclass(frozen=True)
class FillRow:
    fill_id: str
    exchange_order_id: str
    symbol: str
    side: str
    amount: Decimal
    price: Decimal
    quote_notional: Decimal
    fee_status: str
    fee_amount: Decimal | None
    fee_currency: str | None
    executed_at: float
    position_id: str | None


_ORDER_COLS = ("exchange_order_id, intent_id, symbol, side, order_type, amount, price, status, "
               "executed_amount, remaining_amount")
_FILL_COLS = ("fill_id, exchange_order_id, symbol, side, amount, price, quote_notional, fee_status, fee_amount, "
              "fee_currency, executed_at, position_id")


def _d(value):
    return None if value is None else Decimal(value)


def _order(row) -> OrderRow:
    return OrderRow(row[0], row[1], row[2], row[3], row[4], Decimal(row[5]), _d(row[6]), row[7],
                    Decimal(row[8]), Decimal(row[9]))


def _fill(row) -> FillRow:
    return FillRow(row[0], row[1], row[2], row[3], Decimal(row[4]), Decimal(row[5]), Decimal(row[6]), row[7],
                   _d(row[8]), row[9], float(row[10]), row[11])


def order_row(ledger, exchange_order_id: str) -> OrderRow | None:
    with ledger.read_cursor() as cur:
        row = cur.execute(f"SELECT {_ORDER_COLS} FROM orders WHERE exchange_order_id=?",
                          (exchange_order_id,)).fetchone()
    return _order(row) if row else None


def order_for_intent(ledger, intent_id: str) -> OrderRow | None:
    with ledger.read_cursor() as cur:
        row = cur.execute(f"SELECT {_ORDER_COLS} FROM orders WHERE intent_id=?", (intent_id,)).fetchone()
    return _order(row) if row else None


def known_order_ids(ledger) -> dict[str, str | None]:
    """Every exchange order id the ledger knows -> the intent it belongs to (None: no intent)."""
    with ledger.read_cursor() as cur:
        known = {row[0]: row[1] for row in cur.execute("SELECT exchange_order_id, intent_id FROM orders")}
        for eid, intent_id in cur.execute("SELECT exchange_order_id, intent_id FROM intents "
                                          "WHERE exchange_order_id IS NOT NULL"):
            known.setdefault(eid, intent_id)
    return known


def fill_row(ledger, fill_id: str) -> FillRow | None:
    with ledger.read_cursor() as cur:
        row = cur.execute(f"SELECT {_FILL_COLS} FROM fills WHERE fill_id=?", (fill_id,)).fetchone()
    return _fill(row) if row else None


def fills_for_order(ledger, exchange_order_id: str) -> list[FillRow]:
    with ledger.read_cursor() as cur:
        rows = cur.execute(f"SELECT {_FILL_COLS} FROM fills WHERE exchange_order_id=? ORDER BY executed_at, fill_id",
                           (exchange_order_id,)).fetchall()
    return [_fill(row) for row in rows]


def all_fills(ledger) -> list[FillRow]:
    with ledger.read_cursor() as cur:
        rows = cur.execute(f"SELECT {_FILL_COLS} FROM fills ORDER BY executed_at, fill_id").fetchall()
    return [_fill(row) for row in rows]


def cashflows(ledger) -> list[tuple[str, str, Decimal]]:
    """(kind, currency, amount) of every cashflow."""
    with ledger.read_cursor() as cur:
        return [(k, c, Decimal(a)) for k, c, a in cur.execute("SELECT kind, currency, amount FROM cashflows")]


def position_net_base(ledger, position_id: str) -> Decimal:
    """Bought minus sold base amount of a position (from recorded fills), minus known fees paid in base.

    A fee of unknown size is not subtracted: the exchange balance is the final word on what can be sold.
    """
    net = Decimal(0)
    with ledger.read_cursor() as cur:
        for symbol, side, amount, fee_status, fee_amount, fee_currency in cur.execute(
                "SELECT symbol, side, amount, fee_status, fee_amount, fee_currency FROM fills WHERE position_id=?",
                (position_id,)):
            net += Decimal(amount) if side == "buy" else -Decimal(amount)
            if fee_status == "known" and fee_currency == symbol.split("/")[0]:
                net -= Decimal(fee_amount)
    return net


def has_open_intent(ledger, position_id: str) -> bool:
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT 1 FROM intents WHERE position_id=? AND state IN "
                           "('PLANNED','SUBMITTING','UNKNOWN_SUBMISSION','ACKED','PARTIALLY_FILLED')",
                           (position_id,)).fetchone() is not None


def submitting_time(ledger, intent_id: str) -> float | None:
    """When the intent was last moved to SUBMITTING (from the event log), else None."""
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT occurred_at, payload FROM events WHERE kind='intent_state' AND entity_id=? "
                           "ORDER BY event_id DESC", (intent_id,)).fetchall()
    for at, payload in rows:
        try:
            if json.loads(payload).get("payload", {}).get("to") == "SUBMITTING":
                return float(at)
        except ValueError:
            continue
    return None


def fill_count(ledger) -> int:
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT COUNT(*) FROM fills").fetchone()[0]


def trip_count(ledger) -> int:
    """Every safety trip row, cleared ones included (a cleared stop is never deleted)."""
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT COUNT(*) FROM safety_trips").fetchone()[0]


def open_position_ids(ledger) -> list[str]:
    with ledger.read_cursor() as cur:
        return [row[0] for row in cur.execute("SELECT position_id FROM positions WHERE status='open' "
                                              "ORDER BY opened_at, position_id")]


# -- L4b: read-only views (nothing below writes) ---------------------------------------------
_INTENT_COLS_LITERAL = ("intent_id, decision_key, symbol, side, order_type, amount, price, policy_digest, "
                        "position_id, rule_id, state, state_version, exchange_order_id, reason_code, "
                        "client_order_id, created_at, updated_at")


@dataclass(frozen=True)
class CostBasis:
    net_base: Decimal
    cost: Decimal
    avg_cost: Decimal
    opened_at: float | None


@dataclass(frozen=True)
class EventRow:
    event_id: int
    occurred_at: float
    entity_type: str | None
    entity_id: str | None
    payload: dict


def intent_by_decision_key(ledger, decision_key: str) -> Intent | None:
    """The intent recorded for a decision key, in any state (terminal ones included), else None."""
    with ledger.read_cursor() as cur:
        row = cur.execute(f"SELECT {_INTENT_COLS_LITERAL} FROM intents WHERE decision_key=?",
                          (decision_key,)).fetchone()
    return Intent(*row) if row else None


def _fill_order(fill_id: str, executed_at: float) -> tuple[float, int, int, str]:
    """Order fills at one executed_at by numeric trade id; ids without a numeric tail follow, by text."""
    tail = fill_id.rsplit(":", 1)[-1]
    if tail.isascii() and tail.isdigit():
        return (executed_at, 0, int(tail), fill_id)
    return (executed_at, 1, 0, fill_id)


def position_cost(ledger, position_id: str, max_fee_rate: Decimal | None) -> CostBasis:
    """Average-cost basis of a position, rebuilt from its fills (a sell keeps the average price).

    An unknown fee is estimated at ``max_fee_rate`` of the notional on a buy; without a rate the
    cost cannot be bounded.
    """
    with ledger.read_cursor() as cur:
        rows = cur.execute(
            "SELECT fill_id, symbol, side, amount, quote_notional, fee_status, fee_amount, fee_currency, executed_at "
            "FROM fills WHERE position_id=? ORDER BY executed_at", (position_id,)).fetchall()
    rows = [row[1:] for row in sorted(rows, key=lambda row: _fill_order(row[0], row[8]))]
    net_base = Decimal(0)
    cost = Decimal(0)
    avg_cost = Decimal(0)
    opened_at: float | None = None
    for symbol, side, amount_text, notional_text, fee_status, fee_amount, fee_currency, executed_at in rows:
        base = symbol.split("/")[0]
        quote = symbol.split("/")[1]
        amount = Decimal(amount_text)
        notional = Decimal(notional_text)
        if side == "buy":
            if net_base <= 0:
                opened_at = float(executed_at)
            net_base += amount
            if fee_status == "known" and fee_currency == base:
                net_base -= Decimal(fee_amount)
            cost += notional
            if fee_status == "known" and fee_currency == quote:
                cost += Decimal(fee_amount)
            elif fee_status == "unknown":
                if max_fee_rate is None:
                    raise ValueError("max_fee_rate is required")
                cost += max_fee_rate * notional
            avg_cost = cost / net_base if net_base > 0 else Decimal(0)
        else:
            sold = amount
            if fee_status == "known" and fee_currency == base:
                sold += Decimal(fee_amount)
            held_avg = avg_cost if net_base > 0 else Decimal(0)
            net_base -= sold
            cost = held_avg * net_base
            avg_cost = held_avg
            if net_base <= 0:
                cost = Decimal(0)
                avg_cost = Decimal(0)
                opened_at = None
    return CostBasis(net_base, cost, avg_cost, opened_at)


def deployed_cost(ledger, max_fee_rates: Mapping[str, Decimal | None]) -> Decimal:
    """Quote cost tied up in open positions (a symbol absent from the map is treated as unknown)."""
    total = Decimal(0)
    for position_id, symbol in open_positions(ledger):
        total += position_cost(ledger, position_id, max_fee_rates.get(symbol)).cost
    return total


def open_positions(ledger) -> list[tuple[str, str]]:
    """(position_id, symbol) of every open position, oldest first."""
    with ledger.read_cursor() as cur:
        return [(row[0], row[1]) for row in cur.execute(
            "SELECT position_id, symbol FROM positions WHERE status='open' ORDER BY opened_at, position_id")]


def events_of_kinds(ledger, kinds: tuple[str, ...], *, after_event_id: int = 0,
                    limit: int = 1000) -> list[EventRow]:
    """Events of the named kinds, oldest first; ``payload`` is the inner payload."""
    names = tuple(kinds)
    if not names:
        return []
    marks = ",".join("?" for _ in names)
    with ledger.read_cursor() as cur:
        rows = cur.execute(
            f"SELECT event_id, occurred_at, entity_type, entity_id, payload FROM events "
            f"WHERE kind IN ({marks}) AND event_id>? ORDER BY event_id LIMIT ?",
            (*names, int(after_event_id), int(limit))).fetchall()
    out = []
    for event_id, occurred_at, entity_type, entity_id, stored in rows:
        try:
            envelope = json.loads(stored)
        except ValueError:
            envelope = {}
        inner = envelope.get("payload", {}) if isinstance(envelope, dict) else {}
        if not isinstance(inner, dict):
            inner = {}
        out.append(EventRow(int(event_id), float(occurred_at), entity_type, entity_id, inner))
    return out


def events_of_kind(ledger, kind: str, *, after_event_id: int = 0, limit: int = 1000) -> list[EventRow]:
    """Events of one kind, oldest first; ``payload`` is the inner payload, not the stored envelope."""
    return events_of_kinds(ledger, (kind,), after_event_id=after_event_id, limit=limit)


# -- L4b-5: sizing/reconcile views (nothing below writes) -------------------------------------
_OPEN_INTENT_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED")


@dataclass(frozen=True)
class PositionRow:
    position_id: str
    symbol: str
    entry_policy_digest: str
    opened_at: float


def open_position_rows(ledger) -> list[PositionRow]:
    """Every open position, oldest first, with the digest it was entered under."""
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT position_id, symbol, entry_policy_digest, opened_at FROM positions "
                           "WHERE status='open' ORDER BY opened_at, position_id").fetchall()
    return [PositionRow(row[0], row[1], row[2], float(row[3])) for row in rows]


def open_intent_symbols(ledger, side: str) -> frozenset[str]:
    """Symbols with an intent of ``side`` in an OPEN state (the ledger's one-per-side set)."""
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    marks = ",".join("?" for _ in _OPEN_INTENT_STATES)
    with ledger.read_cursor() as cur:
        rows = cur.execute(f"SELECT DISTINCT symbol FROM intents WHERE side=? AND state IN ({marks})",
                           (side, *_OPEN_INTENT_STATES)).fetchall()
    return frozenset(row[0] for row in rows)


def decision_keys(ledger, prefix: str) -> list[str]:
    """Every recorded decision key starting with ``prefix`` (an exact prefix test, never LIKE)."""
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("prefix must be a non-empty str")
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT decision_key FROM intents WHERE substr(decision_key, 1, ?) = ? "
                           "ORDER BY decision_key", (len(prefix), prefix)).fetchall()
    return [row[0] for row in rows]


def capital_quote(ledger, currency: str = "JPY") -> Decimal:
    """Deposits plus signed adjustments minus withdrawals of ``currency`` (as balance does)."""
    total = Decimal(0)
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT kind, amount FROM cashflows WHERE currency=?", (currency,)).fetchall()
    for kind, amount in rows:
        value = Decimal(amount)
        total += -value if kind == "withdrawal" else value
    return total


@dataclass(frozen=True)
class OpenBuyExposure:
    notional: Decimal
    reserved_quote: Decimal


# Intents in these states provably never reached the exchange: they are not exchange history.
_INTENT_STATES_NEVER_SENT = frozenset({"PLANNED", "ABANDONED", "REJECTED", "NOT_CREATED"})


def currencies_with_history(ledger) -> frozenset[str]:
    """Every upper-case currency the ledger has any history for: cashflows, fills and intents.

    A fill is history for both sides of its symbol and for its fee currency.  An intent is
    history for both sides of its symbol once it may have reached the exchange: every state
    except PLANNED, ABANDONED, REJECTED and NOT_CREATED.  That is an exclusion list, so a new
    state counts by default; it recovers a fill the exchange holds after a crash between send
    and record, when the ledger has only the intent.  A symbol that is not a well-formed
    ``BASE/QUOTE`` pair is ignored: this read must never raise.
    """
    seen: set[str] = set()

    def add_symbol(symbol) -> None:
        parts = str(symbol).split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            seen.add(parts[0].upper())
            seen.add(parts[1].upper())

    with ledger.read_cursor() as cur:
        for (currency,) in cur.execute("SELECT currency FROM cashflows"):
            seen.add(str(currency).upper())
        for symbol, fee_currency in cur.execute("SELECT symbol, fee_currency FROM fills"):
            add_symbol(symbol)
            if fee_currency is not None:
                seen.add(str(fee_currency).upper())
        marks = ",".join("?" for _ in _INTENT_STATES_NEVER_SENT)
        states = tuple(sorted(_INTENT_STATES_NEVER_SENT))
        for (symbol,) in cur.execute(f"SELECT symbol FROM intents WHERE state NOT IN ({marks})",
                                     states):
            add_symbol(symbol)
    return frozenset(seen)


def open_buy_exposure(ledger, rules: Mapping[str, MarketRules]) -> OpenBuyExposure:
    """Remaining notional of every open buy and the quote it reserves (fee and unit included)."""
    marks = ",".join("?" for _ in _OPEN_INTENT_STATES)
    with ledger.read_cursor() as cur:
        rows = cur.execute(f"SELECT {_INTENT_COLS_LITERAL} FROM intents WHERE side='buy' "
                           f"AND state IN ({marks})", _OPEN_INTENT_STATES).fetchall()
    notional = Decimal(0)
    reserved_quote = Decimal(0)
    for row in rows:
        intent = Intent(*row)
        if intent.price is None:
            raise ValueError(f"open buy intent {intent.intent_id} has no price")
        amount = Decimal(intent.amount)
        if intent.state in ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION"):
            # The order may already exist on the exchange: reserve the whole amount.
            remaining = amount
        else:  # ACKED / PARTIALLY_FILLED: the fills of its order are already done
            filled = Decimal(0)
            if intent.exchange_order_id is not None:
                filled = sum((fill.amount for fill in fills_for_order(ledger, intent.exchange_order_id)),
                             Decimal(0))
            remaining = max(Decimal(0), amount - filled)
        rule = rules.get(intent.symbol)
        if rule is None or rule.max_fee_rate is None:
            raise ValueError(f"fee rate unknown for {intent.symbol}")
        value = remaining * Decimal(intent.price)
        notional += value
        reserved_quote += value * (1 + abs(rule.max_fee_rate)) + rule.quote_unit
    return OpenBuyExposure(notional, reserved_quote)
