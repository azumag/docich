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


def test_review_screen_shows_a_status_strip_of_verified_facts():
    html = view.HTML

    # The live view's status panel disappears in review mode; the kifu show
    # keeps an at-a-glance status instead (docich#2737): a bounded strip of
    # the already-published facts beside the move list, before the commentary.
    assert ".stat{display:flex;flex-wrap:wrap" in html
    assert ".stat i{font-style:normal" in html
    assert "statLabel={rating:\"レート\",clock:\"考慮時間\",fouls:\"反則\",shapes:\"成打\"}" in html
    assert "'<div class=\"side\">'+(stat?'<div class=\"stat\">'+stat+'</div>':'')+" in html
    # Only the payload's own values are rendered, never invented ones.
    assert "show.status||null" in html and "st[k]" in html


def test_live_view_explains_that_only_postgame_public_kifu_is_shown():
    html = view.HTML

    assert "対局中の駒配置・持ち駒は公開しません" in html
    assert "終局後の感想戦" in html
    assert "検証済みの公開棋譜" in html
