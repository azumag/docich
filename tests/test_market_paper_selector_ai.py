import datetime as dt
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from docich.trading.markets.lab import write_json
from docich.trading.markets.selector import Candidate, SelectorPolicy
from docich.trading.markets.selector_lab import (
    _bounded_candidate,
    advance_selector_challenger,
    finalize_selector_challenger,
    propose_selector,
    selector_assessment,
)


def moment(text="2026-09-16T09:10:00+09:00"):
    return dt.datetime.fromisoformat(text).timestamp()


def row(now, symbol="7203", price="1000", momentum="50", spread="5"):
    return Candidate(
        symbol=symbol,
        ts=now,
        price=price,
        turnover_jpy="50000000",
        momentum_bps=momentum,
        volume_accel="2",
        volatility_bps="30",
        spread_bps=spread,
        tradeable=True,
    )


class SelectorAIContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = moment()
        self.base = SelectorPolicy()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ai_cannot_change_freshness_or_exit_safety(self):
        with self.assertRaises(ValueError):
            _bounded_candidate(self.base, {"candidate_age_s": 60})
        with self.assertRaises(ValueError):
            _bounded_candidate(self.base, {"exit_buffer_s": 0})

    def test_ai_delta_is_bounded(self):
        changed = _bounded_candidate(self.base, {"momentum_weight": "1.25", "candidate_count": 25})
        self.assertEqual(changed.momentum_weight, "1.25")
        self.assertEqual(changed.candidate_count, 25)
        with self.assertRaises(ValueError):
            _bounded_candidate(self.base, {"momentum_weight": "2.00"})
        with self.assertRaises(ValueError):
            _bounded_candidate(self.base, {"min_turnover_jpy": "25000000"})

    def test_proposal_starts_shadow_without_changing_active_policy(self):
        fake = json.dumps({"changes": {"momentum_weight": "1.25"}, "reason": "短期変動を少し重視"})
        result = propose_selector(
            self.root, object(), agents="fixture", report_id="report-1",
            report={"net_pnl_jpy": "0"}, now=self.now, generate=lambda _prompt: fake,
        )
        self.assertEqual(result["status"], "forward_test")
        marker = json.loads((self.root / "selector-challenger.json").read_text())
        self.assertEqual(marker["candidate"]["momentum_weight"], "1.25")
        self.assertFalse(marker["live_enabled"])
        self.assertFalse((self.root / "active-selector-policy.json").exists())

    def test_missing_ai_configuration_fails_closed(self):
        result = propose_selector(
            self.root, object(), agents="", report_id="report-1",
            report={}, now=self.now, generate=None,
        )
        self.assertEqual(result["status"], "needs_ai_configuration")
        self.assertFalse((self.root / "selector-challenger.json").exists())

    def test_future_snapshots_accumulate_without_early_adoption(self):
        fake = json.dumps({"changes": {"momentum_weight": "1.25"}, "reason": "test"})
        propose_selector(
            self.root, object(), agents="fixture", report_id="report-1",
            report={}, now=self.now, generate=lambda _prompt: fake,
        )
        first = [row(self.now, "7203", "1000", "100"), row(self.now, "9984", "2000", "80")]
        second = [row(self.now + 5, "7203", "1010", "100"), row(self.now + 5, "9984", "1990", "80")]
        advance_selector_challenger(self.root, first, now=self.now, session_start=self.now - 120)
        result = advance_selector_challenger(self.root, second, now=self.now + 5, session_start=self.now - 120)
        self.assertEqual(result["status"], "collecting")
        self.assertFalse((self.root / "active-selector-policy.json").exists())
        experiment = json.loads((self.root / "selector-experiment.json").read_text())
        self.assertGreater(experiment["arms"]["baseline"]["samples"], 0)
        self.assertGreater(experiment["arms"]["candidate"]["samples"], 0)

    def _write_ready(self, *, candidate_sum="400", baseline_sum="200", candidate_churn=1,
                     baseline_churn=1):
        candidate_policy = SelectorPolicy(momentum_weight="1.25")
        marker = {
            "id": "a" * 24,
            "baseline": asdict(self.base),
            "candidate": asdict(candidate_policy),
            "created_at": self.now,
            "report_id": "report-1",
            "reason": "fixture",
            "mode": "paper",
            "live_enabled": False,
        }
        experiment = {
            "id": "a" * 24,
            "created_at": self.now,
            "observations": 100,
            "days": ["2026-09-16", "2026-09-17"],
            "arms": {
                "baseline": {"samples": 100, "opportunity_sum_bps": baseline_sum,
                             "churn": baseline_churn, "focused_symbols": [], "prices": {}, "last_replaced_at": 0},
                "candidate": {"samples": 100, "opportunity_sum_bps": candidate_sum,
                              "churn": candidate_churn, "focused_symbols": [], "prices": {}, "last_replaced_at": 0},
            },
        }
        write_json(self.root / "selector-challenger.json", marker)
        write_json(self.root / "selector-experiment.json", experiment)
        return candidate_policy

    def test_qualified_selector_waits_for_flat_then_adopts(self):
        candidate_policy = self._write_ready()
        assessment = selector_assessment(self.root)
        self.assertEqual(assessment["status"], "qualified")
        waiting = finalize_selector_challenger(self.root, now=self.now + 1, has_positions=True)
        self.assertEqual(waiting["status"], "awaiting_flat")
        self.assertTrue((self.root / "selector-challenger.json").exists())
        self.assertFalse((self.root / "active-selector-policy.json").exists())
        adopted = finalize_selector_challenger(self.root, now=self.now + 2, has_positions=False)
        self.assertEqual(adopted["status"], "paper_adopted")
        active = json.loads((self.root / "active-selector-policy.json").read_text())
        self.assertEqual(active, {"policy": asdict(candidate_policy)})
        self.assertFalse((self.root / "selector-challenger.json").exists())
        self.assertTrue((self.root / "selector-experiments" / ("a" * 24) / "verdict.json").exists())

    def test_inferior_selector_is_rejected_and_archived(self):
        self._write_ready(candidate_sum="100", baseline_sum="300")
        assessment = selector_assessment(self.root)
        self.assertEqual(assessment["status"], "rejected")
        rejected = finalize_selector_challenger(self.root, now=self.now + 1, has_positions=False)
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse((self.root / "active-selector-policy.json").exists())
        self.assertFalse((self.root / "selector-challenger.json").exists())

    def test_completed_report_is_not_reproposed(self):
        fake = json.dumps({"changes": {"momentum_weight": "1.25"}, "reason": "test"})
        self._write_ready()
        finalize_selector_challenger(self.root, now=self.now + 1, has_positions=False)
        calls = []
        result = propose_selector(
            self.root, object(), agents="fixture", report_id="report-1", report={},
            now=self.now + 2, generate=lambda prompt: calls.append(prompt) or fake,
        )
        self.assertEqual(result["status"], "paper_adopted")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
