"""``recover(adopt_failed_candidate=True)``: keep a live, failed Soren candidate."""
from __future__ import annotations

import copy
import unittest
import uuid

from docich import game_switch
from docich.game_switch import runtime_names

from test_coordinator import (
    CoordinatorTestBase,
    FakeAdapter,
    FakeAdapterFactory,
    _runtime_dict,
)


class AdoptingAdapter(FakeAdapter):
    name = "soren"

    def adopt_failed_candidate(self, deadline, cancel):
        self.runtime.events.append("adopt")
        hook = self.behavior.get("adopt_hook")
        if hook is not None:
            hook()
        return self.behavior.get("adopt_result", True)


class PlainSorenAdapter(FakeAdapter):
    name = "soren"


class AdoptFactory(FakeAdapterFactory):
    def __call__(self, spec):
        if spec.game == "sorengame":
            key = (spec.game, spec.generation)
            if key not in self.adapters:
                kind = self.behaviors[spec.game].get("kind", AdoptingAdapter)
                self.adapters[key] = kind(spec, self.behaviors[spec.game], game_switch_runtime(spec))
            else:
                self.adapters[key].spec = spec
            return self.adapters[key]
        return super().__call__(spec)


def game_switch_runtime(spec):
    from test_coordinator import FakeRuntime
    runtime = FakeRuntime(spec)
    runtime.materialized = True
    runtime.alive = True
    return runtime


class TestAdoptFailedCandidate(CoordinatorTestBase):
    def setUp(self):
        super().setUp()
        self.behaviors["sorengame"] = {"name": "soren", "agent_enabled": False}
        self.factory = AdoptFactory(self.behaviors)
        self.coordinator = game_switch.GameSwitchCoordinator(
            self.store, self.factory, quiesce_verify_timeout_s=0.3, poll_interval_s=0.01,
            default_timeout_s=60,
            step_timeouts=game_switch.StepTimeouts(
                preflight_s=2.0, stop_agent_s=2.0, start_s=2.0, agent_start_s=2.0,
                cleanup_s=2.0, probe_s=0.5,
            ),
        )
        self.make_failed_state()

    def make_failed_state(self):
        state = self.store.initialize()
        previous = _runtime_dict(6, "hanjuku")
        state.update(phase="ready", active=previous, next_generation=7)
        self.store.canonical.save(state)
        rid = str(uuid.uuid4())
        acceptance = self.store.accept_request(rid, "switch", "sorengame")
        body = dict(request_id=rid, operation="switch", status="failed", from_game="hanjuku",
                    to_game="sorengame", generation=acceptance.generation,
                    error_code="rollback_failed", detail="synthetic rollback failure")
        with self.store.transaction() as tx:
            tx.finish_request(rid, "failed", body)
        failed = {**_runtime_dict(acceptance.generation, "sorengame"), "adapter": "soren",
                  "cleanup_role": "failed_candidate"}
        state = self.store.canonical.load()[0]
        state.update(phase="failed", active=None, previous=previous, candidate=None,
                     retiring=[failed], request_id=None, operation=None, last_result=body,
                     next_generation=acceptance.generation + 1)
        self.store.canonical.save(state)
        self.rid, self.failed, self.previous = rid, failed, previous
        self.receipt = self.store.receipts.load(rid)

    def test_adopts_live_candidate_and_retires_previous_without_stopping_it(self):
        result = self.coordinator.recover(timeout_s=5, adopt_failed_candidate=True)
        self.assertEqual(result.status, "succeeded", result)
        after = self.canonical()
        self.assertEqual(after["phase"], "ready")
        self.assertEqual(after["active"]["runtime_id"], self.failed["runtime_id"])
        self.assertNotIn("cleanup_role", after["active"])
        self.assertIsNone(after["candidate"])
        self.assertIsNone(after["previous"])
        self.assertEqual(after["retiring"], [])
        self.assertEqual(after["last_result"]["operation"], "recover")
        self.assertEqual(after["last_result"]["status"], "succeeded")
        self.assertEqual(after["last_result"]["to_game"], "sorengame")
        self.assertEqual(self.mirror_text(), "sorengame")
        soren = self.factory.adapter("sorengame", self.failed["generation"])
        self.assertEqual(soren.runtime.events, ["adopt"])  # never cleaned up / restarted
        self.assertTrue(soren.runtime.alive)
        # The failed switch's immutable receipt is preserved, not rewritten.
        self.assertEqual(self.store.receipts.load(self.rid), self.receipt)

    def test_unproven_candidate_leaves_canonical_untouched(self):
        self.behaviors["sorengame"]["adopt_result"] = False
        before = self.canonical()
        result = self.coordinator.recover(timeout_s=5, adopt_failed_candidate=True)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.cleanup_pending)
        self.assertEqual(self.canonical(), before)
        soren = self.factory.adapter("sorengame", self.failed["generation"])
        self.assertTrue(soren.runtime.alive)
        self.assertNotIn("cleanup", soren.runtime.events)
        self.assertIsNone(self.factory.adapter("hanjuku", 6))

    def test_adapter_error_during_proof_is_refused_without_commit(self):
        def boom():
            raise RuntimeError("broker unreachable")
        self.behaviors["sorengame"]["adopt_hook"] = boom
        before = self.canonical()
        result = self.coordinator.recover(timeout_s=5, adopt_failed_candidate=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.canonical(), before)

    def test_adapter_without_adoption_support_is_refused(self):
        self.behaviors["sorengame"]["kind"] = PlainSorenAdapter
        before = self.canonical()
        result = self.coordinator.recover(timeout_s=5, adopt_failed_candidate=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.canonical(), before)

    def test_canonical_change_during_proof_is_refused(self):
        def mutate():
            state = self.canonical()
            state = copy.deepcopy(state)
            state["next_generation"] += 5
            self.store.canonical.save(state)
        self.behaviors["sorengame"]["adopt_hook"] = mutate
        result = self.coordinator.recover(timeout_s=5, adopt_failed_candidate=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.canonical()["phase"], "failed")

    def test_wrong_shapes_are_refused(self):
        original = copy.deepcopy(self.canonical())
        for mutate in (
            lambda s: s.update(retiring=[]),
            lambda s: s["retiring"][0].update(cleanup_role="source"),
            lambda s: s.update(retiring=[*s["retiring"], {**_runtime_dict(12, "hanjuku")}],
                               next_generation=13),
        ):
            state = copy.deepcopy(original)
            mutate(state)
            self.store.canonical.save(state)
            result = self.coordinator.recover(timeout_s=5, adopt_failed_candidate=True)
            self.assertEqual(result.status, "failed")
            self.assertEqual(self.canonical()["phase"], "failed")
            self.assertEqual(self.canonical()["retiring"], state["retiring"])


if __name__ == "__main__":
    unittest.main()
