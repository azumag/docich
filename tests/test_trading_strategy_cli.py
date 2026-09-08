from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.cli import main  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402


NOW = 1_800_000_000.0


def frame_payload(closes):
    count = len(closes)
    return {
        "timeframe_seconds": 300,
        "timestamps": [NOW - (count - i) * 300 for i in range(count)],
        "closes": [str(v) for v in closes],
        "volumes": ["10" for _ in closes],
    }


class FakeGateway:
    def discover_markets(self):
        return {
            "BTC/JPY": MarketInfo(
                symbol="BTC/JPY", base="BTC", quote="JPY", spot=True, active=True,
                amount_step=Decimal("0.0001"), min_amount=Decimal("0.0001"), min_cost=Decimal("1"),
            )
        }

    def fetch_market_frames(self, symbols, *, timeframe, limit, now):
        return {
            "BTC/JPY": MarketFrame(
                symbol="BTC/JPY", timeframe_seconds=300,
                timestamps=tuple(now - (7-i) * 300 for i in range(7)),
                closes=tuple(Decimal(str(x)) for x in [100, 100, 101, 102, 103, 104, 105]),
                volumes=tuple(Decimal("10") for _ in range(7)),
            )
        }


class TestTradingStrategyCli(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def strategy_snapshot(self):
        return {
            "mode": "paper",
            "as_of": NOW,
            "capital_reference": "100000",
            "deployed_reference": "0",
            "quote_to_reference": {"JPY": "1"},
            "available_quote": {"JPY": "100000"},
            "markets": {
                "BTC/JPY": {
                    "base": "BTC", "quote": "JPY", "spot": True, "active": True,
                    "amount_step": "0.0001", "min_amount": "0.0001", "min_cost": "1",
                }
            },
            "frames": {"BTC/JPY": frame_payload([100, 100, 101, 102, 103, 104, 105])},
        }

    def test_history_returns_public_normalized_frames(self):
        with patch("docich.trading.cli.BitbankPublicGateway", return_value=FakeGateway()):
            rc, out, err = self.run_cli([
                "trading", "history", "--symbols", "BTC/JPY", "--timeframe", "5m", "--limit", "7"
            ])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "paper")
        self.assertEqual(payload["market_count"], 1)
        self.assertEqual(payload["frames"]["BTC/JPY"]["closes"][-1], "105")
        self.assertNotIn("balance", json.dumps(payload).lower())
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_strategy_cycle_generates_and_fills_momentum_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "strategy.json"
            snapshot.write_text(json.dumps(self.strategy_snapshot()), encoding="utf-8")
            state_dir = Path(tmp) / "state"
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(state_dir), "strategy-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["worker_state"], "strategy_cycle_complete")
            self.assertEqual(payload["signal_summary"]["candidate_count"], 1)
            self.assertEqual(payload["signal_summary"]["selected_count"], 1)
            self.assertEqual(payload["recent_fills"][0]["strategy_id"], "momentum-v1")

    def test_strategy_cycle_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "strategy.json"
            snapshot.write_text(json.dumps(self.strategy_snapshot()), encoding="utf-8")
            argv = ["trading", "--state-dir", str(Path(tmp) / "state"), "strategy-cycle", "--snapshot", str(snapshot)]
            self.assertEqual(self.run_cli(argv)[0], 0)
            rc, out, err = self.run_cli(argv)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(json.loads(out)["recent_fills"]), 1)

    def test_strategy_cycle_rejects_correlated_lower_score_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.strategy_snapshot()
            data["markets"]["ETH/JPY"] = {
                "base": "ETH", "quote": "JPY", "spot": True, "active": True,
                "amount_step": "0.001", "min_amount": "0.001", "min_cost": "1",
            }
            data["frames"]["ETH/JPY"] = frame_payload([200, 200, 202, 204, 206, 208, 210])
            snapshot = Path(tmp) / "strategy.json"
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(Path(tmp) / "state"), "strategy-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["signal_summary"]["candidate_count"], 2)
            self.assertEqual(payload["signal_summary"]["selected_count"], 1)
            self.assertIn("correlated_exposure", payload["skipped_reason_codes"])
            self.assertEqual(len(payload["recent_fills"]), 1)


if __name__ == "__main__":
    unittest.main()
