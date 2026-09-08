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


def market(
    symbol: str = "BTC/JPY",
    *,
    base: str = "BTC",
    quote: str = "JPY",
    amount_step: str = "0.0001",
    min_amount: str = "0.0001",
    min_cost: str = "1",
    market_order_enabled: bool = True,
) -> MarketInfo:
    return MarketInfo(
        symbol=symbol,
        base=base,
        quote=quote,
        spot=True,
        active=True,
        amount_step=D(amount_step),
        min_amount=D(min_amount),
        min_cost=D(min_cost),
        market_order_enabled=market_order_enabled,
    )


def opportunity(
    oid: str,
    symbol: str = "BTC/JPY",
    *,
    score: str = "0.8",
    fraction: str = "1",
    side: str = "buy",
    expires_at: float = NOW + 60,
) -> Opportunity:
    return Opportunity(
        opportunity_id=oid,
        strategy_id="test-v1",
        symbol=symbol,
        side=side,
        score=D(score),
        expected_edge_bps=D("25"),
        max_notional_fraction=D(fraction),
        expires_at=expires_at,
        reason_code="test_signal",
    )


class TestCapitalAllocator(unittest.TestCase):
    def test_caps_single_opportunity_and_total_at_thirty_percent(self):
        result = allocate_opportunities(
            [opportunity("one")],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("10000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertEqual(len(result.decisions), 1)
        self.assertLessEqual(result.decisions[0].reference_notional, D("30000"))

    def test_higher_score_gets_capacity_first(self):
        result = allocate_opportunities(
            [opportunity("low", score="0.2"), opportunity("high", score="0.9")],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("1000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(max_opportunity_fraction=D("0.20")),
            now=NOW,
        )
        self.assertEqual([d.opportunity_id for d in result.decisions], ["high", "low"])
        self.assertEqual(sum(d.reference_notional for d in result.decisions), D("30000"))

    def test_non_jpy_quote_uses_explicit_reference_rate(self):
        eth_usdt = market(
            "ETH/USDT",
            base="ETH",
            quote="USDT",
            amount_step="0.001",
            min_amount="0.001",
            min_cost="1",
        )
        result = allocate_opportunities(
            [opportunity("eth", "ETH/USDT")],
            markets={"ETH/USDT": eth_usdt},
            prices={"ETH/USDT": D("2500")},
            quote_to_reference={"USDT": D("150")},
            available_quote={"USDT": D("500")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        decision = result.decisions[0]
        self.assertEqual(decision.quote, "USDT")
        self.assertLessEqual(decision.reference_notional, D("30000"))
        self.assertEqual(decision.reference_notional, decision.quote_notional * D("150"))

    def test_unknown_quote_conversion_is_skipped(self):
        result = allocate_opportunities(
            [opportunity("eth", "ETH/USDT")],
            markets={"ETH/USDT": market("ETH/USDT", base="ETH", quote="USDT")},
            prices={"ETH/USDT": D("2500")},
            quote_to_reference={},
            available_quote={"USDT": D("500")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "quote_unvalued")

    def test_missing_quote_balance_is_skipped(self):
        result = allocate_opportunities(
            [opportunity("eth", "ETH/USDT")],
            markets={"ETH/USDT": market("ETH/USDT", base="ETH", quote="USDT")},
            prices={"ETH/USDT": D("2500")},
            quote_to_reference={"USDT": D("150")},
            available_quote={},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "quote_unavailable")

    def test_expired_opportunity_is_skipped(self):
        result = allocate_opportunities(
            [opportunity("old", expires_at=NOW)],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("10000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "expired")

    def test_minimum_order_is_not_rounded_up(self):
        tiny = market(amount_step="0.001", min_amount="0.01", min_cost="1000")
        result = allocate_opportunities(
            [opportunity("tiny", fraction="0.01")],
            markets={"BTC/JPY": tiny},
            prices={"BTC/JPY": D("10000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertIn(result.skipped[0].reason_code, {"below_min_amount", "below_min_cost"})

    def test_existing_deployment_reduces_total_capacity(self):
        result = allocate_opportunities(
            [opportunity("one")],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("1000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"),
            deployed_reference=D("25000"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertEqual(len(result.decisions), 1)
        self.assertLessEqual(result.decisions[0].reference_notional, D("5000"))

    def test_market_order_disabled_is_skipped(self):
        result = allocate_opportunities(
            [opportunity("disabled")],
            markets={"BTC/JPY": market(market_order_enabled=False)},
            prices={"BTC/JPY": D("1000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"), deployed_reference=D("0"), policy=CapitalPolicy(), now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "market_order_disabled")

    def test_sell_is_skipped_until_inventory_aware_exit_path_exists(self):
        result = allocate_opportunities(
            [opportunity("sell", side="sell")],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("1000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("100000")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "unsupported_side")


if __name__ == "__main__":
    unittest.main()
