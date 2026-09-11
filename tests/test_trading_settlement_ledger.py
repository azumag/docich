from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision  # noqa: E402
from docich.trading.settlement import MultiLegSettlement, SettlementLeg  # noqa: E402

D = Decimal


def settlement(*, complete=True, reason=None):
    legs = (
        SettlementLeg("BTC/JPY", "buy", D("1000"), D("0.0082"), D("992.2"), D("7.8"), D("0.0082"), 2, D("0"), D("0.99")),
        SettlementLeg("ETH/BTC", "buy", D("0.0082"), D("0.16"), D("0.008"), D("0.0002"), D("0.16"), 1, D("0"), D("0.000008")),
    ) if complete else ()
    return MultiLegSettlement(
        route_id="JPY>BTC@BTC/JPY|BTC>ETH@ETH/BTC|ETH>JPY@ETH/JPY",
        start_asset="JPY",
        start_amount=D("1000"),
        final_amount=D("1030") if complete else None,
        net_edge_bps=D("300") if complete else None,
        complete=complete,
        failed_leg_symbol=None if complete else "ETH/BTC",
        failure_reason=reason,
        residuals={"JPY": D("7.8"), "BTC": D("0.0002")} if complete else {},
        legs=legs,
    )


class TestMultiLegSettlementLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "run" / "trading" / "paper.sqlite3"
        self.ledger = PaperLedger(self.db)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_records_complete_settlement_with_legs_and_residuals(self):
        saved = self.ledger.record_multileg_settlement(
            "settlement-1", settlement(), observed_at=1234.5
        )
        self.assertEqual(saved.settlement_id, "settlement-1")
        self.assertTrue(saved.complete)
        self.assertEqual(saved.final_amount, D("1030"))
        self.assertEqual(len(saved.legs), 2)
        self.assertEqual(saved.legs[0].order_base_amount, D("0.0082"))
        self.assertEqual(saved.residuals["JPY"], D("7.8"))
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)

    def test_position_cost_basis_and_net_positions_from_fills(self):
        def fill(oid, side, amount, price):
            notional = D(amount) * D(price)
            return AllocationDecision(
                opportunity_id=oid, strategy_id="test-v1", symbol="BTC/JPY", side=side,
                quote="JPY", amount=D(amount), price=D(price),
                quote_notional=notional, reference_notional=notional, reason_code="test",
            )

        self.ledger.record_fill(fill("b1", "buy", "1", "100"), timestamp=1000.0)
        self.ledger.record_fill(fill("b2", "buy", "1", "200"), timestamp=1001.0)
        self.ledger.record_fill(fill("s1", "sell", "1", "300"), timestamp=1002.0)
        amount, average, opened_at = self.ledger.position_cost_basis()["BTC/JPY"]
        self.assertEqual(amount, D("1"))
        self.assertEqual(average, D("150"))
        self.assertEqual(opened_at, 1000.0)
        self.assertEqual(self.ledger.positions()["BTC/JPY"], D("1"))

    def test_duplicate_settlement_id_is_idempotent(self):
        first = self.ledger.record_multileg_settlement("same", settlement(), observed_at=1000.0)
        second = self.ledger.record_multileg_settlement("same", settlement(), observed_at=2000.0)
        self.assertEqual(first.settlement_id, second.settlement_id)
        self.assertEqual(len(self.ledger.recent_multileg_settlements(limit=10)), 1)
        self.assertEqual(second.observed_at, 1000.0)

    def test_failed_settlement_is_durable_and_reopenable(self):
        self.ledger.record_multileg_settlement(
            "failed", settlement(complete=False, reason="circuit_break:CIRCUIT_BREAK"), observed_at=1111.0
        )
        self.ledger.close()
        self.ledger = PaperLedger(self.db)
        loaded = self.ledger.get_multileg_settlement("failed")
        self.assertIsNotNone(loaded)
        self.assertFalse(loaded.complete)
        self.assertEqual(loaded.failure_reason, "circuit_break:CIRCUIT_BREAK")
        self.assertEqual(loaded.failed_leg_symbol, "ETH/BTC")
        self.assertEqual(loaded.legs, ())

    def test_rejects_invalid_settlement_identity_or_timestamp(self):
        with self.assertRaises(ValueError):
            self.ledger.record_multileg_settlement("", settlement(), observed_at=1000.0)
        with self.assertRaises(ValueError):
            self.ledger.record_multileg_settlement("nan-time", settlement(), observed_at=float("nan"))

    def test_recent_settlements_are_newest_first(self):
        self.ledger.record_multileg_settlement("old", settlement(), observed_at=1000.0)
        self.ledger.record_multileg_settlement("new", settlement(), observed_at=1001.0)
        self.assertEqual(
            [x.settlement_id for x in self.ledger.recent_multileg_settlements(limit=2)],
            ["new", "old"],
        )


if __name__ == "__main__":
    unittest.main()
