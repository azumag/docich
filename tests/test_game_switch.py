import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from docich.naming import NameValidationError, runtime_names  # noqa: E402


class InjectedCrash(RuntimeError):
    pass


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _runtime(generation: int = 1, game: str = "nethack") -> dict:
    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": "cli",
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": str(uuid.uuid4()),
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-09-02T10:00:00Z",
    }


class GameSwitchTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tempdir.name) / "run"
        self.store = game_switch.GameSwitchStore(self.state_dir)

    def tearDown(self):
        self.tempdir.cleanup()


class TestCanonicalState(GameSwitchTestBase):
    def test_initialize_creates_private_v2_state(self):
        state = self.store.initialize()
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(state["next_generation"], 1)
        self.assertEqual(_mode(self.state_dir / "game_switch.json"), 0o600)
        self.assertEqual(_mode(self.state_dir), 0o700)
        self.assertEqual(_mode(self.state_dir / "locks"), 0o700)

    def test_corrupt_json_fails_closed(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "game_switch.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(game_switch.StateCorruptError):
            self.store.initialize()

    def test_missing_schema_is_not_guessed(self):
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "game_switch.json").write_text('{"phase":"idle"}', encoding="utf-8")
        with self.assertRaises(game_switch.StateCorruptError):
            self.store.initialize()

    def test_explicit_v1_state_is_rejected_fail_closed(self):
        self.state_dir.mkdir(parents=True)
        game_switch.atomic_write_json(
            self.state_dir / "game_switch.json",
            {
                "schema_version": 1,
                "revision": 7,
                "phase": "idle",
                "next_generation": 4,
            },
        )
        with self.assertRaises(game_switch.StateCorruptError):
            self.store.initialize()
        on_disk = json.loads((self.state_dir / "game_switch.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["schema_version"], 1)

    def test_invalid_runtime_identity_is_rejected(self):
        self.store.initialize()
        state, _ = self.store.canonical.load()
        state["active"] = _runtime()
        state["active"]["game_window"] = "game-g999"
        state["next_generation"] = 2
        state["phase"] = "ready"
        with self.assertRaises(game_switch.StateCorruptError):
            self.store.canonical.save(state)

    def test_cross_field_invariants_are_rejected_before_save(self):
        self.store.initialize()
        state, _ = self.store.canonical.load()
        invalid_states = []

        operation_without_request = dict(state)
        operation_without_request["operation"] = "start"
        invalid_states.append(operation_without_request)

        progress_without_owner = dict(state)
        progress_without_owner["phase"] = "starting"
        progress_without_owner["candidate"] = _runtime()
        progress_without_owner["next_generation"] = 2
        invalid_states.append(progress_without_owner)

        idle_with_runtime = dict(state)
        idle_with_runtime["active"] = _runtime()
        idle_with_runtime["next_generation"] = 2
        invalid_states.append(idle_with_runtime)

        for invalid in invalid_states:
            with self.subTest(invalid=invalid):
                with self.assertRaises(game_switch.StateCorruptError):
                    self.store.canonical.save(invalid)

    def test_same_generation_with_different_runtime_id_is_rejected(self):
        self.store.initialize()
        state, _ = self.store.canonical.load()
        request_id = str(uuid.uuid4())
        active = _runtime(1, "nethack")
        candidate = _runtime(1, "robots")
        candidate["runtime_id"] = "g1-bbbbbb"
        state.update(
            {
                "phase": "starting",
                "operation": "switch",
                "request_id": request_id,
                "active": active,
                "candidate": candidate,
                "next_generation": 2,
            }
        )

        with self.assertRaises(game_switch.StateCorruptError):
            self.store.canonical.save(state)


class TestAtomicCommit(GameSwitchTestBase):
    def setUp(self):
        super().setUp()
        self.store.initialize()

    def test_crash_before_replace_preserves_previous_canonical(self):
        def crash(stage, _path):
            if stage == "after_file_fsync":
                raise InjectedCrash(stage)

        with self.assertRaises(InjectedCrash):
            self.store.canonical.transition(
                {"idle"},
                "validating",
                updates={"operation": "start", "request_id": str(uuid.uuid4())},
                crash_hook=crash,
            )
        state, _ = self.store.canonical.load()
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(list(self.state_dir.glob(".game_switch.json.*")), [])

    def test_crash_after_replace_exposes_complete_new_canonical(self):
        request_id = str(uuid.uuid4())

        def crash(stage, _path):
            if stage == "after_replace":
                raise InjectedCrash(stage)

        with self.assertRaises(InjectedCrash):
            self.store.canonical.transition(
                {"idle"},
                "validating",
                updates={"operation": "start", "request_id": request_id},
                crash_hook=crash,
            )
        state, _ = self.store.canonical.load()
        self.assertEqual(state["phase"], "validating")
        self.assertEqual(state["request_id"], request_id)

    def test_fake_runtime_transition_commits_candidate_atomically(self):
        request_id = str(uuid.uuid4())
        candidate = _runtime()
        state = self.store.canonical.transition(
            {"idle"},
            "starting",
            updates={
                "operation": "start",
                "request_id": request_id,
                "candidate": candidate,
                "next_generation": 2,
            },
        )
        self.assertEqual(state["candidate"], candidate)
        self.assertIsNone(state["active"])
        self.assertEqual(state["phase"], "starting")


class TestSwitchLock(GameSwitchTestBase):
    def test_exclusive_lock_rejects_second_writer(self):
        first = game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=True)
        try:
            with self.assertRaises(game_switch.GameSwitchBusyError):
                game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=True)
        finally:
            first.release()

    def test_shared_locks_coexist_and_block_writer(self):
        first = game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=False)
        second = game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=False)
        try:
            with self.assertRaises(game_switch.GameSwitchBusyError):
                game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=True)
        finally:
            second.release()
            first.release()


