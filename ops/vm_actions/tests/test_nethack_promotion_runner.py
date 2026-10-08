from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ops" / "vm_actions"))

from nethack_promotion_runner import (  # noqa: E402
    PromotionRunError,
    PromotionRunSpec,
    run_improvement_cycle,
    run_promotion,
)

CATALOG = ROOT / "config" / "nethack-canary-actions.json"


def arm_of(request_text):
    """The experiment arm of a request.

    The wire ``arm`` field is the controller kind (``baseline`` for both
    promotion arms), so the arena path carries the experiment arm.
    """
    request = json.loads(request_text)
    return Path(request["arena"]["episode_root"]).parts[-3]


def fake_worker(*, baseline=(100, 2), candidate=(120, 3), write_trace_arms=("baseline", "candidate")):
    def worker(request_text, *, docker, image, extra_env=None):
        arm = arm_of(request_text)
        turns, depth = candidate if arm == "candidate" else baseline
        request = json.loads(request_text)
        if arm in write_trace_arms:
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


def isolation_ok():
    return True


class RunnerTests(unittest.TestCase):
    def spec(self, **kwargs):
        params = {
            "candidate_id": "cand-v2",
            "baseline_id": "baseline-v1",
            "candidate_catalog": CATALOG,
            "baseline_catalog": CATALOG,
            "seeds": (1, 2, 3),
        }
        params.update(kwargs)
        return PromotionRunSpec(**params)

    def promote(self, spec, worker, tmp, **kwargs):
        kwargs.setdefault("regression_green", True)
        kwargs.setdefault("smoke_ok", True)
        kwargs.setdefault("isolation_check", isolation_ok)
        return run_promotion(
            spec,
            work_root=Path(tmp),
            worker=worker,
            docker="/usr/bin/docker",
            image="sha256:" + "a" * 64,
            **kwargs,
        )

    def test_promotes_non_regressing_catalog_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, base, cand = self.promote(
                self.spec(), fake_worker(baseline=(100, 2), candidate=(120, 3)), tmp
            )
        self.assertTrue(decision.promote)
        self.assertEqual(len(base.outcomes), 3)
        self.assertEqual(len(cand.outcomes), 3)
        self.assertEqual(cand.trace_unverified, 0)
        self.assertEqual(base.trace_unverified, 0)

    def test_promotes_when_the_real_trace_verifier_accepts_both_arms(self):
        # Only the external command and game process are doubles here: the
        # catalog loader and the P6c trace verifier are the real ones.
        from dataclasses import replace

        from docich.nethack_action_spec import load_action_catalog
        from docich.nethack_canary_tactics import CanaryTacticalPolicy
        from docich.nethack_observation import normalize_tty

        specs = load_action_catalog(CATALOG)

        def frame(turn):
            lines = ["", "-----", "-@---", "-----"] + [""] * 19
            lines.append(f"HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:{turn}")
            return normalize_tty("\n".join(lines) + "\n", cols=80, rows=24)

        def worker(request_text, *, docker, image, extra_env=None):
            arm = arm_of(request_text)
            request = json.loads(request_text)
            root = Path(request["arena"]["episode_root"])
            policy = CanaryTacticalPolicy(specs=load_action_catalog(root / "catalog.json"))
            action = policy.decide(frame(50))
            policy.assert_safe(action)
            trace = root / "action-trace.jsonl"
            trace.write_text(
                json.dumps({"intent": action.intent, "keys": [a.text for a in action.actions],
                            "before": frame(50).raw_text, "after": frame(51).raw_text}) + "\n",
                encoding="utf-8",
            )
            return {
                "worker_status": "completed", "terminal_status": "timeout",
                "turns": 120 if arm == "candidate" else 100,
                "max_depth": 3 if arm == "candidate" else 2, "score": 100,
                "exit_reason": "max_turns", "production_state_touched": False,
            }

        with tempfile.TemporaryDirectory() as tmp:
            decision, base, cand = self.promote(self.spec(), worker, tmp)
        self.assertEqual((base.trace_unverified, cand.trace_unverified), (0, 0))
        self.assertTrue(decision.promote)
        self.assertEqual(len(specs), len(load_action_catalog(CATALOG)))

    def test_both_arms_are_trace_gated_independently(self):
        with tempfile.TemporaryDirectory() as tmp_a, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, base, cand = self.promote(
                self.spec(), fake_worker(write_trace_arms=("candidate",)), tmp_a
            )
            self.assertEqual(base.trace_unverified, 3)
            self.assertEqual(cand.trace_unverified, 0)
        with tempfile.TemporaryDirectory() as tmp_b, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision2, base2, cand2 = self.promote(
                self.spec(), fake_worker(write_trace_arms=()), tmp_b
            )
        self.assertFalse(decision.promote)
        self.assertIn("trace_unverified", decision.reasons)
        self.assertEqual(decision2.reasons, ("trace_unverified",))
        self.assertEqual((base2.trace_unverified, cand2.trace_unverified), (3, 3))

    def test_rejects_missing_candidate_trace_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision, _base, cand = self.promote(
                self.spec(), fake_worker(write_trace_arms=("baseline",)), tmp
            )
        self.assertFalse(decision.promote)
        self.assertIn("trace_unverified", decision.reasons)
        self.assertEqual(cand.trace_unverified, 3)

    def test_rejects_empty_candidate_trace_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(),
        ):
            decision, base, cand = self.promote(self.spec(), fake_worker(), tmp)
        self.assertFalse(decision.promote)
        self.assertIn("trace_unverified", decision.reasons)
        self.assertEqual((base.trace_unverified, cand.trace_unverified), (3, 3))

    def test_rejects_regressing_catalog_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, _base, _cand = self.promote(
                self.spec(), fake_worker(baseline=(100, 3), candidate=(400, 1)), tmp
            )
        self.assertFalse(decision.promote)
        self.assertIn("fitness_regression", decision.reasons)

    def test_rejects_when_smoke_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, _base, _cand = self.promote(self.spec(), fake_worker(), tmp, smoke_ok=False)
        self.assertFalse(decision.promote)
        self.assertIn("smoke_gate_failed", decision.reasons)

    def test_failed_isolation_probe_is_not_a_passing_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, base, cand = self.promote(
                self.spec(), fake_worker(), tmp, isolation_check=lambda: False
            )
        self.assertFalse(decision.promote)
        self.assertIn("candidate_isolation_violation", decision.reasons)
        self.assertTrue(all(item.production_state_touched for item in cand.outcomes))
        self.assertTrue(all(item.production_state_touched for item in base.outcomes))

    def test_baseline_arm_receives_the_explicit_known_good_catalog(self):
        seen = {}

        def worker(request_text, *, docker, image, extra_env=None):
            arm = arm_of(request_text)
            request = json.loads(request_text)
            injected = Path(request["arena"]["episode_root"]) / "catalog.json"
            seen[arm] = injected.read_text(encoding="utf-8")
            trace = Path(request["arena"]["episode_root"]) / "action-trace.jsonl"
            trace.write_text("{}\n", encoding="utf-8")
            return {
                "worker_status": "completed", "terminal_status": "timeout",
                "turns": 100, "max_depth": 2, "score": None,
                "exit_reason": "max_turns", "production_state_touched": False,
            }

        baseline_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, baseline_dir, ignore_errors=True)
        baseline_catalog = baseline_dir / "known-good.json"
        baseline_catalog.write_text(
            CATALOG.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        candidate_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, candidate_dir, ignore_errors=True)
        candidate_catalog = candidate_dir / "candidate.json"
        candidate_catalog.write_text(
            json.dumps(json.loads(CATALOG.read_text(encoding="utf-8")), indent=4) + "\n",
            encoding="utf-8",
        )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            _decision, base, cand = self.promote(
                self.spec(baseline_catalog=baseline_catalog, candidate_catalog=candidate_catalog),
                worker,
                tmp,
            )

        # Neither arm falls back to the image-bundled default catalog.
        self.assertEqual(seen["baseline"], baseline_catalog.read_text(encoding="utf-8"))
        self.assertEqual(seen["candidate"], candidate_catalog.read_text(encoding="utf-8"))
        self.assertNotEqual(seen["baseline"], seen["candidate"])
        # The digest is over the artifact that was actually injected.
        self.assertEqual(
            base.catalog_sha256,
            "sha256:" + hashlib.sha256(baseline_catalog.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            cand.catalog_sha256,
            "sha256:" + hashlib.sha256(candidate_catalog.read_bytes()).hexdigest(),
        )
        self.assertNotEqual(base.catalog_sha256, cand.catalog_sha256)

    def test_catalog_digest_tracks_a_replaced_baseline_artifact(self):
        first = Path(tempfile.mkdtemp())
        second = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, first, ignore_errors=True)
        self.addCleanup(shutil.rmtree, second, ignore_errors=True)
        raw = json.loads(CATALOG.read_text(encoding="utf-8"))
        v1, v2 = first / "baseline.json", second / "baseline.json"
        v1.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        v2.write_text(json.dumps(raw, indent=4) + "\n", encoding="utf-8")

        digests = []
        for path in (v1, v2):
            with tempfile.TemporaryDirectory() as tmp, patch(
                "nethack_promotion_runner.verify_trace_file",
                return_value=(SimpleNamespace(verified=True),),
            ):
                _decision, base, _cand = self.promote(self.spec(baseline_catalog=path), fake_worker(), tmp)
            digests.append(base.catalog_sha256)
        self.assertEqual(digests[0], "sha256:" + hashlib.sha256(v1.read_bytes()).hexdigest())
        self.assertEqual(digests[1], "sha256:" + hashlib.sha256(v2.read_bytes()).hexdigest())
        self.assertNotEqual(digests[0], digests[1])

    def test_request_identity_is_per_episode_and_controller_scoped(self):
        import re

        requests = []

        def worker(request_text, *, docker, image, extra_env=None):
            requests.append((arm_of(request_text), json.loads(request_text)))
            request = json.loads(request_text)
            trace = Path(request["arena"]["episode_root"]) / "action-trace.jsonl"
            trace.write_text("{}\n", encoding="utf-8")
            return {
                "worker_status": "completed", "terminal_status": "timeout",
                "turns": 100, "max_depth": 2, "score": None,
                "exit_reason": "max_turns", "production_state_touched": False,
            }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "nethack_promotion_runner.verify_trace_file",
            return_value=(SimpleNamespace(verified=True),),
        ):
            decision, base, cand = self.promote(self.spec(), worker, tmp)
        self.assertTrue(decision.promote)

        id_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
        for arm in ("baseline", "candidate"):
            arm_requests = [request for name, request in requests if name == arm]
            self.assertEqual(len(arm_requests), 3)
            # One episode per seed, tied to the same seed pairing the gate uses.
            self.assertEqual(
                sorted(request["episode_id"] for request in arm_requests),
                ["000", "001", "002"],
            )
            self.assertEqual(
                sorted(request["seed"] for request in arm_requests), [1, 2, 3]
            )
            for request in arm_requests:
                self.assertEqual(request["experiment_id"], base.experiment_id)
                self.assertEqual(request["experiment_id"], cand.experiment_id)
                self.assertIsNotNone(id_re.fullmatch(request["experiment_id"]))
                # The wire arm is the controller kind, not the experiment arm.
                self.assertEqual(request["arm"], "baseline")
                self.assertEqual(request["controller"], {"kind": "baseline_p3b"})
            # Each seed lands in its own arena, so traces/results cannot be reused.
            roots = {request["arena"]["episode_root"] for request in arm_requests}
            self.assertEqual(len(roots), 3)
        self.assertEqual(base.controller_kind, "baseline_p3b")
        self.assertEqual(cand.arm, "candidate")
        self.assertNotEqual(
            {request["arena"]["episode_root"] for name, request in requests if name == "baseline"},
            {request["arena"]["episode_root"] for name, request in requests if name == "candidate"},
        )

    def test_gates_have_no_permissive_defaults(self):
        for func in (run_promotion, run_improvement_cycle):
            signature = inspect.signature(func)
            for name in ("regression_green", "smoke_ok", "isolation_check"):
                parameter = signature.parameters[name]
                self.assertIs(parameter.default, inspect.Parameter.empty, (func, name))
                self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY, (func, name))
        spec_fields = {
            field.name: field for field in PromotionRunSpec.__dataclass_fields__.values()
        }
        self.assertIs(spec_fields["baseline_catalog"].default, dataclasses.MISSING)

    def test_rejects_specs_that_cannot_be_used_as_evidence(self):
        cases = (
            {"seeds": (1, 1, 2)},
            {"seeds": ()},
            {"seeds": (1, -1)},
            {"baseline_id": "cand-v2"},
            {"baseline_catalog": Path("/nonexistent/known-good.json")},
            # run_promotion has no image-bundled candidate fallback.
            {"candidate_catalog": None},
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp, patch(
                "nethack_promotion_runner.verify_trace_file",
                return_value=(SimpleNamespace(verified=True),),
            ):
                with self.assertRaises(PromotionRunError):
                    self.promote(self.spec(**case), fake_worker(), tmp)

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
                    arm = arm_of(request_text)
                    request = json.loads(request_text)
                    root = Path(request["arena"]["episode_root"])
                    is_candidate = arm == "candidate"
                    # Both arms are injected explicitly; the baseline arm must
                    # be the named known-good catalog, not the image default.
                    injected = root / "catalog.json"
                    self.assertTrue(injected.is_file())
                    injected_text = injected.read_text(encoding="utf-8")
                    if is_candidate:
                        self.assertNotEqual(injected_text, baseline_path.read_text(encoding="utf-8"))
                        raw = json.loads(injected_text)
                        search = next(a for a in raw["actions"] if a["id"] == "search_when_blocked")
                        self.assertTrue(search["enabled"])
                    else:
                        self.assertEqual(injected_text, baseline_path.read_text(encoding="utf-8"))
                    policy = CanaryTacticalPolicy(specs=load_action_catalog(injected))
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
                    verifications = tuple(verify_trace_file(trace, load_action_catalog(injected)))
                    if is_candidate and corruption:
                        self.assertFalse(any(v.verified for v in verifications))
                    else:
                        self.assertTrue(all(v.verified for v in verifications))
                    return {
                        "worker_status": "completed", "terminal_status": "timeout",
                        "turns": 120 if is_candidate else 100,
                        "max_depth": 3 if is_candidate else 2, "score": 100,
                        "exit_reason": "max_turns" if is_candidate else "policy_stall:rest",
                        "production_state_touched": False,
                    }

                decision, signal, specs, base, cand = run_improvement_cycle(
                    self.spec(baseline_catalog=baseline_path, candidate_catalog=None),
                    proposer=CommandCatalogProposer(("fixture-proposer",), runner=enable_search),
                    work_root=work_root,
                    worker=worker,
                    docker="/usr/bin/docker",
                    image="sha256:" + "a" * 64,
                    regression_green=True,
                    smoke_ok=True,
                    isolation_check=isolation_ok,
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
                self.assertEqual(
                    (base.trace_unverified, cand.trace_unverified),
                    (0, 0) if corruption is None else (0, 3),
                )
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
                self.spec(candidate_catalog=None),
                proposer=FakeProposer(),
                work_root=Path(tmp),
                worker=fake_worker(baseline=(100, 2), candidate=(120, 3)),
                docker="/usr/bin/docker",
                image="sha256:" + "a" * 64,
                regression_green=True,
                smoke_ok=True,
                isolation_check=isolation_ok,
            )
        self.assertTrue(decision.promote)
        self.assertTrue(signal.exit_reason)
        self.assertIn("rest", {spec.id for spec in specs})
        self.assertEqual(len(base.outcomes), 3)
        self.assertEqual(len(cand.outcomes), 3)


if __name__ == "__main__":
    unittest.main()
