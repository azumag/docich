"""Synthetic terminal evidence; no production state or raw runtime logs."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from test_nethack_corner import NethackCornerTestBase
from docich import config
from docich.nethack_run import NethackRunStore
from docich.retro_corner import RetroCornerError


class TestTerminalRestore(NethackCornerTestBase):
    def setUp(self):
        super().setUp()
        self.xlog = self.root / "synthetic-xlog"
        with (self.root / "config/games/nethack.toml").open("a") as stream:
            stream.write('\n[nethack]\npersistent_run = true\nplayer_name = "fixture_player"\n'
                         f'save_dir = "{self.root / "saves"}"\n'
                         f'xlogfile = "{self.xlog}"\n'
                         f'dump_dir = "{self.root / "dumps"}"\n')
        self.g = config.load_global(self.root)
        self.run_store = NethackRunStore.from_global(self.g)
        probe = self.run_store.prepare_start(current_is_nethack=False, now=self.now_value)
        self.run = self.run_store.record_started(probe, now=self.now_value)
        self.now_value += timedelta(seconds=30)
        self.mgr, self.coordinator = self.manager(["nethack"])
        self.state = self.mgr._default_state()
        self.state.update(status="active", game="nethack", previous_game="robots",
                          finish_reason="terminal", run_id=self.run["run_id"])
        self.mgr._write_state(self.state)

    def append_terminal(self):
        epoch = self.run["started_epoch"]
        self.xlog.write_text(f'name=fixture_player\tstarttime={epoch}\tendtime={epoch + 20}'
                             '\tdeath=killed by a synthetic monster\tpoints=7\tturns=12\thp=0\n')

    def failed_restore(self, _target):
        raise RetroCornerError("synthetic readiness timeout")

    def test_death_is_durable_before_restore_and_survives_timeout(self):
        self.append_terminal()

        def restore(target):
            self.assertIsNone(self.run_store.current())
            self.assertEqual(self.run_store._load_run_unlocked(self.run["run_id"])["status"], "dead")
            self.failed_restore(target)

        self.coordinator.switch = restore
        with self.assertRaisesRegex(RetroCornerError, "synthetic readiness timeout"):
            self.mgr._finish_locked(self.state, self.now_value)
        state = self.mgr.status()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["run_status"], "dead")
        self.assertNotIn("run_history_error", state)

    def test_late_xlog_is_saved_even_when_restore_raises(self):
        def restore(target):
            self.append_terminal()
            self.failed_restore(target)
        self.coordinator.switch = restore
        with self.assertRaises(RetroCornerError):
            self.mgr._finish_locked(self.state, self.now_value)
        self.assertIsNone(self.run_store.current())
        self.assertEqual(self.mgr.status()["run_status"], "dead")

    def test_missing_evidence_does_not_invent_death_on_timeout(self):
        self.coordinator.switch = self.failed_restore
        with self.assertRaises(RetroCornerError):
            self.mgr._finish_locked(self.state, self.now_value)
        self.assertEqual(self.run_store.current()["status"], "active")

    def test_queued_restore_keeps_terminal_ledger_without_claiming_completion(self):
        self.append_terminal()
        self.coordinator.switch = lambda target: SimpleNamespace(status="queued", detail="fixture queue")
        result = self.mgr._finish_locked(self.state, self.now_value)
        self.assertEqual(result.status, "queued")
        self.assertEqual(self.mgr.status()["status"], "restoring")
        self.assertEqual(self.mgr.status()["run_status"], "dead")

    def test_history_write_failure_does_not_mask_restore_failure(self):
        self.append_terminal()
        self.coordinator.switch = self.failed_restore
        with patch.object(self.mgr._run_store, "record_confirmed_terminal", side_effect=OSError("fixture storage failure")):
            with self.assertRaisesRegex(RetroCornerError, "synthetic readiness timeout"):
                self.mgr._finish_locked(self.state, self.now_value)
        self.assertEqual(self.run_store.current()["status"], "active")
        self.assertIn("fixture storage failure", self.mgr.status()["run_history_error"])

    def test_ownership_rejection_cannot_finalize_run(self):
        self.append_terminal()
        with patch("docich.corner_ownership.verify_runtime", side_effect=RuntimeError("stale owner")):
            with self.assertRaisesRegex(RuntimeError, "stale owner"):
                self.mgr._finish_locked(self.state, self.now_value)
        self.assertEqual(self.run_store.current()["status"], "active")
        self.assertEqual(self.coordinator.calls, [])

    def test_successful_restore_does_not_finalize_twice(self):
        self.append_terminal()
        result = self.mgr._finish_locked(self.state, self.now_value)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.mgr.status()["run_status"], "dead")
        self.assertNotIn("run_history_error", self.mgr.status())