class TestRequestReceipts(GameSwitchTestBase):
    def test_accept_allocates_generation_before_returning(self):
        request_id = str(uuid.uuid4())
        accepted = self.store.accept_request(request_id, "start", "nethack", {"timeout": 30})
        self.assertFalse(accepted.existing)
        self.assertEqual(accepted.generation, 1)
        self.assertEqual(accepted.status, "accepted")

        state, _ = self.store.canonical.load()
        self.assertEqual(state["next_generation"], 2)
        self.assertEqual(state["phase"], "validating")
        self.assertEqual(state["request_id"], request_id)
        receipt_path = self.state_dir / "game-switch" / "requests" / f"{request_id}.json"
        self.assertEqual(_mode(receipt_path), 0o600)
        self.assertEqual(_mode(receipt_path.parent), 0o700)
        self.assertNotIn("payload", accepted.receipt)

    def test_transaction_keeps_writer_lock_after_acceptance(self):
        with self.store.transaction() as transaction:
            request_id = str(uuid.uuid4())
            transaction.accept_request(request_id, "start", "nethack")
            state = transaction.transition({"validating"}, "preparing")
            self.assertEqual(state["phase"], "preparing")
            with self.assertRaises(game_switch.GameSwitchBusyError):
                game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=True)
        acquired = game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=True)
        acquired.release()

    def test_same_request_and_payload_is_idempotent(self):
        request_id = str(uuid.uuid4())
        first = self.store.accept_request(request_id, "switch", "robots", {"timeout": 20})
        second = self.store.accept_request(request_id, "switch", "robots", {"timeout": 20})
        state, _ = self.store.canonical.load()
        self.assertTrue(second.existing)
        self.assertEqual(second.status, "in_progress")
        self.assertEqual(second.generation, first.generation)
        self.assertEqual(second.receipt["runtime_id"], first.receipt["runtime_id"])
        self.assertEqual(state["next_generation"], 2)

    def test_busy_lock_classifies_same_request_and_rejects_other_request(self):
        request_id = str(uuid.uuid4())
        with self.store.transaction() as transaction:
            first = transaction.accept_request(request_id, "switch", "robots")
            retry = self.store.accept_request(request_id, "switch", "robots")
            self.assertTrue(retry.existing)
            self.assertEqual(retry.generation, first.generation)
            with self.assertRaises(game_switch.GameSwitchBusyError):
                self.store.accept_request(str(uuid.uuid4()), "switch", "nethack")

    def test_same_request_with_different_payload_is_rejected(self):
        request_id = str(uuid.uuid4())
        self.store.accept_request(request_id, "switch", "robots", {"timeout": 20})
        with self.assertRaises(game_switch.RequestConflictError):
            self.store.accept_request(request_id, "switch", "robots", {"timeout": 40})

    def test_terminal_result_is_returned_on_retry(self):
        request_id = str(uuid.uuid4())
        self.store.accept_request(request_id, "start", "nethack")
        self.store.finish_request(request_id, "succeeded", {"game": "nethack"})
        retried = self.store.accept_request(request_id, "start", "nethack")
        self.assertEqual(retried.status, "succeeded")
        self.assertEqual(retried.receipt["result"], {"game": "nethack"})

    def test_terminal_result_cannot_be_rewritten(self):
        request_id = str(uuid.uuid4())
        self.store.accept_request(request_id, "start", "nethack")
        self.store.finish_request(request_id, "succeeded", {"game": "nethack"})
        with self.assertRaises(game_switch.RequestConflictError):
            self.store.finish_request(request_id, "failed", {"error": "late"})

    def test_operation_target_contract_is_validated(self):
        with self.assertRaises(NameValidationError):
            self.store.accept_request(str(uuid.uuid4()), "start", None)
        with self.assertRaises(NameValidationError):
            self.store.accept_request(str(uuid.uuid4()), "stop", "nethack")

    def test_crash_after_receipt_commit_reuses_reserved_generation(self):
        request_id = str(uuid.uuid4())

        def crash(stage, _path):
            if stage == "receipt_after_replace":
                raise InjectedCrash(stage)

        with self.assertRaises(InjectedCrash):
            self.store.accept_request(request_id, "start", "nethack", crash_hook=crash)
        receipt = self.store.receipts.load(request_id)
        self.assertEqual(receipt["status"], "allocating")
        self.assertEqual(receipt["generation"], 1)

        retried = self.store.accept_request(request_id, "start", "nethack")
        self.assertTrue(retried.existing)
        self.assertEqual(retried.generation, 1)
        state, _ = self.store.canonical.load()
        self.assertEqual(state["next_generation"], 2)

    def test_dangling_receipt_prevents_generation_reuse_by_other_request(self):
        first_id = str(uuid.uuid4())

        def crash(stage, _path):
            if stage == "receipt_after_replace":
                raise InjectedCrash(stage)

        with self.assertRaises(InjectedCrash):
            self.store.accept_request(first_id, "start", "nethack", crash_hook=crash)
        with self.assertRaises(game_switch.GameSwitchBusyError):
            self.store.accept_request(str(uuid.uuid4()), "start", "robots")
        recovered = self.store.accept_request(first_id, "start", "nethack")
        self.assertTrue(recovered.existing)
        self.assertEqual(recovered.generation, 1)

    def test_in_progress_canonical_cannot_be_overwritten_by_other_request(self):
        first_id = str(uuid.uuid4())
        self.store.accept_request(first_id, "start", "nethack")
        candidate = _runtime()
        self.store.canonical.transition(
            {"validating"},
            "starting",
            updates={"candidate": candidate, "next_generation": 2},
        )

        with self.assertRaises(game_switch.GameSwitchBusyError):
            self.store.accept_request(str(uuid.uuid4()), "switch", "robots")

        state, _ = self.store.canonical.load()
        self.assertEqual(state["request_id"], first_id)
        self.assertEqual(state["phase"], "starting")
        self.assertEqual(state["candidate"], candidate)

    def test_same_request_retry_does_not_rewind_in_progress_canonical(self):
        request_id = str(uuid.uuid4())
        first = self.store.accept_request(request_id, "start", "nethack")
        candidate = _runtime()
        self.store.canonical.transition(
            {"validating"},
            "starting",
            updates={"candidate": candidate, "next_generation": 2},
        )

        retried = self.store.accept_request(request_id, "start", "nethack")

        state, _ = self.store.canonical.load()
        self.assertTrue(retried.existing)
        self.assertEqual(retried.generation, first.generation)
        self.assertEqual(state["phase"], "starting")
        self.assertEqual(state["candidate"], candidate)

    def test_receipt_save_rejects_invalid_data_before_replace(self):
        request_id = str(uuid.uuid4())
        self.store.accept_request(request_id, "start", "nethack")
        valid = self.store.receipts.load(request_id)
        self.assertIsNotNone(valid)

        corruptions = (
            {"status": "typo"},
            {"status": "succeeded", "result": None},
            {"status": "accepted", "result": {"unexpected": True}},
            {"operation": "unknown"},
            {"runtime_id": "g999-abcdef"},
        )
        for updates in corruptions:
            invalid = dict(valid)
            invalid.update(updates)
            with self.subTest(updates=updates):
                with self.assertRaises(game_switch.StateCorruptError):
                    self.store.receipts.save(invalid)
                self.assertEqual(self.store.receipts.load(request_id), valid)

    def test_invalid_request_id_cannot_escape_receipt_directory(self):
        with self.assertRaises(NameValidationError):
            self.store.accept_request("../../outside", "start", "nethack")
        self.assertFalse((self.state_dir.parent / "outside.json").exists())

    def test_prune_never_removes_nonterminal_receipts(self):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for request_id in ids[:2]:
            self.store.accept_request(request_id, "start", "nethack")
            self.store.finish_request(request_id, "succeeded", {})
            self.store.canonical.transition(
                {"validating"},
                "idle",
                updates={"operation": None, "request_id": None},
            )
        self.store.accept_request(ids[2], "start", "nethack")
        removed = self.store.receipts.prune_terminal(max_receipts=2)
        self.assertEqual(removed, 1)
        self.assertIsNotNone(self.store.receipts.load(ids[2]))


if __name__ == "__main__":
    unittest.main()
