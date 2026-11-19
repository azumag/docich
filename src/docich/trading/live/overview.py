"""Allow-listed, read-only overview of the LIVE ledger for the operator WebUI.

This module is pure apart from the ledger's own read cursor.  It never writes, never
touches the filesystem, never imports an exchange and never raises: a closed, corrupt or
foreign ledger reads as ``ledger_readable: false`` with every list/dict empty.

Money is handled as :class:`~decimal.Decimal` and only converted to ``str`` at the JSON
boundary.  The returned document contains no order ids, fill ids, intent ids, position
ids, raw ledger rows, API keys or balances of assets the worker does not watch.

The ``pnl`` view is the net cash result of all recorded fills (the contract calls the
formula "P&L"; the UI labels it 約定による純収支), exactly::

    per symbol = sum(sell notional - buy notional - known fee)

It is deliberately a cash-flow view, not a completed-trade profit/loss figure: open buys
are included too, so an open position's buy lowers the total until it is sold.  It is also
not a mark-to-market valuation.  A fee paid in the pair's base currency is converted at
that fill's price; a fee paid in the quote currency is subtracted directly.  A fill whose
fee status is not
``known`` has its fee treated as zero and is counted in ``pnl.fee_unknown_fills`` (also
stated by ``pnl.unknown_fee_treatment``).  A known fee in a third currency cannot be
valued here: it is also treated as zero and counted in
``pnl.fee_other_currency_fills``.

``trade_pnl`` is the realized result of finished sells, replayed per position with the
same average-cost code as :func:`views.position_cost` (a numeric trade id breaks exact
timestamp ties).  A sell order is one paged row; its fills are summed.  A sell consumes
the basis allocated to the sold base (including a base fee paid from inventory), and its
proceeds are reduced by a known quote fee.  Buy-only, open, unfilled, cashflow and opening
rows never enter it.  An unknown or third-currency fee is zero for the estimate and is
counted/marked; a sale whose inventory cannot be proved is surfaced but excluded from the
numeric totals instead of being given a zero basis.

Open-position cost basis comes from :func:`views.position_cost` with a zero fee rate,
because this read-only view has no market metadata to bound an unknown fee.  The
accounting is the same as the executor's; only an unknown fee is left unestimated.

Round trips are positions whose status is ``closed`` *and* whose own recorded fills
contain a funded buy and a funded sell; a position closed without a completed buy/sell
trip (for example an unfilled buy that was cancelled) is not a round trip and never
enters the win denominator.  A round trip wins when those fills have a positive cash
result under the same formula.  The cash-result figures still include every recorded
fill, so a buy-only closed position keeps its cash-result loss even though it is not a
round trip.  ``since`` is the first fill's ``executed_at`` or ``None`` when there are no
fills.
"""
from __future__ import annotations

from decimal import Decimal

from . import safety, views

_ZERO = Decimal(0)
_OPEN_INTENT_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED")
_TRADE_PAGE_SIZE = 20


def _empty_trade_pnl(*, available: bool) -> dict:
    return {
        "available": available,
        "total": "0" if available else None,
        "by_symbol": {},
        "rows": [],
        "page": 1,
        "page_count": 1,
        "page_size": _TRADE_PAGE_SIZE,
        "total_rows": 0,
        "fee_unknown_fills": 0,
        "fee_other_currency_fills": 0,
        "unknown_fee_treatment": "zero",
        "unaccounted_orders": 0,
        "unaccounted_fills": 0,
        "complete": False if not available else True,
    }


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
        "trade_pnl": _empty_trade_pnl(available=readable),
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
    """(cash result of one fill, fee unknown, fee in another currency)."""
    notional = Decimal(fill.quote_notional)
    signed = notional if fill.side == "sell" else -notional
    fee, unknown, other = _fill_fee_quote(fill)
    return signed - fee, unknown, other


def _is_funded_round_trip(fills: list[views.FillRow]) -> bool:
    """True when these fills contain both a funded buy and a funded sell.

    A position row is created before its buy is sent, so a cancelled, never-filled buy
    leaves a ``closed`` position with no fills at all.  Counting that row (or a closed
    buy-only position) as a round trip would make the win denominator lie.  A fill is
    "funded" when it moved a positive base amount at a positive quote notional.
    """
    sides: set[str] = set()
    for fill in fills:
        if fill.amount > 0 and fill.quote_notional > 0:
            sides.add(fill.side)
    return "buy" in sides and "sell" in sides


def _dec(value: Decimal) -> str:
    return str(value)


