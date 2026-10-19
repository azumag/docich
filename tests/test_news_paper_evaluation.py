"""Tests for News PAPER evaluation module."""
import datetime as dt
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from docich.trading.news_paper.evaluation import (
    EvaluationManifest,
    ArmMetrics,
    CostBreakdown,
    LatencyBreakdown,
    NewsUtilization,
    PolicyTracking,
    ComparisonGate,
    FillRecord,
    PositionEpisode,
    EvaluationResult,
    compute_drawdown,
    compute_win_rate,
    compute_turnover,
    compute_latency_breakdown,
    build_arm_metrics,
    run_evaluation,
    export_evaluation,
    verify_reproducibility,
    D,
    decimal,
)


class TestEvaluationManifest(unittest.TestCase):
    def test_manifest_digest_deterministic(self):
        m1 = EvaluationManifest(
            manifest_version="1",
            ledger_revision="abc123",
            evidence_digest="def456",
            eval_start_ts=1000.0,
            eval_end_ts=2000.0,
            rng_seed=42,
        )
        m2 = EvaluationManifest(
            manifest_version="1",
            ledger_revision="abc123",
            evidence_digest="def456",
            eval_start_ts=1000.0,
            eval_end_ts=2000.0,
            rng_seed=42,
        )
        self.assertEqual(m1.digest(), m2.digest())

    def test_manifest_digest_changes_with_params(self):
        m1 = EvaluationManifest(eval_start_ts=1000.0, rng_seed=1)
        m2 = EvaluationManifest(eval_start_ts=1000.0, rng_seed=2)
        self.assertNotEqual(m1.digest(), m2.digest())


class TestComputeDrawdown(unittest.TestCase):
    def test_drawdown_basic(self):
        series = [
            (1000.0, D("100")),
            (1010.0, D("110")),  # peak
            (1020.0, D("90")),   # trough
            (1030.0, D("95")),
        ]
        dd, peak_ts, trough_ts = compute_drawdown(series)
        # Peak 110, trough 90 => DD = 20/110 = 0.1818...
        self.assertAlmostEqual(float(dd), 20/110, places=4)
        self.assertEqual(peak_ts, 1010.0)
        self.assertEqual(trough_ts, 1020.0)

    def test_drawdown_no_drop(self):
        series = [(1000.0, D("100")), (1010.0, D("110")), (1020.0, D("120"))]
        dd, _, _ = compute_drawdown(series)
        self.assertEqual(dd, D("0"))

    def test_drawdown_empty(self):
        dd, peak_ts, trough_ts = compute_drawdown([])
        self.assertEqual(dd, D("0"))
        self.assertEqual(peak_ts, 0.0)
        self.assertEqual(trough_ts, 0.0)


class TestComputeWinRate(unittest.TestCase):
    def test_win_rate_basic(self):
        episodes = [
            PositionEpisode("TEST", 1, 1000, 1100, D("100"), D("110"), D("10"), D("1"), D("1"), D("0"), D("90"), 100, True),
            PositionEpisode("TEST", 1, 1200, 1300, D("100"), D("90"), D("10"), D("1"), D("1"), D("0"), D("-90"), 100, False),
            PositionEpisode("TEST", 1, 1400, 1500, D("100"), D("105"), D("10"), D("1"), D("1"), D("0"), D("40"), 100, True),
        ]
        total, wins, rate = compute_win_rate(episodes)
        self.assertEqual(total, 3)
        self.assertEqual(wins, 2)
        self.assertAlmostEqual(rate, 2/3, places=4)

    def test_win_rate_empty(self):
        total, wins, rate = compute_win_rate([])
        self.assertEqual(total, 0)
        self.assertEqual(wins, 0)
        self.assertEqual(rate, 0.0)


