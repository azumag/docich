"""Regression tests for P2 watchdog canonical/mirror precedence."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, watchdog  # noqa: E402
from docich.game_switch import GameSwitchStore  # noqa: E402
from docich.state import State  # noqa: E402


class TestWatchdogCanonicalFailClosed(unittest.TestCase):
    def _config(self, root: Path):
        path = root / "docich.toml"
        path.write_text(
            "[paths]\n"
            f'state_dir = "{root / "run"}"\n'
            f'games_dir = "{root / "games"}"\n'
            f'roms_dir = "{root / "roms"}"\n',
            encoding="utf-8",
        )
        return config.load_global(root, config_path=path)

    def test_existing_idle_canonical_does_not_fall_back_to_stale_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = self._config(root)
            state = State(g)
            state.set_current_game("stale-game")
            GameSwitchStore(g.state_dir).canonical.initialize()

            self.assertEqual(
                watchdog._freeze_targets(g, state),
                (None, "game", "agent"),
            )

    def test_corrupt_canonical_does_not_fall_back_to_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = self._config(root)
            state = State(g)
            state.set_current_game("stale-game")
            canonical = g.state_dir / "game_switch.json"
            canonical.write_text("{not-json", encoding="utf-8")

            self.assertEqual(
                watchdog._freeze_targets(g, state),
                (None, "game", "agent"),
            )


if __name__ == "__main__":
    unittest.main()
