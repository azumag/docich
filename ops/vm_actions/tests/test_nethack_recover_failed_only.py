import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from docich import nethack_corner_operator as operator  # noqa: E402
from docich.nethack_corner import NethackCornerError  # noqa: E402


class NetHackRecoverSafetyTests(unittest.TestCase):
    def test_recover_rejects_active_manual_corner_without_switching(self):
        fake_g = SimpleNamespace(config_path=Path("/tmp/docich.toml"))
        manager = mock.Mock()
        manager._locked.return_value.__enter__ = mock.Mock(return_value=None)
        manager._locked.return_value.__exit__ = mock.Mock(return_value=False)
        manager._read_state.return_value = {
            "status": "active",
            "previous_game": "sorengame",
        }
        manager._active_game_reader.return_value = "nethack"

        with mock.patch.object(operator, "load_global", return_value=fake_g), mock.patch.object(
            operator, "ManualNethackCornerManager", return_value=manager
        ):
            with self.assertRaisesRegex(NethackCornerError, "failed manual corner"):
                operator.recover(fake_g.config_path)

        manager._transition_to.assert_not_called()

    def test_recover_holds_manual_lock_across_state_check_and_transition(self):
        fake_g = SimpleNamespace(config_path=Path("/tmp/docich.toml"))
        manager = mock.Mock()
        lock_held = False

        @contextmanager
        def held_lock():
            nonlocal lock_held
            self.assertFalse(lock_held)
            lock_held = True
            try:
                yield
            finally:
                lock_held = False

        def read_state():
            self.assertTrue(lock_held)
            return {"status": "failed", "previous_game": "sorengame"}

        def read_active_game():
            self.assertTrue(lock_held)
            return "nethack"

        def transition(current, previous):
            self.assertTrue(lock_held)
            self.assertEqual(current, "nethack")
            self.assertEqual(previous, "sorengame")

        manager._locked.side_effect = held_lock
        manager._read_state.side_effect = read_state
        manager._active_game_reader.side_effect = read_active_game
        manager._transition_to.side_effect = transition

        with mock.patch.object(operator, "load_global", return_value=fake_g), mock.patch.object(
            operator, "ManualNethackCornerManager", return_value=manager
        ):
            result = operator.recover(fake_g.config_path)

        self.assertEqual(
            result,
            {"status": "recovered", "from_game": "nethack", "to_game": "sorengame"},
        )
        self.assertFalse(lock_held)
        manager._transition_to.assert_called_once_with("nethack", "sorengame")


if __name__ == "__main__":
    unittest.main()
