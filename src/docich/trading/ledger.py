"""Private SQLite ledger for deterministic paper trading."""
from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
import sqlite3

from .models import AllocationDecision, PaperFill


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
"""


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
