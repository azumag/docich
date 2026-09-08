"""Private SQLite ledger for deterministic paper trading."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
import os
from pathlib import Path
import sqlite3

from .models import AllocationDecision, PaperFill
from .settlement import MultiLegSettlement, SettlementLeg


_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_orders (
    opportunity_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quote TEXT NOT NULL,
    amount TEXT NOT NULL,
    price TEXT NOT NULL,
    quote_notional TEXT NOT NULL,
    reference_notional TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quote TEXT NOT NULL,
    amount TEXT NOT NULL,
    price TEXT NOT NULL,
    quote_notional TEXT NOT NULL,
    reference_notional TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    filled_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_multileg_settlements (
    settlement_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL,
    start_asset TEXT NOT NULL,
    start_amount TEXT NOT NULL,
    final_amount TEXT,
    net_edge_bps TEXT,
    complete INTEGER NOT NULL,
    failed_leg_symbol TEXT,
    failure_reason TEXT,
    observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_multileg_legs (
    settlement_id TEXT NOT NULL,
    leg_index INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    input_amount TEXT NOT NULL,
    order_base_amount TEXT NOT NULL,
    consumed_input TEXT NOT NULL,
    residual_input TEXT NOT NULL,
    output_amount TEXT NOT NULL,
    levels_used INTEGER NOT NULL,
    fee_paid_base TEXT NOT NULL,
    fee_paid_quote TEXT NOT NULL,
    PRIMARY KEY (settlement_id, leg_index)
);
CREATE TABLE IF NOT EXISTS paper_multileg_residuals (
    settlement_id TEXT NOT NULL,
    asset TEXT NOT NULL,
    amount TEXT NOT NULL,
    PRIMARY KEY (settlement_id, asset)
);
"""


@dataclass(frozen=True)
class RecordedMultiLegSettlement:
    settlement_id: str
    route_id: str
    start_asset: str
    start_amount: Decimal
    final_amount: Decimal | None
    net_edge_bps: Decimal | None
    complete: bool
    failed_leg_symbol: str | None
    failure_reason: str | None
    observed_at: float
    residuals: dict[str, Decimal]
    legs: tuple[SettlementLeg, ...]


