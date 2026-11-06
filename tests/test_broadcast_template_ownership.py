"""Broadcast HTML ownership regression.

This test intentionally runs without the soviet_now submodule: the broadcast
presentation is an owned docich artifact rather than a generated game asset.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "overlays" / "direct_broadcast_overlay.html"


class BroadcastTemplateOwnershipTest(unittest.TestCase):
    def test_docich_owns_the_multigame_broadcast_template(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        for marker in (
            'id="broadcast-overlay"',
            'data-soren-region',
            'hanjuku-gap-news',
            'gapBuildSkeleton',
            'broadcastGameGap',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_news_area_stays_bounded_and_non_paginated(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("hanjuku-gap-news", html)
        self.assertNotIn("pagingStartedAt", html)
        self.assertNotIn("fitHanjukuGap(", html)


if __name__ == "__main__":
    unittest.main()
