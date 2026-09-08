from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.events import (  # noqa: E402
    PublicEventError,
    append_public_event,
    build_fill_event,
    build_settlement_event,
)
from docich.trading.ledger import RecordedMultiLegSettlement  # noqa: E402
from docich.trading.models import PaperFill  # noqa: E402

D = Decimal


def fill():
    return PaperFill(
        fill_id="paper:opp-1", opportunity_id="opp-1", strategy_id="momentum-v1",
        symbol="BTC/JPY", side="buy", quote="JPY", amount=D("0.001"),
        price=D("10000000"), quote_notional=D("10000"), reference_notional=D("10000"),
        reason_code="momentum_breakout", filled_at=1234.5,
    )


def settlement():
    return RecordedMultiLegSettlement(
        settlement_id="multileg-v1:abc", route_id="JPY>BTC>ETH>JPY",
        start_asset="JPY", start_amount=D("1000"), final_amount=D("1005"),
        net_edge_bps=D("50"), complete=True, failed_leg_symbol=None,
        failure_reason=None, observed_at=1235.0, residuals={}, legs=(),
    )


class TestPublicEventJournal(unittest.TestCase):
    def test_is_private_bounded_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "trading" / "events.jsonl"
            first = build_fill_event(fill())
            self.assertTrue(append_public_event(path, first, max_events=2))
            self.assertFalse(append_public_event(path, first, max_events=2))
            second = dict(first, event_id="fill:2", occurred_at=1236.0)
            third = dict(first, event_id="fill:3", occurred_at=1237.0)
            self.assertTrue(append_public_event(path, second, max_events=2))
            self.assertTrue(append_public_event(path, third, max_events=2))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["event_id"] for row in rows], ["fill:2", "fill:3"])
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_corrupt_existing_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaises(PublicEventError):
                append_public_event(path, build_fill_event(fill()))
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json\n")

    def test_builders_are_allowlisted_and_secret_free(self):
        fill_event = build_fill_event(fill())
        settlement_event = build_settlement_event(settlement())
        self.assertEqual(fill_event["event_id"], "fill:paper:opp-1")
        self.assertEqual(settlement_event["event_id"], "settlement:multileg-v1:abc")
        blob = json.dumps([fill_event, settlement_event], sort_keys=True)
        for banned in ("api_key", "secret", "authorization", "password", "private_payload"):
            self.assertNotIn(banned, blob.lower())

    def test_rejects_unknown_event_fields(self):
        event = build_fill_event(fill())
        event["api_key"] = "must-not-pass"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PublicEventError):
                append_public_event(Path(tmp) / "events.jsonl", event)


if __name__ == "__main__":
    unittest.main()
