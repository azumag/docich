from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402


class TestRobotsConfig(unittest.TestCase):
    def test_robots_opts_into_round_boundary_switching(self):
        repo_root = Path(__file__).resolve().parents[1]
        g = config.load_global(repo_root)
        game = config.load_game(g, "robots")

        self.assertTrue(game.lifecycle.require_round_boundary)


if __name__ == "__main__":
    unittest.main()
