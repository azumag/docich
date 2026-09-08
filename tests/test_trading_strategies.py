from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import Opportunity  # noqa: E402
from docich.trading.strategies import (  # noqa: E402
    StrategyPolicy,
    scan_opportunities,
    select_diversified_opportunities,
)


D = Decimal
NOW = 1_800_000_000.0


def frame(symbol: str, closes) -> MarketFrame:
    values = tuple(D(str(value)) for value in closes)
    count = len(values)
    return MarketFrame(
        symbol=symbol,
        timeframe_seconds=300,
        timestamps=tuple(NOW - (count - i) * 300 for i in range(count)),
        closes=values,
        volumes=tuple(D("10") for _ in values),
    )


def frame_from_returns(symbol: str, returns) -> MarketFrame:
    price = D("100")
    closes = [price]
    for item in returns:
        price *= D("1") + D(str(item))
        closes.append(price)
    return frame(symbol, closes)


def opportunity(oid: str, symbol: str, score: str) -> Opportunity:
    return Opportunity(
        opportunity_id=oid,
        strategy_id="fixture-v1",
        symbol=symbol,
        side="buy",
        score=D(score),
        expected_edge_bps=D("100"),
        max_notional_fraction=D("0.15"),
        expires_at=NOW + 600,
        reason_code="fixture_signal",
    )


class TestStrategyScan(unittest.TestCase):
    def test_momentum_generates_buy_for_strong_recent_return(self):
        frames = {"BTC/JPY": frame("BTC/JPY", [100, 100, 101, 102, 103, 104, 105])}
        result = scan_opportunities(frames, now=NOW, policy=StrategyPolicy(momentum_threshold_bps=D("300")))
        momentum = [item for item in result if item.strategy_id == "momentum-v1"]
        self.assertEqual(len(momentum), 1)
        self.assertEqual(momentum[0].symbol, "BTC/JPY")
        self.assertEqual(momentum[0].reason_code, "momentum_breakout")
        self.assertLessEqual(momentum[0].max_notional_fraction, D("0.15"))

    def test_mean_reversion_generates_buy_for_statistical_discount(self):
        frames = {"ETH/JPY": frame("ETH/JPY", [100, 101, 99, 100, 101, 100, 99, 100, 101, 90])}
        result = scan_opportunities(frames, now=NOW)
        reversion = [item for item in result if item.strategy_id == "mean-reversion-v1"]
        self.assertEqual(len(reversion), 1)
        self.assertEqual(reversion[0].reason_code, "mean_reversion_discount")
        self.assertGreater(reversion[0].expected_edge_bps, 0)

    def test_opportunity_ids_are_deterministic_and_strategy_scoped(self):
        frames = {"BTC/JPY": frame("BTC/JPY", [100, 100, 101, 102, 103, 104, 105, 90])}
        first = scan_opportunities(frames, now=NOW, policy=StrategyPolicy(momentum_threshold_bps=D("1"), mean_reversion_z=D("-1")))
        second = scan_opportunities(frames, now=NOW, policy=StrategyPolicy(momentum_threshold_bps=D("1"), mean_reversion_z=D("-1")))
        self.assertEqual([x.opportunity_id for x in first], [x.opportunity_id for x in second])
        self.assertEqual(len({x.opportunity_id for x in first}), len(first))


class TestDiversification(unittest.TestCase):
    def test_rejects_lower_scored_highly_correlated_symbol(self):
        frames = {
            "A/JPY": frame("A/JPY", [100, 101, 102, 103, 104, 105, 106, 107]),
            "B/JPY": frame("B/JPY", [200, 202, 204, 206, 208, 210, 212, 214]),
            "C/JPY": frame("C/JPY", [100, 99, 101, 100, 102, 101, 103, 102]),
        }
        result = select_diversified_opportunities(
            [opportunity("a", "A/JPY", "0.9"), opportunity("b", "B/JPY", "0.8"), opportunity("c", "C/JPY", "0.7")],
            frames,
            max_pair_correlation=D("0.85"),
        )
        self.assertEqual([x.opportunity_id for x in result.selected], ["a", "c"])
        self.assertEqual(result.rejected[0].opportunity_id, "b")
        self.assertEqual(result.rejected[0].reason_code, "correlated_exposure")

    def test_keeps_only_highest_score_for_same_symbol(self):
        frames = {"BTC/JPY": frame("BTC/JPY", [100, 101, 100, 102, 101, 103, 102, 104])}
        result = select_diversified_opportunities(
            [opportunity("low", "BTC/JPY", "0.4"), opportunity("high", "BTC/JPY", "0.9")],
            frames,
        )
        self.assertEqual([x.opportunity_id for x in result.selected], ["high"])
        self.assertEqual(result.rejected[0].reason_code, "duplicate_symbol")

    def test_keeps_strongly_negatively_correlated_symbol(self):
        returns = ["0.01", "0.02", "-0.01", "0.03", "-0.02", "0.01", "0.02"]
        inverse = [str(-D(value)) for value in returns]
        frames = {
            "A/JPY": frame_from_returns("A/JPY", returns),
            "B/JPY": frame_from_returns("B/JPY", inverse),
        }
        result = select_diversified_opportunities(
            [opportunity("a", "A/JPY", "0.9"), opportunity("b", "B/JPY", "0.8")],
            frames,
            max_pair_correlation=D("0.85"),
        )
        self.assertEqual([x.opportunity_id for x in result.selected], ["a", "b"])
        self.assertFalse(result.rejected)


if __name__ == "__main__":
    unittest.main()
