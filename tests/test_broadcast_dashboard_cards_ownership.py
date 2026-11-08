"""Docich-owned dashboard-card renderer remains usable without soviet_now."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "docich" / "broadcast_dashboard_cards.py"


class DashboardCardsOwnershipTests(unittest.TestCase):
    def test_can_load_and_render_without_soviet_game_submodule(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("def render_game_dashboard(", source)
        self.assertIn("def render_ops_dashboard(", source)
        self.assertIn("def dashboard_css(", source)
        self.assertNotIn("from games.soviet_now", source)
        spec = importlib.util.spec_from_file_location("docich_broadcast_dashboard_cards", MODULE)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        game_raw = (
            "SOREN/CORNER: SOREN91 / soren91 / ACTIVE\n"
            "Live: this corner results 42\n"
            "Stats: 120 results / best=1 / Recent30=5.2\n"
            "  wins=7 / lower rank is better\n"
            "Rank Timeline\n"
            "Last8: 12 9 4 7 1 3 2 5"
        )
        game = module.render_game_dashboard(game_raw)
        self.assertIn("GAME PERFORMANCE", game)
        self.assertIn("BEST", game)
        self.assertEqual(game.count('class="bar-wrap"'), 8)
        self.assertEqual(module.render_game_dashboard("no game"), "")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SOREN_DIRECT_AV_SYNC_STATE_FILE": str(Path(tmp) / "missing.json")}):
                ops = module.render_ops_dashboard("  HEALTH\n    ● Workers 3/3 ONLINE\n    ● Backend LIVE")
        self.assertIn("SYSTEM HEALTH", ops)
        self.assertIn("ALL SYSTEMS NOMINAL", ops)
        self.assertIn(".broadcast-card", module.dashboard_css())
        self.assertEqual(module.render_ops_dashboard("no workers or backend"), "")


if __name__ == "__main__":
    unittest.main()