class TestComputeTurnover(unittest.TestCase):
    def test_turnover_basic(self):
        fills = [
            FillRecord("open", "TEST", 1, D("10"), D("100"), D("1"), ts=1000.0),
            FillRecord("close", "TEST", 1, D("10"), D("110"), D("1"), ts=1100.0),
        ]
        turnover = compute_turnover(fills, D("1000"))
        # Notional = 10*100 + 10*110 = 2100, avg_equity = 1000 => 2.1
        self.assertAlmostEqual(turnover, 2.1, places=4)

    def test_turnover_zero_denominator(self):
        fills = [FillRecord("open", "TEST", 1, D("10"), D("100"), D("1"), ts=1000.0)]
        turnover = compute_turnover(fills, D("0"))
        self.assertIsNone(turnover)

    def test_turnover_none_with_no_fills(self):
        turnover = compute_turnover([], D("1000"))
        self.assertIsNone(turnover)


class TestComputeLatencyBreakdown(unittest.TestCase):
    def test_latency_complete(self):
        events = [
            {"published_at": 1000.0, "fetched_at": 1000.1, "available_at": 1000.2,
             "classified_at": 1000.3, "decided_at": 1000.4, "filled_at": 1000.5},
        ]
        latency = compute_latency_breakdown(events)
        self.assertAlmostEqual(latency.published_to_fetched_ms, 100, places=0)
        self.assertAlmostEqual(latency.fetched_to_available_ms, 100, places=0)
        self.assertAlmostEqual(latency.available_to_classification_ms, 100, places=0)
        self.assertAlmostEqual(latency.classification_to_decision_ms, 100, places=0)
        self.assertAlmostEqual(latency.decision_to_fill_ms, 100, places=0)
        self.assertAlmostEqual(latency.total_ms, 500, places=0)

    def test_latency_partial_missing(self):
        events = [
            {"published_at": 1000.0, "fetched_at": 1000.1},  # missing rest
        ]
        latency = compute_latency_breakdown(events)
        self.assertAlmostEqual(latency.published_to_fetched_ms, 100, places=0)
        self.assertIsNone(latency.fetched_to_available_ms)
        self.assertIn("fetched_to_available_ms", latency.missing_stages)


