"""Read-only paper performance helpers for dashboard/narration/notifications.

All values are derived from the local PAPER ledger and public market prices.
The helpers never mutate the ledger and never access exchange credentials.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import sqlite3
from typing import Mapping

from .market_cache import load_cache
from .paper import DEFAULT_SLIPPAGE_BPS, DEFAULT_TAKER_FEE_RATE

D = Decimal
ZERO = D("0")
JST = dt.timezone(dt.timedelta(hours=9), "JST")
BPS = D("10000")
BENCHMARK_TIMEFRAME_S = 300
BENCHMARK_TOLERANCE_BARS = 2


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


def _history_points(entry: Mapping[str, object]) -> list[tuple[float, Decimal]]:
    raw = entry.get("history")
    if not isinstance(raw, list):
        return []
    points: dict[int, tuple[float, Decimal]] = {}
    for row in raw:
        if isinstance(row, Mapping):
            raw_timestamp = row.get("timestamp")
            raw_close = row.get("close")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            raw_timestamp, raw_close = row[0], row[1]
        else:
            continue
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError):
            continue
        close = _dec(raw_close)
        if not math.isfinite(timestamp) or close is None or close <= 0:
            continue
        points[int(round(timestamp))] = (timestamp, close)
    return sorted(points.values(), key=lambda item: item[0])


def _benchmark_unavailable(
    *, date: str, actual: Decimal | None, reason: str
) -> dict[str, object]:
    return {
        "status": "unavailable",
        "date": date,
        "timeframe_seconds": BENCHMARK_TIMEFRAME_S,
        "basis": "5m_close",
        "scope": "best_single_symbol_round_trip",
        "taker_fee_bps": None,
        "slippage_bps": None,
        "theoretical_pnl_jpy": None,
        "theoretical_return_pct": None,
        "actual_today_realized_pnl_jpy": None if actual is None else str(actual),
        "gap_jpy": None,
        "capture_rate_pct": None,
        "best_symbol": None,
        "best_entry_at": None,
        "best_exit_at": None,
        "best_entry_price": None,
        "best_exit_price": None,
        "observed_bars": 0,
        "observed_symbols": 0,
        "coverage_complete_to_now": False,
        "coverage_ratio": None,
        "reason": reason,
        "definition": (
            "当日5分足の終値だけを使い、1銘柄を1回だけ買って売る場合の比較用の理論値。"
            "元本全額、手数料とスリッページ込みで計算します。"
        ),
    }


def build_theoretical_benchmark(
    cache_path: Path | None,
    *,
    capital_reference: object,
    actual_today_realized: object,
    now: float,
    taker_fee_rate: object = DEFAULT_TAKER_FEE_RATE,
    slippage_bps: object = DEFAULT_SLIPPAGE_BPS,
) -> dict[str, object]:
    """Compare today's realized PAPER P/L with a bounded hindsight benchmark.

    The benchmark is deliberately narrow: it picks the best single symbol and
    one buy-then-sell round trip from timestamped 5-minute closes observed
    during the current JST day.  It is not a backtest, does not combine
    symbols, and never claims that intrabar prices were available.
    """
    moment = float(now)
    date = dt.datetime.fromtimestamp(moment, tz=dt.timezone.utc).astimezone(JST).date().isoformat()
    actual = _dec(actual_today_realized)
    capital = _dec(capital_reference)
    if cache_path is None:
        return _benchmark_unavailable(date=date, actual=actual, reason="history_not_configured")
    if capital is None or capital <= 0:
        return _benchmark_unavailable(date=date, actual=actual, reason="capital_unavailable")

    fee = _dec(taker_fee_rate)
    slippage = _dec(slippage_bps)
    if (
        fee is None or slippage is None or fee < 0 or fee >= 1
        or slippage < 0 or slippage >= BPS
    ):
        return _benchmark_unavailable(date=date, actual=actual, reason="execution_cost_unavailable")

    try:
        cache = load_cache(Path(cache_path))
    except (OSError, ValueError):
        cache = {}
    if not cache:
        return _benchmark_unavailable(date=date, actual=actual, reason="intraday_history_unavailable")

    day_start = _day_start_epoch(moment)
    day_end = day_start + 24 * 60 * 60
    target_end = min(moment, day_end)
    buy_factor = (D("1") + slippage / BPS) * (D("1") + fee)
    sell_factor = (D("1") - slippage / BPS) * (D("1") - fee)
    best: dict[str, object] | None = None
    best_complete: dict[str, object] | None = None
    observed_bars = 0
    observed_symbols = 0
    history_symbols = 0

    for symbol, entry in sorted(cache.items()):
        if not isinstance(entry, Mapping):
            continue
        try:
            timeframe_s = int(entry.get("timeframe_s", BENCHMARK_TIMEFRAME_S))
        except (TypeError, ValueError):
            continue
        if timeframe_s != BENCHMARK_TIMEFRAME_S:
            continue
        points = [
            (timestamp, close)
            for timestamp, close in _history_points(entry)
            if day_start <= timestamp <= target_end + 1
        ]
        if points:
            history_symbols += 1
        if len(points) < 2:
            continue
        observed_symbols += 1
        observed_bars += len(points)
        first_timestamp = points[0][0]
        last_timestamp = points[-1][0]
        elapsed = max(float(timeframe_s), target_end - day_start)
        covered = max(0.0, min(target_end, last_timestamp) - max(day_start, first_timestamp))
        coverage_ratio = min(1.0, covered / elapsed)
        coverage_complete = (
            first_timestamp <= day_start + timeframe_s * BENCHMARK_TOLERANCE_BARS
            and last_timestamp >= target_end - timeframe_s * BENCHMARK_TOLERANCE_BARS
        )

        minimum: tuple[float, Decimal] | None = None
        symbol_best: dict[str, object] | None = None
        for timestamp, price in points:
            if minimum is not None:
                entry_timestamp, entry_price = minimum
                return_fraction = (price * sell_factor) / (entry_price * buy_factor) - D("1")
                if symbol_best is None or return_fraction > symbol_best["return_fraction"]:
                    symbol_best = {
                        "return_fraction": return_fraction,
                        "entry_timestamp": entry_timestamp,
                        "entry_price": entry_price,
                        "exit_timestamp": timestamp,
                        "exit_price": price,
                    }
            if minimum is None or price < minimum[1]:
                minimum = (timestamp, price)
        if symbol_best is None:
            continue
        symbol_best.update({
            "symbol": str(symbol),
            "coverage_complete": coverage_complete,
            "coverage_ratio": coverage_ratio,
            "bars": len(points),
        })
        if best is None or symbol_best["return_fraction"] > best["return_fraction"]:
            best = symbol_best
        if coverage_complete and (
            best_complete is None
            or symbol_best["return_fraction"] > best_complete["return_fraction"]
        ):
            best_complete = symbol_best

    best = best_complete or best
    if best is None:
        reason = "intraday_history_too_short" if history_symbols else "intraday_history_unavailable"
        return _benchmark_unavailable(date=date, actual=actual, reason=reason)

    return_fraction = max(ZERO, best["return_fraction"])
    theoretical_pnl = capital * return_fraction
    theoretical_return_pct = return_fraction * D("100")
    gap = None if actual is None else theoretical_pnl - actual
    capture = (
        None if actual is None or theoretical_pnl <= 0
        else actual / theoretical_pnl * D("100")
    )
    coverage_ratio = float(best["coverage_ratio"])
    return {
        "status": "ready" if bool(best["coverage_complete"]) else "partial",
        "date": date,
        "timeframe_seconds": BENCHMARK_TIMEFRAME_S,
        "basis": "5m_close",
        "scope": "best_single_symbol_round_trip",
        "taker_fee_bps": str(fee * BPS),
        "slippage_bps": str(slippage),
        "theoretical_pnl_jpy": str(theoretical_pnl),
        "theoretical_return_pct": str(theoretical_return_pct),
        "actual_today_realized_pnl_jpy": None if actual is None else str(actual),
        "gap_jpy": None if gap is None else str(gap),
        "capture_rate_pct": None if capture is None else str(capture),
        "best_symbol": best["symbol"],
        "best_entry_at": float(best["entry_timestamp"]),
        "best_exit_at": float(best["exit_timestamp"]),
        "best_entry_price": str(best["entry_price"]),
        "best_exit_price": str(best["exit_price"]),
        "observed_bars": observed_bars,
        "observed_symbols": observed_symbols,
        "coverage_complete_to_now": bool(best["coverage_complete"]),
        "coverage_ratio": coverage_ratio,
        "reason": "partial_intraday_coverage" if not best["coverage_complete"] else "",
        "definition": (
            "当日5分足の終値だけを使い、1銘柄を1回だけ買って売る場合の比較用の理論値。"
            "元本全額、手数料とスリッページ込みで計算します。"
        ),
    }


def build_performance(
    db_path: Path,
    *,
    capital_reference: object,
    positions: Mapping[str, object],
    prices: Mapping[str, object],
    now: float,
    market_cache_path: Path | None = None,
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
    benchmark = build_theoretical_benchmark(
        market_cache_path,
        capital_reference=capital,
        actual_today_realized=realized_today,
        now=now,
    )
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
        "theoretical_benchmark": benchmark,
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


def _signal_context(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        import json
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_round_trips(db_path: Path, *, limit: int = 8) -> list[dict[str, object]]:
    """Pair buys and sells into realized round trips for the corner review.

    Average-cost matching, the same basis as realized P/L. Each record carries
    the entry and exit reason/context so the narration can judge whether the
    decision was right and hand the lesson to strategy improvement.
    """
    target = Path(db_path)
    if not target.is_file() or limit <= 0:
        return []
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error):
        return []
    try:
        raw = conn.execute(
            """SELECT symbol, side, amount, price, filled_at, reason_code, signal_context,
                      strategy_id
                 FROM paper_fills ORDER BY filled_at, rowid"""
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    lots: dict[str, list[object]] = {}
    trips: list[dict[str, object]] = []
    for symbol, side, amount_text, price_text, filled_at, reason_code, context_text, strategy_id in raw:
        amount = _dec(amount_text)
        price = _dec(price_text)
        try:
            stamp = float(filled_at)
        except (TypeError, ValueError):
            continue
        if amount is None or price is None or amount <= 0 or price <= 0:
            continue
        symbol = str(symbol)
        state = lots.setdefault(symbol, [ZERO, ZERO, None, None, None])
        held, cost = state[0], state[1]
        if side == "buy":
            if held <= 0:
                # Opening buy: remember the entry context for the round trip.
                state[2] = stamp
                state[3] = str(reason_code)
                state[4] = _signal_context(context_text)
            lots[symbol][0] = held + amount
            lots[symbol][1] = cost + amount * price
            continue
        if side != "sell" or held <= 0:
            continue
        sold = min(amount, held)
        average = cost / held
        realized = sold * (price - average)
        trips.append(
            {
                "symbol": symbol,
                "amount": str(sold),
                "entry_price": str(average),
                "exit_price": str(price),
                "realized_jpy": str(realized),
                "opened_at": state[2],
                "closed_at": stamp,
                "hold_sec": None if state[2] is None else max(0.0, stamp - float(state[2])),
                "entry_reason": state[3],
                "entry_signal": state[4],
                "exit_reason": str(reason_code),
                "exit_signal": _signal_context(context_text),
                "strategy_id": str(strategy_id),
            }
        )
        lots[symbol][0] = held - sold
        lots[symbol][1] = max(ZERO, cost - average * sold)
        if lots[symbol][0] <= 0:
            lots[symbol][2] = None
            lots[symbol][3] = None
            lots[symbol][4] = None
    trips.sort(key=lambda item: float(item.get("closed_at") or 0.0), reverse=True)
    return trips[: int(limit)]


def recent_orders(db_path: Path, *, limit: int = 6) -> list[dict[str, object]]:
    """Read-only allowlist of the most recent paper orders.

    PAPER executes immediately, so an order and its fill share an
    opportunity, but they are distinct ledger records (decision vs execution).
    """
    target = Path(db_path)
    if not target.is_file() or limit <= 0:
        return []
    try:
        uri = f"file:{target.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error):
        return []
    try:
        raw = conn.execute(
            """SELECT opportunity_id, strategy_id, symbol, side, quote, amount, price,
                      quote_notional, reference_notional, reason_code, created_at
                 FROM paper_orders ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    orders: list[dict[str, object]] = []
    for row in raw:
        try:
            created = float(row[10])
        except (TypeError, ValueError):
            continue
        orders.append(
            {
                "opportunity_id": str(row[0]),
                "strategy_id": str(row[1]),
                "symbol": str(row[2]),
                "side": str(row[3]),
                "quote": str(row[4]),
                "amount": str(row[5]),
                "price": str(row[6]),
                "quote_notional": str(row[7]),
                "reference_notional": str(row[8]),
                "reason_code": str(row[9]),
                "created_at": created,
            }
        )
    return orders
