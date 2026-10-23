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
        # Event-level total latency
        self.assertAlmostEqual(latency.total_ms_median, 500, places=0)
        self.assertAlmostEqual(latency.total_ms_p95, 500, places=0)
        # Stage-level p95
        self.assertAlmostEqual(latency.stage_p95_ms["published_to_fetched_ms"], 100, places=0)

    def test_latency_partial_missing(self):
        events = [
            {"published_at": 1000.0, "fetched_at": 1000.1},  # missing rest
        ]
        latency = compute_latency_breakdown(events)
        self.assertAlmostEqual(latency.published_to_fetched_ms, 100, places=0)
        self.assertIsNone(latency.fetched_to_available_ms)
        self.assertIn("fetched_to_available_ms", latency.missing_stages)
        self.assertIn("available_to_classification_ms", latency.missing_stages)
        self.assertIn("classification_to_decision_ms", latency.missing_stages)
        self.assertIn("decision_to_fill_ms", latency.missing_stages)
        # Total latency should be None since filled_at missing
        self.assertIsNone(latency.total_ms_median)


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
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertTrue(gate.cost_risk_fill_match)
        self.assertEqual(gate.violations, [])

    def test_gate_fail_cost_mismatch(self):
        cb_a = CostBreakdown(cost_allocation_method="pro_rata")
        cb_b = CostBreakdown(cost_allocation_method="fixed")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb_a,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb_b,
                          news_util=NewsUtilization(cost_allocation_method="fixed"),
                          initial_capital_jpy=D("1000"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertFalse(gate.cost_risk_fill_match)
        self.assertIn("cost_allocation_method mismatch", gate.violations)

    def test_gate_fail_synthetic_mixed(self):
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"),
                          policy_tracking=PolicyTracking(synthetic_historical_results=True))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertFalse(gate.cost_risk_fill_match)
        self.assertTrue(gate.synthetic_mixed)

    def test_gate_fail_initial_capital_mismatch(self):
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("2000"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertFalse(gate.cost_risk_fill_match)
        self.assertIn("initial_capital_jpy mismatch", gate.violations)
        # Symmetric: swapping arms yields the same verdict.
        gate_rev = ComparisonGate().check(arm_b, arm_a)
        self.assertFalse(gate_rev.cost_risk_fill_match)
        self.assertIn("initial_capital_jpy mismatch", gate_rev.violations)

    def test_gate_same_conditions_different_performance_passes(self):
        # avg_equity differs (a RESULT); initial capital (a CONDITION) matches.
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"), avg_equity_jpy=D("1045"))
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"), avg_equity_jpy=D("1070"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertTrue(gate.cost_risk_fill_match)
        self.assertEqual(gate.violations, [])

    def test_gate_initial_capital_unverifiable_fails_closed(self):
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        arm_a = ArmMetrics(label="A", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=None)
        arm_b = ArmMetrics(label="B", cost_breakdown=cb,
                          news_util=NewsUtilization(cost_allocation_method="pro_rata"),
                          initial_capital_jpy=D("1000"))
        gate = ComparisonGate().check(arm_a, arm_b)
        self.assertFalse(gate.cost_risk_fill_match)
        self.assertIn("initial capital unverifiable (not declared)", gate.violations)


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
            ledger_revision="rev1",
            evidence_digest="evi1",
            classification_version="cls1",
            price_version="prc1",
            risk_version="risk1",
            cost_version="cst1",
            execution_version="exe1",
            policy_version="pol1",
            model_version="mdl1",
            min_coverage_fraction=0.0,
            cost_allocation_method="pro_rata",
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


        # min_independent_events set via constructor below is not possible (frozen),
        # so recreate manifest with min_independent_events=5
        manifest = EvaluationManifest(
            eval_start_ts=self.now,
            eval_end_ts=self.now + 3600,
            arm_a_label="price_only",
            arm_b_label="news_plus_price",
            rng_seed=42,
            ledger_revision="rev1",
            evidence_digest="evi1",
            classification_version="cls1",
            price_version="prc1",
            risk_version="risk1",
            cost_version="cst1",
            execution_version="exe1",
            policy_version="pol1",
            model_version="mdl1",
            min_independent_events=5,
            min_coverage_fraction=0.0,
            cost_allocation_method="pro_rata",
        )

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
            manifest = EvaluationManifest(
                eval_end_ts=self.now + 3600, rng_seed=1,
                ledger_revision="rev1", evidence_digest="evi1",
                classification_version="cls1", price_version="prc1",
                risk_version="risk1", cost_version="cst1",
                execution_version="exe1", policy_version="pol1",
                model_version="mdl1", min_independent_events=0,
                min_coverage_fraction=0.0,
                cost_allocation_method="pro_rata"
            )
            fills = [self.make_fill("open", "TEST", 1, 10, 100, 1, self.now)]
            equity = [(self.now, D("1000"))]
            latency = []
            news = []
            cb = CostBreakdown(
                cost_allocation_method="pro_rata",
                exit_commission_bps=D("1"), exit_slippage_bps=D("1")
            )
            pt = PolicyTracking(independent_events=1)

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
        manifest = EvaluationManifest(rng_seed=42, ledger_revision="r", evidence_digest="e",
            classification_version="c", price_version="p", risk_version="r", cost_version="c",
            execution_version="e", policy_version="p", model_version="m")
        fills = [FillRecord("open", "TEST", 1, D("10"), D("100"), D("1"), ts=1000.0)]
        equity = [(1000.0, D("1000"))]
        positions = {"TEST": {"side": "1", "qty": "10", "entry": "100", "mark": "100", "mark_ts": "1000.0"}}
        news = [{"event_id": "evt1", "eligible": True, "observed_at": 1000.0}]
        arm_metrics = ArmMetrics(label="test", equity_jpy=D("1000"))
        result = verify_reproducibility(
            manifest,
            fills, equity, positions, news, arm_metrics,
            fills, equity, positions, news, arm_metrics,
            fills, equity, positions, news, arm_metrics,
            fills, equity, positions, news, arm_metrics,
        )
        self.assertTrue(result["reproducible"])

    def test_verify_reproducibility_mismatch(self):
        manifest = EvaluationManifest(rng_seed=42, ledger_revision="r", evidence_digest="e",
            classification_version="c", price_version="p", risk_version="r", cost_version="c",
            execution_version="e", policy_version="p", model_version="m")
        fills1 = [FillRecord("open", "TEST", 1, D("10"), D("100"), D("1"), ts=1000.0)]
        fills2 = [FillRecord("open", "TEST", 1, D("10"), D("101"), D("1"), ts=1000.0)]
        equity = [(1000.0, D("1000"))]
        positions = {"TEST": {"side": "1", "qty": "10", "entry": "100", "mark": "100", "mark_ts": "1000.0"}}
        news = [{"event_id": "evt1", "eligible": True, "observed_at": 1000.0}]
        arm_metrics = ArmMetrics(label="test", equity_jpy=D("1000"))
        result = verify_reproducibility(
            manifest,
            fills1, equity, positions, news, arm_metrics,
            fills2, equity, positions, news, arm_metrics,
            fills1, equity, positions, news, arm_metrics,
            fills1, equity, positions, news, arm_metrics,
        )
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

        # Partial close does NOT create completed episode (only full close does)
        self.assertEqual(metrics.completed_episodes, 0)
        self.assertEqual(metrics.wins, 0)
        self.assertEqual(metrics.win_rate, 0.0)
        self.assertEqual(metrics.partial_fills, 0)  # fill itself not marked partial
        self.assertEqual(metrics.unresolved_positions, 1)
        # But partial_fills tracking counts partial fills
        self.assertEqual(metrics.partial_fills, 0)  # fill not marked partial=True

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


class TestReviewResiduals(unittest.TestCase):
    """Regression tests for review residuals: coverage evidence, digest scope, export consistency."""

    def setUp(self):
        self.now = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()

    def _manifest(self, **kw):
        base = dict(
            eval_start_ts=self.now, eval_end_ts=self.now + 100,
            min_coverage_fraction=0.8,
            cost_allocation_method="pro_rata",
            ledger_revision="rev1", evidence_digest="evi1",
            classification_version="cls1", price_version="prc1",
            risk_version="risk1", cost_version="cst1",
            execution_version="exe1", policy_version="pol1",
            model_version="mdl1",
        )
        base.update(kw)
        return EvaluationManifest(**base)

    def _news(self, **extra):
        ev = {"event_id": "evt1", "eligible": True, "used_for_signal": True,
              "used_for_decision": True, "valid": True, "duplicate": False,
              "held": False, "rejected": False, "match_type": "human",
              "data_gap_seconds": 0, "observation_gap_seconds": 0}
        ev.update(extra)
        return ev

    def test_observation_field_missing_fails_closed(self):
        """Missing observation_gap_seconds key must NOT be treated as zero observation."""
        news = [self._news()]
        del news[0]["observation_gap_seconds"]
        r = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            CostBreakdown(), CostBreakdown(),
            PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        self.assertEqual(r.pass_fail_skip, "fail")
        self.assertGreaterEqual(r.remaining_items, 1)

    def test_observation_field_present_passes(self):
        """Normal case with explicit zero observation evidence passes."""
        news = [self._news()]
        r = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            CostBreakdown(), CostBreakdown(),
            PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        self.assertEqual(r.pass_fail_skip, "pass")
        self.assertEqual(r.remaining_items, 0)

    def test_coverage_100_with_gaps_fails(self):
        """min_coverage=1.0 with any gap fails."""
        news = [self._news(observation_gap_seconds=1)]
        r = run_evaluation(
            self._manifest(min_coverage_fraction=1.0), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            CostBreakdown(), CostBreakdown(),
            PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        self.assertEqual(r.pass_fail_skip, "fail")

    def test_cost_change_changes_eval_digest(self):
        """Cost parameter change must change evaluation_digest."""
        news = [self._news()]
        cb_a = CostBreakdown(total_operating_cost_jpy=D("0"))
        cb_b = CostBreakdown(total_operating_cost_jpy=D("100"))
        r_a = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            cb_a, cb_a, PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        r_b = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            cb_b, cb_b, PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        self.assertNotEqual(r_a.arm_a.evaluation_digest, r_b.arm_a.evaluation_digest)

    def test_latency_change_changes_eval_digest(self):
        """Latency change must change evaluation_digest."""
        news = [self._news()]
        lat_a = [{"published_at": self.now, "fetched_at": self.now + 0.01,
                  "available_at": self.now + 0.02, "classified_at": self.now + 0.03,
                  "decided_at": self.now + 0.04, "filled_at": self.now + 0.05}]
        lat_b = [{"published_at": self.now, "fetched_at": self.now + 0.02,
                  "available_at": self.now + 0.04, "classified_at": self.now + 0.06,
                  "decided_at": self.now + 0.08, "filled_at": self.now + 0.10}]
        r_a = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, lat_a, news,
            [], [(self.now, D("1000"))], {}, lat_a, news,
            CostBreakdown(), CostBreakdown(),
            PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        r_b = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, lat_b, news,
            [], [(self.now, D("1000"))], {}, lat_b, news,
            CostBreakdown(), CostBreakdown(),
            PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        self.assertNotEqual(r_a.arm_a.evaluation_digest, r_b.arm_a.evaluation_digest)

    def test_export_contains_eval_digest(self):
        """Export must carry the same evaluation_digest as the result."""
        news = [self._news()]
        r = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            CostBreakdown(), CostBreakdown(),
            PolicyTracking(independent_events=15), D("1000"), D("1000"), "cmd")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "eval.json"
            export_evaluation(r, out)
            data = json.loads(out.read_text())
        self.assertEqual(data["arm_a"]["evaluation_digest"], r.arm_a.evaluation_digest)
        self.assertEqual(data["arm_b"]["evaluation_digest"], r.arm_b.evaluation_digest)
        self.assertNotEqual(data["arm_a"]["evaluation_digest"], "")

    def test_policy_tracking_change_changes_eval_digest(self):
        """Policy tracking change must change evaluation_digest."""
        news = [self._news()]
        pt_a = PolicyTracking(independent_events=15)
        pt_b = PolicyTracking(independent_events=0)
        r_a = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            CostBreakdown(), CostBreakdown(),
            pt_a, D("1000"), D("1000"), "cmd")
        r_b = run_evaluation(
            self._manifest(), [], [(self.now, D("1000"))], {}, [], news,
            [], [(self.now, D("1000"))], {}, [], news,
            CostBreakdown(), CostBreakdown(),
            pt_b, D("1000"), D("1000"), "cmd")
        self.assertNotEqual(r_a.arm_a.evaluation_digest, r_b.arm_a.evaluation_digest)
        self.assertNotEqual(r_a.arm_a.policy_tracking.independent_events, r_b.arm_a.policy_tracking.independent_events)


