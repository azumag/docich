"""Program-view contract checks for the AI探偵ミステリー board (no live display)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter, program  # noqa: E402
from docich.adapters.mystery_program import MysteryProgramViewAdapter  # noqa: E402
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.mystery_view import DEFAULT_PORT, VIEW_NAME  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        display=SimpleNamespace(
            name=":99", width=1280, height=720,
            viewport_x=0, viewport_y=90,
            viewport_width=960, viewport_height=540,
        ),
    )


def _spec(tmp_path, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game=VIEW_NAME, adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _arg(viewer, flag):
    return viewer[viewer.index(flag) + 1]


def test_mystery_view_is_registered_and_owns_the_full_broadcast_frame(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, MysteryProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    viewer = adapter._viewer_command()
    assert viewer[0] == sys.executable
    # 2026-10-21 owner request: the board is a broadcast-native information
    # screen and owns the whole 1280x720 frame instead of the game viewport.
    assert (_arg(viewer, "--x"), _arg(viewer, "--y")) == ("0", "0")
    assert (_arg(viewer, "--width"), _arg(viewer, "--height")) == ("1280", "720")
    assert f"--app=http://127.0.0.1:{DEFAULT_PORT}/broadcast" in viewer
    assert "--window-size=1280,720" in viewer
    assert "--lang=ja-JP" in viewer
    assert "--disable-translate" in viewer
    assert spec.runtime_id in " ".join(viewer)


def test_mystery_view_falls_back_to_the_game_viewport_without_a_display_size(
        tmp_path, monkeypatch):
    g = _global(tmp_path)
    g.display.width = 0
    g.display.height = 0
    monkeypatch.setattr(program, "_browser_bin", lambda: "/usr/bin/chromium")

    viewer = make_coordinator_adapter(g, _spec(tmp_path))._viewer_command()

    assert (_arg(viewer, "--x"), _arg(viewer, "--y")) == ("0", "90")
    assert (_arg(viewer, "--width"), _arg(viewer, "--height")) == ("960", "540")
    assert "--window-size=960,540" in viewer
