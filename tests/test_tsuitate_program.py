import json
from pathlib import Path
import sys
import time
import uuid
from types import SimpleNamespace

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


def test_broadcast_projection_drops_unreviewed_remote_fields(monkeypatch):
    monkeypatch.setattr(
        tsuitate_view,
        "call_beta_control",
        lambda *_: {
            **_status(state="playing", runId="fixture-run", reservedGames=1),
            "token": "do-not-leak",
            "opponentPieces": [{"square": "5e"}],
        },
    )
    data = tsuitate_view.status_projection("g4-a1b2c3d4", 4, "lease-fixture")
    assert data["ok"] is True
    assert data["gameActive"] is True
    assert data["brainVersion"] == "tsuitate-brain-v2"
    assert "token" not in data
    assert "opponentPieces" not in data
    assert "runId" not in data


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
