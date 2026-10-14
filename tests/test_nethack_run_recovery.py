import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.game_switch import atomic_write_json  # noqa: E402
from docich.nethack_run import NethackRunError, NethackRunStore  # noqa: E402


class TestNethackRunCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        base = Path(self.tempdir.name)
        self.root = base / "repo"
        self.save_dir = base / "playground" / "save"
        self.dump_dir = base / "playground" / "dumps"
        self.xlogfile = base / "playground" / "xlogfile"
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
        self.now = datetime(2026, 9, 16, tzinfo=timezone.utc)

    def tearDown(self):
        self.tempdir.cleanup()

    def _start(self):
        probe = self.store.prepare_start(current_is_nethack=False, now=self.now)
        return self.store.record_started(probe, now=self.now)

    def _terminal_xlog(self, *, death="killed by a newt"):
        self.xlogfile.write_text(
            "\t".join(
                [
                    "version=3.6.7",
                    "points=10",
                    "maxlvl=2",
                    "role=Val",
                    "race=Hum",
                    "gender=Fem",
                    "align=Law",
                    "name=docich",
                    f"death={death}",
                    "turns=50",
                    "achieve=0x0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_meta_counter_recovers_from_durable_run_files(self):
        first = self._start()
        self._terminal_xlog()
        self.store.record_finished(
            now=self.now + timedelta(minutes=5),
            nethack_still_active=False,
        )

        # Simulate a crash window where a durable run existed but the cached
        # expedition counter had not reached the same value.
        atomic_write_json(
            self.store.meta_path,
            {"schema_version": 1, "last_expedition": 0},
        )
        probe = self.store.prepare_start(
            current_is_nethack=False,
            now=self.now + timedelta(days=1),
        )
        self.assertEqual(first["expedition"], 1)
        self.assertEqual(probe["expected_expedition"], 2)
        repaired = json.loads(self.store.meta_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["last_expedition"], 1)

    def test_stale_current_pointer_to_terminal_run_is_repaired(self):
        first = self._start()
        self._terminal_xlog()
        terminal = self.store.record_finished(
            now=self.now + timedelta(minutes=5),
            nethack_still_active=False,
        )
        self.assertEqual(terminal["status"], "dead")

        # Simulate a crash after terminal run body fsync but before current.json
        # unlink became durable.
        atomic_write_json(
            self.store.current_path,
            {"schema_version": 1, "run_id": first["run_id"]},
        )
        probe = self.store.prepare_start(
            current_is_nethack=False,
            now=self.now + timedelta(days=1),
        )
        self.assertEqual(probe["kind"], "new")
        self.assertEqual(probe["expected_expedition"], 2)
        self.assertFalse(self.store.current_path.exists())

    def test_pointer_without_run_body_fails_closed(self):
        self.store._ensure_private_dirs()
        atomic_write_json(
            self.store.current_path,
            {
                "schema_version": 1,
                "run_id": "00000000-0000-0000-0000-000000000001",
            },
        )
        with self.assertRaises(NethackRunError):
            self.store.prepare_start(current_is_nethack=False, now=self.now)


if __name__ == "__main__":
    unittest.main()
