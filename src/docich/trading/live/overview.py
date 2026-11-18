"""Allow-listed, read-only overview of the LIVE ledger for the operator WebUI.

This module is pure apart from the ledger's own read cursor.  It never writes, never
touches the filesystem, never imports an exchange and never raises: a closed, corrupt or
foreign ledger reads as ``ledger_readable: false`` with every list/dict empty.

Money is handled as :class:`~decimal.Decimal` and only converted to ``str`` at the JSON
boundary.  The returned document contains no order ids, fill ids, intent ids, position
ids, raw ledger rows, API keys or balances of assets the worker does not watch.

P&L is the cash result of the recorded fills, exactly::

    per symbol = sum(sell notional - buy notional - known fee)

A fee paid in the pair's base currency is converted at that fill's price; a fee paid in
the quote currency is subtracted directly.  A fill whose fee status is not ``known`` has
its fee treated as zero and is counted in ``pnl.fee_unknown_fills`` (also stated by
``pnl.unknown_fee_treatment``).  A known fee in a third currency cannot be valued here:
it is also treated as zero and counted in ``pnl.fee_other_currency_fills``.  An open
position's buy therefore makes the figure negative until it is sold; this view is not a
mark-to-market valuation.

Open-position cost basis comes from :func:`views.position_cost` with a zero fee rate,
because this read-only view has no market metadata to bound an unknown fee.  The
accounting is the same as the executor's; only an unknown fee is left unestimated.

Round trips are positions whose status is ``closed``; a round trip wins when its own
recorded fills have a positive cash result under the same formula.  ``since`` is the
first fill's ``executed_at`` or ``None`` when there are no fills.
"""
from __future__ import annotations

from decimal import Decimal

from . import safety, views

_ZERO = Decimal(0)
_OPEN_INTENT_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED")


def _empty(*, readable: bool) -> dict:
    """A complete empty document, so the frontend never has to guess at a missing key."""
    return {
        "ledger_readable": readable,
        "positions": [],
        "pnl": {
            "by_symbol": {},
            "total": "0",
            "round_trips": 0,
            "wins": 0,
            "since": None,
            "fee_unknown_fills": 0,
            "fee_other_currency_fills": 0,
            "unknown_fee_treatment": "zero",
        },
        "recent_fills": [],
        "open_intents": {"buy": {}, "sell": {}},
        "safety": {"trips": [], "unresolved_unknown_submissions": 0},
        "policy": {"digest": None, "symbols": []},
        "opening": [],
    }


def _symbol_parts(symbol: str) -> tuple[str, str] | None:
    parts = str(symbol).split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _fill_fee_quote(fill: views.FillRow) -> tuple[Decimal, bool, bool]:
    """(fee in quote units, counted as unknown, fee in another currency), never raises.

    ``unknown`` covers both ``fee_status != "known"`` and a known fee whose size/currency
    is missing (the schema forbids the latter, but this read must not trust the file).
    """
    if fill.fee_status != "known" or fill.fee_amount is None or fill.fee_currency is None:
        return _ZERO, True, False
    parts = _symbol_parts(fill.symbol)
    if parts is None:
        return _ZERO, False, True
    base, quote = parts
    if fill.fee_currency == quote:
        return Decimal(fill.fee_amount), False, False
    if fill.fee_currency == base:
        return Decimal(fill.fee_amount) * Decimal(fill.price), False, False
    return _ZERO, False, True


def _fill_pnl(fill: views.FillRow) -> tuple[Decimal, bool, bool]:
    """(cash P&L of one fill, fee unknown, fee in another currency)."""
    notional = Decimal(fill.quote_notional)
    signed = notional if fill.side == "sell" else -notional
    fee, unknown, other = _fill_fee_quote(fill)
    return signed - fee, unknown, other


def _dec(value: Decimal) -> str:
    return str(value)


