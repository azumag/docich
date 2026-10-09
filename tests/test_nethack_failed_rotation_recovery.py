"""Fail-closed owner recovery tests for an automatic NetHack rotation slot.

No VM, process or game is ever started by these tests.
"""
from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.nethack_corner import (
    GAME_NAME,
    NethackCornerManager,
    _build_parser,
)
from docich.retro_corner import CornerResult, RetroCornerManager


@contextmanager
def owned():
    yield True


class TestAutomaticNethackFailedRestore(TestCase):
    def setUp(self):
        self.manager = object.__new__(NethackCornerManager)
        self.manager.g = SimpleNamespace(state_dir=Path("/unused"))
        self.manager._locked = owned
        self.manager._tick_guard = owned
        self.manager._runtime_screen = Mock(
            return_value="Do you want your possessions identified? [ynq] (n)\nDlvl:1 $:0 HP:0(14) Pw:5(5) AC:4"
        )
        self.manager._read_state = Mock(return_value={
            "status": "failed",
            "game": GAME_NAME,
            "previous_game": "sorengame",
            "rotation_request_id": "rotation-123",
            "switch_request_id": "restore-456",
            "completed_at": "2026-10-09T08:13:07+09:00",
        })
        self.source = {
            "game": GAME_NAME, "adapter": "cli", "generation": 650,
            "runtime_id": "g650-dead", "lease_id": "lease650",
        }
        self.original_result = {
            "request_id": "restore-456",
            "operation": "switch",
            "status": "rolled_back",
            "from_game": GAME_NAME,
            "to_game": "sorengame",
            "restored_generation": 650, "generation": 649,
        }
        self.receipt = {
            "request_id": "restore-456", "operation": "switch",
            "target": "sorengame", "status": "rolled_back",
            "generation": 649,
            "result": self.original_result,
        }
        self.canonical = {
            "phase": "ready", "request_id": None, "candidate": None,
            "previous": None, "active": self.source, "last_result": self.original_result,
            "retiring": [{"game": "sorengame", "adapter": "soren", "generation": 649}],
        }
        self.cleaned = {**self.canonical, "retiring": []}
        self.store = SimpleNamespace(
            canonical=SimpleNamespace(load=Mock(side_effect=[
                (self.canonical, False), (self.cleaned, False),
            ])),
            receipts=SimpleNamespace(load=Mock(return_value=self.receipt)),
        )
        self.manager.store = self.store
        self.manager.coordinator = SimpleNamespace(
            recover=Mock(return_value=SimpleNamespace(
                status="succeeded", cleanup_pending=False,
            )),
        )
        self.manager._write_state = Mock()
        self.manager._restore_canonical_clean = Mock(return_value=True)
        self.manager._restore_replay_source_proved = Mock(return_value=self.source)
        self.manager._recover_restore_failed = Mock(
            return_value=CornerResult("succeeded", game=GAME_NAME,
                                      previous_game="sorengame")
        )
        self.rotation = SimpleNamespace(
            locked=owned, clock=lambda: 1791508200.0,
            load=Mock(return_value={
                "status": "recovery_required", "manual_pending": None,
                "pending": {"corner": GAME_NAME, "request_id": "rotation-123"},
            }),
        )
        self.factory = patch(
            "docich.corner_rotation.CornerRotationManager",
            return_value=self.rotation,
        )
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_exact_terminal_rollback_recovers_and_replays_once(self):
        result = self.manager.recover_failed_rotation()
        self.assertEqual(result.status, "succeeded")
        self.manager.coordinator.recover.assert_called_once_with(
            timeout_s=120.0, expected_snapshot=self.canonical,
        )
        self.manager._restore_replay_source_proved.assert_called_once()
        self.manager._recover_restore_failed.assert_called_once()

    def test_healthy_or_manual_slot_is_not_recovered(self):
        original = self.manager._read_state.return_value
        for change in (
            {"status": "active"},
            {"rotation_request_id": ""},
            {"switch_request_id": "rotation-123"},
            {"previous_game": "pacman4console"},
        ):
            with self.subTest(change=change):
                self.manager._read_state.return_value = {**original, **change}
                result = self.manager.recover_failed_rotation()
                self.assertEqual(result.status, "failed")
                self.manager.coordinator.recover.assert_not_called()

    def test_rotation_reservation_mismatch_does_not_touch_switch(self):
        for pending in (
            None,
            {"corner": "nethack", "request_id": "other"},
            {"corner": "retro", "request_id": "rotation-123"},
        ):
            with self.subTest(pending=pending):
                self.rotation.load.return_value["pending"] = pending
                self.assertEqual(
                    self.manager.recover_failed_rotation().status, "failed"
                )
                self.manager.coordinator.recover.assert_not_called()

    def test_alive_or_unreadable_tty_never_starts_recovery(self):
        for text in (
            "Dlvl:1 HP:14(14)",
            None,
            "Do you want your possessions identified? [ynq] (n)",
            "You die...\\nBut wait... Your medallion begins to glow!\\nDlvl:1 HP:18(18)",
            "Do you want your possessions identified? [ynq] (n)\\nDlvl:1 HP:18(18)",
        ):
            with self.subTest(text=text):
                self.manager._runtime_screen.return_value = text
                self.assertEqual(self.manager.recover_failed_rotation().status,
                                 "failed")
                self.manager.coordinator.recover.assert_not_called()

    def test_source_mismatch_or_foreign_retiring_refuses(self):
        self.canonical["retiring"].append({
            "game": "tsuitate-view", "adapter": "cli",
        })
        self.assertEqual(self.manager.recover_failed_rotation().status, "failed")
        self.manager.coordinator.recover.assert_not_called()

    def test_cleanup_pending_never_replays_restore(self):
        self.manager.coordinator.recover.return_value.cleanup_pending = True
        self.assertEqual(self.manager.recover_failed_rotation().status, "queued")
        self.manager._recover_restore_failed.assert_not_called()

    def test_cleanup_changed_source_never_replays_restore(self):
        self.cleaned["active"] = {**self.source, "generation": 651}
        self.assertEqual(self.manager.recover_failed_rotation().status, "failed")
        self.manager._recover_restore_failed.assert_not_called()

    def test_unproven_replay_fails_closed(self):
        self.manager._restore_replay_source_proved.return_value = None
        self.assertEqual(self.manager.recover_failed_rotation().status, "failed")
        self.manager._recover_restore_failed.assert_not_called()

    def test_persisted_pending_replay_resumes_original_without_new_recover(self):
        """Mid-switch request owns canonical draining, not the old g650."""
        self.manager._read_state.return_value["restore_cleanup"] = {
            "schema_version": 1,
            "rotation_request_id": "rotation-123",
            "restore_request_id": "restore-456",
            "original_generation": 649,
            "source": {key: self.source[key] for key in
                       ("game", "runtime_id", "generation", "lease_id")},
        }
        self.manager._read_state.return_value["restore_recovery"] = {
            "request_id": "replay-789",
        }
        self.manager._restore_recovery_record = Mock(return_value={
            "request_id": "replay-789",
            "expected_source": self.manager._read_state.return_value["restore_cleanup"]["source"],
        })
        self.canonical["phase"] = "draining"
        self.store.canonical.load.side_effect = [(self.canonical, False)]
        self.manager._recover_restore_failed.return_value = CornerResult(
            "queued", game=GAME_NAME,
        )
        got = self.manager.recover_failed_rotation()
        self.assertEqual(got.status, "queued")
        self.manager._restore_recovery_record.assert_called_once()
        self.manager._recover_restore_failed.assert_called_once_with(
            self.manager._read_state.return_value, late_cleanup_proved=True,
        )
        self.manager.coordinator.recover.assert_not_called()

    def test_persisted_terminal_replay_can_reach_rotation_commit(self):
        """After interrupted was persisted, operator must be safely idempotent."""
        self.manager._read_state.return_value.update(
            status="interrupted", restore_recovery={"request_id": "replay-789"},
            restore_cleanup={
                "schema_version": 1,
                "rotation_request_id": "rotation-123",
                "restore_request_id": "restore-456",
                "original_generation": 649,
                "source": {key: self.source[key] for key in
                           ("game", "runtime_id", "generation", "lease_id")},
            },
        )
        self.manager._restore_recovery_record = Mock(
            return_value={
                "request_id": "replay-789",
                "expected_source": self.manager._read_state.return_value["restore_cleanup"]["source"],
            }
        )
        self.manager._restore_recovery_receipt = Mock(return_value={
            "status": "succeeded", "request_id": "replay-789",
        })
        self.manager._restore_landed_proved = Mock(return_value=True)
        self.canonical["phase"] = "ready"
        self.canonical["active"] = {
            "game": "sorengame", "adapter": "soren", "generation": 651,
        }
        self.canonical["retiring"] = []
        self.store.canonical.load.side_effect = [(self.canonical, False)]
        got = self.manager.recover_failed_rotation()
        self.assertEqual(got.status, "succeeded")
        self.manager.coordinator.recover.assert_not_called()
        self.manager._recover_restore_failed.assert_not_called()

    def test_unproven_terminal_replay_must_not_release_rotation(self):
        self.manager._read_state.return_value["status"] = "interrupted"
        self.manager._restore_recovery_record = Mock(return_value=None)
        got = self.manager.recover_failed_rotation()
        self.assertEqual(got.status, "failed")
        self.manager.coordinator.recover.assert_not_called()

    def test_cli_has_fixed_command_and_operator_routes_it(self):
        self.assertEqual(
            _build_parser().parse_args(["recover-failed-rotation"]).command,
            "recover-failed-rotation",
        )
        shell = (Path(__file__).resolve().parents[1] /
                 "ops/vm_actions/recover_corner_rotation.sh").read_text()
        self.assertIn("nethack-corner recover-failed-rotation", shell)
        self.assertIn("corner-rotation recover", shell)