class PaperLedger:
    """Small durable ledger that never stores exchange credentials or payloads."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_fill(row: sqlite3.Row | tuple) -> PaperFill:
        return PaperFill(
            fill_id=row[0],
            opportunity_id=row[1],
            strategy_id=row[2],
            symbol=row[3],
            side=row[4],
            quote=row[5],
            amount=Decimal(row[6]),
            price=Decimal(row[7]),
            quote_notional=Decimal(row[8]),
            reference_notional=Decimal(row[9]),
            reason_code=row[10],
            filled_at=float(row[11]),
        )

    def get_fill_for_opportunity(self, opportunity_id: str) -> PaperFill | None:
        row = self._conn.execute(
            """SELECT fill_id, opportunity_id, strategy_id, symbol, side, quote,
                      amount, price, quote_notional, reference_notional, reason_code, filled_at
                 FROM paper_fills WHERE opportunity_id = ?""",
            (opportunity_id,),
        ).fetchone()
        return None if row is None else self._row_to_fill(row)

    def record_fill(self, decision: AllocationDecision, *, timestamp: float) -> PaperFill:
        existing = self.get_fill_for_opportunity(decision.opportunity_id)
        if existing is not None:
            return existing
        fill_id = f"paper:{decision.opportunity_id}"
        values = (
            decision.opportunity_id,
            decision.strategy_id,
            decision.symbol,
            decision.side,
            decision.quote,
            str(decision.amount),
            str(decision.price),
            str(decision.quote_notional),
            str(decision.reference_notional),
            decision.reason_code,
            float(timestamp),
        )
        with self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO paper_orders
                   (opportunity_id, strategy_id, symbol, side, quote, amount, price,
                    quote_notional, reference_notional, reason_code, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            self._conn.execute(
                """INSERT OR IGNORE INTO paper_fills
                   (fill_id, opportunity_id, strategy_id, symbol, side, quote, amount, price,
                    quote_notional, reference_notional, reason_code, filled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fill_id, *values),
            )
        recorded = self.get_fill_for_opportunity(decision.opportunity_id)
        if recorded is None:
            raise RuntimeError("paper fill was not persisted")
        return recorded

    def recent_fills(self, *, limit: int = 20) -> list[PaperFill]:
        if limit <= 0:
            return []
        rows = self._conn.execute(
            """SELECT fill_id, opportunity_id, strategy_id, symbol, side, quote,
                      amount, price, quote_notional, reference_notional, reason_code, filled_at
                 FROM paper_fills
                ORDER BY filled_at DESC, rowid DESC
                LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [self._row_to_fill(row) for row in rows]

    def deployed_reference(self) -> Decimal:
        total = Decimal("0")
        rows = self._conn.execute(
            "SELECT side, reference_notional FROM paper_fills ORDER BY filled_at, rowid"
        ).fetchall()
        for side, value_text in rows:
            value = Decimal(value_text)
            total += value if side == "buy" else -value
        return max(Decimal("0"), total)

    def positions(self) -> dict[str, Decimal]:
        positions: dict[str, Decimal] = {}
        rows = self._conn.execute(
            "SELECT symbol, side, amount FROM paper_fills ORDER BY filled_at, rowid"
        ).fetchall()
        for symbol, side, amount_text in rows:
            amount = Decimal(amount_text)
            signed = amount if side == "buy" else -amount
            positions[symbol] = positions.get(symbol, Decimal("0")) + signed
        return {symbol: amount for symbol, amount in positions.items() if amount != 0}

    @staticmethod
    def _row_to_settlement_header(row: sqlite3.Row | tuple) -> tuple:
        return (
            row[0], row[1], row[2], Decimal(row[3]),
            None if row[4] is None else Decimal(row[4]),
            None if row[5] is None else Decimal(row[5]),
            bool(row[6]), row[7], row[8], float(row[9]),
        )

    def get_multileg_settlement(self, settlement_id: str) -> RecordedMultiLegSettlement | None:
        row = self._conn.execute(
            """SELECT settlement_id, route_id, start_asset, start_amount, final_amount,
                      net_edge_bps, complete, failed_leg_symbol, failure_reason, observed_at
                 FROM paper_multileg_settlements WHERE settlement_id = ?""",
            (settlement_id,),
        ).fetchone()
        if row is None:
            return None
        header = self._row_to_settlement_header(row)
        leg_rows = self._conn.execute(
            """SELECT symbol, side, input_amount, order_base_amount, consumed_input,
                      residual_input, output_amount, levels_used, fee_paid_base, fee_paid_quote
                 FROM paper_multileg_legs
                WHERE settlement_id = ? ORDER BY leg_index""",
            (settlement_id,),
        ).fetchall()
        legs = tuple(
            SettlementLeg(
                symbol=leg[0], side=leg[1], input_amount=Decimal(leg[2]),
                order_base_amount=Decimal(leg[3]), consumed_input=Decimal(leg[4]),
                residual_input=Decimal(leg[5]), output_amount=Decimal(leg[6]),
                levels_used=int(leg[7]), fee_paid_base=Decimal(leg[8]),
                fee_paid_quote=Decimal(leg[9]),
            )
            for leg in leg_rows
        )
        residual_rows = self._conn.execute(
            "SELECT asset, amount FROM paper_multileg_residuals WHERE settlement_id = ? ORDER BY asset",
            (settlement_id,),
        ).fetchall()
        residuals = {asset: Decimal(amount) for asset, amount in residual_rows}
        return RecordedMultiLegSettlement(*header, residuals=residuals, legs=legs)

    def record_multileg_settlement(
        self, settlement_id: str, settlement: MultiLegSettlement, *, observed_at: float
    ) -> RecordedMultiLegSettlement:
        settlement_id = str(settlement_id).strip()
        if not settlement_id:
            raise ValueError("settlement_id must not be empty")
        observed_at = float(observed_at)
        if not math.isfinite(observed_at):
            raise ValueError("observed_at must be finite")
        existing = self.get_multileg_settlement(settlement_id)
        if existing is not None:
            return existing
        with self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO paper_multileg_settlements
                   (settlement_id, route_id, start_asset, start_amount, final_amount, net_edge_bps,
                    complete, failed_leg_symbol, failure_reason, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    settlement_id, settlement.route_id, settlement.start_asset, str(settlement.start_amount),
                    None if settlement.final_amount is None else str(settlement.final_amount),
                    None if settlement.net_edge_bps is None else str(settlement.net_edge_bps),
                    1 if settlement.complete else 0, settlement.failed_leg_symbol,
                    settlement.failure_reason, float(observed_at),
                ),
            )
            persisted = self._conn.execute(
                "SELECT observed_at FROM paper_multileg_settlements WHERE settlement_id = ?",
                (settlement_id,),
            ).fetchone()
            if persisted is not None and float(persisted[0]) == float(observed_at):
                for index, leg in enumerate(settlement.legs):
                    self._conn.execute(
                        """INSERT OR IGNORE INTO paper_multileg_legs
                           (settlement_id, leg_index, symbol, side, input_amount, order_base_amount,
                            consumed_input, residual_input, output_amount, levels_used,
                            fee_paid_base, fee_paid_quote)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            settlement_id, index, leg.symbol, leg.side, str(leg.input_amount),
                            str(leg.order_base_amount), str(leg.consumed_input),
                            str(leg.residual_input), str(leg.output_amount), int(leg.levels_used),
                            str(leg.fee_paid_base), str(leg.fee_paid_quote),
                        ),
                    )
                for asset, amount in settlement.residuals.items():
                    self._conn.execute(
                        """INSERT OR IGNORE INTO paper_multileg_residuals
                           (settlement_id, asset, amount) VALUES (?, ?, ?)""",
                        (settlement_id, str(asset), str(amount)),
                    )
        recorded = self.get_multileg_settlement(settlement_id)
        if recorded is None:
            raise RuntimeError("multi-leg paper settlement was not persisted")
        return recorded

    def recent_multileg_settlements(self, *, limit: int = 20) -> list[RecordedMultiLegSettlement]:
        if limit <= 0:
            return []
        rows = self._conn.execute(
            """SELECT settlement_id FROM paper_multileg_settlements
                ORDER BY observed_at DESC, rowid DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        result: list[RecordedMultiLegSettlement] = []
        for (settlement_id,) in rows:
            item = self.get_multileg_settlement(settlement_id)
            if item is not None:
                result.append(item)
        return result
