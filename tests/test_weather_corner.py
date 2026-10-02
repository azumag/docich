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


def _weather_audio_view(now):
    from datetime import datetime
    from docich import weather

    target = datetime.fromtimestamp(now, weather.JST).date().isoformat()
    issued = datetime.fromtimestamp(now - 60, weather.JST).isoformat()
    return {
        "schema_version": 1,
        "date": target,
        "generated_at": now - 1,
        "expires_at": now + 300,
        "server_now": now - 1,
        "attribution": weather.ATTRIBUTION,
        "source_url": weather.SOURCE,
        "terms_url": weather.TERMS,
        "cities": [
            {
                "city": city.name,
                "area_code": city.area,
                "station": city.station,
                "office": city.office,
                "date": target,
                "issued_at": issued,
                "weather": "晴れ",
                "high_c": 22,
                "low_c": 15,
                "pops": [
                    {"start": start, "end": start + 6, "percent": None}
                    for start in (0, 6, 12, 18)
                ],
                "source_url": f"{weather.SOURCE}#area_type=offices&area_code={city.office}",
            }
            for city in weather.CITIES
        ],
    }


class FakeWeatherAudioPort:
    def __init__(self, clock, *, auto_play=False):
        from docich.weather_audio import build_weather_audio_receipt

        self.clock = clock
        self.auto_play = auto_play
        self.receipts = {}
        self.enqueued = []
        self.interrupted = []
        self._build_receipt = build_weather_audio_receipt

    def _receipt(self, request, status, reason=None):
        return self._build_receipt(
            request, status=status, recorded_at=self.clock(), reason=reason,
        )

    def enqueue_weather_audio(self, request):
        self.enqueued.append(dict(request))
        status = "played" if self.auto_play else "queued"
        receipt = self._receipt(request, status)
        self.receipts[request["item_key"]] = receipt
        return receipt

    def get_weather_audio_receipt(self, item_key):
        return self.receipts.get(item_key)

    def interrupt_weather_audio(self, item_key):
        self.interrupted.append(item_key)
        request = next(item for item in self.enqueued if item["item_key"] == item_key)
        receipt = self._receipt(request, "rejected", "player_rejected")
        self.receipts[item_key] = receipt
        return receipt

    def finish(self, item_key, status="played"):
        request = next(item for item in self.enqueued if item["item_key"] == item_key)
        reason = None
        if status == "rejected":
            reason = "player_rejected"
        elif status == "interrupted":
            reason = "playback_interrupted"
        receipt = self._receipt(request, status, reason)
        self.receipts[item_key] = receipt


