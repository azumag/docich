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
        # Timeout contract: resolver matches outlast the 600s request
        # default, so the game extends only the boundary wait.
        self.assertEqual(game.lifecycle.boundary_timeout_s, 7200.0)

    def test_boundary_timeout_accepts_plain_numbers(self):
        self.assertEqual(
            config.GameLifecycleConfig(
                require_round_boundary=True, boundary_timeout_s=300
            ).boundary_timeout_s,
            300.0,
        )
        self.assertIsNone(config.GameLifecycleConfig().boundary_timeout_s)

    def test_boundary_timeout_rejects_garbage(self):
        for bad in ("soon", -5, 0, True):
            with self.assertRaises(config.ConfigError):
                config.GameLifecycleConfig(boundary_timeout_s=bad)


if __name__ == "__main__":
    unittest.main()
