"""Offline lifecycle checks for the default-disabled 同志カード大賞 corner."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global
from docich.corner_adapters import CornerExecutionError, DoushiCornerAdapter
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog
from docich.corner_rotation import STOP_REQUEST_DIR, CornerRotationManager
from docich.game_switch import GameSwitchCoordinator, GameSwitchStore, ReadinessTimeoutError, StepTimeouts
from docich.doushi_corner import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    DoushiCornerError,
    DoushiCornerManager,
)
from docich.doushi_view import VIEW_NAME


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
        f'corners=[{{id="doushi-card",adapter="doushi",game="doushi-view",'
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
    return Corner("doushi-card", "doushi", VIEW_NAME, duration_minutes=duration, **overrides)


def _doushi_adapter(g, corner, switch, now, sleep):
    adapter = DoushiCornerAdapter(g, corner)
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


def test_production_catalog_enables_doushi_card_in_auto_rotation():
    # 2026-10-15 オーナー承認: 既定無効・manual_only をやめ、24時間ローテーションの
    # 抽選対象へ入れる（manual_only なし = 自動抽選に参加する）。
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    catalog = load_catalog(g)
    assert len(catalog) == 13
    row = next(c for c in catalog if c.id == "doushi-card")
    assert (row.adapter, row.game) == ("doushi", VIEW_NAME)
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    # duration_minutes 未指定 = doushi_corner.DEFAULT_DURATION_MINUTES(2分) を使う。
    assert row.duration_minutes is None and row.audio_enabled is False
    assert row.fetch_on_start is False and row.target_matches is None


def test_doushi_catalog_row_requires_the_reviewed_identity_and_bounds(tmp_path):
    config = tmp_path / "config.toml"

    def load(row):
        config.write_text(f"[corner_rotation]\ncorners=[{row}]\n")
        return load_catalog(load_global(tmp_path, config))

    row = load('{id="doushi-card",adapter="doushi",game="doushi-view",enabled=false,manual_only=true}')
    assert len(row) == 1 and row[0].manual_only is True and row[0].duration_minutes is None
    assert row[0].audio_enabled is False
    assert load('{id="doushi-card",adapter="doushi",game="doushi-view",duration_minutes=8}')[0].duration_minutes == 8
    assert load('{id="doushi-card",adapter="doushi",game="doushi-view",audio_enabled=true}')[0].audio_enabled is True

    for bad in (
        '{id="doushi",adapter="doushi",game="doushi-view"}',
        '{id="doushi-card",adapter="doushi",game="doushi-card"}',
    ):
        with pytest.raises(CornerCatalogError, match="doushi-card"):
            load(bad)
    with pytest.raises(CornerCatalogError, match="fetch_on_start"):
        load('{id="doushi-card",adapter="doushi",game="doushi-view",fetch_on_start=true}')
    with pytest.raises(CornerCatalogError, match="audio_enabled"):
        load('{id="doushi-card",adapter="doushi",game="doushi-view",audio_enabled="yes"}')
    for duration in ("0", "15", "true", "1.5"):
        with pytest.raises(CornerCatalogError, match="1-14"):
            load(f'{{id="doushi-card",adapter="doushi",game="doushi-view",duration_minutes={duration}}}')


def test_doushi_adapter_rejects_an_unreviewed_identity_and_uses_the_catalog_duration(tmp_path):
    g, _now, _factory, _store, switch = _setup(tmp_path, duration=5)

    adapter = DoushiCornerAdapter(g, _corner(duration=5))
    assert adapter.manager.duration_minutes == 5
    assert adapter.manager.eligible() is True
    assert adapter.manager.resources_released() is True
    assert adapter.state_path.name == "doushi_corner.json"

    default = DoushiCornerAdapter(g, _corner(duration=None))
    assert default.manager.duration_minutes == DEFAULT_DURATION_MINUTES

    with pytest.raises(CornerExecutionError, match="identity"):
        DoushiCornerAdapter(g, Corner("doushi-card", "doushi", "other-view"))
    with pytest.raises(CornerExecutionError, match="identity"):
        DoushiCornerAdapter(g, _corner(live_eligible=True))
    with pytest.raises(DoushiCornerError, match="duration"):
        DoushiCornerManager(g, duration_minutes=MAX_DURATION_MINUTES + 1, coordinator=switch)


def test_doushi_manual_reservation_requires_enabled_and_stays_out_of_auto(tmp_path):
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
            seed="doushi-manual", adapter_factory=StubAdapter,
        )

    _g, disabled = manager_for(tmp_path / "disabled")
    assert disabled._eligible(include_manual_only=True)[1] == {"doushi-card": "disabled-or-paused"}
    with pytest.raises(RotationError, match="eligible manual corner"):
        disabled.queue_manual(VIEW_NAME)

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    config = enabled_root / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{enabled_root / "run"}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        'corners=[{id="doushi-card",adapter="doushi",game="doushi-view",enabled=true,manual_only=true}]\n'
    )
    g = load_global(enabled_root, config)
    manager = CornerRotationManager(
        g, clock=lambda: 1000.0, sleep=lambda _seconds: None,
        seed="doushi-manual", adapter_factory=StubAdapter,
    )
    assert manager._eligible(include_manual_only=False)[1] == {"doushi-card": "manual-only"}
    assert manager.queue_manual(VIEW_NAME)["corner"] == "doushi-card"


def test_doushi_corner_runs_a_bounded_slot_then_restores_the_previous_game(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    manager = CornerRotationManager(
        g,
        clock=lambda: now[0],
        sleep=sleep,
        seed="doushi-test",
        adapter_factory=lambda gg, corner: _doushi_adapter(gg, corner, switch, now, sleep),
    )

    result = manager.tick()

    assert result["status"] == "ready"
    state = json.loads((Path(g.state_dir) / "doushi_corner.json").read_text())
    assert state["status"] == "completed" and state["end_reason"] == "duration"
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["doushi_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    assert state["restored_runtime_identity"]["game"] == "robots"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"
    assert canonical["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]


def test_doushi_corner_manual_stop_restores_before_the_duration_bound(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 5:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "doushi_corner.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "doushi_corner.json", "requested_at": now[0]}))

    adapter = _doushi_adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    assert state["doushi_runtime_identity"]["game"] == VIEW_NAME
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "doushi_corner.json").exists()


def test_doushi_corner_never_restores_over_an_operator_moved_runtime(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _doushi_adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)
    assert adapter.manager._dispatch_start(state) is None
    view_identity = state["doushi_runtime_identity"]

    # An operator moves the shared slot away from the view while it is active.
    moved = switch.switch("nsnake", payload={"expected_source": view_identity})
    assert moved.status == "succeeded"

    assert adapter.manager.run_rotation(request_id) == "completed"

    saved = json.loads(adapter.state_path.read_text())
    assert saved["status"] == "interrupted"
    assert saved["end_reason"] == "operator-moved-during-doushi"
    final, _ = store.canonical.load()
    assert final["active"]["game"] == "nsnake"


def test_doushi_corner_stop_before_start_needs_no_game_switch(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _doushi_adapter(g, _corner(), switch, now, sleep)
    path = Path(g.state_dir) / STOP_REQUEST_DIR / "doushi_corner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state_file": "doushi_corner.json", "requested_at": now[0]}))

    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "operator-stopped-before-start"
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"


def test_released_failed_doushi_owner_allows_a_later_rotation(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _doushi_adapter(g, _corner(), switch, now, sleep)
    old_request = str(uuid.uuid4())
    old = adapter.manager._new_state(old_request)
    old.update(status="failed", completed_at=now[0], last_error_code="prepare_failed")
    adapter.manager._save(old)
    # The old request's durable stop marker must not carry over to the next
    # owner either (Paper's settle mirrors this).
    marker = Path(g.state_dir) / STOP_REQUEST_DIR / "doushi_corner.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"state_file": "doushi_corner.json", "requested_at": now[0]}))

    assert adapter.settle_released_failure(old_request) is True
    settled = json.loads(adapter.state_path.read_text())
    assert settled["status"] == "interrupted"
    assert settled["rotation_request_id"] == old_request
    assert settled["end_reason"] == "released-failure-before-retry"
    assert not marker.exists()

    new_request = str(uuid.uuid4())
    assert adapter.manager.run_rotation(new_request) == "completed"
    current = json.loads(adapter.state_path.read_text())
    assert current["rotation_request_id"] == new_request
    assert current["status"] == "completed"
    assert not marker.exists()


def test_released_failed_doushi_owner_requires_the_exact_request(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    adapter = _doushi_adapter(g, _corner(), switch, now, lambda seconds: None)
    old_request = str(uuid.uuid4())
    old = adapter.manager._new_state(old_request)
    old.update(status="failed", completed_at=now[0])
    adapter.manager._save(old)

    assert adapter.settle_released_failure(str(uuid.uuid4())) is False
    assert json.loads(adapter.state_path.read_text())["status"] == "failed"

    assert switch.switch(VIEW_NAME).status == "succeeded"
    assert adapter.settle_released_failure(old_request) is False
    assert json.loads(adapter.state_path.read_text())["status"] == "failed"


def test_doushi_adapter_carries_the_catalog_audio_opt_in(tmp_path):
    g, _now, _factory, _store, _switch = _setup(tmp_path, duration=3)

    assert DoushiCornerAdapter(g, _corner(duration=3)).manager.audio_enabled is False
    assert DoushiCornerAdapter(g, _corner(duration=3, audio_enabled=True)).manager.audio_enabled is True


def test_doushi_corner_announces_once_per_request_and_records_counters(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    calls = []

    def sleep(seconds):
        now[0] += seconds

    adapter = _doushi_adapter(g, _corner(), switch, now, sleep)
    adapter.manager.announce = lambda g, *, request_id, audio_enabled: calls.append(
        (request_id, audio_enabled)) or {"overlay": "sent", "speech": "skipped"}
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)

    assert adapter.manager._dispatch_start(state) is None

    assert calls == [(request_id, False)]
    assert state["announce_request_id"] == request_id
    assert state["overlay_announcements"] == {"sent": 1, "failed": 0, "skipped": 0}
    assert "narration" not in state  # speech stays off without the opt-in

    # A resumed owner never publishes the same ranking twice.
    adapter.manager._announce_once(state)
    assert calls == [(request_id, False)]


def test_doushi_corner_speaks_only_when_the_audio_opt_in_is_set(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path)
    calls = []

    def sleep(seconds):
        now[0] += seconds

    adapter = _doushi_adapter(g, _corner(audio_enabled=True), switch, now, sleep)
    adapter.manager.announce = lambda g, *, request_id, audio_enabled: calls.append(
        audio_enabled) or {"overlay": "sent", "speech": "sent"}
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)

    assert adapter.manager._dispatch_start(state) is None

    assert calls == [True]
    assert state["narration"] == {"enqueued": 1, "delivery_failed": 0, "skipped": 0}


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
        seed="doushi-announce",
        adapter_factory=lambda gg, corner: _doushi_adapter(gg, corner, switch, now, sleep),
    )
    for adapter in manager.adapters.values():
        adapter.manager.announce = boom

    assert manager.tick()["status"] == "ready"

    state = json.loads((Path(g.state_dir) / "doushi_corner.json").read_text())
    assert state["status"] == "completed"
    assert state["overlay_announcements"] == {"sent": 0, "failed": 1, "skipped": 0}
    assert "SECRET-ANNOUNCER-BODY" not in json.dumps(state)
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
