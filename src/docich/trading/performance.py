"""Read-only paper performance helpers for dashboard/narration/notifications.

All values are derived from the local PAPER ledger and public market prices.
The helpers never mutate the ledger and never access exchange credentials.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Mapping

D = Decimal
ZERO = D("0")
JST = dt.timezone(dt.timedelta(hours=9), "JST")


def _dec(value: object) -> Decimal | None:
    try:
        result = value if isinstance(value, Decimal) else D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _rows(db_path: Path) -> list[tuple[str, str, str, Decimal, Decimal, float]]:
    target = Path(db_path)
    if not target.is_file():
        return []
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error):
        return []
    try:
        raw = conn.execute(
            """SELECT fill_id, symbol, side, amount, price, filled_at
                 FROM paper_fills ORDER BY filled_at, rowid"""
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    result: list[tuple[str, str, str, Decimal, Decimal, float]] = []
    for fill_id, symbol, side, amount_text, price_text, filled_at in raw:
        amount = _dec(amount_text)
        price = _dec(price_text)
        try:
            stamp = float(filled_at)
        except (TypeError, ValueError):
            continue
        if amount is None or price is None or amount <= 0 or price <= 0:
            continue
        result.append((str(fill_id), str(symbol), str(side), amount, price, stamp))
    return result


def _day_start_epoch(now: float) -> float:
    moment = dt.datetime.fromtimestamp(float(now), tz=dt.timezone.utc).astimezone(JST)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def build_performance(
    db_path: Path,
    *,
    capital_reference: object,
    positions: Mapping[str, object],
    prices: Mapping[str, object],
    now: float,
) -> dict[str, object]:
    """Return bounded JPY P/L facts using average-cost accounting.

    ``cumulative_pnl_jpy`` includes realized + current unrealized P/L only
    when every open position has both a usable public price and ledger cost
    basis. ``today_realized`` is exact for fills since JST midnight and is
    intentionally labelled as realized-only; we do not invent a midnight
    mark-to-market baseline.
    """
    capital = _dec(capital_reference)
    day_start = _day_start_epoch(now)
    lots: dict[str, list[Decimal]] = {}
    realized_total = ZERO
    realized_today = ZERO

    for _fill_id, symbol, side, amount, price, filled_at in _rows(db_path):
        held, cost = lots.setdefault(symbol, [ZERO, ZERO])
        if side == "buy":
            lots[symbol][0] = held + amount
            lots[symbol][1] = cost + amount * price
            continue
        if side != "sell" or held <= 0:
            continue
        sold = min(amount, held)
        average = cost / held
        pnl = sold * (price - average)
        realized_total += pnl
        if filled_at >= day_start:
            realized_today += pnl
        lots[symbol][0] = held - sold
        lots[symbol][1] = max(ZERO, cost - average * sold)

    position_rows: list[dict[str, object]] = []
    unrealized = ZERO
    priced = 0
    valued = 0
    total_positions = 0
    for symbol, raw_amount in positions.items():
        amount = _dec(raw_amount)
        if amount is None or amount == 0:
            continue
        total_positions += 1
        held, cost = lots.get(str(symbol), [amount, ZERO])
        # Prefer ledger amount for cost-basis math but never silently replace a
        # public-status position when a malformed/legacy ledger differs.
        basis_amount = held if held > 0 else amount
        average = (cost / basis_amount) if basis_amount > 0 and cost > 0 else None
        price = _dec(prices.get(str(symbol)))
        market_value = None
        position_pnl = None
        if price is not None and price > 0:
            priced += 1
            market_value = amount * price
            if average is not None:
                valued += 1
                position_pnl = amount * (price - average)
                unrealized += position_pnl
        position_rows.append(
            {
                "symbol": str(symbol),
                "amount": str(amount),
                "avg_cost": None if average is None else str(average),
                "last_price": None if price is None else str(price),
                "market_value_jpy": None if market_value is None else str(market_value),
                "unrealized_pnl_jpy": None if position_pnl is None else str(position_pnl),
            }
        )

    position_rows.sort(
        key=lambda item: _dec(item.get("market_value_jpy")) or ZERO,
        reverse=True,
    )
    complete = total_positions == valued
    cumulative = realized_total + unrealized if complete else None
    equity = None if capital is None or cumulative is None else capital + cumulative
    return {
        "as_of": float(now),
        "complete": bool(complete),
        "position_count": total_positions,
        "priced_positions": priced,
        "valued_positions": valued,
        "realized_total_jpy": str(realized_total),
        "today_realized_pnl_jpy": str(realized_today),
        "unrealized_pnl_jpy": str(unrealized) if complete else None,
        "cumulative_pnl_jpy": None if cumulative is None else str(cumulative),
        "equity_jpy": None if equity is None else str(equity),
        "positions": position_rows,
    }


def realized_pnl_for_fill(db_path: Path, fill_id: str) -> Decimal | None:
    """Return realized P/L for one sell fill, or None for buys/unknown rows."""
    target = str(fill_id)
    lots: dict[str, list[Decimal]] = {}
    for current_id, symbol, side, amount, price, _filled_at in _rows(db_path):
        held, cost = lots.setdefault(symbol, [ZERO, ZERO])
        if side == "buy":
            lots[symbol][0] = held + amount
            lots[symbol][1] = cost + amount * price
            if current_id == target:
                return None
            continue
        if side != "sell" or held <= 0:
            if current_id == target:
                return None
            continue
        sold = min(amount, held)
        average = cost / held
        pnl = sold * (price - average)
        lots[symbol][0] = held - sold
        lots[symbol][1] = max(ZERO, cost - average * sold)
        if current_id == target:
            return pnl
    return None
