"""C. nsnake 自動プレイ有効化の config テスト。

config/games/nsnake.toml は tracked wrapper と command brain を参照し、
本番 [retro_corner].games に追加できる形状であることを既存ロード経路で検証する。
ゲーム本体 (/usr/games/nsnake) は実測していないため、ここでは実行はしない。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "src"))

from docich import config as config_module  # noqa: E402
from docich.adapters.cli_game import cli_command_list  # noqa: E402
from docich.retro_corner import load_retro_corner_config  # noqa: E402


class TestNsnakeAgentConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = config_module.load_global(ROOT, ROOT / "config" / "docich.toml")
        cls.game = config_module.load_game(cls.g, "nsnake")

    def test_command_points_at_tracked_wrapper(self):
        self.assertEqual(
            cli_command_list(self.game),
            ["/bin/sh", "games/cli-wrappers/nsnake_docich.sh"],
        )

    def test_agent_is_command_brain_running_restored_script(self):
        self.assertTrue(self.game.agent.enabled)
        self.assertEqual(self.game.agent.brain, "command")
        self.assertEqual(self.game.agent.command, ["python3", "brains/nsnake/brain.py"])

    def test_brain_builds_and_answers_direction_from_pane_text(self):
        from docich.adapters.base import Observation
        from docich.agent.brains import CommandBrain, build_brain

        brain = build_brain(self.g, self.game)
        self.assertIsInstance(brain, CommandBrain)
        def pane(rows):
            lines = ["lnsnake 3.0.0qqqqqqqqqqqqArcade Modek"]
            lines.append("x" + "a" * 78 + "x")
            lines.extend("x" + "a" + r.ljust(76) + "a" + "x" for r in rows)
            lines.append("x" + "a" * 78 + "x")
            lines.append(
                "xHi-Score 0                Score 0                   Speed 1                   x"
            )
            return "\n".join(lines)

        actions = brain.decide(Observation(
            game="nsnake", title="Snake", adapter="cli", ts=0,
            kind="text", text=pane(["", "  oo@  ", "", "", "", "", " " * 35 + "$"]),
        ))
        self.assertTrue(actions, "brain must act on a play field")
        self.assertEqual(actions[0].type, "key")
        self.assertEqual(actions[0].keys, ["Down"])

    def test_corner_self_play_flag_present(self):
        corner = self.game.raw.get("corner", {})
        self.assertIs(corner.get("self_play"), True)


def build_brain_for_test(g, game):
    from docich.agent.brains import build_brain

    return build_brain(g, game)


class TestLiveRetroCornerGames(unittest.TestCase):
    def test_live_config_includes_ninvaders_and_nsnake(self):
        live_g = config_module.load_global(ROOT, ROOT / "config" / "docich.soren-live.toml")
        cfg = load_retro_corner_config(live_g)
        self.assertIn("ninvaders", cfg.games)
        self.assertIn("nsnake", cfg.games)
        # bot_eval が ninvaders/nsnake の両方に対応したので、改善 agents は非空。
        self.assertTrue(cfg.improve_agents.strip())
        self.assertEqual(cfg.target_matches, 3)

    def test_default_profile_stays_disabled(self):
        cfg = load_retro_corner_config(config_module.load_global(ROOT, ROOT / "config" / "docich.toml"))
        self.assertFalse(cfg.enabled)


if __name__ == "__main__":
    unittest.main()
