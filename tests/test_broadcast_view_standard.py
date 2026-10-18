"""Repository standard: no browser translation over a docich broadcast page.

Two halves must hold for every docich HTML broadcast view, and both are
enforced here so a new view cannot quietly regress:

1. the chromium command built by the view adapter passes
   ``BROADCAST_TRANSLATE_ARGS``, and a Japanese page pins ``--lang=ja-JP``;
2. the served HTML declares ``JAPANESE_PAGE_MARKERS``.

The market views (``stock-paper-view`` / ``fx-paper-view``) share
``ProgramViewAdapter._viewer_command`` with the PAPER dashboard, so they pass
the same command check by construction; their HTML is checked below.  The
NetHack tiles browser is checked in ``tests/test_nethack_tiles_supervisor.py``
because it serves the game's own UI rather than a docich Japanese page.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter, program  # noqa: E402
from docich.broadcast_view import (  # noqa: E402
    BROADCAST_TRANSLATE_ARGS,
    JAPANESE_LANG_ARG,
    japanese_page_markers_missing,
)
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.trading.markets import program as market_program  # noqa: E402
from docich import doushi_view, mystery_view, othello_view, puyo_view, tsuitate_view, weather_view, yoso_view  # noqa: E402

#: Every docich HTML broadcast view, by the game name its adapter is registered
#: under.  A new view must be added here and must use the shared standard.
BROADCAST_VIEWS = (
    "paper-view", "weather-view", "tsuitate-view", "yosou-view", "doushi-view",
    "othello-view", "puyo-view",
    "mystery-view",
)

#: The PAPER dashboard ships as a static asset rather than a template constant.
PAPER_DASHBOARD = ROOT / "src" / "docich" / "trading" / "dashboard_assets" / "index.html"


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        repo_root=ROOT,
        display=SimpleNamespace(
            name=":98", width=1280, height=720,
            viewport_x=0, viewport_y=90,
            viewport_width=960, viewport_height=540,
        ),
    )


def _spec(tmp_path, game, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game=game, adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


@pytest.mark.parametrize("game", BROADCAST_VIEWS)
def test_broadcast_view_disables_browser_translation(game, tmp_path, monkeypatch):
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")
    adapter = make_coordinator_adapter(_global(tmp_path), _spec(tmp_path, game))
    command = " ".join(adapter._viewer_command())

    for argument in BROADCAST_TRANSLATE_ARGS:
        assert argument in command, f"{game} is missing {argument}"
    # Every docich broadcast page is Japanese: the UI language is pinned too.
    assert JAPANESE_LANG_ARG in command
    assert "--app=http://127.0.0.1:" in command


def test_translate_standard_disables_both_chromium_gates():
    # Chromium honored either switch at different versions; both stay set.
    assert "--disable-translate" in BROADCAST_TRANSLATE_ARGS
    assert "--disable-features=Translate,TranslateUI,BackForwardCache" in BROADCAST_TRANSLATE_ARGS


def test_served_japanese_pages_declare_notranslate():
    pages = {
        "tsuitate-view": tsuitate_view.HTML,
        "weather-view": weather_view.HTML,
        "yosou-view": yoso_view.HTML,
        "doushi-view": doushi_view.HTML,
        "othello-view": othello_view.HTML,
        "mystery-view": mystery_view.HTML,
        "market-view": market_program.HTML,
        "puyo-view": puyo_view.HTML,
        "paper-view": PAPER_DASHBOARD.read_text(encoding="utf-8"),
    }
    for name, html in pages.items():
        assert japanese_page_markers_missing(html) == [], f"{name} must declare the markers"


def test_unknown_or_empty_page_is_reported_as_missing_markers():
    # The check is honest for a view that has not adopted the standard yet.
    assert japanese_page_markers_missing("<html lang='ja'><body>hi</body>") != []
    assert japanese_page_markers_missing(None) != []
