"""Offline lifecycle checks for the default-disabled 予想王 corner."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global
from docich.corner_adapters import CornerExecutionError, YosoCornerAdapter
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog
from docich.corner_rotation import STOP_REQUEST_DIR, CornerRotationManager
from docich.game_switch import GameSwitchCoordinator, GameSwitchStore, ReadinessTimeoutError, StepTimeouts
from docich.yoso_corner import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    YosoCornerError,
    YosoCornerManager,
)
from docich.yoso_view import VIEW_NAME


class FakeRuntimeAdapter:
    agent_enabled = False

    def __init__(self, spec, *, boundary=False):
        self.spec = spec
        self.name = "program" if spec.game == VIEW_NAME else "cli"
        self.requires_round_boundary = boundary
        self.round_boundary_timeout_s = None
        self.live = False
        self.events = []
        self.boundary_entered = threading.Event()
        self.boundary_release = threading.Event()
        if not boundary:
            self.request_round_boundary = None

    def preflight(self, deadline, cancel):
        self.events.append("preflight")

    def materialize_runtime(self, deadline, cancel):
        self.live = True
        self.events.append("materialize")

    def readiness(self, deadline, cancel):
        self.events.append("readiness")

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
    def __init__(self):
        self.adapters = {}

    def __call__(self, spec):
        if spec.runtime_id not in self.adapters:
            self.adapters[spec.runtime_id] = FakeRuntimeAdapter(spec)
        return self.adapters[spec.runtime_id]


def _setup(tmp_path, *, clock=None, enabled=True, manual_only=False, duration=1):
    state = tmp_path / "run"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{state}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        f'corners=[{{id="yosou-oh",adapter="yoso",game="yosou-view",'
        f'enabled={str(enabled).lower()},manual_only={str(manual_only).lower()},'
        f'duration_minutes={duration}}}]\n'
    )
    g = load_global(tmp_path, config)
    now = [time.time() if clock is None else clock]
    factory = FakeFactory()
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


def _corner(duration=1, **overrides):
    return Corner("yosou-oh", "yoso", VIEW_NAME, duration_minutes=duration, **overrides)


def _yoso_adapter(g, corner, switch, now, sleep):
    adapter = YosoCornerAdapter(g, corner)
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


def test_production_catalog_enables_yosou_oh_for_the_automatic_rotation():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    catalog = load_catalog(g)
    assert len(catalog) == 13
    row = next(c for c in catalog if c.id == "yosou-oh")
    assert (row.adapter, row.game) == ("yoso", VIEW_NAME)
    # 2026-10-15 オーナー判断: 手動専用を解除し、自動抽選＋手動予約の両方を使う。
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    assert row.duration_minutes is None and row.audio_enabled is True
    assert row.fetch_on_start is False and row.target_matches is None


def test_yoso_catalog_row_requires_the_reviewed_identity_and_bounds(tmp_path):
    config = tmp_path / "config.toml"

    def load(row):
        config.write_text(f"[corner_rotation]\ncorners=[{row}]\n")
        return load_catalog(load_global(tmp_path, config))

    row = load('{id="yosou-oh",adapter="yoso",game="yosou-view",enabled=false,manual_only=true}')
    assert len(row) == 1 and row[0].manual_only is True and row[0].duration_minutes is None
    assert row[0].audio_enabled is False
    assert load('{id="yosou-oh",adapter="yoso",game="yosou-view",duration_minutes=8}')[0].duration_minutes == 8
    assert load('{id="yosou-oh",adapter="yoso",game="yosou-view",audio_enabled=true}')[0].audio_enabled is True

    for bad in (
        '{id="yoso",adapter="yoso",game="yosou-view"}',
        '{id="yosou-oh",adapter="yoso",game="yoso-view"}',
    ):
        with pytest.raises(CornerCatalogError, match="yosou-oh"):
            load(bad)
    with pytest.raises(CornerCatalogError, match="fetch_on_start"):
        load('{id="yosou-oh",adapter="yoso",game="yosou-view",fetch_on_start=true}')
    with pytest.raises(CornerCatalogError, match="audio_enabled"):
        load('{id="yosou-oh",adapter="yoso",game="yosou-view",audio_enabled="yes"}')
    for duration in ("0", "15", "true", "1.5"):
        with pytest.raises(CornerCatalogError, match="1-14"):
            load(f'{{id="yosou-oh",adapter="yoso",game="yosou-view",duration_minutes={duration}}}')


def test_yoso_adapter_rejects_an_unreviewed_identity_and_uses_the_catalog_duration(tmp_path):
    g, _now, _factory, _store, switch = _setup(tmp_path, duration=5)

    adapter = YosoCornerAdapter(g, _corner(duration=5))
    assert adapter.manager.duration_minutes == 5
    assert adapter.manager.eligible() is True
    assert adapter.manager.resources_released() is True
    assert adapter.state_path.name == "yoso_corner.json"

    default = YosoCornerAdapter(g, _corner(duration=None))
    assert default.manager.duration_minutes == DEFAULT_DURATION_MINUTES

    with pytest.raises(CornerExecutionError, match="identity"):
        YosoCornerAdapter(g, Corner("yosou-oh", "yoso", "other-view"))
    with pytest.raises(CornerExecutionError, match="identity"):
        YosoCornerAdapter(g, _corner(live_eligible=True))
    with pytest.raises(YosoCornerError, match="duration"):
        YosoCornerManager(g, duration_minutes=MAX_DURATION_MINUTES + 1, coordinator=switch)


def test_yoso_manual_reservation_requires_enabled_and_stays_out_of_auto(tmp_path):
    from docich.corner_rotation import RotationError

    class StubAdapter:
        def __init__(self, _g, corner):
            self.corner = corner

        def eligible(self):
            return True

        def observations(self):
            return []

        def resources_released(self):
            return True

    def manager_for(root):
        root.mkdir(parents=True, exist_ok=True)
        g, now, *_rest = _setup(root, enabled=False, manual_only=True)
        return g, CornerRotationManager(
            g, clock=lambda: now[0], sleep=lambda _seconds: None,
            seed="yoso-manual", adapter_factory=StubAdapter,
        )

    _g, disabled = manager_for(tmp_path / "disabled")
    assert disabled._eligible(include_manual_only=True)[1] == {"yosou-oh": "disabled-or-paused"}
    with pytest.raises(RotationError, match="eligible manual corner"):
        disabled.queue_manual(VIEW_NAME)

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    config = enabled_root / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{enabled_root / "run"}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        'corners=[{id="yosou-oh",adapter="yoso",game="yosou-view",enabled=true,manual_only=true}]\n'
    )
    g = load_global(enabled_root, config)
    manager = CornerRotationManager(
        g, clock=lambda: 1000.0, sleep=lambda _seconds: None,
        seed="yoso-manual", adapter_factory=StubAdapter,
    )
    assert manager._eligible(include_manual_only=False)[1] == {"yosou-oh": "manual-only"}
    assert manager.queue_manual(VIEW_NAME)["corner"] == "yosou-oh"


def test_yoso_corner_runs_a_bounded_slot_then_restores_the_previous_game(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    manager = CornerRotationManager(
        g,
        clock=lambda: now[0],
        sleep=sleep,
        seed="yoso-test",
        adapter_factory=lambda gg, corner: _yoso_adapter(gg, corner, switch, now, sleep),
    )

    result = manager.tick()

    assert result["status"] == "ready"
    state = json.loads((Path(g.state_dir) / "yoso_corner.json").read_text())
    assert state["status"] == "completed" and state["end_reason"] == "duration"
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["yoso_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    assert state["restored_runtime_identity"]["game"] == "robots"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"
    assert canonical["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]


def test_yoso_corner_manual_stop_restores_before_the_duration_bound(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 5:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "yoso_corner.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "yoso_corner.json", "requested_at": now[0]}))

    adapter = _yoso_adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    assert state["yoso_runtime_identity"]["game"] == VIEW_NAME
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "yoso_corner.json").exists()


def test_yoso_corner_never_restores_over_an_operator_moved_runtime(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _yoso_adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)
    assert adapter.manager._dispatch_start(state) is None
    view_identity = state["yoso_runtime_identity"]

    # An operator moves the shared slot away from the view while it is active.
    moved = switch.switch("nsnake", payload={"expected_source": view_identity})
    assert moved.status == "succeeded"

    assert adapter.manager.run_rotation(request_id) == "completed"

    saved = json.loads(adapter.state_path.read_text())
    assert saved["status"] == "interrupted"
    assert saved["end_reason"] == "operator-moved-during-yosou"
    final, _ = store.canonical.load()
    assert final["active"]["game"] == "nsnake"


def test_yoso_corner_stop_before_start_needs_no_game_switch(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _yoso_adapter(g, _corner(), switch, now, sleep)
    path = Path(g.state_dir) / STOP_REQUEST_DIR / "yoso_corner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state_file": "yoso_corner.json", "requested_at": now[0]}))

    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "operator-stopped-before-start"
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"


def test_yoso_adapter_carries_the_catalog_audio_opt_in(tmp_path):
    g, _now, _factory, _store, _switch = _setup(tmp_path, duration=3)

    assert YosoCornerAdapter(g, _corner(duration=3)).manager.audio_enabled is False
    assert YosoCornerAdapter(g, _corner(duration=3, audio_enabled=True)).manager.audio_enabled is True


def test_yoso_corner_announces_once_per_request_and_records_counters(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    calls = []

    def sleep(seconds):
        now[0] += seconds

    adapter = _yoso_adapter(g, _corner(), switch, now, sleep)
    adapter.manager.announce = lambda g, *, request_id, audio_enabled: calls.append(
        (request_id, audio_enabled)
    ) or {"overlay": {"sent": 2, "failed": 0, "skipped": 0},
         "speech": {"sent": 0, "failed": 0, "skipped": 2}}
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)

    assert adapter.manager._dispatch_start(state) is None

    assert calls == [(request_id, False)]
    assert state["announce_request_id"] == request_id
    # The introduction and the ranking are two overlay events.
    assert state["overlay_announcements"] == {"sent": 2, "failed": 0, "skipped": 0}
    assert "narration" not in state  # speech stays off without the opt-in

    # A resumed owner never publishes the same ranking twice.
    adapter.manager._announce_once(state)
    assert calls == [(request_id, False)]


def test_yoso_corner_speaks_only_when_the_audio_opt_in_is_set(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    calls = []

    def sleep(seconds):
        now[0] += seconds

    adapter = _yoso_adapter(g, _corner(audio_enabled=True), switch, now, sleep)
    adapter.manager.announce = lambda g, *, request_id, audio_enabled: calls.append(
        audio_enabled
    ) or {"overlay": {"sent": 2, "failed": 0, "skipped": 0},
         "speech": {"sent": 2, "failed": 0, "skipped": 0}}
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)

    assert adapter.manager._dispatch_start(state) is None

    assert calls == [True]
    # The introduction and the ranking are two speech items.
    assert state["narration"] == {"enqueued": 2, "delivery_failed": 0, "skipped": 0}


def test_a_failing_announcer_never_fails_the_corner(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    def boom(*_args, **_kwargs):
        raise RuntimeError("SECRET-ANNOUNCER-BODY")

    manager = CornerRotationManager(
        g,
        clock=lambda: now[0],
        sleep=sleep,
        seed="yoso-announce",
        adapter_factory=lambda gg, corner: _yoso_adapter(gg, corner, switch, now, sleep),
    )
    for adapter in manager.adapters.values():
        adapter.manager.announce = boom

    assert manager.tick()["status"] == "ready"

    state = json.loads((Path(g.state_dir) / "yoso_corner.json").read_text())
    assert state["status"] == "completed"
    assert state["overlay_announcements"] == {"sent": 0, "failed": 2, "skipped": 0}
    assert "SECRET-ANNOUNCER-BODY" not in json.dumps(state)
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"


@pytest.mark.parametrize(
    "stop_requested, elapsed, end_reason",
    [(True, 1.0, "manual"), (False, 60.0, "duration"), (False, 61.0, "duration")],
)
def test_yoso_accepted_start_replay_skips_announcements_when_ending(
        stop_requested, elapsed, end_reason):
    # Replay the exact successful start receipt with only memory fakes. A stop
    # cannot cancel an accepted GameSwitch request, so its owner still restores.
    request_id = "11111111-1111-4111-8111-111111111111"
    identity = {"game": VIEW_NAME, "runtime_id": "memory-yoso",
                "generation": 2, "lease_id": request_id}
    receipt = {
        "request_id": request_id, "status": "succeeded", "target": VIEW_NAME,
        "operation": "start", "generation": 2, "updated_at": "1970-01-01T00:16:40Z",
        "result": {"request_id": request_id, "status": "succeeded",
                   "to_game": VIEW_NAME, "active_runtime": identity},
    }
    result = SimpleNamespace(status="succeeded", request_id=request_id,
                             to_game=VIEW_NAME, generation=2, receipt=receipt)
    state = {
        "rotation_request_id": request_id, "start_request_id": request_id,
        "switch_request_id": request_id, "status": "starting",
        "previous_runtime_identity": None,
    }
    announcements, restores = [], []
    manager = object.__new__(YosoCornerManager)
    manager.g = SimpleNamespace()
    manager.duration_minutes = 1
    manager.audio_enabled = True
    manager.clock = lambda: 1000.0 + elapsed
    manager.poll_s = 1.0
    manager.store = SimpleNamespace(
        receipts=SimpleNamespace(load=lambda _request_id: receipt))
    manager.coordinator = SimpleNamespace(start=lambda *_a, **_k: result)
    manager._read_state = lambda: state
    manager._save = lambda _state: None
    manager._stop_requested = lambda: stop_requested
    manager._canonical = lambda: {
        "phase": "ready", "active": identity, "candidate": None,
        "previous": None, "retiring": [],
    }
    manager.announce = lambda *_a, **_k: announcements.append(_k) or {
        "overlay": "sent", "speech": "sent"}

    def restore(_state, *, end_reason):
        restores.append(end_reason)
        _state.update(status="completed", end_reason=end_reason)
        return "completed"

    manager._restore = restore

    assert manager.run_rotation(request_id) == "completed"
    assert announcements == []
    assert restores == [end_reason]
    assert state["status"] == "completed" and state["end_reason"] == end_reason
    assert "announce_request_id" not in state


def test_yoso_timer_safe_restore_replay_restores_the_previous_game(tmp_path):
    # #2199: the common scheduler may replay a recorded, failed end switch for a
    # finished view. The replay must use the recorded request and previous game,
    # stay bounded, and never extend a boundary.
    g, now, _factory, store, switch = _setup(tmp_path)
    calls = []
    original_switch = switch.switch

    def recording_switch(target, **kwargs):
        calls.append((target, kwargs))
        return original_switch(target, **kwargs)

    switch.switch = recording_switch
    adapter = _yoso_adapter(g, _corner(), switch, now, lambda seconds: now.__setitem__(0, now[0] + seconds))
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)
    assert adapter.manager._dispatch_start(state) is None
    restore_request_id = str(uuid.uuid4())
    state.update(status="restoring", restore_request_id=restore_request_id,
                 switch_request_id=restore_request_id, end_reason="duration")
    adapter.manager._save(state)
    calls.clear()  # only the restore replay matters below

    assert adapter.manager.recover_failed_restore() == "completed"

    target, kwargs = calls[0]
    assert target == "robots"
    # The original request is terminal-failed and cannot be resumed, so the
    # first attempt records exactly one fresh bounded replay request.
    assert kwargs["request_id"] != restore_request_id
    assert kwargs["timeout_s"] == 420.0
    assert kwargs["allow_boundary_timeout_extension"] is False
    assert len(calls) == 1
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    saved = json.loads(adapter.state_path.read_text())
    assert saved["status"] == "completed" and saved["end_reason"] == "duration"
    assert saved["restore_recovery"]["request_id"] == kwargs["request_id"]
    assert saved["restore_recovery"]["original_request_id"] == restore_request_id
    # A settled replay is never replayed twice.
    assert adapter.manager.recover_failed_restore() == "noop"


def test_yoso_restore_replay_is_a_noop_without_a_recorded_restore(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    adapter = _yoso_adapter(g, _corner(), switch, now, lambda seconds: None)

    assert adapter.manager.recover_failed_restore() == "noop"

    state = adapter.manager._new_state(str(uuid.uuid4()))
    state.update(status="interrupted")
    adapter.manager._save(state)

    assert adapter.manager.recover_failed_restore() == "noop"


def test_yoso_restore_replay_waits_for_an_in_flight_switch(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)
    adapter = _yoso_adapter(g, _corner(), switch, now, lambda seconds: None)
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)
    assert adapter.manager._dispatch_start(state) is None
    restore_request_id = str(uuid.uuid4())
    state.update(status="restoring", restore_request_id=restore_request_id,
                 switch_request_id=restore_request_id)
    adapter.manager._save(state)
    canonical, _ = store.canonical.load()
    canonical.update(phase="stopping", operation="switch", request_id=str(uuid.uuid4()))
    store.canonical.save(canonical)
    dispatched = []
    adapter.manager.coordinator = SimpleNamespace(
        switch=lambda *args, **kwargs: dispatched.append(args),
        stop=lambda **kwargs: dispatched.append(kwargs))

    assert adapter.manager.recover_failed_restore() == "queued"
    assert dispatched == []


def test_view_adapters_expose_the_timer_safe_restore_replay(tmp_path):
    from docich.corner_adapters import TsuitateCornerAdapter, WeatherCornerAdapter

    g, _now, _factory, _store, _switch = _setup(tmp_path)
    yoso = YosoCornerAdapter(g, _corner())
    yoso.manager.recover_failed_restore = lambda: "sentinel"
    assert yoso.recover_failed_restore() == "sentinel"

    weather = WeatherCornerAdapter(g, Corner("weather", "weather", "weather-view",
                                            duration_minutes=1))
    weather.manager.recover_failed_restore = lambda: "sentinel"
    assert weather.recover_failed_restore() == "sentinel"

    tsuitate = TsuitateCornerAdapter(g, Corner("tsuitate", "tsuitate", "tsuitate-view"))
    tsuitate.manager.recover_failed_restore = lambda: "sentinel"
    assert tsuitate.recover_failed_restore() == "sentinel"


def test_yoso_restore_replay_reuses_one_fresh_request_when_it_fails(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    adapter = _yoso_adapter(g, _corner(), switch, now, lambda seconds: None)
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)
    assert adapter.manager._dispatch_start(state) is None
    original = str(uuid.uuid4())
    state.update(status="restoring", restore_request_id=original, switch_request_id=original)
    adapter.manager._save(state)
    calls = []
    adapter.manager.coordinator = SimpleNamespace(
        switch=lambda target, **kwargs: calls.append((target, kwargs)) or SimpleNamespace(
            status="failed", error_code="timeout"),
        stop=lambda **kwargs: calls.append(kwargs))

    with pytest.raises(YosoCornerError):
        adapter.manager.recover_failed_restore()
    first = json.loads(adapter.state_path.read_text())["restore_recovery"]["request_id"]
    assert first != original and calls[0][1]["request_id"] == first

    with pytest.raises(YosoCornerError):
        adapter.manager.recover_failed_restore()
    second = json.loads(adapter.state_path.read_text())["restore_recovery"]["request_id"]
    assert second == first
    assert [call[1]["request_id"] for call in calls if isinstance(call, tuple)] == [first, first]


def _voice_manager(monkeypatch, *, in_flight, now, sleep):
    """A manager whose audio wait is driven by a stubbed queue observation."""
    request_id = "22222222-2222-4222-8222-222222222222"
    identity = {"game": VIEW_NAME, "runtime_id": "memory-yoso",
                "generation": 3, "lease_id": request_id}
    state = {"yoso_runtime_identity": identity, "status": "active",
             "voice_pending": 2, "ends_at": now[0] + 3.0}
    restores = []
    manager = object.__new__(YosoCornerManager)
    manager.g = SimpleNamespace()
    manager.clock = lambda: now[0]
    manager.sleep = sleep
    manager.poll_s = 1.0
    manager._stop_requested = lambda: False
    manager._canonical = lambda: {"phase": "ready", "active": identity, "candidate": None,
                                  "previous": None, "retiring": []}

    def restore(_state, *, end_reason):
        restores.append(end_reason)
        _state.update(status="completed", end_reason=end_reason)
        return "completed"

    manager._restore = restore
    monkeypatch.setattr("docich.yoso_narration.voice_items_in_flight",
                        lambda _g: in_flight)
    return manager, state, restores


@pytest.mark.parametrize("in_flight, expected", [(0, "audio-complete"), (1, "duration")])
def test_yoso_ends_with_the_read_aloud_not_with_the_duration(
        monkeypatch, in_flight, expected):
    """A finished read-aloud ends the corner; a queued item falls back to the cap."""
    now = [1000.0]

    def sleep(seconds):
        now[0] += seconds

    manager, state, restores = _voice_manager(
        monkeypatch, in_flight=in_flight, now=now, sleep=sleep)

    assert manager._wait_and_restore(state) == "completed"
    assert restores == [expected]
    assert state["end_reason"] == expected


def test_yoso_never_ends_early_without_queued_speech(monkeypatch):
    """With no speech item enqueued there is nothing to wait for or to shorten."""
    now = [1000.0]

    def sleep(seconds):
        now[0] += seconds

    manager, state, restores = _voice_manager(
        monkeypatch, in_flight=0, now=now, sleep=sleep)
    state.pop("voice_pending")

    assert manager._wait_and_restore(state) == "completed"
    assert restores == ["duration"]
