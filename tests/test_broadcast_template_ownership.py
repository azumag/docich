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

    def test_next_corner_card_keeps_a_prediction_hold_visible(self):
        # Owner 2026-11-15: the advance notice must stay above the prediction
        # panel. A prediction hold shows the round's remaining games (never a
        # clock); a post-corner handoff says 予想開始待ち and must not remove
        # the card.
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="next-corner"', html)
        self.assertIn("state?.nextCorner", html)
        self.assertIn("notice.hold === 'prediction'", html)
        self.assertIn("notice.hold === 'prediction-pending'", html)
        self.assertIn("NEXT CORNER · 予想中", html)
        self.assertIn("NEXT CORNER · 予想準備中", html)
        self.assertIn("remainingGames", html)
        self.assertIn("試合後", html)
        self.assertIn("予想開始待ち", html)
        self.assertIn("予想終了後", html)
        # Both cards are laid out together: the feed height reserves the
        # prediction panel and the notice instead of hiding either one.
        self.assertIn(
            'html[data-prediction-active="1"][data-next-corner="1"] #feed '
            '{ height: 568px; }',
            html,
        )

    def test_next_corner_title_and_time_are_large_and_not_clipped(self):
        # Owner 2026-11-15: the advance notice's title and start time were too
        # small to read. The card is a fixed-height box with overflow hidden,
        # so the larger type must still fit: the eye-brow, both rows and the
        # padding have to stay inside the declared height.
        html = TEMPLATE.read_text(encoding="utf-8")
        card = _declarations(html, "#next-corner")
        head = _declarations(html, ".next-corner-head")
        label = _declarations(html, ".next-corner-label")
        when = _declarations(html, ".next-corner-when")

        def px(decl, prop):
            match = re.search(rf"{prop}:\s*([0-9.]+)px", decl)
            self.assertTrue(match, f"missing {prop} in {decl!r}")
            return float(match.group(1))

        self.assertGreaterEqual(px(label, "font-size"), 16)
        self.assertGreaterEqual(px(when, "font-size"), 16)
        # Owner 2026-11-15: the corner name and the remaining-games line are
        # read together, so they share the larger size.
        self.assertEqual(px(label, "font-size"), px(when, "font-size"))
        rows = (px(head, "line-height") + px(label, "margin-top")
                + px(label, "line-height") + px(when, "line-height"))
        outer = px(card, "height")
        self.assertLessEqual(
            rows + 2 * px(card, "padding") + 2 * px(card, "border"), outer)

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
