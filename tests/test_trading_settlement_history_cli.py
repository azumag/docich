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
from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import CircuitBreakStatus  # noqa: E402

D = Decimal
NOW = 1_800_000_000.0


def market(symbol, base, quote):
    return MarketInfo(
        symbol, base, quote, True, True,
        amount_step=D("0.0001"), min_amount=D("0.0001"),
        taker_fee_rate=D("0.001"),
    )


def level(price, amount):
    return DepthLevel(D(str(price)), D(str(amount)))


def triangle_markets():
    return {
        "BTC/JPY": market("BTC/JPY", "BTC", "JPY"),
        "ETH/BTC": market("ETH/BTC", "ETH", "BTC"),
        "ETH/JPY": market("ETH/JPY", "ETH", "JPY"),
    }


def triangle_depth(now):
    return {
        "BTC/JPY": DepthBook("BTC/JPY", (level(99, 200),), (level(100, 50), level(110, 100)), now),
        "ETH/BTC": DepthBook("ETH/BTC", (level(0.049, 3000),), (level(0.05, 3000),), now),
        "ETH/JPY": DepthBook("ETH/JPY", (level(5.2, 1000), level(4.8, 2000)), (level(5.3, 3000),), now),
    }


class Gateway:
    def __init__(self, *, circuit_break=False):
        self.circuit_break = circuit_break

    def discover_markets(self):
        return triangle_markets()

    def fetch_depth_books(self, symbols, *, now, limit=20):
        return triangle_depth(now)

    def fetch_circuit_break_statuses(self, symbols):
        result = {symbol: CircuitBreakStatus(symbol, "NONE", "NORMAL", NOW) for symbol in symbols}
        if self.circuit_break:
            result["ETH/BTC"] = CircuitBreakStatus("ETH/BTC", "CIRCUIT_BREAK", "NORMAL", NOW)
        return result


class TestSettlementHistoryCli(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_recorded_scan_is_idempotent_and_queryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            argv = [
                "trading", "--state-dir", str(state), "arbitrage-depth-scan",
                "--min-edge-bps", "20", "--probe-asset", "JPY",
                "--probe-amounts", "1000", "--record",
            ]
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=Gateway()):
                first_rc, first_out, first_err = self.run_cli(argv)
                second_rc, second_out, second_err = self.run_cli(argv)
            self.assertEqual(first_rc, 0, first_err)
            self.assertEqual(second_rc, 0, second_err)
            first = json.loads(first_out)["candidates"][0]["settlements"][0]
            second = json.loads(second_out)["candidates"][0]["settlements"][0]
            self.assertEqual(first["settlement_id"], second["settlement_id"])
            self.assertTrue(first["recorded"])
            self.assertTrue(second["recorded"])

            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(state), "settlement-history", "--limit", "10"
            ])
            self.assertEqual(rc, 0, err)
            history = json.loads(out)
            self.assertEqual(history["count"], 1)
            item = history["settlements"][0]
            self.assertEqual(item["settlement_id"], first["settlement_id"])
            self.assertTrue(item["complete"])
            self.assertGreater(len(item["legs"]), 0)
            self.assertNotIn("api_key", json.dumps(history).lower())

    def test_failed_settlement_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=Gateway(circuit_break=True)):
                rc, out, err = self.run_cli([
                    "trading", "--state-dir", str(state), "arbitrage-depth-scan",
                    "--min-edge-bps", "20", "--probe-asset", "JPY",
                    "--probe-amounts", "1000", "--record",
                ])
            self.assertEqual(rc, 0, err)
            saved = json.loads(out)["candidates"][0]["settlements"][0]
            self.assertFalse(saved["complete"])
            self.assertTrue(saved["recorded"])

            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(state), "settlement-history", "--limit", "10"
            ])
            self.assertEqual(rc, 0, err)
            item = json.loads(out)["settlements"][0]
            self.assertFalse(item["complete"])
            self.assertEqual(item["failure_reason"], "circuit_break:CIRCUIT_BREAK")

    def test_market_constraint_change_creates_new_settlement_observation(self):
        class ChangedGateway(Gateway):
            def discover_markets(self):
                data = triangle_markets()
                data["BTC/JPY"] = MarketInfo(
                    "BTC/JPY", "BTC", "JPY", True, True,
                    amount_step=D("0.001"), min_amount=D("0.001"),
                    taker_fee_rate=D("0.001"),
                )
                return data

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            argv = [
                "trading", "--state-dir", str(state), "arbitrage-depth-scan",
                "--min-edge-bps", "20", "--probe-asset", "JPY",
                "--probe-amounts", "1000", "--record",
            ]
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=Gateway()):
                self.assertEqual(self.run_cli(argv)[0], 0)
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=ChangedGateway()):
                self.assertEqual(self.run_cli(argv)[0], 0)
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(state), "settlement-history", "--limit", "10"
            ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["count"], 2)

    def test_settlement_id_is_namespaced_by_model_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            argv = [
                "trading", "--state-dir", str(state), "arbitrage-depth-scan",
                "--min-edge-bps", "20", "--probe-asset", "JPY",
                "--probe-amounts", "1000",
            ]
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=Gateway()):
                rc, out, err = self.run_cli(argv)
            self.assertEqual(rc, 0, err)
            settlement_id = json.loads(out)["candidates"][0]["settlements"][0]["settlement_id"]
            self.assertTrue(settlement_id.startswith("multileg-v1:"))

    def test_record_without_candidate_does_not_create_database(self):
        class StarGateway:
            def discover_markets(self):
                return {
                    "BTC/JPY": market("BTC/JPY", "BTC", "JPY"),
                    "ETH/JPY": market("ETH/JPY", "ETH", "JPY"),
                    "SOL/JPY": market("SOL/JPY", "SOL", "JPY"),
                }
            def fetch_circuit_break_statuses(self, *args, **kwargs):
                raise AssertionError("no circuit request expected")
            def fetch_depth_books(self, *args, **kwargs):
                raise AssertionError("no depth request expected")

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=StarGateway()):
                rc, out, err = self.run_cli([
                    "trading", "--state-dir", str(state), "arbitrage-depth-scan", "--record"
                ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["candidate_count"], 0)
            self.assertFalse((state / "paper.sqlite3").exists())

    def test_history_absent_is_empty_without_creating_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(state), "settlement-history", "--limit", "10"
            ])
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["settlements"], [])
            self.assertFalse((state / "paper.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
