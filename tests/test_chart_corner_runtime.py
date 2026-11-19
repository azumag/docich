"""Offline catalog / GameSwitch lifecycle contracts for the chart corner (#659)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.chart_view import VIEW_NAME  # noqa: E402
from docich.chart_corner_runtime import DEFAULT_DURATION_MINUTES  # noqa: E402
from docich.config import load_global  # noqa: E402
from docich.corner_adapters import ChartCornerAdapter  # noqa: E402
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog  # noqa: E402
from docich.corner_rotation import STOP_REQUEST_DIR  # noqa: E402
from docich.game_switch import (  # noqa: E402
    GameSwitchCoordinator, GameSwitchStore, StepTimeouts,
)


class FakeRuntimeAdapter:
    agent_enabled = False

    def __init__(self, spec):
        self.spec = spec
        self.name = "program" if spec.game == VIEW_NAME else "cli"
        self.requires_round_boundary = False
        self.request_round_boundary = None
        self.live = False

    def preflight(self, deadline, cancel):
        pass

    def materialize_runtime(self, deadline, cancel):
        self.live = True

    def readiness(self, deadline, cancel):
        pass

    def alive(self, deadline, cancel):
        return self.live

    def cleanup_runtime(self, deadline, cancel):
        self.live = False

    def start_agent(self, deadline, cancel):
        return None

    def stop_agent(self, deadline, cancel):
        pass


class FakeFactory:
    def __init__(self):
        self.adapters = {}

    def __call__(self, spec):
        if spec.runtime_id not in self.adapters:
            self.adapters[spec.runtime_id] = FakeRuntimeAdapter(spec)
        return self.adapters[spec.runtime_id]


def _corner(duration=None, **overrides):
    values = dict(id="chart-659", adapter="chart", game=VIEW_NAME,
                  enabled=True, manual_only=True, live_eligible=False, audio_enabled=False)
    values.update(overrides)
    if duration is not None:
        values["duration_minutes"] = duration
    return Corner(**values)


def _setup(tmp_path, *, duration=1):
    state = tmp_path / "run"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{state}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        f'corners=[{{id="chart-659",adapter="chart",game="chart-659",'
        f'enabled=true,manual_only=true,live_eligible=false,duration_minutes={duration}}}]\n'
    )
    g = load_global(tmp_path, config)
    now = [time.time()]
    store = GameSwitchStore(g.state_dir)
    switch = GameSwitchCoordinator(
        store, FakeFactory(), default_timeout_s=4.0,
        quiesce_verify_timeout_s=0.3, poll_interval_s=0.01,
        round_reacquire_timeout_s=0.3,
        step_timeouts=StepTimeouts(preflight_s=1.0, round_boundary_s=3.0, stop_agent_s=1.0,
                                   start_s=1.0, agent_start_s=1.0, cleanup_s=1.0, probe_s=0.3),
    )
    assert switch.start("robots").status == "succeeded"
    return g, now, store, switch


def _adapter(g, switch, now, sleep, *, duration=1):
    adapter = ChartCornerAdapter(g, _corner(duration=duration))
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


# --- catalog / adapter identity -----------------------------------------------


def test_production_catalog_registers_chart_manual_only():
    catalog = load_catalog(load_global(ROOT, ROOT / "config/docich.soren-live.toml"))
    row = next(c for c in catalog if c.id == "chart-659")
    assert (row.adapter, row.game) == ("chart", VIEW_NAME)
    assert row.enabled and row.manual_only and row.live_eligible is False
    assert row.fetch_on_start is False and row.audio_enabled is False


def test_catalog_row_requires_reviewed_identity(tmp_path):
    config = tmp_path / "config.toml"

    def load(row):
        config.write_text(f"[corner_rotation]\ncorners=[{row}]\n")
        return load_catalog(load_global(tmp_path, config))

    row = load('{id="chart-659",adapter="chart",game="chart-659",enabled=false,manual_only=true}')
    assert len(row) == 1 and row[0].manual_only is True
    assert load('{id="chart-659",adapter="chart",game="chart-659",manual_only=true,duration_minutes=5}')[0].duration_minutes == 5
    for bad in (
        '{id="chart",adapter="chart",game="chart-659",manual_only=true}',
        '{id="chart-659",adapter="chart",game="other-view",manual_only=true}',
        '{id="chart-659",adapter="chart",game="chart-659"}',
    ):
        with pytest.raises(CornerCatalogError):
            load(bad)
    with pytest.raises(CornerCatalogError, match="fetch_on_start"):
        load('{id="chart-659",adapter="chart",game="chart-659",manual_only=true,fetch_on_start=true}')


def test_adapter_identity_and_bounds(tmp_path):
    g = SimpleNamespace(state_dir=tmp_path / "run")
    adapter = ChartCornerAdapter(g, _corner())
    assert adapter.manager.eligible() is True
    assert adapter.manager.resources_released() is True
    assert adapter.state_path.name == "chart_corner_659_runtime.json"
    assert adapter.manager.duration_minutes == DEFAULT_DURATION_MINUTES
    with pytest.raises(Exception, match="identity"):
        ChartCornerAdapter(g, _corner(game="other-view"))
    with pytest.raises(Exception, match="identity"):
        ChartCornerAdapter(g, _corner(live_eligible=True))
    with pytest.raises(Exception, match="audio"):
        ChartCornerAdapter(g, _corner(audio_enabled=1))


# --- bounded lifecycle ---------------------------------------------------------


def test_runs_bounded_slot_then_restores_previous_game(tmp_path):
    g, now, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, switch, now, sleep)
    request_id = str(uuid.uuid4())
    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "duration"
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["chart_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"


def test_manual_stop_restores_before_duration(tmp_path):
    g, now, store, switch = _setup(tmp_path)
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 5:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "chart_corner_659_runtime.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "chart_corner_659_runtime.json",
                                        "requested_at": now[0]}))

    adapter = _adapter(g, switch, now, sleep)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"
    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "chart_corner_659_runtime.json").exists()
