"""Offline lifecycle checks for the default-disabled ぷよぷよクローン corner."""
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
from docich.corner_adapters import CornerExecutionError, PuyoCornerAdapter
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog
from docich.corner_rotation import STOP_REQUEST_DIR, CornerRotationManager
from docich.game_switch import (
    GameSwitchCoordinator,
    GameSwitchStore,
    ReadinessTimeoutError,
    StepTimeouts,
)
from docich.puyo_program import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    PuyoProgramError,
    PuyoProgramManager,
)
from docich.puyo_view import VIEW_NAME


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


def _setup(tmp_path, *, clock=None, enabled=False, manual_only=True, duration=1):
    state = tmp_path / "run"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{state}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        f'corners=[{{id="puyo-corner",adapter="puyo",game="puyo-view",'
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
    return Corner("puyo-corner", "puyo", VIEW_NAME, duration_minutes=duration, **overrides)


def _puyo_adapter(g, corner, switch, now, sleep):
    adapter = PuyoCornerAdapter(g, corner)
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def test_production_catalog_activates_puyo_with_the_tts_opt_in():
    # 2026-10-18 オーナー承認 (PR-4): 有効化 (手動予約) + 読み上げ opt-in。
    # 自動抽選 (manual_only 外し) は本番チャット取り出し配線確認後の別承認ステップ。
    import tomllib

    raw = Path(__file__).resolve().parents[1] / "config" / "docich.soren-live.toml"
    corners = tomllib.loads(raw.read_text(encoding="utf-8"))["corner_rotation"]["corners"]
    rows = [row for row in corners if row.get("id") == "puyo-corner"]
    assert len(rows) == 1
    row = rows[0]
    assert row["adapter"] == "puyo" and row["game"] == "puyo-view"
    assert row["enabled"] is True
    assert row["audio_enabled"] is True
    assert row["manual_only"] is True


def test_puyo_catalog_row_requires_the_reviewed_identity_and_bounds(tmp_path):
    state = tmp_path / "run"
    config = tmp_path / "config.toml"

    def catalog(row):
        config.write_text(
            f'[paths]\nstate_dir="{state}"\n'
            '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
            f"corners=[{row}]\n"
        )
        g = load_global(tmp_path, config)
        return load_catalog(g)

    row = catalog('{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,manual_only=true}')
    assert row[0].duration_minutes is None and row[0].enabled is False
    assert row[0].manual_only is True and row[0].live_eligible is False

    for bad in (
        '{id="puyo",adapter="puyo",game="puyo-view",enabled=false,manual_only=true}',
        '{id="puyo-corner",adapter="puyo",game="doushi-view",enabled=false,manual_only=true}',
        '{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,fetch_on_start=true}',
        '{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,manual_only=false}',
        '{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,manual_only=true,live_eligible=true}',
        '{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,manual_only=true,duration_minutes=0}',
        '{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,manual_only=true,duration_minutes=15}',
    ):
        with pytest.raises(CornerCatalogError):
            catalog(bad)
    bounded = catalog('{id="puyo-corner",adapter="puyo",game="puyo-view",enabled=false,manual_only=true,duration_minutes=14}')
    assert bounded[0].duration_minutes == 14


# ---------------------------------------------------------------------------
# adapter identity
# ---------------------------------------------------------------------------


def test_puyo_adapter_rejects_an_unreviewed_identity_and_uses_the_catalog_duration(tmp_path):
    g, *_rest = _setup(tmp_path, duration=3)
    with pytest.raises(CornerExecutionError):
        PuyoCornerAdapter(g, _corner(duration=3, live_eligible=True))
    adapter = PuyoCornerAdapter(g, _corner(duration=3))
    assert adapter.manager.duration_minutes == 3
    assert adapter.eligible() is True
    assert adapter.state_path.name == "puyo_program.json"
    unbounded = PuyoCornerAdapter(g, Corner("puyo-corner", "puyo", VIEW_NAME, duration_minutes=None))
    assert unbounded.manager.duration_minutes == DEFAULT_DURATION_MINUTES


def test_puyo_manual_reservation_requires_enabled_and_stays_out_of_auto(tmp_path):
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

    disabled_root = tmp_path / "disabled"
    disabled_root.mkdir()
    g, now, *_rest = _setup(disabled_root, enabled=False, manual_only=True)
    disabled = CornerRotationManager(
        g, clock=lambda: now[0], sleep=lambda _seconds: None,
        seed="puyo-manual", adapter_factory=StubAdapter,
    )
    assert disabled._eligible(include_manual_only=True)[1] == {"puyo-corner": "disabled-or-paused"}
    with pytest.raises(RotationError, match="eligible manual corner"):
        disabled.queue_manual(VIEW_NAME)

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    g, now, *_rest = _setup(enabled_root, enabled=True, manual_only=True)
    manager = CornerRotationManager(
        g, clock=lambda: 1000.0, sleep=lambda _seconds: None,
        seed="puyo-manual", adapter_factory=StubAdapter,
    )
    assert manager._eligible(include_manual_only=False)[1] == {"puyo-corner": "manual-only"}
    assert manager.queue_manual(VIEW_NAME)["corner"] == "puyo-corner"


