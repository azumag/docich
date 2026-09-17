from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ops" / "vm_actions"))

from nethack_promotion_runner import PromotionRunSpec, run_improvement_cycle, run_promotion  # noqa: E402

CATALOG = ROOT / "config" / "nethack-canary-actions.json"


def fake_worker(*, baseline=(100, 2), candidate=(120, 3), write_candidate_trace=True):
    def worker(request_text, *, docker, image, extra_env=None):
        is_candidate = bool(extra_env) and "DOCICH_CANARY_CATALOG" in extra_env
        turns, depth = candidate if is_candidate else baseline
        request = json.loads(request_text)
        if is_candidate and write_candidate_trace:
            trace = Path(request["arena"]["episode_root"]) / "action-trace.jsonl"
            trace.write_text("{}\n", encoding="utf-8")
        return {
            "worker_status": "completed",
            "terminal_status": "timeout",
            "turns": turns,
            "max_depth": depth,
            "score": None,
            "exit_reason": "max_turns",
            "production_state_touched": False,
        }
    return worker


class RunnerTests(unittest.TestCase):
    def spec(self):
        return PromotionRunSpec(
            candidate_id="cand-v2",
            baseline_id="baseline-v1",
            candidate_catalog=CATALOG,
            seeds=(1, 2, 3),
        )

    def test_promotes_non_regressing_catalog_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, base, cand = run_promotion(
                self.spec(),
                work_root=Path(tmp),
                worker=fake_worker(baseline=(100, 2), candidate=(120, 3)),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
            )
        self.assertTrue(decision.promote)
        self.assertEqual(len(base.outcomes), 3)
        self.assertEqual(len(cand.outcomes), 3)
        self.assertEqual(cand.trace_unverified, 0)

    def test_rejects_missing_candidate_trace_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision, _base, cand = run_promotion(
                self.spec(),
                work_root=Path(tmp),
                worker=fake_worker(write_candidate_trace=False),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
            )
        self.assertFalse(decision.promote)
        self.assertIn("trace_unverified", decision.reasons)
        self.assertEqual(cand.trace_unverified, 3)

    def test_rejects_empty_candidate_trace_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(),
        ):
            decision, _base, cand = run_promotion(
                self.spec(),
                work_root=Path(tmp),
                worker=fake_worker(),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
            )
        self.assertFalse(decision.promote)
        self.assertIn("trace_unverified", decision.reasons)
        self.assertEqual(cand.trace_unverified, 3)

    def test_rejects_regressing_catalog_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, _base, _cand = run_promotion(
                self.spec(),
                work_root=Path(tmp),
                worker=fake_worker(baseline=(100, 3), candidate=(400, 1)),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
            )
        self.assertFalse(decision.promote)
        self.assertIn("fitness_regression", decision.reasons)

    def test_rejects_when_smoke_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, _base, _cand = run_promotion(
                self.spec(),
                work_root=Path(tmp),
                worker=fake_worker(),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
                smoke_ok=False,
            )
        self.assertFalse(decision.promote)
        self.assertIn("smoke_gate_failed", decision.reasons)

    def test_improvement_cycle_proposes_verifies_and_gates(self):
        from dataclasses import replace

        from docich.nethack_action_spec import load_action_catalog

        class FakeProposer:
            def propose(self, request, *, allowed_effects):
                specs = load_action_catalog(CATALOG)
                return tuple(
                    replace(spec, enabled=False) if spec.id == "rest" else spec for spec in specs
                )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, signal, specs, base, cand = run_improvement_cycle(
                self.spec(),
                proposer=FakeProposer(),
                baseline_catalog=CATALOG,
                work_root=Path(tmp),
                worker=fake_worker(baseline=(100, 2), candidate=(120, 3)),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
            )
        self.assertTrue(decision.promote)
        self.assertTrue(signal.exit_reason)
        self.assertIn("rest", {spec.id for spec in specs})
        self.assertEqual(len(base.outcomes), 3)
        self.assertEqual(len(cand.outcomes), 3)


if __name__ == "__main__":
    unittest.main()
