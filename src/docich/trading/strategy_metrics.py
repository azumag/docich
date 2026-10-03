"""Average-cost exit evaluation for one declarative PAPER strategy experiment."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
import math
from pathlib import Path
import sqlite3
import time

from .models import TradingValidationError, as_decimal
from .strategy_lab import StrategyExperiment

D = Decimal
ZERO = D("0")


class _LedgerError(Exception):
    def __init__(self, status: str, reason_code: str):
        self.status = status
        self.reason_code = reason_code


def _ledger_rows(db_path: Path, *, now: float):
    """Read one snapshot, retaining historical and unrelated inventory changes."""
    conn = None
    try:
        if not db_path.is_file():
            raise _LedgerError("unavailable", "ledger_missing")
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        rows = conn.execute(
            """SELECT symbol, side, strategy_id, amount, price, filled_at
                 FROM paper_fills ORDER BY filled_at, rowid"""
        ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise _LedgerError("unavailable", "ledger_read_error") from exc
    finally:
        if conn is not None:
            conn.close()
    result = []
    for symbol, side, strategy_id, amount, price, filled_at in rows:
        try:
            stamp = float(filled_at)
            if not math.isfinite(stamp) or stamp < 0:
                raise ValueError("invalid timestamp")
            if stamp > now:
                continue
            quantity = as_decimal(amount, "amount")
            effective_price = as_decimal(price, "price")
            if (
                not isinstance(symbol, str) or not symbol.strip()
                or not isinstance(strategy_id, str) or not strategy_id.strip()
                or side not in {"buy", "sell"}
                or quantity <= 0 or effective_price <= 0
            ):
                raise ValueError("invalid fill")
        except (DecimalException, ValueError, TypeError, OverflowError) as exc:
            # Do not silently skip a bad acquisition and credit a later sale.
            # Reasons stay allowlisted; no raw database values reach status.
            raise _LedgerError("invalid", "ledger_invalid_row") from exc
        result.append((symbol, side, strategy_id, quantity, effective_price, stamp))
    return result


@dataclass
class _Lot:
    held: Decimal = ZERO
    cost: Decimal = ZERO
    self_amount: Decimal = ZERO
    self_cost: Decimal = ZERO


@dataclass
class _Results:
    closed: int = 0
    wins: int = 0
    cumulative: Decimal = ZERO
    gains: Decimal = ZERO
    losses: Decimal = ZERO
    peak: Decimal = ZERO
    max_drawdown: Decimal = ZERO

    def add(self, pnl: Decimal) -> None:
        self.closed += 1
        self.wins += int(pnl > 0)
        self.cumulative += pnl
        self.gains += max(ZERO, pnl)
        self.losses += max(ZERO, -pnl)
        self.peak = max(self.peak, self.cumulative)
        self.max_drawdown = max(self.max_drawdown, self.peak - self.cumulative)

    @property
    def profit_factor(self) -> Decimal | None:
        return None if self.losses == 0 else self.gains / self.losses


def _base_result(spec: StrategyExperiment, now: float | None) -> dict[str, object]:
    return {
        "schema_version": 2,
        "experiment_id": spec.experiment_id,
        "activated_at": float(spec.activated_at),
        "evaluated_at": now,
        "accounting_scope": "experiment_exit_decisions",
        "attribution_method": "proportional_average_cost",
        "attribution_note": (
            "self_entryは現在の実験開始後の自己購入、carry_inは開始前または他戦略の購入。"
            "共有在庫の売却ごとに数量と取得原価を比例消費する。"
            "混在売却は両由来のclosed_sellsに1件ずつ、総件数には1件だけ数える。"
            "総損益は実験期間の売却判断に対応する実現損益であり、"
            "持越し損益や未実現損益を分離した戦略全体の優位性を示すものではない。"
        ),
        "status": "ok",
        "reason_code": "evaluated",
        "closed_sells": None,
        "wins": None,
        "win_rate": None,
        "realized_pnl_jpy": None,
        "profit_factor": None,
        "max_realized_drawdown_pct": None,
        "ignored_unpaired_exits": None,
        "self_entry_closed_sells": None,
        "self_entry_realized_pnl_jpy": None,
        "carry_in_closed_sells": None,
        "carry_in_realized_pnl_jpy": None,
        "mixed_origin_closed_sells": None,
        "open_position_count": None,
        "inventory_complete": False,
        "promotion_ready": False,
    }


def evaluate_strategy_experiment(
    trading_dir,
    spec: StrategyExperiment,
    *,
    capital_jpy: object,
    now: float | None = None,
) -> dict[str, object]:
    """Evaluate this activation's exits against the shared PAPER ledger basis.

    Every historical fill updates shared average-cost inventory. Only sells
    attributed to this experiment at/after its current activation enter its
    results. A previous activation reusing the same id is carry-in inventory.
    Origin attribution consumes both inventory pools proportionally, including
    when another strategy sells. It never manufactures a missing acquisition.

    Invalid/unreadable ledgers return unavailable metrics, not a successful
    zero-trade result. The upper bound is also the evaluation timestamp so an
    experiment boundary can be evaluated without including later executions.
    """
    try:
        moment = time.time() if now is None else float(now)
        if isinstance(now, bool) or not math.isfinite(moment) or moment < float(spec.activated_at):
            raise ValueError("invalid evaluation clock")
    except (TypeError, ValueError, OverflowError):
        result = _base_result(spec, None)
        result.update(status="invalid", reason_code="evaluation_invalid_clock")
        return result

    result = _base_result(spec, moment)
    try:
        capital = as_decimal(capital_jpy, "capital_jpy")
        if capital <= 0:
            raise TradingValidationError("capital_jpy must be positive")
    except (TradingValidationError, DecimalException):
        result.update(status="invalid", reason_code="evaluation_invalid_capital")
        return result

    try:
        rows = _ledger_rows(Path(trading_dir) / "paper.sqlite3", now=moment)
    except _LedgerError as exc:
        result.update(status=exc.status, reason_code=exc.reason_code)
        return result

    lots: dict[str, _Lot] = {}
    positions: dict[str, Decimal] = {}
    total, self_entry, carry_in = _Results(), _Results(), _Results()
    ignored_unpaired_exits = 0
    mixed_origin_closed_sells = 0
    entry_prefix = f"lab:{spec.experiment_id}:"
    prefixes = (entry_prefix, f"lab-exit:{spec.experiment_id}:")

    try:
        for symbol, side, strategy_id, amount, price, filled_at in rows:
            lot = lots.setdefault(symbol, _Lot())
            positions[symbol] = positions.get(symbol, ZERO) + (amount if side == "buy" else -amount)
            current = filled_at >= spec.activated_at and strategy_id.startswith(prefixes)
            if side == "buy":
                lot.held += amount
                lot.cost += amount * price
                if current and strategy_id.startswith(entry_prefix):
                    lot.self_amount += amount
                    lot.self_cost += amount * price
                continue

            if current and amount > lot.held:
                ignored_unpaired_exits += 1
            if lot.held <= 0:
                continue
            sold = min(amount, lot.held)
            # Keep the total identical to the account's average-cost method.
            average = lot.cost / lot.held
            pnl = sold * (price - average)
            if lot.self_amount == lot.held:
                own_amount, own_cost = sold, average * sold
                own_pnl = pnl
            elif sold == lot.held:
                own_amount, own_cost = lot.self_amount, lot.self_cost
                own_pnl = own_amount * price - own_cost
            else:
                own_amount = sold * lot.self_amount / lot.held
                own_cost = sold * lot.self_cost / lot.held
                own_pnl = own_amount * price - own_cost
            inherited_amount = sold - own_amount
            inherited_pnl = pnl - own_pnl

            if current:
                total.add(pnl)
                if own_amount > 0:
                    self_entry.add(own_pnl)
                if inherited_amount > 0:
                    carry_in.add(inherited_pnl)
                if own_amount > 0 and inherited_amount > 0:
                    mixed_origin_closed_sells += 1

            # All sells consume both pools. Ignoring unrelated sells would
            # allow a later experiment exit to spend the same basis twice.
            if sold == lot.held:
                lots[symbol] = _Lot()
            else:
                lot.held -= sold
                lot.cost = max(ZERO, lot.cost - average * sold)
                lot.self_amount -= own_amount
                lot.self_cost = max(ZERO, lot.self_cost - own_cost)

        profit_factor = total.profit_factor
        drawdown_pct = total.max_drawdown / capital * D("100")
        inventory_complete = all(
            amount >= 0 and amount == lots[symbol].held
            for symbol, amount in positions.items()
        )
        result.update({
            "status": "partial" if ignored_unpaired_exits else "ok",
            "reason_code": "ledger_incomplete_basis" if ignored_unpaired_exits else "evaluated",
            "closed_sells": total.closed,
            "wins": total.wins,
            "win_rate": None if total.closed == 0 else str(D(total.wins) / D(total.closed)),
            "realized_pnl_jpy": str(total.cumulative),
            "profit_factor": None if profit_factor is None else str(profit_factor),
            "max_realized_drawdown_pct": str(drawdown_pct),
            "ignored_unpaired_exits": ignored_unpaired_exits,
            "self_entry_closed_sells": self_entry.closed,
            "self_entry_realized_pnl_jpy": str(self_entry.cumulative),
            "carry_in_closed_sells": carry_in.closed,
            # Preserve the total/component reconciliation with Decimal's
            # rounding as quantities are repeatedly partially liquidated.
            "carry_in_realized_pnl_jpy": str(total.cumulative - self_entry.cumulative),
            "mixed_origin_closed_sells": mixed_origin_closed_sells,
            "open_position_count": sum(amount != 0 for amount in positions.values()),
            "inventory_complete": inventory_complete,
            "promotion_ready": bool(
                not ignored_unpaired_exits
                and inventory_complete
                and carry_in.closed == 0
                and total.closed >= 20
                and total.cumulative > 0
                and profit_factor is not None
                and profit_factor >= D("1.20")
                and drawdown_pct <= D("10")
            ),
        })
    except DecimalException:
        result.update(status="invalid", reason_code="ledger_invalid_row")
    return result