def _trade_page_number(value) -> int:
    """A 1-based page request; anything unparseable or below 1 means page 1."""
    if isinstance(value, bool):
        return 1
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _trade_fee_flags(fill: views.FillRow) -> tuple[bool, bool]:
    """(fee unknown, fee in a third currency), from the same policy as the cash view."""
    if fill.fee_status != "known" or fill.fee_amount is None or fill.fee_currency is None:
        return True, False
    parts = _symbol_parts(fill.symbol)
    if parts is None:
        return False, True
    base, quote = parts
    if fill.fee_currency in (base, quote):
        return False, False
    return False, True


def _sell_quote_fee(fill: views.FillRow) -> Decimal:
    """A known quote fee on a sell, otherwise zero (the cash-view estimate policy)."""
    if fill.fee_status != "known" or fill.fee_amount is None or fill.fee_currency is None:
        return _ZERO
    parts = _symbol_parts(fill.symbol)
    if parts is None:
        return _ZERO
    return Decimal(fill.fee_amount) if fill.fee_currency == parts[1] else _ZERO


def _sold_base(fill: views.FillRow) -> Decimal:
    """Base inventory a sell consumes, including a known base fee paid from inventory."""
    sold = Decimal(fill.amount)
    parts = _symbol_parts(fill.symbol)
    if (parts is not None and fill.fee_status == "known" and fill.fee_amount is not None
            and fill.fee_currency == parts[0]):
        sold += Decimal(fill.fee_amount)
    return sold


def _trade_row(rows: dict, fill: views.FillRow) -> dict:
    row = rows.get(fill.exchange_order_id)
    if row is None:
        row = {
            "_order_id": fill.exchange_order_id,
            "at": fill.executed_at,
            "symbol": fill.symbol,
            "amount": _ZERO,
            "fills": 0,
            "accounted": True,
            "unaccounted_reason": None,
            "fee_unknown": False,
            "fee_other_currency": False,
            "pnl": _ZERO,
        }
        rows[fill.exchange_order_id] = row
    if fill.executed_at > row["at"]:
        row["at"] = fill.executed_at
    if fill.symbol != row["symbol"]:
        row["accounted"] = False
        row["unaccounted_reason"] = "mixed_symbol"
    row["amount"] += Decimal(fill.amount)
    row["fills"] += 1
    return row