class TestVersionBindingAndMarkAge(unittest.TestCase):
    """N08 version binding in the gate + mark age display (R5/R8 follow-ups)."""

    def _manifest(self, risk_version="r"):
        return EvaluationManifest(
            ledger_revision="x", evidence_digest="y", classification_version="c",
            price_version="p", risk_version=risk_version, cost_version="co",
            execution_version="e", policy_version="pol", model_version="m",
            eval_start_ts=0.0, eval_end_ts=3600.0, min_coverage_fraction=0.0,
            min_independent_events=0,
            cost_allocation_method="pro_rata",
        )

    def _arm(self, cap="1000", fee="10"):
        return ArmMetrics(
            label="A",
            cost_breakdown=CostBreakdown(cost_allocation_method="pro_rata", exit_commission_bps=D(fee)),
            news_util=NewsUtilization(cost_allocation_method="pro_rata"),
            avg_equity_jpy=D(cap),
            initial_capital_jpy=D("1000"),
        )

    def test_gate_records_declared_versions(self):
        m = self._manifest()
        g = ComparisonGate().check(self._arm(), self._arm(), m)
        self.assertTrue(g.cost_risk_fill_match)
        self.assertEqual(g.conditions.get("manifest_risk_version"), "r")
        self.assertEqual(g.conditions.get("manifest_execution_version"), "e")

    def test_gate_flags_missing_declared_versions(self):
        bad = self._manifest(risk_version="")
        g = ComparisonGate().check(self._arm(), self._arm(), bad)
        self.assertFalse(g.cost_risk_fill_match)
        self.assertTrue(any("manifest version fields missing" in v for v in g.violations))

    def test_gate_without_manifest_unchanged(self):
        g = ComparisonGate().check(self._arm(), self._arm())
        self.assertTrue(g.cost_risk_fill_match)
        self.assertNotIn("manifest_risk_version", g.conditions)

    def test_gate_binds_declared_allocation_method(self):
        m = self._manifest()  # declares pro_rata
        arm_ok = ArmMetrics(
            label="A",
            cost_breakdown=CostBreakdown(cost_allocation_method="pro_rata"),
            news_util=NewsUtilization(cost_allocation_method="pro_rata"),
            initial_capital_jpy=D("1000"),
        )
        g_ok = ComparisonGate().check(arm_ok, arm_ok, m)
        self.assertTrue(g_ok.cost_risk_fill_match)
        self.assertEqual(g_ok.conditions.get("manifest_cost_allocation_method"), "pro_rata")
        arm_bad = ArmMetrics(
            label="A",
            cost_breakdown=CostBreakdown(cost_allocation_method="fixed"),
            news_util=NewsUtilization(cost_allocation_method="fixed"),
            initial_capital_jpy=D("1000"),
        )
        g_bad = ComparisonGate().check(arm_bad, arm_bad, m)
        self.assertFalse(g_bad.cost_risk_fill_match)
        self.assertTrue(any("does not match manifest declaration" in v for v in g_bad.violations))

    def test_r3_unset_coverage_and_allocation_fail_closed(self):
        m = EvaluationManifest(
            ledger_revision="x", evidence_digest="y", classification_version="c",
            price_version="p", risk_version="r", cost_version="co",
            execution_version="e", policy_version="pol", model_version="m",
            eval_start_ts=0.0, eval_end_ts=3600.0, min_independent_events=0,
            # min_coverage_fraction and cost_allocation_method intentionally unset
        )
        violations = m.validate()
        self.assertTrue(any("min_coverage_fraction unset" in v for v in violations))
        self.assertTrue(any("cost_allocation_method unset" in v for v in violations))
        # run_evaluation skips (not pass/fail) on the unset manifest
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking(independent_events=1)
        eq = [(0.0, D("1000")), (3600.0, D("1000"))]
        news = [{"event_id": "e1", "eligible": True, "observation_gap_seconds": 0}]
        r = run_evaluation(m, [], eq, {}, [], news, [], eq, {}, [], news, cb, cb, pt,
                           D("1000"), D("1000"), "t")
        self.assertEqual(r.pass_fail_skip, "skip")
        self.assertTrue(any("unset" in v for v in r.limitations))
        # the old 0.8 default must NOT silently operate
        self.assertNotIn("coverage_fraction_unverifiable", " ".join(r.limitations))


    def test_mark_age_displayed(self):
        eq = [(0.0, D("1000")), (1000.0, D("1000"))]
        positions = {"TEST": {"side": 1, "qty": "10", "entry": "100", "mark": "105", "mark_ts": 900.0}}
        m = build_arm_metrics([], eq, positions, [], [], CostBreakdown(cost_allocation_method="pro_rata"),
                              PolicyTracking(), "A", D("1000"))
        self.assertAlmostEqual(m.mark_age_seconds, 100.0, places=3)

    def test_mark_age_unverifiable_without_mark_ts(self):
        eq = [(0.0, D("1000")), (1000.0, D("1000"))]
        positions = {"TEST": {"side": 1, "qty": "10", "entry": "100", "mark": "105"}}
        m = build_arm_metrics([], eq, positions, [], [], CostBreakdown(cost_allocation_method="pro_rata"),
                              PolicyTracking(), "A", D("1000"))
        self.assertIsNone(m.mark_age_seconds)

    def test_holdout_limitation_recorded(self):
        m = self._manifest()
        m_holdout = replace(m, holdout_start_ts=1800.0)
        cb = CostBreakdown(cost_allocation_method="pro_rata")
        pt = PolicyTracking(independent_events=1)
        eq = [(0.0, D("1000")), (3600.0, D("1000"))]
        news = [{"event_id": "e1", "eligible": True, "observation_gap_seconds": 0}]
        r = run_evaluation(m_holdout, [], eq, {}, [], news, [], eq, {}, [], news, cb, cb, pt,
                           D("1000"), D("1000"), "t")
        self.assertTrue(any("holdout_start_ts declared but holdout boundary not enforced" in l
                            for l in r.limitations))


