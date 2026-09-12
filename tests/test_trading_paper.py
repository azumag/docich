from __future__ import annotations

from dataclasses import replace
import json
import stat
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision, SkipDecision  # noqa: E402
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

    def test_default_cost_model_worsens_both_sides(self):
        buy = self.broker.fill(decision("cost-buy"), timestamp=1000.0)
        sell_decision = replace(
            decision("cost-sell"), side="sell", reason_code="take_profit"
        )
        sell = self.broker.fill(sell_decision, timestamp=1001.0)
        self.assertEqual(buy.price, D("1000000") * D("1.0005") * D("1.0012"))
        self.assertEqual(sell.price, D("1000000") * D("0.9995") * D("0.9988"))
        self.assertGreater(buy.price, D("1000000"))
        self.assertLess(sell.price, D("1000000"))

    def test_cost_model_can_be_disabled_for_accounting_fixtures(self):
        broker = PaperBroker(self.ledger, taker_fee_rate="0", slippage_bps="0")
        fill = broker.fill(decision("zero-cost"), timestamp=1000.0)
        self.assertEqual(fill.price, D("1000000"))


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

    def test_status_preserves_safe_skip_symbol_side_reason(self):
        skip = SkipDecision("opp-x", "XRP/JPY", "below_min_amount", side="buy")
        payload = build_public_status(
            worker_state="idle", last_cycle_at=123.0, eligible_symbols=["XRP/JPY"],
            capital_reference=D("100000"), deployed_reference=D("0"), open_positions={},
            recent_fills=[], skipped_reason_codes=[skip.reason_code],
        )
        self.assertEqual(payload["skipped_reason_codes"], ["below_min_amount"])
        self.assertEqual(
            payload["skipped_decisions"],
            [{"symbol": "XRP/JPY", "side": "buy", "reason_code": "below_min_amount"}],
        )

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

    def test_worker_summary_is_allowlisted(self):
        payload = build_public_status(
            worker_state="paper_worker_idle", last_cycle_at=123.0,
            eligible_symbols=["BTC/JPY"], capital_reference=D("10000"),
            deployed_reference=D("0"), open_positions={}, recent_fills=[],
            skipped_reason_codes=[],
            worker_summary={
                "cycle_index": 2, "last_success_at": 123.0,
                "frame_error_count": 1, "error_codes": ["frame_fetch_error"],
                "api_key": "must-not-leak",
            },
        )
        self.assertEqual(payload["worker_summary"]["cycle_index"], 2)
        self.assertEqual(payload["worker_summary"]["frame_error_count"], 1)
        blob = json.dumps(payload, sort_keys=True)
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