# ---------------------------------------------------------------------------
# lifecycle (GameSwitch harness)
# ---------------------------------------------------------------------------


def test_puyo_corner_runs_a_bounded_slot_then_restores_the_previous_game(tmp_path):
    # The production row is disabled+manual-only, so the slot is exercised the
    # way the owner UI reserves it: directly through the bounded manager.
    g, now, _factory, store, switch = _setup(tmp_path, enabled=True)

    def sleep(seconds):
        now[0] += seconds

    adapter = _puyo_adapter(g, _corner(duration=1), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "duration"
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["puyo_program_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    assert state["restored_runtime_identity"]["game"] == "robots"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"
    assert canonical["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]


def test_puyo_corner_manual_stop_restores_before_the_duration_bound(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path, enabled=True)
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 5:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "puyo_program.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "puyo_program.json", "requested_at": now[0]}))

    adapter = _puyo_adapter(g, _corner(duration=10), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    assert state["puyo_program_runtime_identity"]["game"] == VIEW_NAME
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "puyo_program.json").exists()


def test_puyo_corner_stop_before_start_needs_no_game_switch(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _puyo_adapter(g, _corner(), switch, now, sleep)
    path = Path(g.state_dir) / STOP_REQUEST_DIR / "puyo_program.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state_file": "puyo_program.json", "requested_at": now[0]}))

    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "operator-stopped-before-start"
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"


def test_released_failed_puyo_owner_allows_a_later_rotation(tmp_path):
    g, now, _factory, _store, switch = _setup(tmp_path, enabled=True)

    def sleep(seconds):
        now[0] += seconds

    adapter = _puyo_adapter(g, _corner(), switch, now, sleep)
    old_request = str(uuid.uuid4())
    old = adapter.manager._new_state(old_request)
    old.update(status="failed", completed_at=now[0], last_error_code="prepare_failed")
    adapter.manager._save(old)
    # The old request's durable stop marker must not carry over to the next
    # owner either.
    marker = Path(g.state_dir) / STOP_REQUEST_DIR / "puyo_program.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"state_file": "puyo_program.json", "requested_at": now[0]}))

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


def test_puyo_duration_bounds_fail_closed(tmp_path):
    assert DEFAULT_DURATION_MINUTES == 2
    assert MAX_DURATION_MINUTES == 14
    g, *_rest = _setup(tmp_path)
    for bad in (0, 15):
        with pytest.raises(PuyoProgramError):
            PuyoProgramManager(g, duration_minutes=bad)
    ok = PuyoProgramManager(g, duration_minutes=14)
    assert ok.duration_minutes == 14


def test_puyo_slot_announces_start_and_result_once(tmp_path):
    import datetime as dt

    g, now, _factory, store, switch = _setup(tmp_path, enabled=True)
    calls = []

    def fake_announce(gg, title, body, **kwargs):
        calls.append({"title": title, "body": body,
                      "audio": kwargs.get("audio_enabled"),
                      "request_id": kwargs.get("request_id")})
        return {"overlay": "sent", "speech": "sent"}

    def sleep(seconds):
        now[0] += seconds

    adapter = _puyo_adapter(g, _corner(duration=1), switch, now, sleep)
    adapter.manager.audio_enabled = True
    adapter.manager.announce = fake_announce

    # The live runtime records every finished round into the shared ledger;
    # one completed round inside this window must be picked up by the result
    # announcement (ended inside [started_at - 60, now]).
    ledger_path = g.state_dir / "puyo_corner_ledger.json"
    ledger_path.write_text(json.dumps({"schema": 1, "entries": [{
        "ruleset_id": "x", "started_at": None,
        "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "outcome": "user_win", "phase": "END", "round": 1,
        "winner": "user", "user_score": 1200, "ai_score": 900,
        "participants": ["u1"],
    }]}) + "\n", encoding="utf-8")

    request_id = str(uuid.uuid4())
    assert adapter.manager.run_rotation(request_id) == "completed"

    assert len(calls) == 2
    assert calls[0]["title"] == "ぷよぷよ対戦開始"
    assert "!puyo join" in calls[0]["body"]
    assert calls[1]["request_id"] == request_id
    assert "プレイヤーの勝ち" in calls[1]["body"] or "結果" in calls[1]["body"]
    for row in calls:
        assert row["request_id"] == request_id and row["audio"] is True

    recorded = adapter.manager._read_state().get("announce")
    assert recorded["start"]["request_id"] == request_id
    assert recorded["result"]["request_id"] == request_id
    assert recorded["start"]["overlay"] == "sent" and recorded["start"]["speech"] == "sent"

    # resuming a completed slot must never repeat the announcements
    assert adapter.manager.run_rotation(request_id) == "completed"
    assert len(calls) == 2
