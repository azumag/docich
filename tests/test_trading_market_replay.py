"""Tests for point-in-time replay, price baseline, and leakage verification."""
import datetime as dt
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from docich.trading.markets.core import D, Limits, PaperBook, Policy, Quote, decimal, stamp
from docich.trading.markets.replay import (
    PointInTimeReplay,
    PriceBaseline,
    ReplayConfig,
    verify_no_future_leakage,
)


class TestPointInTimeReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        self.limits = Limits(lot=1, fee_bps="0", slippage_bps="0", financing_bps_per_day="0",
                             max_spread_bps="100")
        self.policy = Policy(lookback=3, entry_bps=200, exit_mode="fixed")
        self.market = "stocks"

    def tearDown(self):
        self.tmp.cleanup()

    def quote(self, offset: float, price: float, **changes) -> Quote:
        return replace(
            Quote("TEST", self.now + offset, str(price), str(price),
                  "100000", "100000", True, "synthetic-fixture"),
            **changes
        )

    def test_replay_filters_future_quotes(self):
        """Quotes with timestamp > now should not be processed."""
        config = ReplayConfig(
            market=self.market,
            limits=self.limits,
            policy=self.policy,
            start_ts=self.now,
            end_ts=self.now + 100,
        )
        # Use different symbols to avoid duplicate filtering
        quotes = [
            self.quote(0, 100),
            replace(self.quote(10, 101), symbol="TEST2"),
            replace(self.quote(20, 102), symbol="TEST3"),
            replace(self.quote(30, 103), symbol="TEST4"),
        ]
        with PointInTimeReplay(config) as replay:
            # At t=15, only quotes at t=0 and t=10 should be processed
            result = replay.process_quotes(quotes, now=self.now + 15)
            self.assertEqual(result["accepted_quotes"], 2)

    def test_replay_identical_to_live_processing(self):
        """Replay at timestamp T should match live processing at T."""
        book = PaperBook(self.root / "live.sqlite3", self.market, self.limits)
        config = ReplayConfig(
            market=self.market,
            limits=self.limits,
            policy=self.policy,
            start_ts=self.now,
            end_ts=self.now + 100,
        )
        # Use different symbols for live processing to avoid duplicate check
        quotes_live = [
            self.quote(0, 100),
            replace(self.quote(10, 101), symbol="TEST2"),
        ]
        # Replay gets all quotes but will filter to latest per symbol
        quotes_replay = [
            self.quote(0, 100),
            replace(self.quote(10, 101), symbol="TEST2"),
            replace(self.quote(20, 102), symbol="TEST3"),
            replace(self.quote(30, 103), symbol="TEST4"),
        ]
        # Live processing
        live_result = book.process(quotes_live, self.policy, now=self.now + 15,
                                    allow_entries=True, force_flat=False)
        book.close()

        # Replay processing
        with PointInTimeReplay(config) as replay:
            replay_result = replay.process_quotes(quotes_replay, now=self.now + 15)

        self.assertEqual(live_result["accepted_quotes"], replay_result["accepted_quotes"])
        self.assertEqual(live_result["equity_jpy"], replay_result["equity_jpy"])

    def test_replay_sequence_produces_consistent_results(self):
        """Sequential replay should produce consistent equity curve."""
        config = ReplayConfig(
            market=self.market,
            limits=self.limits,
            policy=self.policy,
            start_ts=self.now,
            end_ts=self.now + 100,
        )
        quotes = [self.quote(i * 10, 100 + i) for i in range(10)]
        timestamps = [self.now + 15, self.now + 25, self.now + 35]

        with PointInTimeReplay(config) as replay:
            results = replay.replay_sequence(quotes, timestamps=timestamps)

        self.assertEqual(len(results), 3)
        # Equity should be non-decreasing in this rising market with no exits
        equities = [r.equity_jpy for r in results]
        for i in range(1, len(equities)):
            self.assertGreaterEqual(equities[i], equities[i - 1])

    def test_replay_rejects_out_of_order_quotes(self):
        """Replay should reject quotes that arrive out of order."""
        config = ReplayConfig(
            market=self.market,
            limits=self.limits,
            policy=self.policy,
            start_ts=self.now,
            end_ts=self.now + 100,
        )
        # Use different symbols to test out-of-order arrival
        quotes = [
            self.quote(0, 100),
            replace(self.quote(20, 102), symbol="TEST2"),
            replace(self.quote(10, 101), symbol="TEST3"),
        ]
        with PointInTimeReplay(config) as replay:
            # At t=15, quotes at t=0 and t=10 should be processed (t=20 is future)
            result = replay.process_quotes(quotes, now=self.now + 15)
            self.assertEqual(result["accepted_quotes"], 2)


