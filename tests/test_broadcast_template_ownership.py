"""Broadcast HTML ownership regression.

This test intentionally runs without the soviet_now submodule: the broadcast
presentation is an owned docich artifact rather than a generated game asset.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "overlays" / "direct_broadcast_overlay.html"


def _declarations(html: str, selector: str) -> str:
    """Return the body of the exact ``selector { ... }`` rule."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", html)
    assert match, f"missing rule {selector}"
    return match.group(1)


class BroadcastTemplateOwnershipTest(unittest.TestCase):
    def test_docich_owns_the_multigame_broadcast_template(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        for marker in (
            'id="broadcast-overlay"',
            'data-soren-region',
            'hanjuku-gap-news',
            'gapBuildSkeleton',
            'game-gap',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_paper_pnl_titles_wrap_without_ellipsis(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("card.classList.add('paper-pnl')", html)
        self.assertIn(".toast.paper-pnl .toast-title", html)
        self.assertIn("white-space: normal;", html)
        self.assertIn("text-overflow: clip;", html)

    def test_paper_and_live_trade_cards_have_distinct_backgrounds(self):
        # Owner 2026-11-08: PAPER and real-money short reports must be told
        # apart by card background. LIVE is keyed by the `実売｜`/`実買｜`
        # title prefix; PAPER keeps `売｜`/`買｜`/`裁定｜`.
        html = TEMPLATE.read_text(encoding="utf-8")
        paper = _declarations(html, ".toast.paper-trade")
        live = _declarations(html, ".toast.live-trade")
        paper_bg = re.search(r"background:\s*(#[0-9a-fA-F]{6})", paper)
        live_bg = re.search(r"background:\s*(#[0-9a-fA-F]{6})", live)
        self.assertTrue(paper_bg and live_bg)
        self.assertNotEqual(paper_bg.group(1).lower(), live_bg.group(1).lower())
        # Neither may fall back to the shared worker-card navy.
        self.assertNotEqual(paper_bg.group(1).lower(), "#051422")
        self.assertNotEqual(live_bg.group(1).lower(), "#051422")
        self.assertIn("card.classList.add(liveTrade ? 'live-trade' : 'paper-trade')", html)
        # `実売｜` must not be swallowed by the PAPER pattern: PAPER's regex is
        # anchored at the start, so `実` prefixed titles never match it.
        self.assertIn("/^実(?:売|買)｜/", html)
        self.assertIn("/^(?:売|買|裁定)｜/", html)

    def test_news_area_stays_bounded_and_non_paginated(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("hanjuku-gap-news", html)
        self.assertNotIn("pagingStartedAt", html)
        self.assertNotIn("fitHanjukuGap(", html)

    def test_notification_strip_draws_no_colored_left_band(self):
        # Owner 2026-11-08: the left overlay's notification strip (decisions,
        # decision entries, state-change toasts) must not draw the colored
        # edge band. Tone classes may keep their border-color, but no base
        # rule may reintroduce a left border.
        html = TEMPLATE.read_text(encoding="utf-8")
        for selector in (
            ".hanjuku-gap-decisions",
            ".hanjuku-gap-decision",
            ".hanjuku-gap-toast",
        ):
            with self.subTest(selector=selector):
                self.assertNotRegex(_declarations(html, selector),
                                    r"border-left\s*:")


if __name__ == "__main__":
    unittest.main()
