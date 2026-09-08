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
    return MarketInfo(symbol, base, quote, True, True, amount_step=D("0.0001"), min_amount=D("0.0001"), taker_fee_rate=D("0.001"))


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


class TestTradingDepthCli(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_no_triangle_skips_depth_requests(self):
        class StarGateway:
            def discover_markets(self):
                return {
                    "BTC/JPY": market("BTC/JPY", "BTC", "JPY"),
                    "ETH/JPY": market("ETH/JPY", "ETH", "JPY"),
                    "SOL/JPY": market("SOL/JPY", "SOL", "JPY"),
                }
            def fetch_depth_books(self, *args, **kwargs):
                raise AssertionError("no depth request expected without a triangle")
        with patch("docich.trading.cli.BitbankPublicGateway", return_value=StarGateway()):
            rc, out, err = self.run_cli(["trading", "arbitrage-depth-scan"])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["candidate_count"], 0)
        self.assertEqual(payload["depth_market_count"], 0)

    def test_reports_size_probes_with_depth_slippage_and_no_fill(self):
        class TriangleGateway:
            def discover_markets(self):
                return triangle_markets()
            def fetch_depth_books(self, symbols, *, now, limit=20):
                self.last_symbols = tuple(symbols)
                return triangle_depth(now)
            def fetch_circuit_break_statuses(self, symbols):
                return {symbol: CircuitBreakStatus(symbol, "NONE", "NORMAL", NOW) for symbol in symbols}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("docich.trading.cli.time.time", return_value=NOW), \
                 patch("docich.trading.cli.BitbankPublicGateway", return_value=TriangleGateway()):
                rc, out, err = self.run_cli([
                    "trading", "--state-dir", str(Path(tmp) / "state"),
                    "arbitrage-depth-scan", "--min-edge-bps", "20",
                    "--probe-asset", "JPY", "--probe-amounts", "1000,3000,10000",
                ])
            self.assertEqual(rc, 0, err)
            payload = json.loads(out)
            self.assertGreaterEqual(payload["candidate_count"], 1)
            candidate = payload["candidates"][0]
            probes = candidate["depth_simulations"]
            self.assertEqual([p["start_amount"] for p in probes], ["1000", "3000", "10000"])
            settlements = candidate["settlements"]
            self.assertEqual([p["start_amount"] for p in settlements], ["1000", "3000", "10000"])
            self.assertTrue(settlements[0]["complete"])
            self.assertIn("order_base_amount", settlements[0]["legs"][0])
            self.assertEqual(settlements[0]["failure_reason"], None)
            self.assertTrue(probes[0]["complete"])
            self.assertGreater(D(probes[0]["net_edge_bps"]), D("0"))
            self.assertLess(D(probes[-1]["net_edge_bps"]), D(probes[0]["net_edge_bps"]))
            self.assertGreater(probes[-1]["legs"][0]["levels_used"], 1)
            first_leg = probes[0]["legs"][0]
            self.assertIn("fee_paid_base", first_leg)
            self.assertIn("fee_paid_quote", first_leg)
            self.assertIn("traded_base_amount", first_leg)
            self.assertNotIn("fee_paid", first_leg)
            self.assertFalse(payload.get("fills"))
            self.assertFalse((Path(tmp) / "state" / "paper.sqlite3").exists())

    def test_settlement_uses_time_after_public_fetches_for_status_freshness(self):
        class TriangleGateway:
            def discover_markets(self):
                return triangle_markets()
            def fetch_depth_books(self, symbols, *, now, limit=20):
                return triangle_depth(now)
            def fetch_circuit_break_statuses(self, symbols):
                return {
                    symbol: CircuitBreakStatus(symbol, "NONE", "NORMAL", NOW - 600, fetched_at=NOW + 1.5)
                    for symbol in symbols
                }
        with patch("docich.trading.cli.time.time", side_effect=[NOW, NOW + 2]), \
             patch("docich.trading.cli.BitbankPublicGateway", return_value=TriangleGateway()):
            rc, out, err = self.run_cli([
                "trading", "arbitrage-depth-scan", "--min-edge-bps", "20",
                "--probe-asset", "JPY", "--probe-amounts", "1000",
            ])
        self.assertEqual(rc, 0, err)
        settlement = json.loads(out)["candidates"][0]["settlements"][0]
        self.assertTrue(settlement["complete"])

    def test_circuit_break_blocks_constraint_settlement(self):
        class TriangleGateway:
            def discover_markets(self):
                return triangle_markets()
            def fetch_depth_books(self, symbols, *, now, limit=20):
                return triangle_depth(now)
            def fetch_circuit_break_statuses(self, symbols):
                result = {symbol: CircuitBreakStatus(symbol, "NONE", "NORMAL", NOW) for symbol in symbols}
                result["ETH/BTC"] = CircuitBreakStatus("ETH/BTC", "CIRCUIT_BREAK", "NORMAL", NOW)
                return result
        with patch("docich.trading.cli.time.time", return_value=NOW), \
             patch("docich.trading.cli.BitbankPublicGateway", return_value=TriangleGateway()):
            rc, out, err = self.run_cli([
                "trading", "arbitrage-depth-scan", "--min-edge-bps", "20",
                "--probe-asset", "JPY", "--probe-amounts", "1000",
            ])
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)
        self.assertGreaterEqual(payload["candidate_count"], 1)
        settlement = payload["candidates"][0]["settlements"][0]
        self.assertFalse(settlement["complete"])
        self.assertEqual(settlement["failed_leg_symbol"], "ETH/BTC")
        self.assertEqual(settlement["failure_reason"], "circuit_break:CIRCUIT_BREAK")

    def test_invalid_probe_amount_fails_closed(self):
        with patch("docich.trading.cli.BitbankPublicGateway"):
            rc, out, err = self.run_cli([
                "trading", "arbitrage-depth-scan", "--probe-amounts", "1000,-1"
            ])
        self.assertEqual(rc, 2)
        self.assertIn("probe", err.lower())
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
