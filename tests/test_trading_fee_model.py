from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.arbitrage import ArbitrageLeg, ArbitrageRoute, TopOfBook, scan_triangular_arbitrage
from docich.trading.depth import DepthBook, DepthLevel, simulate_route_depth
from docich.trading.models import MarketInfo

D = Decimal
NOW = 1_800_000_000.0


def level(price, amount):
    return DepthLevel(D(str(price)), D(str(amount)))


class TestBitbankFeeAssetSemantics(unittest.TestCase):
    def test_buy_quote_fee_consumes_quote_budget_instead_of_base_output(self):
        route = ArbitrageRoute(
            "fee-route", "JPY",
            (
                ArbitrageLeg("BTC/JPY", "JPY", "BTC", "buy", D("100"), D("0.01"), D("0.01"), D("1000"), fee_rate_base=D("0")),
                ArbitrageLeg("ETH/BTC", "BTC", "ETH", "buy", D("1"), D("0"), D("1"), D("1000"), fee_rate_base=D("0")),
                ArbitrageLeg("ETH/JPY", "ETH", "JPY", "sell", D("101"), D("0"), D("101"), D("1000"), fee_rate_base=D("0")),
            ),
            D("0"), D("1000"),
        )
        books = {
            "BTC/JPY": DepthBook("BTC/JPY", (level(99, 100),), (level(100, 100),), NOW),
            "ETH/BTC": DepthBook("ETH/BTC", (level(0.9, 100),), (level(1, 100),), NOW),
            "ETH/JPY": DepthBook("ETH/JPY", (level(101, 100),), (level(102, 100),), NOW),
        }
        result = simulate_route_depth(route, books, start_amount=D("101"), start_asset="JPY")
        self.assertTrue(result.complete)
        self.assertEqual(result.legs[0].fee_paid_quote, D("1"))
        self.assertEqual(result.legs[0].fee_paid_base, D("0"))
        self.assertEqual(result.legs[0].output_amount, D("1"))
        self.assertEqual(result.final_amount, D("101"))

    def test_buy_base_fee_reduces_received_base(self):
        route = ArbitrageRoute(
            "base-fee-route", "JPY",
            (
                ArbitrageLeg("BTC/JPY", "JPY", "BTC", "buy", D("100"), D("0"), D("0.0099"), D("1000"), fee_rate_base=D("0.01")),
                ArbitrageLeg("ETH/BTC", "BTC", "ETH", "buy", D("1"), D("0"), D("1"), D("1000"), fee_rate_base=D("0")),
                ArbitrageLeg("ETH/JPY", "ETH", "JPY", "sell", D("100"), D("0"), D("100"), D("1000"), fee_rate_base=D("0")),
            ),
            D("-100"), D("1000"),
        )
        books = {
            "BTC/JPY": DepthBook("BTC/JPY", (level(99, 100),), (level(100, 100),), NOW),
            "ETH/BTC": DepthBook("ETH/BTC", (level(0.9, 100),), (level(1, 100),), NOW),
            "ETH/JPY": DepthBook("ETH/JPY", (level(100, 100),), (level(101, 100),), NOW),
        }
        result = simulate_route_depth(route, books, start_amount=D("100"), start_asset="JPY")
        self.assertTrue(result.complete)
        self.assertEqual(result.legs[0].fee_paid_base, D("0.01"))
        self.assertEqual(result.legs[0].output_amount, D("0.99"))
        self.assertEqual(result.final_amount, D("99"))

    def test_top_of_book_scanner_uses_separate_fee_assets(self):
        markets = {
            "BTC/JPY": MarketInfo("BTC/JPY", "BTC", "JPY", True, True, taker_fee_rate_base=D("0"), taker_fee_rate_quote=D("0.01")),
            "ETH/BTC": MarketInfo("ETH/BTC", "ETH", "BTC", True, True, taker_fee_rate_base=D("0"), taker_fee_rate_quote=D("0")),
            "ETH/JPY": MarketInfo("ETH/JPY", "ETH", "JPY", True, True, taker_fee_rate_base=D("0"), taker_fee_rate_quote=D("0")),
        }
        books = {
            "BTC/JPY": TopOfBook("BTC/JPY", D("99"), D("100"), NOW, D("100"), D("100")),
            "ETH/BTC": TopOfBook("ETH/BTC", D("0.9"), D("1"), NOW, D("100"), D("100")),
            "ETH/JPY": TopOfBook("ETH/JPY", D("101"), D("102"), NOW, D("100"), D("100")),
        }
        routes = scan_triangular_arbitrage(markets, books, now=NOW, min_net_edge_bps=D("-1"))
        jpy_route = next(route for route in routes if any(leg.from_asset == "JPY" for leg in route.legs))
        buy_leg = next(leg for leg in jpy_route.legs if leg.symbol == "BTC/JPY" and leg.side == "buy")
        self.assertEqual(buy_leg.fee_rate_quote, D("0.01"))
        self.assertEqual(buy_leg.fee_rate_base, D("0"))
        self.assertEqual(buy_leg.multiplier, D("1") / D("101"))


if __name__ == "__main__":
    unittest.main()
