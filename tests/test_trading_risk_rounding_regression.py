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


class TestSubunitLotRoundingRegression(unittest.TestCase):
    def test_exchange_minimum_with_fractional_step_rounds_up_and_executes(self):
        market = MarketInfo(
            symbol="BTC/JPY",
            base="BTC",
            quote="JPY",
            spot=True,
            active=True,
            amount_step=D("0.0001"),
            min_amount=D("0.0001"),
            min_cost=D("1"),
            market_order_enabled=True,
        )
        opportunity = Opportunity(
            opportunity_id="btc-min-lot",
            strategy_id="test-v1",
            symbol="BTC/JPY",
            side="buy",
            score=D("0.8"),
            expected_edge_bps=D("25"),
            max_notional_fraction=D("0.08"),
            expires_at=NOW + 60,
            reason_code="test_signal",
        )

        result = allocate_opportunities(
            [opportunity],
            markets={"BTC/JPY": market},
            prices={"BTC/JPY": D("10000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("10000")},
            capital_reference=D("10000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )

        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(result.decisions[0].amount, D("0.0001"))
        self.assertEqual(result.decisions[0].quote_notional, D("1000"))


if __name__ == "__main__":
    unittest.main()
