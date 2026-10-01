"""Offline lifecycle checks for the opt-in weather corner."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global
from docich.game_switch import GameSwitchCoordinator, GameSwitchStore, ReadinessTimeoutError, StepTimeouts
from docich.corner_adapters import WeatherCornerAdapter
from docich.corner_catalog import CornerCatalogError, load_catalog
from docich.corner_rotation import CornerRotationManager
from docich.weather_corner import WeatherCornerError, WeatherCornerManager


class FakeRuntimeAdapter:
    agent_enabled = False

    def __init__(self, spec, *, boundary=False, fail_readiness=False):
        self.spec = spec
        self.name = "program" if spec.game == "weather-view" else "cli"
        self.requires_round_boundary = boundary
        self.round_boundary_timeout_s = None
        self.fail_readiness = fail_readiness
        self.live = False
        self.events = []
        self.boundary_entered = threading.Event()
        self.boundary_release = threading.Event()
        if not boundary:
            # GameSwitch's optional boundary contract uses method absence to
            # mean no drain request is needed for this runtime.
            self.request_round_boundary = None

    def preflight(self, deadline, cancel):
        self.events.append("preflight")

    def materialize_runtime(self, deadline, cancel):
        self.live = True
        self.events.append("materialize")

    def readiness(self, deadline, cancel):
        self.events.append("readiness")
        if self.fail_readiness:
            self.fail_readiness = False
            raise ReadinessTimeoutError("synthetic readiness failure")

    def alive(self, deadline, cancel):
        return self.live

    def cleanup_runtime(self, deadline, cancel):
        self.events.append("cleanup")
        self.live = False

    def start_agent(self, deadline, cancel):
        return None

    def stop_agent(self, deadline, cancel):
        self.events.append("stop_agent")

    def request_round_boundary(self, request_id, deadline, cancel):
        self.events.append("boundary_wait")
        self.boundary_entered.set()
        while not self.boundary_release.wait(0.01):
            if cancel.is_set() or time.monotonic() >= deadline:
                raise ReadinessTimeoutError("synthetic boundary timeout")
        self.events.append("boundary_ack")

    def cancel_round_boundary(self, request_id, deadline, cancel):
        self.boundary_release.set()
        return True


class FakeFactory:
    def __init__(self, *, boundary_generation=None, fail_weather=False):
        self.boundary_generation = boundary_generation
        self.fail_weather = fail_weather
        self.adapters = {}

    def __call__(self, spec):
        key = spec.runtime_id
        if key not in self.adapters:
            adapter = FakeRuntimeAdapter(
                spec,
                boundary=(spec.game == "robots" and spec.generation == self.boundary_generation),
                fail_readiness=(self.fail_weather and spec.game == "weather-view"),
            )
            self.adapters[key] = adapter
        return self.adapters[key]


def _setup(tmp_path, *, fail_weather=False, duration=1, clock=None, boundary_generation=1):
    state = tmp_path / "run"
    soren = tmp_path / "soren"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{state}"\n'
        f'[webui]\nsoren_root="{soren}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        f'corners=[{{id="weather",adapter="weather",game="weather-view",enabled=true,duration_minutes={duration}}}]\n'
    )
    g = load_global(tmp_path, config)
    now = [time.time() if clock is None else clock]
    factory = FakeFactory(boundary_generation=boundary_generation, fail_weather=fail_weather)
    store = GameSwitchStore(g.state_dir)
    switch = GameSwitchCoordinator(
        store,
        factory,
        default_timeout_s=4.0,
        quiesce_verify_timeout_s=0.3,
        poll_interval_s=0.01,
        round_reacquire_timeout_s=0.3,
        step_timeouts=StepTimeouts(
            preflight_s=1.0,
            round_boundary_s=3.0,
            stop_agent_s=1.0,
            start_s=1.0,
            agent_start_s=1.0,
            cleanup_s=1.0,
            probe_s=0.3,
        ),
    )
    assert switch.start("robots").status == "succeeded"
    return g, now, factory, store, switch


def _weather_adapter(g, corner, switch, now, sleep):
    adapter = WeatherCornerAdapter(g, corner)
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


def test_weather_catalog_is_disabled_by_default_and_requires_bounded_duration(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[corner_rotation]\ncorners=[{id="weather",adapter="weather",game="weather-view"}]\n')
    catalog = load_catalog(load_global(tmp_path, config))
    assert len(catalog) == 1
    assert catalog[0].enabled is False
    assert catalog[0].duration_minutes is None

    config.write_text('[corner_rotation]\ncorners=[{id="weather",adapter="weather",game="weather-view",enabled=true}]\n')
    with pytest.raises(CornerCatalogError, match="duration_minutes"):
        load_catalog(load_global(tmp_path, config))

    for duration in (0, 15, True, 1.5):
        literal = str(duration).lower() if isinstance(duration, bool) else str(duration)
        config.write_text(
            '[corner_rotation]\ncorners=[{id="weather",adapter="weather",game="weather-view",'
            f'enabled=true,duration_minutes={literal}}}]\n'
        )
        with pytest.raises(CornerCatalogError, match="weather"):
            load_catalog(load_global(tmp_path, config))


def test_weather_runtime_does_not_publish_an_unreviewed_stream_title(tmp_path, monkeypatch):
    import docich.stream_category as stream_category

    calls = []
    monkeypatch.setattr(stream_category, "commit_hook", lambda _g: calls.append)
    manager = WeatherCornerManager(SimpleNamespace(state_dir=tmp_path), duration_minutes=1)
    manager.coordinator.post_commit("weather-view")
    manager.coordinator.post_commit("robots")
    assert calls == ["robots"]


def test_weather_corner_waits_for_game_boundary_then_restores_under_program_slot(tmp_path, monkeypatch):
    from docich import hanjuku_predictions
    import docich.weather_corner as weather_corner

    g, now, factory, store, switch = _setup(tmp_path)
    monkeypatch.setattr(hanjuku_predictions, "tick", lambda _g: None)
    monkeypatch.setattr(
        weather_corner,
        "read_view",
        lambda _path, clock=None: {"expires_at": now[0] + 900},
    )
    def sleep(seconds):
        now[0] += seconds

    manager = CornerRotationManager(
        g,
        clock=lambda: now[0],
        sleep=sleep,
        seed="weather-test",
        adapter_factory=lambda _g, corner: _weather_adapter(g, corner, switch, now, sleep),
    )
    result_box = []
    errors = []

    def run():
        try:
            result_box.append(manager.tick())
        except Exception as exc:  # surfaced in the main test thread
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    old = factory.adapters[next(key for key in factory.adapters if "g1-" in key)]
    assert old.boundary_entered.wait(2.0)
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "draining"
    assert old.live is True
    assert "cleanup" not in old.events
    assert not any(a.spec.game == "weather-view" and a.live for a in factory.adapters.values())

    registry = Path(g.webui.soren_root) / "tmp/state/docich_program_active.json"
    assert json.loads(registry.read_text())["owner_state"].endswith("weather_corner.json")
    old.boundary_release.set()
    worker.join(5.0)
    assert not worker.is_alive()
    assert errors == [], (
        errors,
        json.loads((Path(g.state_dir) / "weather_corner.json").read_text()),
        store.canonical.load()[0],
    )
    assert result_box[0]["status"] == "ready"
    state = json.loads((Path(g.state_dir) / "weather_corner.json").read_text())
    assert state["status"] == "completed"
    assert state["end_reason"] == "duration"
    assert state["previous_runtime_identity"]["lease_id"] is not None
    assert state["weather_runtime_identity"]["runtime_id"] != state["previous_runtime_identity"]["runtime_id"]
    assert state["restored_runtime_identity"]["game"] == "robots"
    assert state["restored_runtime_identity"]["generation"] > state["previous_runtime_identity"]["generation"]
    assert state["restored_runtime_identity"]["lease_id"]
    final, _ = store.canonical.load()
    assert final["phase"] == "ready"
    assert final["active"]["game"] == "robots"
    assert final["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]
    assert final["active"]["lease_id"] == state["restored_runtime_identity"]["lease_id"]
    assert old.events.index("boundary_ack") < old.events.index("stop_agent")
    assert old.events.index("stop_agent") < old.events.index("cleanup")


def test_failed_weather_readiness_reconciles_only_the_exact_game_switch_rollback(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, factory, store, switch = _setup(tmp_path, fail_weather=True, boundary_generation=None)
    monkeypatch.setattr(weather_corner, "read_view", lambda _path, clock=None: {"expires_at": now[0] + 900})
    adapter = WeatherCornerAdapter(g, SimpleNamespace(duration_minutes=1))
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    request_id = str(uuid.uuid4())
    with pytest.raises(WeatherCornerError, match="did not succeed"):
        adapter.run({"request_id": request_id})
    assert adapter.reconcile_failed_start(request_id) is True
    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "switch-terminal-before-corner-active"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready"
    assert canonical["active"]["game"] == "robots"
    assert canonical["active"]["runtime_id"] != state["previous_runtime_identity"]["runtime_id"]
    assert canonical["active"]["generation"] > state["previous_runtime_identity"]["generation"]
    assert not any(a.spec.game == "weather-view" and a.live for a in factory.adapters.values())


def test_weather_never_restores_over_an_operator_moved_runtime(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, factory, store, switch = _setup(tmp_path, boundary_generation=None)
    expired = [False]
    def read_view(_path, clock=None):
        if expired[0]:
            raise weather_corner.WeatherError("synthetic expiry")
        return {"expires_at": now[0] + 900}
    monkeypatch.setattr(weather_corner, "read_view", read_view)
    manager = WeatherCornerManager(g, duration_minutes=1, coordinator=switch, clock=lambda: now[0])
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    assert manager._dispatch_start(state) is None
    weather_identity = state["weather_runtime_identity"]
    assert weather_identity["lease_id"]

    moved = switch.switch("nethack").status
    assert moved == "succeeded"
    operator_owner, _ = store.canonical.load()
    assert operator_owner["active"]["game"] == "nethack"
    operator_identity = dict(operator_owner["active"])
    expired[0] = True
    now[0] += 5
    assert manager.run_rotation(request_id) == "completed"
    final, _ = store.canonical.load()
    assert final["active"] == operator_identity
    state = json.loads(manager.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "operator-moved-before-restore"


def test_forecast_expiry_restores_only_while_weather_identity_is_still_active(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, _factory, store, switch = _setup(tmp_path, boundary_generation=None)
    expired = [False]
    def read_view(_path, clock=None):
        if expired[0]:
            raise weather_corner.WeatherError("synthetic expiry")
        return {"expires_at": now[0] + 900}
    monkeypatch.setattr(weather_corner, "read_view", read_view)
    manager = WeatherCornerManager(g, duration_minutes=1, coordinator=switch, clock=lambda: now[0])
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    assert manager._dispatch_start(state) is None
    expired[0] = True

    assert manager.run_rotation(request_id) == "completed"
    final, _ = store.canonical.load()
    state = json.loads(manager.state_path.read_text())
    assert final["phase"] == "ready"
    assert final["active"]["game"] == "robots"
    assert state["status"] == "completed"
    assert state["end_reason"] == "forecast-expired"


def test_same_weather_game_with_new_generation_and_lease_is_not_this_owner(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, _factory, store, switch = _setup(tmp_path, boundary_generation=None)
    monkeypatch.setattr(weather_corner, "read_view", lambda _path, clock=None: {"expires_at": now[0] + 900})
    manager = WeatherCornerManager(g, duration_minutes=1, coordinator=switch, clock=lambda: now[0])
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    assert manager._dispatch_start(state) is None
    owner_identity = dict(state["weather_runtime_identity"])

    assert switch.switch("nethack").status == "succeeded"
    assert switch.switch("weather-view").status == "succeeded"
    replacement, _ = store.canonical.load()
    replacement_identity = {key: replacement["active"][key] for key in owner_identity}
    assert replacement_identity["game"] == owner_identity["game"]
    assert replacement_identity["generation"] != owner_identity["generation"]
    assert replacement_identity["runtime_id"] != owner_identity["runtime_id"]
    assert replacement_identity["lease_id"] != owner_identity["lease_id"]

    assert manager.run_rotation(request_id) == "completed"
    final, _ = store.canonical.load()
    state = json.loads(manager.state_path.read_text())
    assert final["active"] == replacement["active"]
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "operator-moved-during-weather"


def test_start_source_fence_leaves_an_operator_replacement_untouched(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, _factory, store, switch = _setup(tmp_path, boundary_generation=None)
    monkeypatch.setattr(weather_corner, "read_view", lambda _path, clock=None: {"expires_at": now[0] + 900})
    manager = WeatherCornerManager(g, duration_minutes=1, coordinator=switch, clock=lambda: now[0])
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    expected_source = dict(state["previous_runtime_identity"])
    assert switch.switch("nethack").status == "succeeded"
    replacement, _ = store.canonical.load()

    with pytest.raises(WeatherCornerError, match="did not succeed"):
        manager._dispatch_start(state)
    assert manager.reconcile_failed_start(request_id) is True
    final, _ = store.canonical.load()
    state = json.loads(manager.state_path.read_text())
    assert final["active"] == replacement["active"]
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "switch-terminal-before-corner-active"
    assert expected_source["lease_id"] != replacement["active"]["lease_id"]
