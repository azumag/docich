from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.arbitrage import ArbitrageLeg, ArbitrageRoute
from docich.trading.depth import DepthBook, DepthLevel, simulate_route_depth

D = Decimal
NOW = 1_800_000_000.0


def level(price, amount):
    return DepthLevel(D(str(price)), D(str(amount)))


def route():
    legs = (
        ArbitrageLeg("BTC/JPY", "JPY", "BTC", "buy", D("100"), D("0.001"), D("0.00999"), D("10000")),
        ArbitrageLeg("ETH/BTC", "BTC", "ETH", "buy", D("0.05"), D("0.001"), D("19.98"), D("100")),
        ArbitrageLeg("ETH/JPY", "ETH", "JPY", "sell", D("5.2"), D("0.001"), D("5.1948"), D("100")),
    )
    return ArbitrageRoute("route", "JPY", legs, D("380"), D("500"))


def books():
    return {
        "BTC/JPY": DepthBook("BTC/JPY", bids=(level(99, 20),), asks=(level(100, 5), level(110, 20)), as_of=NOW),
        "ETH/BTC": DepthBook("ETH/BTC", bids=(level(0.049, 500),), asks=(level(0.05, 500),), as_of=NOW),
        "ETH/JPY": DepthBook("ETH/JPY", bids=(level(5.2, 100), level(4.8, 500)), asks=(level(5.3, 500),), as_of=NOW),
    }


class TestDepthRouteSimulation(unittest.TestCase):
    def test_small_trade_uses_best_levels_and_keeps_positive_edge(self):
        result = simulate_route_depth(route(), books(), start_amount=D("100"), start_asset="JPY")
        self.assertTrue(result.complete)
        self.assertGreater(result.net_edge_bps, D("0"))
        self.assertEqual(result.legs[0].levels_used, 1)
        self.assertEqual(result.start_asset, "JPY")

    def test_larger_trade_consumes_deeper_levels_and_has_worse_edge(self):
        small = simulate_route_depth(route(), books(), start_amount=D("100"), start_asset="JPY")
        large = simulate_route_depth(route(), books(), start_amount=D("1000"), start_asset="JPY")
        self.assertTrue(large.complete)
        self.assertGreater(large.legs[0].levels_used, 1)
        self.assertLess(large.net_edge_bps, small.net_edge_bps)

    def test_insufficient_depth_stops_route_without_inventing_liquidity(self):
        result = simulate_route_depth(route(), books(), start_amount=D("10000"), start_asset="JPY")
        self.assertFalse(result.complete)
        self.assertIsNone(result.net_edge_bps)
        self.assertIsNotNone(result.failed_leg_symbol)
        self.assertLess(result.legs[-1].filled_input, result.legs[-1].input_amount)

    def test_can_rotate_cycle_to_requested_start_asset(self):
        btc_start = ArbitrageRoute("route", "BTC", route().legs[1:] + route().legs[:1], D("380"), D("5"))
        result = simulate_route_depth(btc_start, books(), start_amount=D("100"), start_asset="JPY")
        self.assertTrue(result.complete)
        self.assertEqual(result.start_asset, "JPY")
        self.assertEqual(result.legs[0].from_asset, "JPY")


if __name__ == "__main__":
    unittest.main()
