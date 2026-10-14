import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, state as state_mod  # noqa: E402


class TestState(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        self.state = state_mod.State(self.g)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ensure_creates_directories(self):
        self.assertFalse(self.state.state_dir.exists())
        self.state.ensure()
        self.assertTrue(self.state.state_dir.is_dir())
        self.assertTrue(self.state.logs_dir.is_dir())
        self.assertTrue(self.state.screenshots_dir.is_dir())
        self.assertTrue(self.state.retroarch_dir.is_dir())

    def test_ensure_is_idempotent(self):
        self.state.ensure()
        self.state.ensure()  # should not raise
        self.assertTrue(self.state.state_dir.is_dir())

    def test_current_game_none_when_not_set(self):
        self.assertIsNone(self.state.current_game())

    def test_set_and_get_current_game(self):
        self.state.set_current_game("nethack")
        self.assertEqual(self.state.current_game(), "nethack")

    def test_set_current_game_creates_state_dir(self):
        self.assertFalse(self.state.state_dir.exists())
        self.state.set_current_game("nethack")
        self.assertTrue(self.state.state_dir.is_dir())

    def test_overwrite_current_game(self):
        self.state.set_current_game("nethack")
        self.state.set_current_game("hanjuku-hero")
        self.assertEqual(self.state.current_game(), "hanjuku-hero")

    def test_clear_current_game(self):
        self.state.set_current_game("nethack")
        self.state.clear_current_game()
        self.assertIsNone(self.state.current_game())

    def test_clear_current_game_when_absent_does_not_raise(self):
        self.state.clear_current_game()  # should not raise
        self.assertIsNone(self.state.current_game())

    def test_paths_are_under_state_dir(self):
        self.assertEqual(self.state.logs_dir, self.state.state_dir / "logs")
        self.assertEqual(self.state.screenshots_dir, self.state.state_dir / "screenshots")
        self.assertEqual(self.state.retroarch_dir, self.state.state_dir / "retroarch")


if __name__ == "__main__":
    unittest.main()