def test_weather_catalog_is_disabled_by_default_and_requires_bounded_duration(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[corner_rotation]\ncorners=[{id="weather",adapter="weather",game="weather-view"}]\n')
    catalog = load_catalog(load_global(tmp_path, config))
    assert len(catalog) == 1
    assert catalog[0].enabled is False
    assert catalog[0].duration_minutes is None
    assert catalog[0].audio_enabled is False

    config.write_text(
        '[corner_rotation]\ncorners=[{id="weather",adapter="weather",game="weather-view",'
        'audio_enabled=true}]\n'
    )
    assert load_catalog(load_global(tmp_path, config))[0].audio_enabled is True

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

    config.write_text(
        '[corner_rotation]\ncorners=[{id="weather",adapter="weather",game="weather-view",'
        'audio_enabled="yes"}]\n'
    )
    with pytest.raises(CornerCatalogError):
        load_catalog(load_global(tmp_path, config))

    config.write_text(
        '[corner_rotation]\ncorners=[{id="normal",adapter="game",game="robots",'
        'audio_enabled=true}]\n'
    )
    with pytest.raises(CornerCatalogError, match="weather adapter"):
        load_catalog(load_global(tmp_path, config))


def test_weather_audio_sends_one_item_at_a_time_and_reuses_receipt_after_restart(tmp_path, monkeypatch):
    import datetime as dt
    import docich.weather_corner as weather_corner

    fixed = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc).timestamp()
    g, now, _factory, store, switch = _setup(tmp_path, clock=fixed, boundary_generation=None)
    forecast = _weather_audio_view(now[0])
    monkeypatch.setattr(weather_corner, "read_view", lambda _path, clock=None: forecast)
    port = FakeWeatherAudioPort(lambda: now[0])
    manager = WeatherCornerManager(
        g, duration_minutes=1, coordinator=switch, audio_enabled=True,
        audio_port=port, clock=lambda: now[0],
    )
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    assert manager._dispatch_start(state) is None

    manager._advance_audio_delivery(state)
    first = port.enqueued[0]
    assert first["item_index"] == 0
    assert len(port.enqueued) == 1

    # A restarted owner first reads the durable receipt and does not enqueue
    # the same item a second time.
    resumed = WeatherCornerManager(
        g, duration_minutes=1, coordinator=switch, audio_enabled=True,
        audio_port=port, clock=lambda: now[0],
    )
    resumed_state = resumed._read_state()
    resumed._advance_audio_delivery(resumed_state)
    assert len(port.enqueued) == 1

    port.finish(first["item_key"])
    resumed._advance_audio_delivery(resumed_state)
    assert resumed_state["audio_delivery"]["next_index"] == 1
    resumed._advance_audio_delivery(resumed_state)
    assert [item["item_index"] for item in port.enqueued] == [0, 1]

    # Manual restore interrupts just the current weather item before asking
    # GameSwitch to restore the previous runtime.
    assert resumed._restore(resumed_state, end_reason="manual") == "completed"
    final_state = resumed._read_state()
    assert final_state["audio_delivery"]["status"] == "stopped"
    assert port.interrupted == [port.enqueued[1]["item_key"]]
    final, _ = store.canonical.load()
    assert final["phase"] == "ready" and final["active"]["game"] == "robots"


def test_weather_audio_marks_complete_only_after_all_thirteen_played_receipts(tmp_path, monkeypatch):
    import datetime as dt
    import docich.weather_corner as weather_corner

    fixed = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc).timestamp()
    g, now, _factory, _store, _switch = _setup(tmp_path, clock=fixed, boundary_generation=None)
    forecast = _weather_audio_view(now[0])
    monkeypatch.setattr(weather_corner, "read_view", lambda _path, clock=None: forecast)
    port = FakeWeatherAudioPort(lambda: now[0], auto_play=True)
    manager = WeatherCornerManager(
        g, duration_minutes=1, audio_enabled=True, audio_port=port,
        clock=lambda: now[0],
    )
    state = {
        "rotation_request_id": str(uuid.uuid4()),
        "weather_runtime_identity": {
            "game": "weather-view", "runtime_id": "g2-a1b2c3d4",
            "generation": 2, "lease_id": str(uuid.uuid4()),
        },
    }
    for _ in range(13):
        manager._advance_audio_delivery(state)
    assert [item["item_index"] for item in port.enqueued] == list(range(13))
    assert state["audio_delivery"]["status"] == "completed"
    assert state["audio_delivery"]["next_index"] == 13


@pytest.mark.parametrize(
    ("status", "reason"),
    [("rejected", "player_rejected"), ("interrupted", "playback_interrupted")],
)
def test_weather_audio_stops_after_any_non_played_terminal_receipt(
    tmp_path, monkeypatch, status, reason,
):
    import datetime as dt
    import docich.weather_corner as weather_corner

    fixed = dt.datetime(2026, 10, 2, 12, tzinfo=dt.timezone.utc).timestamp()
    g, now, _factory, _store, _switch = _setup(tmp_path, clock=fixed, boundary_generation=None)
    forecast = _weather_audio_view(now[0])
    monkeypatch.setattr(weather_corner, "read_view", lambda _path, clock=None: forecast)
    port = FakeWeatherAudioPort(lambda: now[0])
    manager = WeatherCornerManager(
        g, duration_minutes=1, audio_enabled=True, audio_port=port,
        clock=lambda: now[0],
    )
    state = {
        "rotation_request_id": str(uuid.uuid4()),
        "weather_runtime_identity": {
            "game": "weather-view", "runtime_id": "g2-a1b2c3d4",
            "generation": 2, "lease_id": str(uuid.uuid4()),
        },
    }
    manager._advance_audio_delivery(state)
    port.finish(port.enqueued[0]["item_key"], status)
    manager._advance_audio_delivery(state)
    assert state["audio_delivery"]["status"] == "stopped"
    assert state["audio_delivery"]["reason"] == f"{status}:{reason}"
    assert len(port.enqueued) == 1


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