def live_overview(ledger, *, now: float) -> dict:
    """Return the documented, JSON-safe snapshot of ``ledger``; never raises.

    ``now`` is accepted for a uniform caller signature; nothing here is time relative.
    A ledger passed as ``None`` or one whose reads fail returns ``ledger_readable: false``
    with empty collections and zero P&L.
    """
    del now  # the overview is a pure function of the ledger's recorded facts
    try:
        fills = views.all_fills(ledger)

        by_symbol: dict[str, Decimal] = {}
        fee_unknown = 0
        fee_other = 0
        for fill in fills:
            value, unknown, other = _fill_pnl(fill)
            by_symbol[fill.symbol] = by_symbol.get(fill.symbol, _ZERO) + value
            fee_unknown += 1 if unknown else 0
            fee_other += 1 if other else 0

        # Closed positions and their own cash result (same formula as the symbol P&L).
        with ledger.read_cursor() as cur:
            closed_ids = [row[0] for row in
                          cur.execute("SELECT position_id FROM positions WHERE status='closed'")]
        closed = set(closed_ids)
        per_position: dict[str, Decimal] = {}
        for fill in fills:
            if fill.position_id in closed:
                per_position[fill.position_id] = (
                    per_position.get(fill.position_id, _ZERO) + _fill_pnl(fill)[0])
        wins = sum(1 for value in per_position.values() if value > 0)

        positions = []
        for row in views.open_position_rows(ledger):
            basis = views.position_cost(ledger, row.position_id, _ZERO)
            positions.append({
                "symbol": row.symbol,
                "net_base": _dec(basis.net_base),
                "avg_cost": _dec(basis.avg_cost),
                "cost": _dec(basis.cost),
                "entry_policy_digest": row.entry_policy_digest[:12],
                "opened_at": row.opened_at,
            })

        recent = [
            {
                "at": fill.executed_at,
                "symbol": fill.symbol,
                "side": fill.side,
                "amount": str(fill.amount),
                "price": str(fill.price),
                "notional": str(fill.quote_notional),
            }
            for fill in reversed(fills[-20:])
        ]

        counts: dict[str, dict[str, int]] = {"buy": {}, "sell": {}}
        for intent in ledger.list_intents(states=_OPEN_INTENT_STATES):
            side = intent.side if intent.side in counts else None
            if side is None:
                continue
            counts[side][intent.state] = counts[side].get(intent.state, 0) + 1
        open_intents = {
            "buy": {state: counts["buy"][state] for state in sorted(counts["buy"])},
            "sell": {state: counts["sell"][state] for state in sorted(counts["sell"])},
        }

        safety_state = safety.read_safety_state(ledger)
        if not safety_state.ledger_readable:
            raise RuntimeError("safety tables are unreadable")
        safety_view = {
            "trips": [trip.reason for trip in safety_state.trips],
            "unresolved_unknown_submissions": safety_state.unresolved_unknown_submissions,
        }

        digest = ledger.active_policy_set_digest()
        symbols: list[str] = []
        if digest is not None:
            try:
                symbols = sorted(ledger.load_policy_set(digest).policies)
            except Exception:
                symbols = []

        with ledger.read_cursor() as cur:
            opening_rows = cur.execute(
                "SELECT currency, amount, occurred_at FROM cashflows "
                "WHERE cashflow_id LIKE 'auto_opening:%' ORDER BY occurred_at, cashflow_id").fetchall()
        opening = [
            {"currency": str(currency), "amount": str(Decimal(amount)), "at": float(at)}
            for currency, amount, at in opening_rows
        ]

        total = sum(by_symbol.values(), _ZERO)
        return {
            "ledger_readable": True,
            "positions": positions,
            "pnl": {
                "by_symbol": {symbol: _dec(value) for symbol, value in sorted(by_symbol.items())},
                "total": _dec(total),
                "round_trips": len(closed_ids),
                "wins": wins,
                "since": fills[0].executed_at if fills else None,
                "fee_unknown_fills": fee_unknown,
                "fee_other_currency_fills": fee_other,
                "unknown_fee_treatment": "zero",
            },
            "recent_fills": recent,
            "open_intents": open_intents,
            "safety": safety_view,
            "policy": {
                "digest": digest[:12] if digest is not None else None,
                "symbols": symbols,
            },
            "opening": opening,
        }
    except Exception:
        return _empty(readable=False)


__all__ = ["live_overview"]
