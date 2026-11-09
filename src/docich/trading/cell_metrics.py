"""Read-only per-cell (experiment x symbol) realized P/L aggregation for PAPER.

Stage (1) of docich#3623.  Nothing here is wired into the worker, experiment
rotation, early stop, ``promotion_ready`` or ``status.json``; it only reads the
private PAPER ledger and returns facts.

Accounting
----------
* One shared average-cost pool per symbol, replaying every fill in
  ``ORDER BY filled_at, rowid`` order with the same arithmetic as
  ``performance.build_performance``.  Sells of any strategy (lab or not) consume
  inventory, so the pool stays correct; only ``lab-exit:<experiment>:...``
  sells are *attributed* to a cell ``(experiment_id, symbol)``.
* ``realized_pnl_jpy`` is the P/L as recorded in the ledger.  Summed over all
  cells plus ``non_cell_realized_pnl_jpy`` it equals ``realized_total_jpy`` of
  ``build_performance`` (the result carries both for reconciliation).
* ``carry_in_fraction`` is the share of a cell's sold quantity that came from
  inventory bought by a *different* experiment (or by a non-lab strategy).
  Origin is tracked per symbol and consumed proportionally on every sell, the
  same method as ``strategy_metrics`` (``proportional_average_cost``).
  Inventory bought by an earlier activation of the *same* experiment id counts
  as that experiment's own.

Buy-fee correction
------------------
Fee history of PAPER buy fills (by ``cost_context``):

* ``cost_context`` NULL (before PR #1905 introduced the cost model; 2026-09-09
  .. 2026-10-07 JST in production): the broker applied ``DEFAULT_TAKER_FEE_RATE``
  (0.0012) to *both* sides, ``effective = price*(1+slip)*(1+fee)`` (see commits
  3a4d750656, 1600cd8a7c, a20fad6083: ``PaperBroker(ledger)`` with the default
  rate at every call site).  Those buy prices already include the fee.
* ``paper-cost-v1`` (#1905, 2026-10-07 on): the fee came from the market's
  ``taker_fee_rate_base``, which bitbank publishes as 0, so buys were recorded
  with ``fee_rate`` "0" and carry *no* fee.  This is the bug being corrected.
* ``paper-cost-v2`` (PR #3744): buys charge ``max(quote, base)`` and already
  include the fee in the fill price.

``corrected_*`` figures re-price only the fee-free buys as ``price * (1 + rate)``;
sell fills are never changed.  The decision is made per buy fill:

1. ``cost_context`` absent (NULL / column missing): pre-#1905 fill that already
   carries the 0.0012 fee -> do NOT correct (counted in
   ``legacy_no_cost_context``, a subset of ``already_fee_bearing``).
2. ``cost_context`` present but not a JSON object, or without a parseable
   non-negative ``fee_rate`` -> the ledger is *not* aggregated
   (``invalid`` / ``cost_context_invalid``): we cannot tell whether the fee is
   in the price.
3. recorded ``fee_rate`` > 0 -> the fee is already in the fill price -> do not
   correct (any model version).
4. recorded ``fee_rate`` == 0 and model ``paper-cost-vN`` with N >= 2 -> zero is
   the deliberate v2 result (market publishes zero on both sides) -> do not
   correct.
5. recorded ``fee_rate`` == 0 and model exactly ``paper-cost-v1`` -> the known
   free-buy bug -> correct.
6. recorded ``fee_rate`` == 0 and any other / missing model -> fail closed
   (``invalid`` / ``cost_context_unknown_model``) rather than guess.

Rate used when correcting: a recorded quote-side market fee when the context
carries one (``fee_rate_quote`` / ``taker_fee_rate_quote``, > 0); otherwise
0.0010 for ``BTC/JPY`` and 0.0012 for other pairs.  Slippage is not touched.
Buys recorded with a non-zero base fee lower than the quote fee are left as
recorded (not topped up).

Return blocks
-------------
For each closed cell sell ``r = pnl / cost`` with ``cost = sold * average
cost`` of the pool at the sale.  Sells whose recorded cost basis is <= 1 JPY
(dust buys) are excluded from the blocks (and counted in
``dust_excluded_sells``).  Blocks are grouped by JST calendar day of
``filled_at`` and carry the count and mean ``r`` (recorded and
fee-corrected).

Failure policy (as ``strategy_metrics``)
----------------------------------------
A missing/unreadable ledger or any malformed fill row returns an explicit
non-``ok`` status with ``cells = None``; it is never an empty success.  An
empty but valid ledger returns ``status = "ok"`` and ``cells = []``.  A sell
larger than the held quantity (an incomplete ledger basis), by *any* strategy,
makes the status ``partial`` (``ignored_unpaired_sells``); cells are still
returned.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, DecimalException
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from .paper import DEFAULT_TAKER_FEE_RATE

D = Decimal
ZERO = D("0")
ONE = D("1")
JST = dt.timezone(dt.timedelta(hours=9), "JST")

SCHEMA_VERSION = 1
FEE_AWARE_MIN_MODEL_VERSION = 2  # paper-cost-v2: buys already carry the fee
FEE_FREE_BUY_MODEL = "paper-cost-v1"  # buys recorded fee_rate 0 and carry no fee
BTC_JPY_BUY_FEE_RATE = D("0.0010")
JPY_PAIR_BUY_FEE_RATE = D("0.0012")
DUST_COST_THRESHOLD_JPY = D("1")
CELL_EXIT_PREFIX = "lab-exit"
CELL_ENTRY_PREFIX = "lab"

_MODEL_RE = re.compile(r"^paper-cost-v(\d+)$")
_QUOTE_FEE_KEYS = ("fee_rate_quote", "taker_fee_rate_quote")


class _CellMetricsError(Exception):
    def __init__(self, status: str, reason_code: str):
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def experiment_id_from_strategy_id(strategy_id: str, prefix: str) -> str | None:
    """Return the experiment id of ``<prefix>:<experiment>:<rule...>`` or None."""
    parts = strategy_id.split(":")
    if len(parts) >= 3 and parts[0] == prefix and parts[1]:
        return parts[1]
    return None


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("not a number")
    number = value if isinstance(value, Decimal) else D(str(value))
    if not number.is_finite():
        raise ValueError("not finite")
    return number


def buy_fee_correction_rate(symbol: str, cost_context_text: object) -> Decimal | None:
    """Return the buy fee rate to add for this buy fill, or None for no correction.

    Raises ``_CellMetricsError`` when ``cost_context`` is present but unusable.
    """
    if cost_context_text is None:
        return None  # pre-#1905 fill: the 0.0012 fee is already in the price
    try:
        context = json.loads(str(cost_context_text))
        if not isinstance(context, dict):
            raise ValueError("cost_context is not an object")
        recorded = _decimal(context["fee_rate"])
        if recorded < 0:
            raise ValueError("negative fee_rate")
    except (KeyError, TypeError, ValueError, DecimalException) as exc:
        raise _CellMetricsError("invalid", "cost_context_invalid") from exc
    if recorded > 0:
        return None
    model = context.get("model")
    if model == FEE_FREE_BUY_MODEL:
        return _fallback_rate(symbol, context)
    match = _MODEL_RE.match(model) if isinstance(model, str) else None
    if match is not None and int(match.group(1)) >= FEE_AWARE_MIN_MODEL_VERSION:
        return None
    raise _CellMetricsError("invalid", "cost_context_unknown_model")


def _fallback_rate(symbol: str, context: dict) -> Decimal:
    for key in _QUOTE_FEE_KEYS:
        if key in context:
            try:
                rate = _decimal(context[key])
            except (ValueError, DecimalException):
                continue
            if 0 < rate < 1:
                return rate
    if symbol == "BTC/JPY":
        return BTC_JPY_BUY_FEE_RATE
    if symbol.endswith("/JPY"):
        return JPY_PAIR_BUY_FEE_RATE
    return DEFAULT_TAKER_FEE_RATE


class _Pool:
    """Average-cost pool of one symbol (recorded and fee-corrected bases)."""

    __slots__ = ("held", "cost", "corrected_cost", "origins")

    def __init__(self) -> None:
        self.held = ZERO
        self.cost = ZERO
        self.corrected_cost = ZERO
        self.origins: dict[str | None, Decimal] = {}


class _Cell:
    __slots__ = (
        "closed", "wins", "corrected_wins", "pnl", "corrected_pnl", "sold_qty",
        "carry_qty", "dust", "days",
    )

    def __init__(self) -> None:
        self.closed = 0
        self.wins = 0
        self.corrected_wins = 0
        self.pnl = ZERO
        self.corrected_pnl = ZERO
        self.sold_qty = ZERO
        self.carry_qty = ZERO
        self.dust = 0
        # day -> [count, sum r, sum corrected r]
        self.days: dict[str, list] = {}


def _validated(raw: tuple) -> tuple[str, str, str, Decimal, Decimal, float, object]:
    try:
        symbol, side, strategy_id, amount, price, filled_at, cost_context = raw
        stamp = float(filled_at)
        quantity = _decimal(amount)
        unit_price = _decimal(price)
        if (
            not isinstance(symbol, str) or not symbol.strip()
            or not isinstance(strategy_id, str) or not strategy_id.strip()
            or side not in {"buy", "sell"}
            or not math.isfinite(stamp) or stamp < 0
            or quantity <= 0 or unit_price <= 0
        ):
            raise ValueError("invalid fill")
    except (DecimalException, ValueError, TypeError, OverflowError) as exc:
        raise _CellMetricsError("invalid", "ledger_invalid_row") from exc
    return symbol, side, strategy_id, quantity, unit_price, stamp, cost_context


def _read_ledger(db_path: Path) -> list[tuple]:
    conn = None
    try:
        if not db_path.is_file():
            raise _CellMetricsError("unavailable", "ledger_missing")
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(paper_fills)")}
        if not columns:
            raise _CellMetricsError("unavailable", "ledger_read_error")
        context_column = "cost_context" if "cost_context" in columns else "NULL"
        return conn.execute(
            f"""SELECT symbol, side, strategy_id, amount, price, filled_at, {context_column}
                  FROM paper_fills ORDER BY filled_at, rowid"""
        ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise _CellMetricsError("unavailable", "ledger_read_error") from exc
    finally:
        if conn is not None:
            conn.close()


def _base_result() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason_code": "aggregated",
        "accounting_scope": "lab_exit_sells_by_experiment_and_symbol",
        "attribution_method": "proportional_average_cost",
        "cells": None,
        "total_realized_pnl_jpy": None,
        "cell_realized_pnl_jpy": None,
        "non_cell_realized_pnl_jpy": None,
        "ignored_unpaired_sells": None,
        "buy_fee_correction": None,
    }


def aggregate_cell_metrics(db_path: Path | str) -> dict[str, object]:
    """Aggregate per-cell metrics from a PAPER ledger file (opened ``mode=ro``)."""
    try:
        rows = _read_ledger(Path(db_path))
    except _CellMetricsError as exc:
        result = _base_result()
        result.update(status=exc.status, reason_code=exc.reason_code)
        return result
    return aggregate_cell_metrics_from_rows(rows)


def aggregate_cell_metrics_from_rows(rows: Iterable[tuple]) -> dict[str, object]:
    """Aggregate raw ``(symbol, side, strategy_id, amount, price, filled_at,
    cost_context)`` rows, already in ``filled_at, rowid`` order."""
    result = _base_result()
    pools: dict[str, _Pool] = {}
    cells: dict[tuple[str, str], _Cell] = {}
    total_pnl = ZERO
    cell_pnl = ZERO
    unpaired = 0
    buys = {"total": 0, "corrected": 0, "legacy_no_cost_context": 0, "already_fee_bearing": 0}

    try:
        for raw in rows:
            symbol, side, strategy_id, amount, price, stamp, context = _validated(tuple(raw))
            pool = pools.setdefault(symbol, _Pool())
            if side == "buy":
                rate = buy_fee_correction_rate(symbol, context)
                buys["total"] += 1
                if rate is None:
                    buys["already_fee_bearing"] += 1
                    buys["legacy_no_cost_context"] += int(context is None)
                else:
                    buys["corrected"] += 1
                pool.held += amount
                pool.cost += amount * price
                pool.corrected_cost += amount * price * (ONE if rate is None else ONE + rate)
                origin = experiment_id_from_strategy_id(strategy_id, CELL_ENTRY_PREFIX)
                pool.origins[origin] = pool.origins.get(origin, ZERO) + amount
                continue

            if amount > pool.held:
                unpaired += 1
            if pool.held <= 0:
                continue
            held = pool.held
            sold = min(amount, held)
            average = pool.cost / held
            corrected_average = pool.corrected_cost / held
            pnl = sold * (price - average)
            corrected_pnl = sold * (price - corrected_average)
            total_pnl += pnl

            experiment = experiment_id_from_strategy_id(strategy_id, CELL_EXIT_PREFIX)
            if experiment is not None:
                cell = cells.setdefault((experiment, symbol), _Cell())
                own = min(sold, sold * pool.origins.get(experiment, ZERO) / held)
                cell.closed += 1
                cell.wins += int(pnl > 0)
                cell.corrected_wins += int(corrected_pnl > 0)
                cell.pnl += pnl
                cell.corrected_pnl += corrected_pnl
                cell.sold_qty += sold
                cell.carry_qty += sold - own
                cell_pnl += pnl
                cost_basis = sold * average
                if cost_basis <= DUST_COST_THRESHOLD_JPY:
                    cell.dust += 1
                else:
                    day = dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).astimezone(JST).date().isoformat()
                    block = cell.days.setdefault(day, [0, ZERO, ZERO])
                    block[0] += 1
                    block[1] += pnl / cost_basis
                    block[2] += corrected_pnl / (sold * corrected_average)

            # Every sell consumes the pool, attributed or not.
            remaining = held - sold
            if remaining > 0:
                for key in list(pool.origins):
                    pool.origins[key] = pool.origins[key] * remaining / held
            else:
                pool.origins.clear()
            pool.held = remaining
            pool.cost = max(ZERO, pool.cost - average * sold)
            pool.corrected_cost = max(ZERO, pool.corrected_cost - corrected_average * sold)
    except _CellMetricsError as exc:
        result.update(status=exc.status, reason_code=exc.reason_code)
        return result
    except (DecimalException, OverflowError, OSError, ValueError):
        # includes a timestamp outside the platform's datetime range
        result.update(status="invalid", reason_code="ledger_invalid_row")
        return result

    cell_rows = []
    for (experiment, symbol), cell in sorted(cells.items()):
        blocks = [
            {
                "day": day,
                "count": count,
                "mean_return": str(total / count),
                "mean_return_corrected": str(corrected_total / count),
            }
            for day, (count, total, corrected_total) in sorted(cell.days.items())
        ]
        cell_rows.append({
            "experiment_id": experiment,
            "symbol": symbol,
            "closed_sells": cell.closed,
            "wins": cell.wins,
            "corrected_wins": cell.corrected_wins,
            "realized_pnl_jpy": str(cell.pnl),
            "corrected_realized_pnl_jpy": str(cell.corrected_pnl),
            "sold_quantity": str(cell.sold_qty),
            "carry_in_quantity": str(cell.carry_qty),
            "carry_in_fraction": str(cell.carry_qty / cell.sold_qty),
            "dust_excluded_sells": cell.dust,
            "return_blocks": blocks,
        })
    result.update({
        "status": "partial" if unpaired else "ok",
        "reason_code": "ledger_incomplete_basis" if unpaired else "aggregated",
        "cells": cell_rows,
        "total_realized_pnl_jpy": str(total_pnl),
        "cell_realized_pnl_jpy": str(cell_pnl),
        "non_cell_realized_pnl_jpy": str(total_pnl - cell_pnl),
        "ignored_unpaired_sells": unpaired,
        "buy_fee_correction": {
            "buy_fills": buys["total"],
            "corrected": buys["corrected"],
            "already_fee_bearing": buys["already_fee_bearing"],
            "legacy_no_cost_context": buys["legacy_no_cost_context"],
        },
    })
    return result
