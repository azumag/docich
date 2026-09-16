import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.nethack_run import (  # noqa: E402
    NethackRunError,
    NethackRunStore,
    classify_terminal_record,
    parse_xlog_line,
)


class NethackRunStoreTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        self.save_dir = Path(self.tempdir.name) / "playground" / "save"
        self.dump_dir = Path(self.tempdir.name) / "playground" / "dumps"
        self.xlogfile = Path(self.tempdir.name) / "playground" / "xlogfile"
        self.save_dir.mkdir(parents=True)
        self.dump_dir.mkdir(parents=True)
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "nethack.toml").write_text(
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
            '[cli]\ncommand = "nethack"\n\n'
            '[nethack]\npersistent_run = true\nplayer_name = "docich"\n'
            f'save_dir = "{self.save_dir}"\n'
            f'xlogfile = "{self.xlogfile}"\n'
            f'dump_dir = "{self.dump_dir}"\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.store = NethackRunStore.from_global(self.g)
        assert self.store is not None
        self.now = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tempdir.cleanup()

    def start(self, *, current_is_nethack=False):
        probe = self.store.prepare_start(
            current_is_nethack=current_is_nethack,
            now=self.now,
        )
        return self.store.record_started(probe, now=self.now)

    def append_xlog(self, **overrides):
        fields = {
            "version": "3.6.7",
            "points": "972",
            "deathlev": "4",
            "maxlvl": "5",
            "hp": "0",
            "maxhp": "28",
            "role": "Val",
            "race": "Hum",
            "gender": "Fem",
            "align": "Law",
            "name": "docich",
            "death": "killed by a water elemental",
            "turns": "3711",
            "achieve": "0x0",
            "starttime": "1789500000",
            "endtime": "1789503600",
            "realtime": "3600",
        }
        fields.update({key: str(value) for key, value in overrides.items()})
        line = "\t".join(f"{key}={value}" for key, value in fields.items()) + "\n"
        with self.xlogfile.open("a", encoding="utf-8") as stream:
            stream.write(line)

    def test_parse_xlog_preserves_value_after_first_equals(self):
        record = parse_xlog_line("name=docich\tdeath=killed by trap=a\tturns=12\n")
        self.assertEqual(record["name"], "docich")
        self.assertEqual(record["death"], "killed by trap=a")
        self.assertEqual(record["turns"], "12")

    def test_terminal_classifier_prefers_ascension_bits(self):
        self.assertEqual(
            classify_terminal_record({"achieve": "0x100", "death": "anything"}),
            "ascended",
        )
        self.assertEqual(classify_terminal_record({"death": "quit"}), "ended")
        self.assertEqual(classify_terminal_record({"death": "escaped"}), "ended")
        self.assertEqual(
            classify_terminal_record({"death": "killed by a grid bug"}), "dead"
        )

    def test_new_run_allocates_private_expedition_and_current_pointer(self):
        run = self.start()
        self.assertEqual(run["expedition"], 1)
        self.assertEqual(run["status"], "active")
        self.assertFalse(run["recovered_existing_save"])
        self.assertFalse(run["adopted_active_runtime"])
        current = self.store.current()
        self.assertEqual(current["run_id"], run["run_id"])
        self.assertEqual((self.g.state_dir / "nethack").stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (self.g.state_dir / "nethack" / ".lock").stat().st_mode & 0o777,
            0o600,
        )

    def test_suspend_and_resume_keep_same_expedition(self):
        first = self.start()
        (self.save_dir / "1000docich").write_bytes(b"save")
        suspended = self.store.record_finished(
            now=self.now + timedelta(minutes=30),
            nethack_still_active=False,
        )
        self.assertEqual(suspended["status"], "suspended")

        probe = self.store.prepare_start(
            current_is_nethack=False,
            now=self.now + timedelta(days=1),
        )
        # NetHack consumes the normal save while restoring it.
        (self.save_dir / "1000docich").unlink()
        resumed = self.store.record_started(probe, now=self.now + timedelta(days=1))
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertEqual(resumed["expedition"], 1)
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(len(resumed["sessions"]), 2)

    def test_suspended_run_without_save_fails_closed(self):
        self.start()
        (self.save_dir / "1000docich").write_bytes(b"save")
        self.store.record_finished(
            now=self.now + timedelta(minutes=30),
            nethack_still_active=False,
        )
        (self.save_dir / "1000docich").unlink()
        with self.assertRaises(NethackRunError):
            self.store.prepare_start(
                current_is_nethack=False,
                now=self.now + timedelta(days=1),
            )

    def test_existing_untracked_save_is_adopted_not_deleted(self):
        save = self.save_dir / "1000docich"
        save.write_bytes(b"existing")
        run = self.start()
        self.assertTrue(run["recovered_existing_save"])
        self.assertEqual(run["expedition"], 1)
        self.assertTrue(save.exists())

    def test_other_players_save_is_not_adopted_for_docich(self):
        other = self.save_dir / "1000otherdocich"
        other.write_bytes(b"other-player-save")
        run = self.start()
        self.assertFalse(run["recovered_existing_save"])
        self.assertEqual(run["expedition"], 1)
        self.assertTrue(other.exists())

    def test_existing_active_runtime_is_adopted_when_history_is_empty(self):
        run = self.start(current_is_nethack=True)
        self.assertTrue(run["adopted_active_runtime"])
        self.assertFalse(run["recovered_existing_save"])

    def test_external_safe_suspend_is_reconciled(self):
        run = self.start(current_is_nethack=True)
        (self.save_dir / "1000docich").write_bytes(b"save")
        probe = self.store.prepare_start(
            current_is_nethack=False,
            now=self.now + timedelta(hours=1),
        )
        current = self.store.current()
        self.assertEqual(current["status"], "suspended")
        self.assertEqual(current["run_id"], run["run_id"])
        self.assertEqual(
            current["sessions"][-1]["outcome"], "external_suspend_detected"
        )
        self.assertEqual(probe["kind"], "existing")

    def test_active_history_without_runtime_or_save_fails_closed(self):
        self.start(current_is_nethack=True)
        with self.assertRaises(NethackRunError):
            self.store.prepare_start(
                current_is_nethack=False,
                now=self.now + timedelta(hours=1),
            )

    def test_death_uses_only_new_player_xlog_record(self):
        # This old record is before the run's byte baseline and must be ignored.
        self.append_xlog(death="killed by an old monster", points=1, turns=2)
        run = self.start()
        self.append_xlog(
            death="killed by a water elemental",
            points=972,
            turns=3711,
            maxlvl=5,
        )
        finished = self.store.record_finished(
            now=self.now + timedelta(hours=1),
            nethack_still_active=False,
        )
        self.assertEqual(finished["run_id"], run["run_id"])
        self.assertEqual(finished["status"], "dead")
        self.assertEqual(finished["death_reason"], "killed by a water elemental")
        self.assertEqual(finished["score"], 972)
        self.assertEqual(finished["turns"], 3711)
        self.assertEqual(finished["max_depth"], 5)
        self.assertEqual(finished["role"], "Val")
        self.assertIsNone(self.store.current())

    def test_ascension_and_amulet_bits_are_recorded(self):
        self.start()
        self.append_xlog(death="ascended", achieve="0x120", points=123456)
        finished = self.store.record_finished(
            now=self.now + timedelta(hours=2),
            nethack_still_active=False,
        )
        self.assertEqual(finished["status"], "ascended")
        self.assertEqual(finished["achievement_bits"], 0x120)
        self.assertTrue(finished["got_amulet"])

    def test_quit_is_terminal_but_not_mislabeled_as_death(self):
        self.start()
        self.append_xlog(death="quit")
        finished = self.store.record_finished(
            now=self.now + timedelta(minutes=5),
            nethack_still_active=False,
        )
        self.assertEqual(finished["status"], "ended")
        self.assertEqual(finished["death_reason"], "quit")

    def test_missing_or_truncated_xlog_never_invents_death_reason(self):
        self.start()
        finished = self.store.record_finished(
            now=self.now + timedelta(minutes=5),
            nethack_still_active=False,
        )
        self.assertEqual(finished["status"], "ended_unknown")
        self.assertIsNone(finished["death_reason"])
        self.assertEqual(finished["terminal"]["analysis_error"], "xlogfile-missing")

        # New expedition with a nonzero baseline, then destructive truncation.
        self.append_xlog(name="other", death="quit")
        second = self.start()
        self.xlogfile.write_text("", encoding="utf-8")
        truncated = self.store.record_finished(
            now=self.now + timedelta(days=1),
            nethack_still_active=False,
        )
        self.assertEqual(second["expedition"], 2)
        self.assertEqual(truncated["status"], "ended_unknown")
        self.assertEqual(truncated["terminal"]["analysis_error"], "xlogfile-truncated")
        self.assertIsNone(truncated["death_reason"])

    def test_new_dumplog_is_attached_but_old_one_is_ignored(self):
        old = self.dump_dir / "nethack.docich.old.log"
        old.write_text("old", encoding="utf-8")
        old_ns = 1_700_000_000_000_000_000
        os.utime(old, ns=(old_ns, old_ns))
        self.start()
        new = self.dump_dir / "nethack.docich.new.log"
        new.write_text("new", encoding="utf-8")
        new_ns = old_ns + 10_000
        os.utime(new, ns=(new_ns, new_ns))
        self.append_xlog(death="killed by a newt")
        finished = self.store.record_finished(
            now=self.now + timedelta(hours=1),
            nethack_still_active=False,
        )
        self.assertEqual(finished["dump_file"], new.name)

    def test_finish_while_nethack_remains_active_does_not_fake_terminal(self):
        run = self.start(current_is_nethack=True)
        finished = self.store.record_finished(
            now=self.now + timedelta(minutes=10),
            nethack_still_active=True,
        )
        self.assertEqual(finished["run_id"], run["run_id"])
        self.assertEqual(finished["status"], "active")
        self.assertEqual(finished["sessions"][-1]["outcome"], "continued")
        self.assertEqual(self.store.current()["run_id"], run["run_id"])


if __name__ == "__main__":
    unittest.main()