def test_stop_recovers_a_weather_start_committed_before_corner_owner_write(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, _factory, store, switch = _setup(tmp_path, boundary_generation=None)
    monkeypatch.setattr(weather_corner, "read_view",
                        lambda _path, clock=None: {"expires_at": now[0] + 900})
    manager = WeatherCornerManager(g, duration_minutes=1, coordinator=switch,
                                   clock=lambda: now[0])
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    expected_source = dict(state["previous_runtime_identity"])

    # Simulate a crash after GameSwitch durably commits but before the local
    # corner owner can record weather_runtime_identity.
    started = switch.switch(
        "weather-view", request_id=request_id,
        payload={"expected_source": expected_source},
    )
    assert started.status == "succeeded"
    receipt = store.receipts.load(request_id)
    committed_identity = receipt["result"]["active_runtime"]
    assert json.loads(manager.state_path.read_text())["status"] == "starting"

    assert manager.stop() == "queued"
    assert manager.run_rotation(request_id) == "completed"

    final, _ = store.canonical.load()
    state = json.loads(manager.state_path.read_text())
    assert state["status"] == "completed"
    assert state["end_reason"] == "manual"
    assert state["weather_runtime_identity"] == committed_identity
    assert final["phase"] == "ready"
    assert final["active"]["game"] == "robots"
    assert final["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]
    assert state["restored_runtime_identity"]["runtime_id"] != committed_identity["runtime_id"]


def test_stop_keeps_queued_start_owner_until_game_switch_terminally_fences_it(tmp_path, monkeypatch):
    import docich.weather_corner as weather_corner

    g, now, factory, store, switch = _setup(tmp_path, boundary_generation=1)
    monkeypatch.setattr(weather_corner, "read_view",
                        lambda _path, clock=None: {"expires_at": now[0] + 900})
    operator_results = []
    operator_errors = []

    def move_operator_runtime():
        try:
            operator_results.append(switch.switch("nethack"))
        except Exception as exc:
            operator_errors.append(exc)

    operator = threading.Thread(target=move_operator_runtime)
    operator.start()
    old = factory.adapters[next(key for key in factory.adapters if "g1-" in key)]
    assert old.boundary_entered.wait(2.0)

    manager = WeatherCornerManager(g, duration_minutes=1, coordinator=switch,
                                   clock=lambda: now[0])
    request_id = str(uuid.uuid4())
    state = manager._new_state({"request_id": request_id, "selected_at": now[0]})
    manager._save(state)
    assert manager._dispatch_start(state) == "queued"
    assert store.receipts.load(request_id)["status"] == "queued"

    assert manager.stop() == "queued"
    stop_path = Path(g.state_dir) / "corner-stop-requests" / manager.path.name
    assert stop_path.exists()
    assert manager.run_rotation(request_id) == "queued"
    state = json.loads(manager.state_path.read_text())
    assert state["status"] == "starting"
    assert state["switch_request_id"] == request_id
    assert store.receipts.load(request_id)["status"] == "queued"
    assert stop_path.exists()

    old.boundary_release.set()
    operator.join(5.0)
    assert not operator.is_alive()
    assert operator_errors == []
    assert operator_results[0].status == "succeeded"
    expected_source = state["previous_runtime_identity"]
    fenced = switch.switch(
        "weather-view", request_id=request_id,
        payload={"expected_source": expected_source},
    )
    assert fenced.status == "failed"
    assert manager.reconcile_failed_start(request_id) is True

    final, _ = store.canonical.load()
    state = json.loads(manager.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "switch-terminal-before-corner-active"
    assert final["phase"] == "ready"
    assert final["active"]["game"] == "nethack"
    assert not stop_path.exists()
