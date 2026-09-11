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

    def test_minimum_order_beyond_hard_cap_is_still_skipped(self):
        # When the exchange minimum itself exceeds the hard per-opportunity cap,
        # the order cannot be executed within policy and is skipped.
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

    def test_minimum_order_is_bumped_within_hard_cap(self):
        # A fraction-sized order below the exchange minimum is bumped up to the
        # minimum (rounded up to the step) when it still fits the hard cap, so
        # the bot can execute instead of always skipping.
        result = allocate_opportunities(
            [opportunity("bump", fraction="0.08")],
            markets={"BTC/JPY": market(amount_step="1", min_amount="10", min_cost="1000")},
            prices={"BTC/JPY": D("100")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("10000")},
            capital_reference=D("10000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertEqual(len(result.decisions), 1)
        decision = result.decisions[0]
        self.assertEqual(decision.amount, D("10"))
        self.assertGreaterEqual(decision.quote_notional, D("1000"))
        self.assertLessEqual(decision.reference_notional, D("3000"))

    def test_full_deployment_reports_cap_exhausted_not_below_min(self):
        # A tiny remaining budget must report the real reason (cap exhausted),
        # not below_min_amount.
        result = allocate_opportunities(
            [opportunity("one", fraction="0.08")],
            markets={"BTC/JPY": market(amount_step="0.0001", min_amount="0.0001")},
            prices={"BTC/JPY": D("10000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("7000")},
            capital_reference=D("10000"),
            deployed_reference=D("2999.9999954"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "total_cap_exhausted")

    def test_min_cost_floor_bumps_amount_even_when_min_amount_is_met(self):
        # min_amount is satisfied, but the quote notional is below min_cost, so
        # the size must grow to meet the cost floor (within the hard cap).
        result = allocate_opportunities(
            [opportunity("cost", fraction="0.05")],
            markets={"BTC/JPY": market(amount_step="1", min_amount="1", min_cost="1000")},
            prices={"BTC/JPY": D("100")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("10000")},
            capital_reference=D("10000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertEqual(len(result.decisions), 1)
        self.assertGreaterEqual(result.decisions[0].quote_notional, D("1000"))

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

    def test_sell_is_allocated_when_inventory_exists(self):
        result = allocate_opportunities(
            [opportunity("sell", side="sell")],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("1000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("0")},
            available_base={"BTC/JPY": D("0.002")},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertEqual(len(result.decisions), 1)
        decision = result.decisions[0]
        self.assertEqual(decision.side, "sell")
        self.assertEqual(decision.amount, D("0.002"))
        self.assertEqual(decision.quote_notional, D("2000"))

    def test_sell_without_inventory_is_skipped(self):
        result = allocate_opportunities(
            [opportunity("sell", side="sell")],
            markets={"BTC/JPY": market()},
            prices={"BTC/JPY": D("1000000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("0")},
            available_base={},
            capital_reference=D("100000"),
            deployed_reference=D("0"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        self.assertFalse(result.decisions)
        self.assertEqual(result.skipped[0].reason_code, "no_inventory")

    def test_sell_proceeds_fund_a_new_buy_in_the_same_pass(self):
        result = allocate_opportunities(
            [
                opportunity("buy", fraction="1"),
                opportunity("exit", symbol="ETH/JPY", side="sell", score="0.9"),
            ],
            markets={"BTC/JPY": market(), "ETH/JPY": market("ETH/JPY", base="ETH")},
            prices={"BTC/JPY": D("1000000"), "ETH/JPY": D("1000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("0")},
            available_base={"ETH/JPY": D("10")},
            capital_reference=D("100000"),
            deployed_reference=D("30000"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        kinds = [(d.side, d.symbol) for d in result.decisions]
        self.assertIn(("sell", "ETH/JPY"), kinds)
        self.assertIn(("buy", "BTC/JPY"), kinds)

    def test_profitable_sell_cannot_expand_same_cycle_total_deployment_cap(self):
        result = allocate_opportunities(
            [
                opportunity("buy-a", "BTC/JPY", score="0.9", fraction="1"),
                opportunity("buy-b", "XRP/JPY", score="0.8", fraction="1"),
                opportunity("exit", "ETH/JPY", side="sell", score="1"),
            ],
            markets={
                "BTC/JPY": market("BTC/JPY", base="BTC", amount_step="1", min_amount="1"),
                "XRP/JPY": market("XRP/JPY", base="XRP", amount_step="1", min_amount="1"),
                "ETH/JPY": market("ETH/JPY", base="ETH", amount_step="1", min_amount="1"),
            },
            prices={"BTC/JPY": D("1000"), "XRP/JPY": D("1000"), "ETH/JPY": D("1000")},
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": D("0")},
            available_base={"ETH/JPY": D("40")},
            capital_reference=D("100000"),
            deployed_reference=D("30000"),
            policy=CapitalPolicy(),
            now=NOW,
        )
        buys = [d for d in result.decisions if d.side == "buy"]
        self.assertEqual(sum(d.reference_notional for d in buys), D("30000"))
        self.assertEqual([d.opportunity_id for d in buys], ["buy-a"])


if __name__ == "__main__":
    unittest.main()