class TestBuildArmMetrics(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()

    def make_fill(self, kind, symbol, side, qty, price, fee, ts, net_pnl="0", financing="0", partial=False, original_qty=None):
        return FillRecord(
            kind=kind, symbol=symbol, side=side, qty=D(str(qty)), price=D(str(price)),
            fee=D(str(fee)), financing=D(str(financing)), net_pnl_jpy=D(str(net_pnl)),
            ts=ts, partial=partial, original_qty=D(str(original_qty)) if original_qty else None
        )

    def test_build_arm_metrics_basic(self):
        fills = [
            self.make_fill("open", "TEST", 1, 10, 100, 1, self.now),
            self.make_fill("close", "TEST", 1, 10, 110, 1, self.now + 100, "90"),
        ]
        equity_series = [(self.now, D("1000")), (self.now + 100, D("1090"))]
        positions = {}
        latency_events = [{"published_at": self.now, "fetched_at": self.now + 1,
                          "available_at": self.now + 2, "classified_at": self.now + 3,
                          "decided_at": self.now + 4, "filled_at": self.now + 5}]
        news_events = [
            {"event_id": "evt1", "eligible": True, "used_for_signal": True, "used_for_decision": True,
             "valid": True, "duplicate": False, "held": False, "rejected": False,
             "observation_gap_seconds": 0, "match_type": "human", "data_gap_seconds": 0},
        ]
        cost_breakdown = CostBreakdown(
            commission_jpy=D("2"), slippage_jpy=D("1"), financing_jpy=D("0"),
            llm_api_cost_jpy=D("100"), data_api_cost_jpy=D("50"),
            total_trading_cost_jpy=D("3"), total_operating_cost_jpy=D("150"),
        )
        policy_tracking = PolicyTracking(
            policies_tested=1, train_events=10, val_events=5, holdout_events=5,
            independent_events=15
        )

        metrics = build_arm_metrics(
            fills, equity_series, positions, latency_events, news_events,
            cost_breakdown, policy_tracking, "price_only", D("1045")
        )

        self.assertEqual(metrics.label, "price_only")
        self.assertEqual(metrics.completed_episodes, 1)
        self.assertEqual(metrics.wins, 1)
        self.assertEqual(metrics.win_rate, 1.0)
        self.assertEqual(metrics.realized_pnl_jpy, D("90"))
        self.assertAlmostEqual(float(metrics.max_drawdown_fraction), 0, places=4)
        self.assertIsNotNone(metrics.turnover)
        self.assertEqual(metrics.news_util.unique_eligible_events, 1)
        self.assertEqual(metrics.news_util.events_used_for_signal, 1)
        self.assertEqual(metrics.news_util.events_used_for_decision, 1)


class TestComparisonGate(unittest.TestCase):
    def test_gate_pass_same_conditions(self):
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertTrue(gate.cost_risk_fill_match)
        self.assertEqual(gate.violations, [])

    def test_gate_fail_cost_mismatch(self):
        cb_a = CostBreakdown(cost_allocation_method="pro_rata")
        cb_b = CostBreakdown(cost_allocation_method="fixed")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb_a,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb_b,
                          news_util=NewsUtilization(cost_allocation_method="fixed"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertFalse(gate.cost_risk_fill_match)
        self.assertIn("cost_allocation_method mismatch", gate.violations)

    def test_gate_fail_synthetic_mixed(self):
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          policy_tracking=PolicyTracking(synthetic_historical_results=True))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertFalse(gate.cost_risk_fill_match)
        self.assertTrue(gate.synthetic_mixed)


class TestRunEvaluation(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()

    def make_fill(self, kind, symbol, side, qty, price, fee, ts, net_pnl="0", financing="0"):
        return FillRecord(
            kind=kind, symbol=symbol, side=side, qty=D(str(qty)), price=D(str(price)),
            fee=D(str(fee)), financing=D(str(financing)), net_pnl_jpy=D(str(net_pnl)), ts=ts
        )

    def test_run_evaluation_basic(self):
        manifest = EvaluationManifest(
            eval_start_ts=self.now,
            eval_end_ts=self.now + 3600,
            arm_a_label="price_only",
            arm_b_label="news_plus_price",
            rng_seed=42,
        )

        fills_a = [
            self.make_fill("open", "TEST", 1, 10, 100, 1, self.now),
            self.make_fill("close", "TEST", 1, 10, 110, 1, self.now + 100, "90"),
        ]
        fills_b = [
            self.make_fill("open", "TEST", 1, 10, 100, 1, self.now),
            self.make_fill("close", "TEST", 1, 10, 115, 1, self.now + 100, "140"),
        ]
        equity_a = [(self.now, D("1000")), (self.now + 100, D("1090"))]
        equity_b = [(self.now, D("1000")), (self.now + 100, D("1140"))]
        positions = {}
        latency = [{"published_at": self.now, "fetched_at": self.now + 1,
                    "available_at": self.now + 2, "classified_at": self.now + 3,
                    "decided_at": self.now + 4, "filled_at": self.now + 5}]
        news = [{"event_id": "evt1", "eligible": True, "used_for_signal": True,
                 "used_for_decision": True, "valid": True, "duplicate": False,
                 "held": False, "rejected": False, "observation_gap_seconds": 0,
                 "match_type": "human", "data_gap_seconds": 0}]
        cb = CostBreakdown(
            commission_jpy=D("2"), slippage_jpy=D("1"), financing_jpy=D("0"),
            llm_api_cost_jpy=D("100"), data_api_cost_jpy=D("50"),
            total_trading_cost_jpy=D("3"), total_operating_cost_jpy=D("150"),
            cost_allocation_method="pro_rata"
        )
        pt = PolicyTracking(policies_tested=1, train_events=10, val_events=5,
                           holdout_events=5, independent_events=15)

        result = run_evaluation(
            manifest, fills_a, equity_a, positions, latency, news,
            fills_b, equity_b, positions, latency, news,
            cb, cb, pt, D("1045"), D("1070"), "test_command"
        )

        self.assertEqual(result.manifest.arm_a_label, "price_only")
        self.assertEqual(result.manifest.arm_b_label, "news_plus_price")
        self.assertFalse(result.live_eligible)
        self.assertEqual(result.arm_a.label, "price_only")
        self.assertEqual(result.arm_b.label, "news_plus_price")
        self.assertIn(result.pass_fail_skip, ("pass", "fail"))


class TestExportEvaluation(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()

    def make_fill(self, kind, symbol, side, qty, price, fee, ts, net_pnl="0"):
        return FillRecord(
            kind=kind, symbol=symbol, side=side, qty=D(str(qty)), price=D(str(price)),
            fee=D(str(fee)), net_pnl_jpy=D(str(net_pnl)), ts=ts
        )

    def test_export_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "eval.json"
            manifest = EvaluationManifest(eval_end_ts=self.now + 3600, rng_seed=1)
            fills = [self.make_fill("open", "TEST", 1, 10, 100, 1, self.now)]
            equity = [(self.now, D("1000"))]
            latency = []
            news = []
            cb = CostBreakdown(cost_allocation_method="pro_rata")
            pt = PolicyTracking()

            result = run_evaluation(
                manifest, fills, equity, {}, latency, news,
                fills, equity, {}, latency, news,
                cb, cb, pt, D("1000"), D("1000"), "test"
            )

            export_evaluation(result, out_path)

            self.assertTrue(out_path.exists())
            with out_path.open() as f:
                data = json.load(f)
            self.assertEqual(data["live_eligible"], False)
            self.assertIn("manifest", data)
            self.assertIn("arm_a", data)
            self.assertIn("arm_b", data)


class TestVerifyReproducibility(unittest.TestCase):
    def test_verify_reproducibility_match(self):
        manifest = EvaluationManifest(rng_seed=42)
        prev = manifest.digest()
        curr = manifest.digest()
        result = verify_reproducibility(manifest, prev, curr)
        self.assertTrue(result["reproducible"])

    def test_verify_reproducibility_mismatch(self):
        manifest = EvaluationManifest(rng_seed=42)
        result = verify_reproducibility(manifest, "abc", "def")
        self.assertFalse(result["reproducible"])


class TestFixturesHandCalculation(unittest.TestCase):
    """Verify metrics match hand calculation for key fixtures."""

    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()

    def make_fill(self, kind, symbol, side, qty, price, fee, ts, net_pnl="0", financing="0"):
        return FillRecord(
            kind=kind, symbol=symbol, side=side, qty=D(str(qty)), price=D(str(price)),
            fee=D(str(fee)), financing=D(str(financing)), net_pnl_jpy=D(str(net_pnl)), ts=ts
        )

    def test_buy_only_no_close(self):
        """Buy only, no close - should have 0 completed episodes, open position."""
        fills = [self.make_fill("open", "TEST", 1, 10, 100, 1, self.now)]
        equity = [(self.now, D("1000"))]
        positions = {"TEST": {"side": 1, "qty": "10", "entry": "100", "mark": "105", "mark_ts": self.now}}
        latency = []
        news = []
        cb = CostBreakdown(
            commission_jpy=D("1"), slippage_jpy=D("1"), financing_jpy=D("0"),
            llm_api_cost_jpy=D("0"), data_api_cost_jpy=D("0"),
            total_trading_cost_jpy=D("2"), total_operating_cost_jpy=D("0"),
            cost_allocation_method="pro_rata"
        )
        pt = PolicyTracking()

        metrics = build_arm_metrics(
            fills, equity, positions, latency, news, cb, pt, "price_only", D("1000")
        )

        self.assertEqual(metrics.completed_episodes, 0)
        self.assertEqual(metrics.wins, 0)
        self.assertEqual(metrics.win_rate, 0.0)
        self.assertEqual(metrics.open_positions, 1)
        self.assertEqual(metrics.unresolved_positions, 1)

    def test_partial_sell(self):
        """Partial sell - track as partial fill, not full episode."""
        fills = [
            self.make_fill("open", "TEST", 1, 100, 100, 1, self.now),
            self.make_fill("close", "TEST", 1, 30, 110, 1, self.now + 100, "297"),  # partial
        ]
        equity = [(self.now, D("10000")), (self.now + 100, D("10297"))]
        positions = {"TEST": {"side": 1, "qty": "70", "entry": "100", "mark": "110", "mark_ts": self.now + 100}}
        latency = []
        news = []
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking()

        metrics = build_arm_metrics(fills, equity, positions, latency, news, cb, pt, "price_only", D("10148"))

        # Partial close creates episode but with partial qty
        self.assertEqual(metrics.completed_episodes, 1)
        self.assertEqual(metrics.partial_fills, 0)  # fill itself not marked partial
        self.assertEqual(metrics.unresolved_positions, 1)

    def test_loss_episode(self):
        """Loss episode - win_rate should reflect loss."""
        fills = [
            self.make_fill("open", "TEST", 1, 10, 100, 1, self.now),
            self.make_fill("close", "TEST", 1, 10, 90, 1, self.now + 100, "-102"),
        ]
        equity = [(self.now, D("1000")), (self.now + 100, D("898"))]
        positions = {}
        latency = []
        news = []
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking()

        metrics = build_arm_metrics(fills, equity, positions, latency, news, cb, pt, "price_only", D("949"))

        self.assertEqual(metrics.completed_episodes, 1)
        self.assertEqual(metrics.wins, 0)
        self.assertEqual(metrics.win_rate, 0.0)

    def test_financing_cost_included(self):
        """Financing cost should be included in cost breakdown and exit cost."""
        fills = [
            self.make_fill("open", "TEST", 1, 10, 100, 1, self.now),
            self.make_fill("close", "TEST", 1, 10, 110, 1, self.now + 86400, "90", "50"),  # 1 day financing
        ]
        equity = [(self.now, D("1000")), (self.now + 86400, D("1090"))]
        positions = {}
        latency = []
        news = []
        cb = CostBreakdown(
            commission_jpy=D("2"), slippage_jpy=D("1"), financing_jpy=D("50"),
            llm_api_cost_jpy=D("0"), data_api_cost_jpy=D("0"),
            total_trading_cost_jpy=D("53"), total_operating_cost_jpy=D("0"),
            cost_allocation_method="pro_rata"
        )
        pt = PolicyTracking()

        metrics = build_arm_metrics(fills, equity, positions, latency, news, cb, pt, "price_only", D("1045"))

        # Financing included in realized PnL
        self.assertEqual(metrics.realized_pnl_jpy, D("90"))
        # Cost breakdown includes financing
        self.assertEqual(metrics.cost_breakdown.financing_jpy, D("50"))

    def test_zero_trades(self):
        """Zero trades - all metrics should be zero/None."""
        fills = []
        equity = [(self.now, D("1000"))]
        positions = {}
        latency = []
        news = []
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking()

        metrics = build_arm_metrics(fills, equity, positions, latency, news, cb, pt, "price_only", D("1000"))

        self.assertEqual(metrics.completed_episodes, 0)
        self.assertEqual(metrics.wins, 0)
        self.assertEqual(metrics.win_rate, 0.0)
        self.assertEqual(metrics.realized_pnl_jpy, D("0"))
        self.assertIsNone(metrics.turnover)

    def test_unresolved_position(self):
        """Unresolved position - tracked separately, not in win rate."""
        fills = [self.make_fill("open", "TEST", 1, 10, 100, 1, self.now)]
        equity = [(self.now, D("1000"))]
        positions = {"TEST": {"side": 1, "qty": "10", "entry": "100", "mark": "105", "mark_ts": self.now}}
        latency = []
        news = []
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking()

        metrics = build_arm_metrics(fills, equity, positions, latency, news, cb, pt, "price_only", D("1000"))

        self.assertEqual(metrics.completed_episodes, 0)
        self.assertEqual(metrics.unresolved_positions, 1)
        self.assertEqual(metrics.open_positions, 1)

    def test_missing_data_gaps_tracked(self):
        """Missing data gaps should be tracked in data_gaps_seconds."""
        fills = [self.make_fill("open", "TEST", 1, 10, 100, 1, self.now)]
        equity = [(self.now, D("1000"))]
        positions = {}
        latency = []
        news = [{"data_gap_seconds": 300}, {"data_gap_seconds": 600}]
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking()

        metrics = build_arm_metrics(fills, equity, positions, latency, news, cb, pt, "price_only", D("1000"))

        self.assertEqual(metrics.data_gaps_seconds, 900)


if __name__ == "__main__":
    unittest.main()