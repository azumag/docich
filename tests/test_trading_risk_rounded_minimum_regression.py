from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.models import MarketInfo, Opportunity  # noqa: E402
from docich.trading.risk import CapitalPolicy, allocate_opportunities  # noqa: E402


D = Decimal
NOW = 1_800_000_000.0


def stepped_market() -> MarketInfo:
    return MarketInfo(
        symbol="TEST/JPY",
        base="TEST",
        quote="JPY",
        spot=True,
        active=True,
        amount_step=D("2"),
        min_amount=D("2.1"),
        min_cost=D("1"),
        market_order_enabled=True,
    )


def buy() -> Opportunity:
    return Opportunity(
        opportunity_id="rounded-min",
        strategy_id="test-v1",
        symbol="TEST/JPY",
        side="buy",
        score=D("0.8"),
        expected_edge_bps=D("25"),
        max_notional_fraction=D("1"),
        expires_at=NOW + 60,
        reason_code="test_signal",
    )


class TestRoundedMinimumLimits(unittest.TestCase):
    def test_rounded_minimum_cannot_cross_hard_opportunity_cap(self):
        # The raw minimum is 210 JPY, but exchange lot rounding makes the
        # executable minimum 4 units = 400 JPY. The hard cap is 300 JPY.
        result = allocate_opportunities(
            [buy()],
            markets={"TEST/JPY": stepped_market()},
            prices={"TEST/JPY": D("100")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("1000")},
            capital_reference=D("1000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(
                max_opportunity_fraction=D("0.30"),
                max_total_deployed_fraction=D("1"),
            ),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "below_min_amount")

    def test_rounded_minimum_cannot_overdraw_quote_funding(self):
        # The raw minimum appears to fit 300 JPY, but the rounded executable
        # minimum costs 400 JPY and must fail closed instead of overdrawing.
        result = allocate_opportunities(
            [buy()],
            markets={"TEST/JPY": stepped_market()},
            prices={"TEST/JPY": D("100")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("300")},
            capital_reference=D("1000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(
                max_opportunity_fraction=D("1"),
                max_total_deployed_fraction=D("1"),
            ),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "quote_unavailable")


if __name__ == "__main__":
    unittest.main()