class TestLaterCleanupReceiptProof(TestCase):
    def test_immutable_incomplete_receipt_needs_explicit_later_proof(self):
        request_id = "00000000-0000-4000-8000-000000000001"
        result = {
            "request_id": request_id, "operation": "switch",
            "status": "rolled_back", "from_game": "nethack",
            "to_game": "sorengame", "generation": 651,
            "cleanup_pending": True,
        }
        receipt = {
            "request_id": request_id, "operation": "switch",
            "target": "sorengame", "status": "rolled_back",
            "generation": 651, "result": result,
        }
        state = {
            "status": "failed", "game": "nethack",
            "previous_game": "sorengame", "switch_request_id": request_id,
            "rotation_request_id": "00000000-0000-4000-8000-000000000002",
            "completed_at": "2026-10-09T08:13:07+09:00",
        }
        manager = object.__new__(RetroCornerManager)
        manager.store = SimpleNamespace(receipts=SimpleNamespace(
            load=Mock(return_value=receipt)
        ))
        self.assertIsNone(manager._restore_failed_receipt(state))
        proved = manager._restore_failed_receipt(
            state, late_cleanup_proved=True,
        )
        self.assertIsNotNone(proved)
        self.assertIs(proved[0], receipt)
        self.assertTrue(receipt["result"]["cleanup_pending"])
