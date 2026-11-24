"""Fixed owner-only release of a consumed switch's loop pause (docich #2061).

The release must refuse unless every proof holds, must touch only the one
marker, and must leave a private backup behind.  Nothing here starts a game:
the supervisor owns that decision.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.game_switch import GameSwitchCoordinator, GameSwitchStore  # noqa: E402
from docich.naming import runtime_directory, runtime_names  # noqa: E402
from docich.soren_loop_pause_recovery import (  # noqa: E402
    REASON_EXIT_CODES,
    REASONS,
    RELEASE_PHASES,
    SOURCE_GAME,
    OrphanedLoopPauseRelease,
    completed_result,
    exit_code,
    incomplete_result,
)
from test_coordinator import FakeAdapterFactory  # noqa: E402

REQUEST_ID = "0f5d2a1c-3b4e-4a5d-8f6a-1b2c3d4e5f60"
OTHER_REQUEST = "11111111-2222-4333-8444-555555555555"
GENERATION = 3


def runtime(generation=GENERATION, game="sorengame", adapter="soren"):
    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": adapter,
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": None,
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-10-11T00:00:00Z",
    }


class ReleaseFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "run-soren-live"
        self.soren = self.root / "soren"
        self.marker = self.soren / "tmp" / "state" / "soren_loop.paused"
        self.record = self.soren / "tmp" / "state" / "game_lifecycle" / "loop_pause.json"
        self.record.parent.mkdir(parents=True)
        # The adapter-side snapshot #2396 writes for cross-instance recovery.
        # It is outside this operator's contract and must survive untouched.
        self.resumed_snapshot = self.soren / "tmp" / "state" / "soren_loop.resumed"
        self.resumed_snapshot.write_text(
            json.dumps({"request_id": REQUEST_ID, "expected_request_id": REQUEST_ID,
                        "identity": [4242, 777]})
        )
        self.store = GameSwitchStore(self.state_dir)
        self.store.canonical.initialize()
        # Materialize the coordinator lock file now so the byte-for-byte
        # "only the marker moved" check is not confused by lock creation.
        with self.store.lock(exclusive=True):
            pass
        self.write_canonical()
        self.write_marker()

    def tearDown(self):
        self.tmp.cleanup()

    def write_canonical(self, **overrides):
        state = dict(self.store.canonical.load()[0])
        state.update(
            {
                "phase": "failed",
                "operation": None,
                "request_id": None,
                "active": None,
                "previous": None,
                "retiring": [],
                "candidate": runtime(),
                "next_generation": GENERATION + 1,
            }
        )
        state.update(overrides)
        return self.store.canonical.save(state)

    def write_marker(self, owner=REQUEST_ID, marker_created=True, body=None, record_owner=None):
        self.marker.write_text(body if body is not None else f"lifecycle:{owner}\n")
        self.record.write_text(
            json.dumps(
                {
                    "request_id": record_owner or owner,
                    "loop_marker_created": marker_created,
                }
            )
        )

    def write_receipt(self, request_id=REQUEST_ID, **overrides):
        names = runtime_names(GENERATION)
        runtime_id = f"g{GENERATION}-abcdef"
        receipt = {
            "request_id": request_id,
            "operation": "switch",
            "target": "moon-buggy",
            "payload_hash": "a" * 64,
            "generation": GENERATION,
            "runtime_id": runtime_id,
            "runtime_dir": str(runtime_directory(self.state_dir, runtime_id)),
            "game_window": names.game_window,
            "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "status": "succeeded",
            "result": {
                "status": "succeeded",
                "request_id": request_id,
                "operation": "switch",
                "from_game": "sorengame",
                "to_game": "moon-buggy",
                "generation": GENERATION + 1,
                "source_runtime": runtime(),
                "active_runtime": {
                    "game": "moon-buggy",
                    "runtime_id": f"g{GENERATION + 1}-abcdef",
                    "generation": GENERATION + 1,
                    "lease_id": None,
                },
            },
            "created_at": "2026-10-11T00:00:00Z",
        }
        receipt.update(overrides)
        return self.store.receipts.save(receipt)

    def release(self):
        return OrphanedLoopPauseRelease(
            self.state_dir, self.soren, clock=lambda: 1791000000.0
        ).run()

    def snapshot(self):
        found = {}
        for base in (self.state_dir, self.soren):
            for path in base.rglob("*"):
                if path.is_file():
                    found[str(path.relative_to(self.root))] = path.read_bytes()
        return found


class ProvenReleaseTests(ReleaseFixture):
    def test_releases_a_proven_orphaned_pause(self):
        self.write_receipt()
        original = self.marker.read_bytes()

        result = self.release()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["released"], "soren_loop_pause")
        self.assertTrue(result["ownership_proven"])
        self.assertEqual(result["backup"], "private")
        self.assertFalse(self.marker.exists())
        backups = sorted((self.soren / "tmp" / "debug").glob("loop-pause-release-*.paused"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(os.stat(backups[0]).st_mode & 0o777, 0o600)

    def test_release_touches_only_the_marker(self):
        self.write_receipt()
        before = self.snapshot()

        self.assertEqual(self.release()["status"], "completed")

        after = self.snapshot()
        removed = set(before) - set(after)
        added = set(after) - set(before)
        self.assertEqual(removed, {str(self.marker.relative_to(self.root))})
        self.assertEqual(len(added), 1)
        self.assertIn("/tmp/debug/loop-pause-release-", next(iter(added)))
        for path in set(before) & set(after):
            self.assertEqual(before[path], after[path], path)
        # The ownership record survives as the durable proof, and the
        # adapter-side snapshot #2396 owns is outside this contract.
        self.assertTrue(self.record.is_file())
        self.assertTrue(self.resumed_snapshot.is_file())

    def test_rotate_receipt_away_from_sorengame_also_proves_ownership(self):
        self.write_receipt(operation="rotate", target=None)
        result = self.release()
        self.assertEqual(result["status"], "completed")
        self.assertFalse(self.marker.exists())

    def test_a_failed_candidate_seated_in_retiring_is_also_proven(self):
        # The production code resolves failed candidates from `candidate` OR
        # `retiring` (adapters/soren.py::_owns_failed_candidate_cleanup), so the
        # #2061 state may seat the Soren candidate in retiring instead.
        self.write_receipt()
        self.write_canonical(
            candidate=None,
            retiring=[{**runtime(), "cleanup_role": "failed_candidate"}],
        )

        result = self.release()

        self.assertEqual(result["status"], "completed")
        self.assertFalse(self.marker.exists())


class RefusalTests(ReleaseFixture):
    def assert_refused(self, reason):
        result = self.release()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["reason"], reason)
        self.assertIsNone(result["released"])
        self.assertFalse(result["ownership_proven"])
        self.assertIsNone(result["backup"])
        self.assertTrue(self.marker.is_file(), "a refusal must never remove the marker")
        self.assertFalse((self.soren / "tmp" / "debug").exists())

    def test_a_live_game_refuses(self):
        self.write_receipt()
        self.write_canonical(
            phase="failed",
            active=runtime(generation=4, game="moon-buggy", adapter="retro"),
            candidate=runtime(),
            next_generation=5,
        )
        self.assert_refused("game_still_active")

    def test_a_releasable_phase_with_a_previous_soren_seat_refuses(self):
        self.write_receipt()
        self.write_canonical(phase="recovery_required", previous=runtime(generation=2))
        self.assert_refused("game_still_active")

    def test_ready_phase_refuses(self):
        self.write_receipt()
        self.write_canonical(phase="ready", candidate=None, active=runtime())
        self.assert_refused("phase_not_releasable")

    def test_without_a_soren_candidate_refuses(self):
        self.write_receipt()
        self.write_canonical(candidate=None, retiring=[])
        self.assert_refused("no_soren_candidate")

    def test_a_non_soren_retiring_seat_does_not_prove_a_candidate(self):
        self.write_receipt()
        self.write_canonical(
            candidate=None,
            retiring=[runtime(game="moon-buggy", adapter="retro", generation=4)],
            next_generation=5,
        )
        self.assert_refused("no_soren_candidate")

    def test_a_live_broker_request_refuses(self):
        # The proven recovery archives the broker records before releasing, so
        # a non-empty broker must refuse rather than race that handover.
        self.write_receipt()
        (self.soren / "tmp" / "state" / "game_lifecycle" / "request.json").write_text(
            json.dumps({"request_id": OTHER_REQUEST, "status": "stopping"})
        )
        self.assert_refused("handover_in_flight")

    def test_a_parked_broker_ack_refuses(self):
        self.write_receipt()
        (self.soren / "tmp" / "state" / "game_lifecycle" / "ack.json").write_text(
            json.dumps({"request_id": OTHER_REQUEST, "status": "stopped"})
        )
        self.assert_refused("handover_in_flight")

    def test_operator_owned_marker_refuses(self):
        self.write_receipt()
        self.write_marker(body="operator\n")
        self.assert_refused("pause_not_owned")

    def test_a_record_that_does_not_claim_creation_refuses(self):
        self.write_receipt()
        self.write_marker(marker_created=False)
        self.assert_refused("pause_not_owned")

    def test_a_record_for_another_request_refuses(self):
        self.write_receipt()
        self.write_marker(owner=OTHER_REQUEST, record_owner=REQUEST_ID)
        self.assert_refused("pause_not_owned")

    def test_a_missing_receipt_refuses(self):
        self.assert_refused("receipt_not_owner")

    def test_a_failed_receipt_refuses(self):
        self.write_receipt(status="failed", result={"status": "failed"})
        self.assert_refused("receipt_not_owner")

    def test_a_receipt_that_switches_back_to_sorengame_refuses(self):
        receipt = self.write_receipt()
        receipt["result"]["to_game"] = "sorengame"
        self.store.receipts.save(receipt)
        self.assert_refused("receipt_not_owner")

    def test_a_receipt_without_a_recorded_result_refuses(self):
        receipt = self.write_receipt()
        receipt["result"] = None
        (self.state_dir / "game-switch" / "requests" / f"{REQUEST_ID}.json").write_text(
            json.dumps(receipt)
        )
        self.assert_refused("receipt_not_owner")

    def test_a_receipt_that_is_still_the_live_request_refuses(self):
        self.write_receipt()
        self.write_canonical(
            phase="failed",
            operation="switch",
            request_id=REQUEST_ID,
            deadline_at="2026-10-11T02:00:00Z",
        )
        self.assert_refused("receipt_not_owner")

    def test_a_missing_canonical_state_refuses(self):
        self.write_receipt()
        (self.state_dir / "game_switch.json").unlink()
        # A missing file loads the initial idle state, which is not releasable.
        self.assert_refused("canonical_unavailable")


class FixedSchemaTests(unittest.TestCase):
    def test_the_guard_binds_the_production_soren_adapter_identity(self):
        # The guard only fires for a seat the production Soren adapter owns.  A
        # rename must fail here rather than silently turn the operator into a
        # no-op that refuses every real recovery.
        from docich.adapters.soren import SorenCoordinatorAdapter

        self.assertEqual(SorenCoordinatorAdapter.name, "soren")
        self.assertEqual(SOURCE_GAME, "sorengame")
        # The allowed phase set is deliberately narrower than the candidate
        # cleanup contract's {rolling_back, failed, ready, idle}; widening it is
        # an owner decision, so pin the current set.
        self.assertEqual(RELEASE_PHASES, frozenset({"failed", "recovery_required"}))

    def test_incomplete_results_never_leak_identity_or_paths(self):
        for reason in sorted(REASONS):
            with self.subTest(reason=reason):
                result = incomplete_result(reason)
                self.assertEqual(
                    set(result), {"status", "reason", "released", "ownership_proven", "backup"}
                )
                self.assertEqual(result["status"], "incomplete")
                self.assertIn(result["reason"], REASONS)

    def test_unknown_reasons_collapse_to_unexpected_failure(self):
        for value in ("", "anything", None, 7, {"a": 1}, ["x"]):
            with self.subTest(value=value):
                self.assertEqual(incomplete_result(value)["reason"], "unexpected_failure")


class ExitCodeTests(unittest.TestCase):
    """One fixed exit code per refusal reason, and 0 only for a release."""

    def test_every_reason_has_a_distinct_fixed_code(self):
        self.assertEqual(set(REASON_EXIT_CODES), set(REASONS))
        codes = list(REASON_EXIT_CODES.values())
        self.assertEqual(len(codes), len(set(codes)), "exit codes must not collide")
        for code in codes:
            with self.subTest(code=code):
                self.assertIs(type(code), int)
                self.assertNotIn(code, (0, 1))

    def test_incomplete_results_map_to_their_reason_code(self):
        for reason in sorted(REASONS):
            with self.subTest(reason=reason):
                self.assertEqual(exit_code(incomplete_result(reason)), REASON_EXIT_CODES[reason])

    def test_completed_result_exits_zero(self):
        self.assertEqual(exit_code(completed_result()), 0)

    def test_unknown_or_malformed_results_fail_closed(self):
        for value in (None, 7, "x", [], {"status": "incomplete"},
                      {"status": "incomplete", "reason": "nope"}):
            with self.subTest(value=value):
                self.assertEqual(exit_code(value), REASON_EXIT_CODES["unexpected_failure"])


class RealCoordinatorIntegrationTests(unittest.TestCase):
    """The proof must accept a receipt the real coordinator actually wrote.

    The hand-built fixtures above are validated by the store's own loaders, but
    only a real switch proves that the operator fires for the #2061 shape: the
    receipt's ``result`` direction, its ``source_runtime`` identity, and the
    canonical failed-candidate seat all come from production code here.
    """

    def test_a_real_succeeded_switch_receipt_proves_the_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_dir = root / "run-soren-live"
            soren = root / "soren"
            (soren / "tmp" / "state" / "game_lifecycle").mkdir(parents=True)
            marker = soren / "tmp" / "state" / "soren_loop.paused"
            store = GameSwitchStore(state_dir)
            factory = FakeAdapterFactory({"sorengame": {"name": "soren"}, "robots": {}})
            coordinator = GameSwitchCoordinator(
                store, factory, quiesce_verify_timeout_s=0.3, poll_interval_s=0.01,
                default_timeout_s=60,
            )
            self.assertEqual(coordinator.start("sorengame").status, "succeeded")
            source = store.canonical.load()[0]["active"]
            self.assertEqual((source["game"], source["adapter"]), ("sorengame", "soren"))

            switched = coordinator.switch("robots", request_id=str(uuid.uuid4()))
            self.assertEqual(switched.status, "succeeded")
            request_id = switched.request_id
            receipt = store.receipts.load(request_id)
            self.assertEqual(receipt["status"], "succeeded")
            self.assertEqual(receipt["operation"], "switch")
            self.assertEqual(receipt["result"]["from_game"], "sorengame")
            self.assertEqual(receipt["result"]["to_game"], "robots")

            # The switch that parked the loop is consumed; the marker and its
            # ownership record survive exactly as #2061 found them.
            marker.write_text(f"lifecycle:{request_id}\n")
            (soren / "tmp" / "state" / "game_lifecycle" / "loop_pause.json").write_text(
                json.dumps({"request_id": request_id, "loop_marker_created": True})
            )
            # Canonical in the failed-candidate shape the recovery started from:
            # no active seat, the stopped Soren runtime seated as the candidate.
            state = dict(store.canonical.load()[0])
            state.update(
                {
                    "phase": "failed",
                    "operation": None,
                    "request_id": None,
                    "active": None,
                    "candidate": dict(source),
                    "retiring": [],
                }
            )
            store.canonical.save(state)

            released = OrphanedLoopPauseRelease(state_dir, soren).run()

            self.assertEqual(released["status"], "completed", released)
            self.assertEqual(released["released"], "soren_loop_pause")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
