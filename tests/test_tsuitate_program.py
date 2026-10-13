import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter
from docich.adapters import cli_game, tsuitate_program
from docich.adapters.base import AdapterError
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec
from docich.naming import runtime_names
from docich import tsuitate_view


def _global(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    return SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        display=SimpleNamespace(
            name=":98", viewport_x=0, viewport_y=90,
            viewport_width=960, viewport_height=540,
        ),
    )


def _spec(tmp_path, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game="tsuitate-view", adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _status(**overrides):
    value = {
        "state": "stopped",
        "runId": None,
        "gameId": None,
        "brainVersion": "tsuitate-brain-v2",
        "completedGames": 0,
        "reservedGames": 0,
        "stopRequested": False,
        "readyForNextRun": True,
    }
    value.update(overrides)
    return value


def _response(data, status=200):
    class Response:
        def __init__(self):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _limit):
            return json.dumps(data).encode("utf-8")

    return Response()


def test_tsuitate_view_factory_uses_generation_owned_loopback_page(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(tsuitate_program, "_browser_bin", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(tsuitate_program, "call_beta_control", lambda *_: _status())

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, tsuitate_program.TsuitateProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    assert adapter._game_command() == [
        sys.executable, "-m", "docich.tsuitate_view",
        "--port", "8804",
        "--runtime-id", spec.runtime_id,
        "--generation", str(spec.generation),
        "--lease-id", spec.lease_id,
    ]
    viewer = adapter._viewer_command()
    assert "--app=http://127.0.0.1:8804/broadcast" in viewer
    assert spec.runtime_id in " ".join(viewer)


def test_tsuitate_view_preflight_requires_ready_beta_owner(tmp_path, monkeypatch):
    g = _global(tmp_path)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    monkeypatch.setattr(
        tsuitate_program, "call_beta_control",
        lambda *_: _status(state="paused", readyForNextRun=False),
    )

    with pytest.raises(AdapterError, match="次局を開始できる状態"):
        adapter.preflight(time.monotonic() + 5, None)


def _settled_result(**overrides):
    value = {"outcome": "win", "reason": "checkmate", "endedAt": "2026-10-06T00:05:00.000Z",
             "moveNumber": 9, "resultConfidence": "verified"}
    value.update(overrides)
    return value


def test_broadcast_view_shows_the_settled_result_but_never_a_live_one(monkeypatch):
    monkeypatch.setattr(
        tsuitate_view, "call_beta_control",
        lambda *_: {**_status(state="playing", runId="fixture-run", reservedGames=1),
                    "gameResult": _settled_result()},
    )
    # A result attached to a live match is withheld: the Worker's settled-only
    # rule is re-checked here so a drifted control response cannot leak it.
    live = tsuitate_view.status_projection("g4-a1b2c3d4", 4, "lease-fixture")
    assert live["ok"] is True and live["gameResult"] is None

    monkeypatch.setattr(
        tsuitate_view, "call_beta_control",
        lambda *_: {**_status(state="finished", runId="fixture-run", completedGames=1,
                               reservedGames=1, readyForNextRun=True),
                    "gameResult": _settled_result()},
    )
    finished = tsuitate_view.status_projection("g4-a1b2c3d4", 4, "lease-fixture")
    assert finished["gameResult"] == _settled_result()
    assert "result" in tsuitate_view.HTML and "rv-outcome" in tsuitate_view.HTML
    assert "AIの勝ち" in tsuitate_view.HTML and "詰み" in tsuitate_view.HTML
    for forbidden in ["yourPieces", "yourHand", "decisions", "usi", "opponentPieces"]:
        assert forbidden not in tsuitate_view.HTML

    monkeypatch.setattr(
        tsuitate_view, "call_beta_control",
        lambda *_: {**_status(state="finished", runId="fixture-run", readyForNextRun=True),
                    "gameResult": {"outcome": "victory"}},
    )
    malformed = tsuitate_view.status_projection("g4-a1b2c3d4", 4, "lease-fixture")
    assert malformed["ok"] is True and malformed["gameResult"] is None


def test_broadcast_projection_drops_unreviewed_remote_fields(monkeypatch):
    monkeypatch.setattr(
        tsuitate_view,
        "call_beta_control",
        lambda *_: {
            **_status(state="playing", runId="fixture-run", reservedGames=1),
            "token": "do-not-leak",
            "opponentPieces": [{"square": "5e"}],
            "playerView": {
                "yourColor": "sente",
                "yourPieces": [{"square": "5i", "role": "king", "private": "drop"}],
                "yourHand": {"pawn": 1},
                "turn": "sente", "moveNumber": 9,
                "clocks": {"senteMs": 1000, "goteMs": 2000, "running": "sente", "serverTime": 3000},
                "fouls": {"you": 1, "opponent": 2},
                "youInCheck": False, "opponentInCheck": True, "status": "playing",
                "opponentPieces": [{"square": "5a", "role": "king"}],
            },
        },
    )
    data = tsuitate_view.status_projection("g4-a1b2c3d4", 4, "lease-fixture")
    assert data["ok"] is True
    assert data["gameActive"] is True
    assert data["brainVersion"] == "tsuitate-brain-v2"
    assert "token" not in data
    assert "opponentPieces" not in data
    assert "runId" not in data
    assert "playerView" not in data
    assert data["spectatorView"] == {
        "yourColor": "sente", "turn": "sente", "moveNumber": 9,
        "clocks": {"senteMs": 1000, "goteMs": 2000, "running": "sente", "serverTime": 3000},
        "status": "playing",
    }
    assert "yourPieces" not in data["spectatorView"]
    assert "yourHand" not in data["spectatorView"]
    assert "opponentPieces" not in tsuitate_view.HTML
    assert "公平性のため配信しません" in tsuitate_view.HTML


@pytest.mark.parametrize(("reason", "expected"), [
    ("repetition", "千日手"),
    ("stalemate", "stalemate"),
])
@pytest.mark.parametrize("surface", ["broadcast", "webui"])
def test_terminal_reason_labels_preserve_the_reported_reason(reason, expected, surface):
    # Repetition does not imply perpetual check. Keep stalemate's source term
    # until its site-specific rules have been established, rather than inventing
    # a shogi adjudication or deriving a win/draw from this reason alone.
    if surface == "broadcast":
        html, name = tsuitate_view.HTML, "reasonLabel"
    else:
        html = (ROOT / "src/docich/webui_resources/index.html").read_text(encoding="utf-8")
        name = "GAME_REASON_JA"
    declaration = re.search(rf"const\s+{name}\s*=\s*\{{([^}}]+)\}};", html)
    assert declaration is not None
    labels = dict(re.findall(r'([a-z_]+):"([^"]+)"', declaration.group(1)))
    assert labels[reason] == expected


def test_tsuitate_readiness_requires_exact_runtime_identity(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    data = {
        "ok": True,
        "runtime_id": spec.runtime_id,
        "generation": spec.generation,
        "lease_id": spec.lease_id,
    }
    monkeypatch.setattr(tsuitate_program, "urlopen", lambda *_args, **_kwargs: _response(data))

    adapter.readiness(time.monotonic() + 5, None)

    bad = dict(data, runtime_id="g3-old")
    monkeypatch.setattr(tsuitate_program, "urlopen", lambda *_args, **_kwargs: _response(bad))
    now = time.monotonic()
    checks = iter((now, now + 5))
    monkeypatch.setattr(tsuitate_program, "monotonic", lambda: next(checks))
    with pytest.raises(ReadinessTimeoutError, match="not ready"):
        adapter.readiness(now + 5, None)


def test_tsuitate_materialize_runtime_carries_the_import_path(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(tsuitate_program, "_browser_bin", lambda: "/usr/bin/chromium")
    adapter = make_coordinator_adapter(g, spec)

    recorded: dict[str, Any] = {}

    class _RecordingTmux:
        def session_target_exists(self, _name):
            return False

        def window_target_exists(self, _target):
            return False

        def create_game_session_owned(self, session, cmd, cols, rows, ownership, env=None):
            recorded["session"] = session
            recorded["session_env"] = env

        def create_window_owned(self, name, cmd, ownership, env=None):
            recorded["window_env"] = env

    monkeypatch.setattr(adapter, "tmux", _RecordingTmux())
    adapter.materialize_runtime(time.monotonic() + 5, None)

    # The pane that imports docich must receive the import path from the
    # command itself: tmux builds a new session's environment from the server
    # and never inherits the operator's PYTHONPATH.
    assert recorded["session"] == spec.adapter_session
    assert recorded["session_env"] == {"PYTHONPATH": str(tsuitate_program.DOCICH_SRC)}
    # The viewer window is a browser, so it needs no import path.
    assert "PYTHONPATH" not in (recorded["window_env"] or {})


def test_tsuitate_session_env_is_what_makes_the_view_importable(tmp_path):
    g = _global(tmp_path)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    carried = adapter._game_session_env()
    assert carried is not None
    assert carried == {"PYTHONPATH": str(tsuitate_program.DOCICH_SRC)}
    assert (Path(carried["PYTHONPATH"]) / "docich" / "tsuitate_view.py").is_file()

    def _import(extra):
        return subprocess.run(
            [sys.executable, "-c", "import docich.tsuitate_view"],
            env={"PATH": os.environ["PATH"], **extra},
            capture_output=True,
        ).returncode

    # Without the carried path the pane dies exactly as it did in production:
    # ModuleNotFoundError, the session disappears before create_game_session_
    # owned can read its ownership back, and the whole switch rolls back with
    # "no such session: $N".
    assert _import({}) != 0
    assert _import(carried) == 0


def test_real_cli_games_keep_no_extra_session_env():
    class _Plain(cli_game.CliCoordinatorAdapter):
        def __init__(self):
            pass

    assert _Plain()._game_session_env() is None
