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

    def test_improvement_cycle_enables_reviewed_search_candidate(self):
        # Only the external command and game process are doubles. Catalog
        # validation, policy, frame normalization, trace verifier and gate are
        # real. These fixture frames/outcomes do NOT measure game performance.
        from dataclasses import replace

        from docich.nethack_action_spec import load_action_catalog, verify_trace_file
        from docich.nethack_canary_tactics import CanaryTacticalPolicy
        from docich.nethack_catalog_proposer import CommandCatalogProposer, catalog_to_dict
        from docich.nethack_observation import normalize_tty

        baseline_specs = tuple(
            replace(spec, enabled=False) if spec.id == "search_when_blocked" else spec
            for spec in load_action_catalog(CATALOG)
        )

        def enable_search(command, *, input, **kwargs):
            request = json.loads(input)
            self.assertEqual(request["failure"]["stall_intent"], "rest")
            self.assertNotIn("keys", request["allowed_new_action_effects"])
            raw = request["catalog"]
            self.assertEqual(raw, catalog_to_dict(baseline_specs))
            search = next(a for a in raw["actions"] if a["id"] == "search_when_blocked")
            self.assertFalse(search["enabled"])
            search["enabled"] = True
            return SimpleNamespace(returncode=0, stdout=json.dumps(raw).encode())

        def frame(turn):
            # A complete 80x24 TTY frame, not an empty verifier placeholder.
            lines = ["", "-----", "-@---", "-----"] + [""] * 19
            lines.append(f"HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:{turn}")
            return normalize_tty("\n".join(lines) + "\n", cols=80, rows=24)

        for corruption in (None, "keys", "precondition"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                work_root = Path(tmp)
                baseline_path = work_root / "baseline-catalog.json"
                baseline_path.write_text(json.dumps(catalog_to_dict(baseline_specs)), encoding="utf-8")
                intents = []

                def worker(request_text, *, docker, image, extra_env=None):
                    request = json.loads(request_text)
                    root = Path(request["arena"]["episode_root"])
                    is_candidate = "DOCICH_CANARY_CATALOG" in extra_env
                    # The runner's baseline uses the installed/default catalog;
                    # emulate an installation with reviewed search disabled.
                    catalog = root / "catalog.json" if is_candidate else baseline_path
                    policy = CanaryTacticalPolicy(specs=load_action_catalog(catalog))
                    before, after = frame(50), frame(51)
                    action = policy.decide(before)
                    policy.assert_safe(action)
                    expected = "search_when_blocked" if is_candidate else "rest"
                    self.assertEqual(action.intent, expected)
                    keys = [a.text for a in action.actions]
                    self.assertEqual(keys, ["s"] if is_candidate else ["."])
                    intents.append(action.intent)
                    record = {"intent": action.intent, "keys": keys,
                              "before": before.raw_text, "after": after.raw_text}
                    if is_candidate and corruption == "keys":
                        record["keys"] = ["."]
                    if is_candidate and corruption == "precondition":
                        record["before"] = before.raw_text.replace("-@---", "-@.--")
                    trace = root / "action-trace.jsonl"
                    trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
                    if not is_candidate:
                        self.assertTrue(all(v.verified for v in verify_trace_file(trace, baseline_specs)))
                    return {
                        "worker_status": "completed", "terminal_status": "timeout",
                        "turns": 120 if is_candidate else 100,
                        "max_depth": 3 if is_candidate else 2, "score": 100,
                        "exit_reason": "max_turns" if is_candidate else "policy_stall:rest",
                        "production_state_touched": False,
                    }

                decision, signal, specs, base, cand = run_improvement_cycle(
                    self.spec(),
                    proposer=CommandCatalogProposer(("fixture-proposer",), runner=enable_search),
                    baseline_catalog=baseline_path,
                    work_root=work_root,
                    worker=worker,
                    docker="/usr/bin/docker",
                    image="sha256:" + "a" * 64,
                )
                expected_specs = tuple(
                    replace(s, enabled=True) if s.id == "search_when_blocked" else s
                    for s in baseline_specs
                )
                self.assertEqual(specs, expected_specs)  # enabled is the only data diff
                self.assertEqual(signal.stall_intent, "rest")
                self.assertEqual(intents, ["rest"] * 3 + ["search_when_blocked"] * 3)
                self.assertEqual(len(base.outcomes), 3)
                self.assertEqual(len(cand.outcomes), 3)
                self.assertEqual(decision.promote, corruption is None)
                self.assertEqual(cand.trace_unverified, 0 if corruption is None else 3)
                self.assertEqual(decision.reasons, () if corruption is None else ("trace_unverified",))

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
