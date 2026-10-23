"""Regression coverage for the Tsuitate post-game review layout."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import tsuitate_view as view  # noqa: E402


def test_review_mode_replaces_live_view_with_visible_board_layout():
    html = view.HTML

    # The review payload must not sit in an uncaptured second grid row.  It is
    # hidden during the live match and promoted into the fixed 960x540 canvas
    # only after applyReview marks the page as reviewing.
    assert ".kifu{display:none}" in html
    assert "body.reviewing main>section{display:none}" in html
    assert "body.reviewing .kifu{height:500px;display:grid" in html
    assert "grid-template-columns:414px minmax(0,1fr)" in html

    # The published kifu is rendered as an actual 9x9 board beside the move
    # list/commentary, rather than as unstyled block elements below the canvas.
    assert ".board{grid-column:1;grid-row:2;width:414px;height:414px" in html
    assert "grid-template-columns:repeat(9,1fr)" in html
    assert ".sq{" in html and ".sq.gote{transform:rotate(180deg)}" in html
    assert ".side{grid-column:2;grid-row:2" in html
    assert 'document.body.classList.add("reviewing")' in html
    assert '<div id="kifu" class="kifu"></div>' in html


def test_live_view_explains_that_only_postgame_public_kifu_is_shown():
    html = view.HTML

    assert "対局中の駒配置・持ち駒は公開しません" in html
    assert "終局後の感想戦" in html
    assert "検証済みの公開棋譜" in html