class TestRunExportVerifyRegression(unittest.TestCase):
    """E2E: run_evaluation -> export -> verify_reproducibility on real outputs.

    Guards the single-schema contract between the export digest and the
    reproducibility digest so the two paths cannot silently diverge again.
    """

    def _manifest(self):
        return EvaluationManifest(
            ledger_revision="x", evidence_digest="y", classification_version="c",
            price_version="p", risk_version="r", cost_version="co",
            execution_version="e", policy_version="pol", model_version="m",
            eval_start_ts=0.0, eval_end_ts=3600.0, min_coverage_fraction=0.0,
            min_independent_events=0,
        )

    def _arms(self, total_operating="0"):
        cb = CostBreakdown(cost_allocation_method="pro_rata", total_operating_cost_jpy=D(total_operating))
        pt = PolicyTracking(independent_events=1)
        eq = [(0.0, D("1000")), (3600.0, D("1000"))]
        news = [{"event_id": "e1", "eligible": True, "observation_gap_seconds": 0}]
        arm_a = build_arm_metrics([], eq, {}, [], news, cb, pt, "A", D("1000"))
        arm_b = build_arm_metrics([], eq, {}, [], news, cb, pt, "B", D("1000"))
        return arm_a, arm_b, eq, news

    def _verify(self, manifest, arm_a_prev, arm_a_curr, arm_b_prev, arm_b_curr, eq, news):
        return verify_reproducibility(
            manifest,
            [], eq, {}, news, arm_a_prev,
            [], eq, {}, news, arm_a_curr,
            [], eq, {}, news, arm_b_prev,
            [], eq, {}, news, arm_b_curr,
        )

    def test_e2e_same_inputs_reproducible(self):
        manifest = self._manifest()
        a1, b1, eq, news = self._arms()
        a2, b2, _, _ = self._arms()
        result = run_evaluation(manifest, [], eq, {}, [], news, [], eq, {}, [], news,
                                CostBreakdown(cost_allocation_method="pro_rata"),
                                CostBreakdown(cost_allocation_method="pro_rata"),
                                PolicyTracking(independent_events=1), D("1000"), D("1000"), "t")
        self.assertFalse(result.live_eligible)
        out = self._verify(manifest, a1, a2, b1, b2, eq, news)
        self.assertTrue(out["reproducible"])
        self.assertEqual(a1.evaluation_digest, a2.evaluation_digest)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "eval.json"
            export_evaluation(result, p)
            data = json.loads(p.read_text())
            self.assertFalse(data["live_eligible"])
            self.assertIn("arm_a", data)
            self.assertIn("arm_b", data)

    def test_e2e_cost_change_breaks_repro(self):
        manifest = self._manifest()
        a1, b1, eq, news = self._arms(total_operating="0")
        a2, b2, _, _ = self._arms(total_operating="100")
        out = self._verify(manifest, a1, a2, b1, b2, eq, news)
        self.assertFalse(out["reproducible"])
        self.assertNotEqual(a1.evaluation_digest, a2.evaluation_digest)


if __name__ == "__main__":
    unittest.main()