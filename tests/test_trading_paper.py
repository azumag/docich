from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision  # noqa: E402
from docich.trading.paper import PaperBroker  # noqa: E402
from docich.trading.status import build_public_status, write_public_status  # noqa: E402


D = Decimal


def decision(oid: str = "opp-1", *, amount: str = "0.01") -> AllocationDecision:
    price = D("1000000")
    qty = D(amount)
    quote_notional = qty * price
    return AllocationDecision(
        opportunity_id=oid,
        strategy_id="momentum-v1",
        symbol="BTC/JPY",
        side="buy",
        quote="JPY",
        amount=qty,
        price=price,
        quote_notional=quote_notional,
        reference_notional=quote_notional,
        reason_code="momentum_breakout",
    )


class TestPaperLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "run" / "trading" / "paper.sqlite3"
        self.ledger = PaperLedger(self.db)
        self.broker = PaperBroker(self.ledger)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_database_file_is_private(self):
        self.assertTrue(self.db.is_file())
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)

    def test_duplicate_opportunity_is_idempotent(self):
        first = self.broker.fill(decision(), timestamp=1000.0)
        second = self.broker.fill(decision(), timestamp=1001.0)
        self.assertEqual(first.fill_id, second.fill_id)
        self.assertEqual(len(self.ledger.recent_fills(limit=10)), 1)

    def test_positions_aggregate_filled_amounts(self):
        self.broker.fill(decision("a", amount="0.01"), timestamp=1000.0)
        self.broker.fill(decision("b", amount="0.02"), timestamp=1001.0)
        positions = self.ledger.positions()
        self.assertEqual(positions["BTC/JPY"], D("0.03"))

    def test_recent_fills_are_newest_first(self):
        self.broker.fill(decision("old"), timestamp=1000.0)
        self.broker.fill(decision("new"), timestamp=1001.0)
        fills = self.ledger.recent_fills(limit=2)
        self.assertEqual([fill.opportunity_id for fill in fills], ["new", "old"])


class TestPublicTradingStatus(unittest.TestCase):
    def test_status_schema_is_allowlisted_and_secret_free(self):
        payload = build_public_status(
            worker_state="idle",
            last_cycle_at=123.0,
            eligible_symbols=["BTC/JPY", "ETH/USDT"],
            capital_reference=D("100000"),
            deployed_reference=D("30000"),
            open_positions={"BTC/JPY": D("0.01")},
            recent_fills=[],
            skipped_reason_codes=["quote_unvalued"],
        )
        self.assertEqual(payload["mode"], "paper")
        self.assertEqual(payload["market_count"], 2)
        blob = json.dumps(payload, sort_keys=True)
        for banned in ("api_key", "secret", "authorization", "password", "private_payload"):
            self.assertNotIn(banned, blob.lower())

    def test_signal_summary_is_allowlisted(self):
        payload = build_public_status(
            worker_state="idle", last_cycle_at=123.0, eligible_symbols=["BTC/JPY"],
            capital_reference=D("100000"), deployed_reference=D("0"), open_positions={},
            recent_fills=[], skipped_reason_codes=[],
            signal_summary={"candidate_count": 1, "strategy_ids": ["momentum-v1"], "api_key": "must-not-leak"},
        )
        blob = json.dumps(payload, sort_keys=True)
        self.assertEqual(payload["signal_summary"]["candidate_count"], 1)
        self.assertNotIn("api_key", blob.lower())
        self.assertNotIn("must-not-leak", blob)

    def test_status_file_is_atomic_private_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "trading" / "status.json"
            payload = build_public_status(
                worker_state="idle",
                last_cycle_at=None,
                eligible_symbols=[],
                capital_reference=D("0"),
                deployed_reference=D("0"),
                open_positions={},
                recent_fills=[],
                skipped_reason_codes=[],
            )
            write_public_status(path, payload)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