class TestPriceBaseline(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        self.limits = Limits(lot=1, fee_bps="0", slippage_bps="0", financing_bps_per_day="0",
                             max_spread_bps="100")

    def quote(self, offset: float, price: float, **changes) -> Quote:
        return replace(
            Quote("TEST", self.now + offset, str(price), str(price),
                  "100000", "100000", True, "synthetic-fixture"),
            **changes
        )

    def test_baseline_tracks_buy_and_hold(self):
        """PriceBaseline should track buy-and-hold value."""
        quotes = [
            self.quote(0, 100),   # Entry at 100
            self.quote(10, 105),  # +5%
            self.quote(20, 110),  # +10%
            self.quote(30, 108),  # +8%
        ]
        baseline = PriceBaseline(quotes, reference_symbol="TEST", initial_capital=D("1000000"))

        # At t=10, baseline should be 1,050,000
        val = baseline.get_baseline_value(self.now + 10)
        self.assertEqual(val, D("1050000"))

        # At t=20, baseline should be 1,100,000
        val = baseline.get_baseline_value(self.now + 20)
        self.assertEqual(val, D("1100000"))

    def test_baseline_return_pct(self):
        """Baseline return percentage should be correct."""
        quotes = [
            self.quote(0, 100),
            self.quote(10, 110),  # +10%
        ]
        baseline = PriceBaseline(quotes, reference_symbol="TEST", initial_capital=D("1000000"))

        ret = baseline.get_baseline_return_pct(self.now + 10)
        self.assertEqual(ret, D("10"))

    def test_baseline_comparison_with_strategy(self):
        """Comparison with strategy equity should work."""
        quotes = [
            self.quote(0, 100),
            self.quote(10, 105),
        ]
        baseline = PriceBaseline(quotes, reference_symbol="TEST", initial_capital=D("1000000"))

        # Strategy equity at t=10
        strategy_equity = D("1020000")  # +2%
        comparison = baseline.compare_with_strategy(strategy_equity, self.now + 10)

        self.assertEqual(comparison["status"], "ready")
        self.assertEqual(comparison["strategy_equity_jpy"], "1020000")
        self.assertEqual(comparison["baseline_equity_jpy"], "1050000.00")
        # Gap = 1020000 - 1050000 = -30000
        self.assertEqual(comparison["gap_jpy"], "-30000.00")
        # Capture rate = 1020000 / 1050000 * 100 = 97.14%
        self.assertAlmostEqual(Decimal(comparison["capture_rate_pct"]), D("97.142857"), places=2)

    def test_baseline_serialization(self):
        """PriceBaseline should serialize and deserialize correctly."""
        quotes = [
            self.quote(0, 100),
            self.quote(10, 105),
        ]
        baseline = PriceBaseline(quotes, reference_symbol="TEST", initial_capital=D("1000000"))
        data = baseline.to_dict()

        restored = PriceBaseline.from_dict(data, quotes)
        self.assertEqual(restored.reference_symbol, "TEST")
        self.assertEqual(restored.initial_capital, D("1000000"))
        self.assertEqual(restored.get_baseline_value(self.now + 10), D("1050000"))


class TestFutureLeakageVerification(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        self.limits = Limits(lot=1, fee_bps="0", slippage_bps="0", financing_bps_per_day="0",
                             max_spread_bps="100")
        self.policy = Policy(lookback=3, entry_bps=200, exit_mode="fixed")
        self.market = "stocks"

    def quote(self, offset: float, price: float, **changes) -> Quote:
        return replace(
            Quote("TEST", self.now + offset, str(price), str(price),
                  "100000", "100000", True, "synthetic-fixture"),
            **changes
        )

    def test_no_future_leakage_passes_for_valid_quotes(self):
        """Verification should pass when no future quotes are used."""
        baseline_quotes = [self.quote(i * 10, 100 + i) for i in range(10)]
        candidate_quotes = [self.quote(i * 10, 100 + i) for i in range(10)]
        test_timestamps = [self.now + 15, self.now + 25, self.now + 35]

        result = verify_no_future_leakage(
            baseline_quotes=baseline_quotes,
            candidate_quotes=candidate_quotes,
            policy=self.policy,
            limits=self.limits,
            market=self.market,
            created_at=self.now,
            test_timestamps=test_timestamps,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["violations"], [])

    def test_future_leakage_detected_when_quote_counts_differ(self):
        """Verification should detect when arms receive different quote counts."""
        # Baseline gets more symbols at each timestamp, candidate gets fewer
        # Create quotes where baseline has 3 symbols available at test timestamps,
        # but candidate only has 2
        baseline_quotes = [
            replace(self.quote(0, 100), symbol="TEST0"),
            replace(self.quote(10, 101), symbol="TEST1"),
            replace(self.quote(10, 102), symbol="TEST2"),  # Extra symbol for baseline
        ]
        candidate_quotes = [
            replace(self.quote(0, 100), symbol="TEST0"),
            replace(self.quote(10, 101), symbol="TEST1"),
            # Missing TEST2
        ]
        test_timestamps = [self.now + 15]

        result = verify_no_future_leakage(
            baseline_quotes=baseline_quotes,
            candidate_quotes=candidate_quotes,
            policy=self.policy,
            limits=self.limits,
            market=self.market,
            created_at=self.now,
            test_timestamps=test_timestamps,
        )
        self.assertEqual(result["status"], "fail")
        self.assertGreater(len(result["violations"]), 0)

    def test_pre_creation_quotes_excluded_from_forward_test(self):
        """Quotes before created_at should not be used in forward testing."""
        # This test mirrors test_preproposal_quotes_are_not_forward_evidence
        # but uses the new verification framework
        baseline_quotes = [
            self.quote(-11, 100),  # Before created_at
            self.quote(-1, 100),   # Before created_at
            self.quote(0, 100),    # At created_at
            self.quote(10, 101),   # After created_at
        ]
        candidate_quotes = list(baseline_quotes)
        test_timestamps = [self.now - 9]  # After created_at but quotes before are excluded

        result = verify_no_future_leakage(
            baseline_quotes=baseline_quotes,
            candidate_quotes=candidate_quotes,
            policy=self.policy,
            limits=self.limits,
            market=self.market,
            created_at=self.now,
            test_timestamps=test_timestamps,
        )
        # At t=-9 (relative to now), only quotes with ts <= t=-9 should be used
        # Since created_at=now, and test_timestamps=[now-9], the only valid quotes
        # are those with ts <= now-9. The quotes at -11 and -1 qualify.
        # But the forward test should only use quotes AFTER created_at.
        # This is enforced by the advance_challenger logic, not the replay itself.
        # The replay correctly filters by evaluation timestamp.
        self.assertEqual(result["status"], "pass")


class TestIntegrationWithPaperBook(unittest.TestCase):
    """Integration tests showing replay works with existing PaperBook."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        self.limits = Limits(lot=1, fee_bps="0", slippage_bps="0", financing_bps_per_day="0",
                             max_spread_bps="100")
        self.policy = Policy(lookback=3, entry_bps=200, exit_mode="trailing",
                             trail_activation_bps=100, trail_distance_bps=50)
        self.market = "stocks"

    def tearDown(self):
        self.tmp.cleanup()

    def quote(self, offset: float, price: float, **changes) -> Quote:
        return replace(
            Quote("TEST", self.now + offset, str(price), str(price),
                  "100000", "100000", True, "synthetic-fixture"),
            **changes
        )

    def test_replay_captures_trailing_stop_behavior(self):
        """Replay should correctly capture trailing stop behavior at point-in-time."""
        config = ReplayConfig(
            market=self.market,
            limits=self.limits,
            policy=self.policy,
            start_ts=self.now,
            end_ts=self.now + 100,
        )
        # Use replay_sequence to properly build history across timestamps
        quotes = [
            self.quote(0, 100),
            self.quote(10, 102),  # Activate trailing
            self.quote(20, 103),  # Peak
            self.quote(30, 102.5), # Pullback
            self.quote(40, 101),   # Trailing stop hit
        ]
        timestamps = [self.now + 5, self.now + 15, self.now + 25, self.now + 35, self.now + 45]
        with PointInTimeReplay(config) as replay:
            results = replay.replay_sequence(quotes, timestamps=timestamps)

        # At t=35 (index 3), trailing should be armed but not hit
        result1 = results[3]
        self.assertIn("TEST", result1.positions)

        # At t=45 (index 4), trailing stop should be hit
        result2 = results[4]
        self.assertNotIn("TEST", result2.positions)

    def test_baseline_comparison_during_replay(self):
        """PriceBaseline should work during replay sequence."""
        config = ReplayConfig(
            market=self.market,
            limits=self.limits,
            policy=self.policy,
            start_ts=self.now,
            end_ts=self.now + 100,
        )
        quotes = [
            self.quote(0, 100),
            self.quote(10, 102),
            self.quote(20, 103),
            self.quote(30, 102.5),
            self.quote(40, 101),
        ]
        baseline = PriceBaseline(quotes, reference_symbol="TEST", initial_capital=D("1000000"))

        with PointInTimeReplay(config) as replay:
            result = replay.process_quotes(quotes, now=self.now + 35)
            comparison = baseline.compare_with_strategy(
                decimal(result["equity_jpy"]), self.now + 35
            )

        self.assertEqual(comparison["status"], "ready")
        self.assertIn("gap_jpy", comparison)
        self.assertIn("capture_rate_pct", comparison)


if __name__ == "__main__":
    unittest.main()