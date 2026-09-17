from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ops" / "vm_actions"))

from nethack_promotion_runner import PromotionRunSpec, run_promotion  # noqa: E402

CATALOG = ROOT / "config" / "nethack-canary-actions.json"


def fake_worker(*, baseline=(100, 2), candidate=(120, 3)):
    def worker(request_text, *, docker, image, extra_env=None):
        is_candidate = bool(extra_env) and "DOCICH_CANARY_CATALOG" in extra_env
        turns, depth = candidate if is_candidate else baseline
        request = json.loads(request_text)
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
        with tempfile.TemporaryDirectory() as tmp:
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

    def test_rejects_regressing_catalog_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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


if __name__ == "__main__":
    unittest.main()
