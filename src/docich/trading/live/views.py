"""Read-only queries over the LIVE ledger used by the executor, reconcile and balance code."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal


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