def _trade_pnl(fills: list[views.FillRow], known_positions: set[str], *, page) -> dict:
    """Realized sell P&L, replayed per position with :func:`views.cost_basis_steps`.

    A sell order is one row.  If any of its fills cannot be proved against a preceding
    average-cost basis (unlinked position, missing/excess inventory), the whole order is
    surfaced as unaccounted and contributes nothing to ``total``/``by_symbol``.
    """
    by_position: dict[str | None, list[views.FillRow]] = {}
    for fill in fills:
        parts = _symbol_parts(fill.symbol)
        if parts is None or parts[1] != "JPY":
            continue
        if fill.position_id is None and fill.side != "sell":
            continue
        by_position.setdefault(fill.position_id, []).append(fill)

    rows: dict[str, dict] = {}
    unknown_fee_fills: set[str] = set()
    other_fee_fills: set[str] = set()

    for position_id, position_fills in by_position.items():
        if position_id is None:
            # Unlinked orders carry no inventory to replay, so their fills need no ordering.
            for fill in position_fills:
                if fill.side != "sell":
                    continue
                row = _trade_row(rows, fill)
                row["accounted"] = False
                row["unaccounted_reason"] = row["unaccounted_reason"] or "unlinked_position"
                own_unknown, own_other = _trade_fee_flags(fill)
                row["fee_unknown"] = row["fee_unknown"] or own_unknown
                row["fee_other_currency"] = row["fee_other_currency"] or own_other
                if own_unknown:
                    unknown_fee_fills.add(fill.fill_id)
                if own_other:
                    other_fee_fills.add(fill.fill_id)
            continue

        before = views.CostBasis(_ZERO, _ZERO, _ZERO, None)
        cycle_unknown: set[str] = set()
        cycle_other: set[str] = set()
        broken = False
        for fill, after in views.cost_basis_steps(position_fills, _ZERO):
            own_unknown, own_other = _trade_fee_flags(fill)
            if fill.side == "buy":
                if before.net_base <= 0:
                    broken = False
                    cycle_unknown.clear()
                    cycle_other.clear()
                if own_unknown:
                    cycle_unknown.add(fill.fill_id)
                if own_other:
                    cycle_other.add(fill.fill_id)
                before = after
                continue

            row = _trade_row(rows, fill)
            if cycle_unknown or own_unknown:
                row["fee_unknown"] = True
                unknown_fee_fills.update(cycle_unknown)
                if own_unknown:
                    unknown_fee_fills.add(fill.fill_id)
            if cycle_other or own_other:
                row["fee_other_currency"] = True
                other_fee_fills.update(cycle_other)
                if own_other:
                    other_fee_fills.add(fill.fill_id)

            reason = None
            if position_id not in known_positions:
                reason = "missing_position"
            elif broken:
                reason = "basis_unavailable"
            elif before.net_base <= 0:
                reason = "missing_basis"
            elif _sold_base(fill) > before.net_base:
                reason = "excess_basis"

            if reason is None:
                if row["accounted"]:
                    row["pnl"] += (Decimal(fill.quote_notional) - _sell_quote_fee(fill)
                                   - (before.cost - after.cost))
            else:
                row["accounted"] = False
                row["unaccounted_reason"] = row["unaccounted_reason"] or reason
                broken = True
            before = after

    ordered_rows = sorted(rows.values(), key=lambda row: (row["at"], row["_order_id"]), reverse=True)
    unaccounted = [row for row in ordered_rows if not row["accounted"]]
    total_rows = len(ordered_rows)
    page_count = max(1, (total_rows + _TRADE_PAGE_SIZE - 1) // _TRADE_PAGE_SIZE)
    page_number = min(_trade_page_number(page), page_count)
    start = (page_number - 1) * _TRADE_PAGE_SIZE
    paged = ordered_rows[start:start + _TRADE_PAGE_SIZE]

    by_symbol: dict[str, Decimal] = {}
    total = _ZERO
    accounted = 0
    for row in ordered_rows:
        if row["accounted"]:
            accounted += 1
            by_symbol[row["symbol"]] = by_symbol.get(row["symbol"], _ZERO) + row["pnl"]
            total += row["pnl"]

    return {
        "available": True,
        # No sales is a valid zero; sales that all lack a provable basis are not a zero result.
        "total": _dec(total) if (not total_rows or accounted) else None,
        "by_symbol": {symbol: _dec(value) for symbol, value in sorted(by_symbol.items())},
        "rows": [
            {
                "at": float(row["at"]),
                "symbol": row["symbol"],
                "amount": _dec(row["amount"]),
                "pnl": _dec(row["pnl"]) if row["accounted"] else None,
                "status": "accounted" if row["accounted"] else "unaccounted",
                "unaccounted_reason": row["unaccounted_reason"],
                "fee_unknown": bool(row["fee_unknown"]),
                "fee_other_currency": bool(row["fee_other_currency"]),
                "fills": int(row["fills"]),
            }
            for row in paged
        ],
        "page": page_number,
        "page_count": page_count,
        "page_size": _TRADE_PAGE_SIZE,
        "total_rows": total_rows,
        "fee_unknown_fills": len(unknown_fee_fills),
        "fee_other_currency_fills": len(other_fee_fills),
        "unknown_fee_treatment": "zero",
        "unaccounted_orders": len(unaccounted),
        "unaccounted_fills": sum(row["fills"] for row in unaccounted),
        "complete": not unaccounted,
    }


def live_overview(ledger, *, now: float, trade_page=1) -> dict:
    """Return the documented, JSON-safe snapshot of ``ledger``; never raises.

    ``now`` is accepted for a uniform caller signature; nothing here is time relative.
    ``trade_page`` is a 1-based realized-P&L page; invalid or out-of-range values are
    clamped to the first/last page.  A ledger passed as ``None`` or one whose reads fail
    returns ``ledger_readable: false`` with empty collections and zero cash result.
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

        # Closed positions, the fills that belong to them, and their own cash result
        # (same formula as the symbol cash result).  The round-trip denominator is not every
        # closed row: an unfilled buy that was cancelled closes a row with no fills.
        with ledger.read_cursor() as cur:
            position_rows = cur.execute("SELECT position_id, status FROM positions").fetchall()
        known_positions = {row[0] for row in position_rows}
        closed = {row[0] for row in position_rows if row[1] == "closed"}
        closed_fills: dict[str, list[views.FillRow]] = {}
        for fill in fills:
            if fill.position_id in closed:
                closed_fills.setdefault(fill.position_id, []).append(fill)
        per_position = {
            position_id: sum((_fill_pnl(fill)[0] for fill in rows), _ZERO)
            for position_id, rows in closed_fills.items()
        }
        trips = [position_id for position_id, rows in closed_fills.items()
                 if _is_funded_round_trip(rows)]
        wins = sum(1 for position_id in trips if per_position[position_id] > 0)

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
        trade_pnl = _trade_pnl(fills, known_positions, page=trade_page)
        return {
            "ledger_readable": True,
            "positions": positions,
            "trade_pnl": trade_pnl,
            "pnl": {
                "by_symbol": {symbol: _dec(value) for symbol, value in sorted(by_symbol.items())},
                "total": _dec(total),
                "round_trips": len(trips),
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
