"""Live sidebar turns are observations, not action counts or terminal results."""
import fcntl
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docich.nethack_run import (
    GameSwitchBusyError,
    NethackPersistenceSettings,
    NethackRunError,
    NethackRunStore,
)


class NethackLiveTurnsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.g = SimpleNamespace(state_dir=self.root / "state")
        self.settings = NethackPersistenceSettings(
            player_name="docich", save_dir=self.root / "save",
            xlogfile=self.root / "xlogfile", dump_dir=self.root / "dumps",
        )
        self.settings.save_dir.mkdir()
        self.settings.dump_dir.mkdir()
        env = patch.dict(os.environ, {"DOCICH_GAME_SESSION": "docich-nethack-g1"})
        env.start()
        self.addCleanup(env.stop)
        self.store = NethackRunStore(self.g, self.settings)
        self.now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        self.state = {
            "phase": "ready",
            "active": {
                "game": "nethack", "runtime_id": "nethack-g1", "generation": 1,
                "lease_id": "lease-1", "adapter_session": "docich-nethack-g1",
            },
        }
        self.switch = SimpleNamespace(
            canonical=SimpleNamespace(load=Mock(side_effect=lambda: (self.state, None))),
            lock=Mock(side_effect=lambda **kwargs: nullcontext()),
        )
        switch_patch = patch("docich.nethack_run.GameSwitchStore", return_value=self.switch)
        switch_patch.start()
        self.addCleanup(switch_patch.stop)
        self.run = self.start()

    def start(self):
        probe = self.store.prepare_start(current_is_nethack=False, now=self.now)
        return self.store.record_started(probe, now=self.now)

    def sample(self, turn=42, **changes):
        value = {
            "ts": self.now.timestamp() + 1, "phase": "sent", "turn": turn,
            "depth": 1, "hp": 12, "hp_max": 12, "conditions": [],
            "prompt": "none", "player": [3, 4], "intent": "explore_step",
            "resolved_intent": "explore_step", "key": "l",
            "frame_hash": "a" * 64, "map_hash": "b" * 64,
        }
        value.update(changes)
        return value

    def finish(self, turns=43):
        self.settings.xlogfile.write_text(
            f"name=docich\tturns={turns}\tpoints=123\tdeath=killed by a grid bug\n",
            encoding="utf-8",
        )
        return self.store.record_finished(
            now=self.now + timedelta(minutes=1), nethack_still_active=False,
        )

    def test_live_turn_reaches_existing_current_run_sidebar_source(self):
        before = self.store.current()
        self.assertIsNone(before["turns"])
        self.assertTrue(self.store.append_progress_sample(self.sample()))
        pointer = json.loads(self.store.current_path.read_text())
        shown = json.loads((self.store.runs_dir / f"{pointer['run_id']}.json").read_text())
        self.assertEqual(shown, {**before, "turns": 42})
        self.assertIsNone(shown["terminal"])
        self.switch.lock.assert_called_once_with(exclusive=False)

    def test_sent_keys_do_not_increment_observed_counter(self):
        for _ in range(3):
            self.store.append_progress_sample(self.sample(0))
        self.assertEqual(self.store.current()["turns"], 0)
        self.store.append_progress_sample(self.sample(17, phase="hold", key=None))
        self.assertEqual(self.store.current()["turns"], 17)

    def test_missing_turn_is_not_invented_or_reset(self):
        self.store.append_progress_sample(self.sample(None))
        self.assertIsNone(self.store.current()["turns"])
        self.store.append_progress_sample(self.sample())
        self.store.append_progress_sample(self.sample(None))
        self.assertEqual(self.store.current()["turns"], 42)

    def test_invalid_turn_is_rejected_before_publication(self):
        for turn in (True, False, -1, 2.5, "42"):
            with self.subTest(turn=turn), self.assertRaises(NethackRunError):
                self.store.append_progress_sample(self.sample(turn))
        self.assertIsNone(self.store.current()["turns"])

    def test_duplicate_and_older_turns_do_not_rewrite_run(self):
        self.store.append_progress_sample(self.sample())
        with patch.object(self.store, "_write_run_unlocked") as write:
            self.store.append_progress_sample(self.sample())
            self.store.append_progress_sample(self.sample(41))
        write.assert_not_called()
        self.assertEqual(self.store.current()["turns"], 42)

    def test_trace_cap_does_not_freeze_live_turns(self):
        with patch("docich.nethack_run.MAX_PROGRESS_TRACE_BYTES", 160):
            self.assertFalse(self.store.append_progress_sample(self.sample(42)))
            self.assertFalse(self.store.append_progress_sample(self.sample(43)))
        self.assertEqual(self.store.current()["turns"], 43)
        trace = self.store.progress_dir / f"{self.run['run_id']}.jsonl"
        self.assertLessEqual(trace.stat().st_size, 160)

    def test_game_switch_contention_skips_counter_but_keeps_trace(self):
        self.switch.lock.side_effect = GameSwitchBusyError("busy")
        self.assertTrue(self.store.append_progress_sample(self.sample()))
        self.assertIsNone(self.store.current()["turns"])

    def test_run_lock_contention_never_waits(self):
        real_flock = fcntl.flock

        def checked_flock(fd, flags):
            if flags & fcntl.LOCK_EX:
                # Fail immediately on a regression instead of hanging the test.
                self.assertTrue(flags & fcntl.LOCK_NB)
            return real_flock(fd, flags)

        with self.store._locked():
            with patch("docich.nethack_run.fcntl.flock", side_effect=checked_flock):
                self.assertTrue(self.store.append_progress_sample(self.sample()))
        self.assertIsNone(self.store.current()["turns"])
        self.store.append_progress_sample(self.sample(43))
        self.assertEqual(self.store.current()["turns"], 43)

    def test_unknown_or_transitioning_runtime_is_not_published(self):
        for phase, active in (
            ("ready", None), ("draining", self.state["active"]),
            ("ready", {**self.state["active"], "game": "robots"}),
            ("ready", {**self.state["active"], "generation": True}),
        ):
            with self.subTest(phase=phase, active=active):
                self.state = {"phase": phase, "active": active}
                self.store.append_progress_sample(self.sample())
                self.assertIsNone(self.store.current()["turns"])

    def test_old_worker_session_is_rejected_even_before_first_sample(self):
        self.state["active"]["adapter_session"] = "docich-nethack-g2"
        self.store.append_progress_sample(self.sample())
        self.assertIsNone(self.store.current()["turns"])

    def test_unbound_legacy_worker_does_not_publish_current_runtime(self):
        with patch.dict(os.environ, {}, clear=True):
            unbound = NethackRunStore(self.g, self.settings)
        self.assertTrue(unbound.append_progress_sample(self.sample()))
        self.assertIsNone(self.store.current()["turns"])

    def test_runtime_generation_and_lease_are_pinned(self):
        self.store.append_progress_sample(self.sample())
        for field, value in (("runtime_id", "nethack-g2"), ("generation", 2),
                             ("lease_id", "lease-2")):
            with self.subTest(field=field):
                previous = self.state["active"][field]
                self.state["active"][field] = value
                self.store.append_progress_sample(self.sample(99))
                self.assertEqual(self.store.current()["turns"], 42)
                self.state["active"][field] = previous

    def test_sample_before_current_session_is_rejected(self):
        self.store.append_progress_sample(self.sample(ts=self.now.timestamp() - 1))
        self.assertIsNone(self.store.current()["turns"])

    def test_suspend_resume_keeps_count_and_accepts_fresh_worker(self):
        self.store.append_progress_sample(self.sample())
        save = self.settings.save_dir / "1000docich"
        save.write_bytes(b"save")
        suspended = self.store.record_finished(
            now=self.now + timedelta(minutes=1), nethack_still_active=False,
        )
        self.assertEqual(suspended["turns"], 42)
        self.assertFalse(self.store.append_progress_sample(self.sample(100)))
        self.now += timedelta(days=1)
        probe = self.store.prepare_start(current_is_nethack=False, now=self.now)
        save.unlink()
        resumed = self.store.record_started(probe, now=self.now)
        self.assertEqual(resumed["run_id"], self.run["run_id"])
        self.assertEqual(resumed["turns"], 42)
        fresh = NethackRunStore(self.g, self.settings)
        fresh.append_progress_sample(self.sample(50))
        self.assertEqual(fresh.current()["turns"], 50)

    def test_terminal_xlog_overrides_live_value_and_cannot_be_overwritten(self):
        self.store.append_progress_sample(self.sample(99))
        finished = self.finish(turns=43)
        self.assertEqual(finished["turns"], 43)
        self.assertEqual(finished["terminal"]["turns"], 43)
        self.assertFalse(self.store.append_progress_sample(self.sample(100)))
        self.assertEqual(self.store._read_json(self.store._run_path(self.run["run_id"])), finished)

    def test_terminal_transition_between_trace_read_and_publication(self):
        publish = self.store._publish_observed_turn

        def finish_then_publish(run_id, sample):
            self.finish(turns=43)
            publish(run_id, sample)

        with patch.object(self.store, "_publish_observed_turn", side_effect=finish_then_publish):
            self.store.append_progress_sample(self.sample(99))
        finished = self.store._read_json(self.store._run_path(self.run["run_id"]))
        self.assertEqual(finished["turns"], 43)
        self.assertEqual(finished["status"], "dead")

    def test_pointer_switch_between_read_and_publication_cannot_update_new_run(self):
        publish = self.store._publish_observed_turn

        def replace_then_publish(run_id, sample):
            self.finish()
            self.now += timedelta(minutes=2)
            self.start()
            publish(run_id, sample)

        with patch.object(self.store, "_publish_observed_turn", side_effect=replace_then_publish):
            self.store.append_progress_sample(self.sample(99))
        self.assertNotEqual(self.store.current()["run_id"], self.run["run_id"])
        self.assertIsNone(self.store.current()["turns"])

    def test_old_store_cannot_follow_pointer_to_new_adventure(self):
        self.store.append_progress_sample(self.sample())
        self.finish()
        self.now += timedelta(minutes=2)
        second = self.start()
        self.store.append_progress_sample(self.sample(999))
        self.assertIsNone(self.store.current()["turns"])
        fresh = NethackRunStore(self.g, self.settings)
        fresh.append_progress_sample(self.sample(1))
        self.assertEqual(fresh.current()["turns"], 1)
        self.assertEqual(fresh.current()["run_id"], second["run_id"])


if __name__ == "__main__":
    unittest.main()
