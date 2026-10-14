from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.arbitrage import ArbitrageLeg, ArbitrageRoute  # noqa: E402
from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import (  # noqa: E402
    CircuitBreakStatus,
    simulate_multileg_settlement,
)

D = Decimal
NOW = 1_800_000_000.0


def leg(symbol, from_asset, to_asset, side, price):
    return ArbitrageLeg(symbol, from_asset, to_asset, side, D(str(price)), D("0"), D("1"), D("100000"))


def route():
    return ArbitrageRoute(
        "route", "JPY",
        (
            leg("A/JPY", "JPY", "A", "buy", 100),
            leg("B/A", "A", "B", "buy", 2),
            leg("B/JPY", "B", "JPY", "sell", 210),
        ),
        D("0"), D("100000"),
    )


def markets(*, first_min="1", market_order_enabled=True):
    return {
        "A/JPY": MarketInfo("A/JPY", "A", "JPY", True, True, amount_step=D("1"), min_amount=D(first_min), market_order_enabled=market_order_enabled),
        "B/A": MarketInfo("B/A", "B", "A", True, True, amount_step=D("1"), min_amount=D("1")),
        "B/JPY": MarketInfo("B/JPY", "B", "JPY", True, True, amount_step=D("1"), min_amount=D("1")),
    }


def books():
    return {
        "A/JPY": DepthBook("A/JPY", (DepthLevel(D("99"), D("100")),), (DepthLevel(D("100"), D("100")),), NOW),
        "B/A": DepthBook("B/A", (DepthLevel(D("1.9"), D("100")),), (DepthLevel(D("2"), D("100")),), NOW),
        "B/JPY": DepthBook("B/JPY", (DepthLevel(D("210"), D("100")),), (DepthLevel(D("211"), D("100")),), NOW),
    }


def statuses(mode="NONE", as_of=NOW):
    return {symbol: CircuitBreakStatus(symbol, mode, "NORMAL", as_of) for symbol in markets()}


class TestMultiLegSettlement(unittest.TestCase):
    def test_rounds_base_amount_down_and_preserves_start_asset_dust(self):
        result = simulate_multileg_settlement(
            route(), books(), markets(), statuses(), start_amount=D("1050"), start_asset="JPY", now=NOW,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.legs[0].order_base_amount, D("10"))
        self.assertEqual(result.legs[0].residual_input, D("50"))
        self.assertEqual(result.final_amount, D("1100"))
        self.assertEqual(result.residuals["JPY"], D("50"))

    def test_below_minimum_amount_fails_closed(self):
        result = simulate_multileg_settlement(
            route(), books(), markets(first_min="2"), statuses(), start_amount=D("150"), start_asset="JPY", now=NOW,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.failed_leg_symbol, "A/JPY")
        self.assertEqual(result.failure_reason, "below_min_amount")

    def test_stale_depth_fails_closed(self):
        stale_books = books()
        original = stale_books["B/A"]
        stale_books["B/A"] = DepthBook(original.symbol, original.bids, original.asks, NOW - 10)
        result = simulate_multileg_settlement(
            route(), stale_books, markets(), statuses(), start_amount=D("1000"), start_asset="JPY", now=NOW,
            max_book_age_seconds=2,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.failed_leg_symbol, "B/A")
        self.assertEqual(result.failure_reason, "depth_stale")

    def test_missing_amount_constraints_fail_closed(self):
        constrained = markets()
        constrained["B/A"] = MarketInfo("B/A", "B", "A", True, True, amount_step=None, min_amount=D("1"))
        result = simulate_multileg_settlement(
            route(), books(), constrained, statuses(), start_amount=D("1000"), start_asset="JPY", now=NOW,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.failed_leg_symbol, "B/A")
        self.assertEqual(result.failure_reason, "market_constraints_missing")

    def test_market_order_disabled_fails_before_execution(self):
        result = simulate_multileg_settlement(
            route(), books(), markets(market_order_enabled=False), statuses(), start_amount=D("1000"), start_asset="JPY", now=NOW,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.failure_reason, "market_order_disabled")
        self.assertEqual(result.legs, ())

    def test_circuit_break_mode_blocks_market_style_settlement(self):
        state = statuses()
        state["B/A"] = CircuitBreakStatus("B/A", "CIRCUIT_BREAK", "NORMAL", NOW)
        result = simulate_multileg_settlement(
            route(), books(), markets(), state, start_amount=D("1000"), start_asset="JPY", now=NOW,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.failed_leg_symbol, "B/A")
        self.assertEqual(result.failure_reason, "circuit_break:CIRCUIT_BREAK")

    def test_non_normal_fee_type_fails_closed(self):
        state = statuses()
        state["B/A"] = CircuitBreakStatus("B/A", "NONE", "DYNAMIC", NOW)
        result = simulate_multileg_settlement(
            route(), books(), markets(), state, start_amount=D("1000"), start_asset="JPY", now=NOW,
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.failed_leg_symbol, "B/A")
        self.assertEqual(result.failure_reason, "fee_type:DYNAMIC")

    def test_old_exchange_timestamp_is_allowed_when_status_was_fetched_now(self):
        state = statuses()
        state["B/A"] = CircuitBreakStatus("B/A", "NONE", "NORMAL", NOW - 600, fetched_at=NOW)
        result = simulate_multileg_settlement(
            route(), books(), markets(), state, start_amount=D("1000"), start_asset="JPY", now=NOW,
        )
        self.assertTrue(result.complete)

    def test_missing_or_stale_circuit_status_fails_closed(self):
        missing = statuses(); missing.pop("B/A")
        r1 = simulate_multileg_settlement(route(), books(), markets(), missing, start_amount=D("1000"), start_asset="JPY", now=NOW)
        self.assertEqual(r1.failure_reason, "circuit_status_missing")
        stale = statuses(); stale["B/A"] = CircuitBreakStatus("B/A", "NONE", "NORMAL", NOW - 600, fetched_at=NOW - 30)
        r2 = simulate_multileg_settlement(route(), books(), markets(), stale, start_amount=D("1000"), start_asset="JPY", now=NOW, max_status_age_seconds=5)
        self.assertEqual(r2.failure_reason, "circuit_status_stale")


if __name__ == "__main__":
    unittest.main()
