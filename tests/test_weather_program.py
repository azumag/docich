from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import make_coordinator_adapter  # noqa: E402
from docich.adapters import cli_game  # noqa: E402
from docich.adapters import weather_program  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.weather import WeatherError, write_json  # noqa: E402
from docich.weather_view import handler_for  # noqa: E402
from test_weather import NOW, bundle  # noqa: E402


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
        game="weather-view", adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


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


def test_weather_view_factory_is_generation_owned_and_uses_existing_viewport(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(weather_program, "_browser_bin", lambda: "/usr/bin/chromium")

    adapter = make_coordinator_adapter(g, spec)

    assert isinstance(adapter, weather_program.WeatherProgramViewAdapter)
    assert adapter.requires_round_boundary is False
    assert adapter._game_command() == [
        str(Path(weather_program.__file__).resolve().parents[3] / "bin" / "docich-weather"),
        "--state-dir", str(g.state_dir / "weather"), "serve",
        "--port", "8803", "--runtime-id", spec.runtime_id,
        "--generation", str(spec.generation), "--lease-id", spec.lease_id,
    ]
    viewer = adapter._viewer_command()
    assert viewer[0] == sys.executable
    assert "--width" in viewer and viewer[viewer.index("--width") + 1] == "960"
    assert "--height" in viewer and viewer[viewer.index("--height") + 1] == "540"
    assert "--app=http://127.0.0.1:8803/broadcast" in viewer
    assert "--window-size=960,540" in viewer
    assert spec.runtime_id in " ".join(viewer)
    assert "serve" not in viewer and "enqueue_audio_text" not in " ".join(viewer)


def test_weather_view_preflight_requires_an_existing_validated_snapshot(tmp_path, monkeypatch):
    g = _global(tmp_path)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    monkeypatch.setattr(weather_program.ProgramViewAdapter, "preflight", lambda *_: None)

    with pytest.raises(AdapterError, match="snapshot is missing"):
        adapter.preflight(time.monotonic() + 10, None)

    assert not adapter.weather_state_dir.exists()


def test_weather_view_preflight_only_reads_the_snapshot(tmp_path, monkeypatch):
    g = _global(tmp_path)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    adapter.snapshot_path.parent.mkdir(parents=True)
    write_json(adapter.snapshot_path, bundle())
    seen = []
    monkeypatch.setattr(
        weather_program, "read_view",
        lambda path: seen.append(path) or {"ok": True},
    )
    monkeypatch.setattr(weather_program.ProgramViewAdapter, "preflight", lambda *_: None)

    adapter.preflight(time.monotonic() + 10, None)

    assert seen == [adapter.snapshot_path]


@pytest.mark.parametrize("identity", ["runtime_id", "generation", "lease_id"])
def test_weather_view_readiness_rejects_another_runtime(identity, tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "cities": [{} for _ in range(11)],
    }
    data[identity] = {
        "runtime_id": "g3-old",
        "generation": spec.generation - 1,
        "lease_id": str(uuid.uuid4()),
    }[identity]
    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(weather_program, "urlopen", lambda *_args, **_kwargs: _response(data))
    now = time.monotonic()
    checks = iter((now, now + 5))
    monkeypatch.setattr(weather_program, "monotonic", lambda: next(checks))

    with pytest.raises(ReadinessTimeoutError, match="not fresh and ready"):
        adapter.readiness(now + 5, None)


def test_weather_view_readiness_accepts_only_exact_runtime_response(tmp_path, monkeypatch):
    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = make_coordinator_adapter(g, spec)
    data = {
        "ok": True, "runtime_id": spec.runtime_id,
        "generation": spec.generation, "lease_id": spec.lease_id,
        "cities": [{} for _ in range(11)],
    }
    requests = []

    def open_local(url, *, timeout):
        requests.append((url, timeout))
        return _response(data)

    monkeypatch.setattr(cli_game.CliCoordinatorAdapter, "readiness", lambda *_: None)
    monkeypatch.setattr(weather_program, "urlopen", open_local)
    adapter.readiness(time.monotonic() + 5, None)

    assert requests == [("http://127.0.0.1:8803/api/weather", 2.0)]


@pytest.mark.parametrize("generation", [True, 0, -1, "4"])
def test_weather_handler_rejects_invalid_generation(tmp_path, generation):
    with pytest.raises(WeatherError, match="invalid-generation"):
        handler_for(tmp_path / "snapshot.json", "g4-a1b2c3d4", generation=generation)
