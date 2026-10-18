"""Offline wiring contracts for the #697 law-simulator corner (production slice).

Covers: the authored session bank (engine-validated), the catalog/adapter
identity contracts, the GameSwitch-integrated bounded lifecycle, and the
loopback view (payload re-derivation, fail-closed validation, identity).
No network, no LLM, no Twitch.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sys
import threading
import time
import uuid

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.communist_law_simulator import (  # noqa: E402
    CORNER_ID,
    MAX_SESSION_LAWS,
    LawSimError,
    parse_session,
    session_trace,
)
from docich.config import load_global  # noqa: E402
from docich.corner_adapters import CornerExecutionError, LawSimCornerAdapter  # noqa: E402
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog  # noqa: E402
from docich.corner_rotation import STOP_REQUEST_DIR, CornerRotationManager  # noqa: E402
from docich.game_switch import (  # noqa: E402
    GameSwitchCoordinator,
    GameSwitchStore,
    ReadinessTimeoutError,
    RuntimeSpec,
    StepTimeouts,
)
from docich.lawsim_corner import (  # noqa: E402
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    LawSimCornerError,
    LawSimCornerManager,
)
from docich.lawsim_sessions import (  # noqa: E402
    SESSION_BANK,
    select_index,
    session_count,
    session_for_index,
)
from docich.lawsim_view import (  # noqa: E402
    DEFAULT_PORT,
    HTML,
    VIEW_NAME,
    LawSimViewError,
    handler_for,
    load_payload,
)
from docich.adapters.lawsim_program import (  # noqa: E402
    AdapterError,
    LawSimProgramViewAdapter,
    lawsim_view_game_config,
)
from docich.naming import runtime_names  # noqa: E402


# --- session bank -----------------------------------------------------------


def test_session_bank_entries_are_engine_valid_and_bounded():
    assert 1 <= session_count() <= 8
    seen_laws = set()
    for index, data in enumerate(SESSION_BANK):
        spec = parse_session(data)
        trace = session_trace(data)
        assert spec.title.strip() and spec.republic.strip()
        assert 1 <= len(spec.laws) <= MAX_SESSION_LAWS
        assert len(set(spec.laws)) == len(spec.laws)
        seen_laws.update(spec.laws)
        assert trace["corner"] == CORNER_ID
        assert trace["finalStatus"] in {
            "plan_achieved", "plan_incomplete", "collapse_public_unrest",
        }
    assert seen_laws  # every session uses reviewed law ids


def test_session_bank_covers_achievement_collapse_and_incomplete():
    statuses = [session_trace(data)["finalStatus"] for data in SESSION_BANK]
    assert "plan_achieved" in statuses
    assert "collapse_public_unrest" in statuses
    assert "plan_incomplete" in statuses


def test_session_selection_rotates_deterministically_by_generation():
    assert [select_index(gen) for gen in (1, 2, 3, 4, 5)] == [0, 1, 2, 0, 1]
    for bad in (0, -1, 1.5, True, "3"):
        with pytest.raises(ValueError):
            select_index(bad)  # type: ignore[arg-type]
    with pytest.raises(IndexError):
        session_for_index(session_count())


# --- catalog and adapter identity contracts ---------------------------------


def test_production_catalog_enables_lawsim_in_auto_rotation():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    catalog = load_catalog(g)
    row = next(c for c in catalog if c.id == "communist-law-simulator")
    assert (row.adapter, row.game) == ("lawsim", VIEW_NAME)
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    # duration_minutes 未指定 = lawsim_corner.DEFAULT_DURATION_MINUTES(3分) を使う。
    assert row.duration_minutes is None and row.audio_enabled is False
    assert row.fetch_on_start is False and row.target_matches is None


def test_lawsim_catalog_row_requires_the_reviewed_identity_and_bounds(tmp_path):
    config = tmp_path / "config.toml"

    def load(row):
        config.write_text(f"[corner_rotation]\ncorners=[{row}]\n")
        return load_catalog(load_global(tmp_path, config))

    row = load('{id="communist-law-simulator",adapter="lawsim",game="lawsim-view",enabled=false,manual_only=true}')
    assert len(row) == 1 and row[0].manual_only is True and row[0].duration_minutes is None
    assert load('{id="communist-law-simulator",adapter="lawsim",game="lawsim-view",duration_minutes=8}')[0].duration_minutes == 8

    for bad in (
        '{id="lawsim",adapter="lawsim",game="lawsim-view"}',
        '{id="communist-law-simulator",adapter="lawsim",game="lawsim-corner"}',
    ):
        with pytest.raises(CornerCatalogError, match="communist-law-simulator"):
            load(bad)
    with pytest.raises(CornerCatalogError, match="fetch_on_start"):
        load('{id="communist-law-simulator",adapter="lawsim",game="lawsim-view",fetch_on_start=true}')
    with pytest.raises(CornerCatalogError, match="audio_enabled"):
        load('{id="communist-law-simulator",adapter="lawsim",game="lawsim-view",audio_enabled=true}')
    for duration in ("0", "15", "true", "1.5"):
        with pytest.raises(CornerCatalogError, match="1-14"):
            load(f'{{id="communist-law-simulator",adapter="lawsim",game="lawsim-view",duration_minutes={duration}}}')


def _corner(duration=None, **overrides):
    kwargs = {"duration_minutes": duration}
    kwargs.update(overrides)
    return Corner("communist-law-simulator", "lawsim", VIEW_NAME, **kwargs)


def test_lawsim_adapter_rejects_an_unreviewed_identity_and_uses_the_catalog_duration(tmp_path):
    (tmp_path / "docich.toml").write_text("", encoding="utf-8")
    from types import SimpleNamespace
    g = SimpleNamespace(state_dir=tmp_path / "run")

    adapter = LawSimCornerAdapter(g, _corner(duration=5))
    assert adapter.manager.duration_minutes == 5
    assert adapter.manager.eligible() is True
    assert adapter.manager.resources_released() is True
    assert adapter.state_path.name == "lawsim_corner.json"

    default = LawSimCornerAdapter(g, _corner())
    assert default.manager.duration_minutes == DEFAULT_DURATION_MINUTES

    with pytest.raises(CornerExecutionError, match="identity"):
        LawSimCornerAdapter(g, Corner("communist-law-simulator", "lawsim", "other-view"))
    with pytest.raises(CornerExecutionError, match="identity"):
        LawSimCornerAdapter(g, _corner(live_eligible=True))
    with pytest.raises(CornerExecutionError, match="audio"):
        LawSimCornerAdapter(g, _corner(audio_enabled=True))
    with pytest.raises(LawSimCornerError, match="duration"):
        LawSimCornerManager(g, duration_minutes=MAX_DURATION_MINUTES + 1)


# --- GameSwitch-integrated bounded lifecycle (doushi contract) ---------------


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


def _setup(tmp_path, *, duration=1):
    state = tmp_path / "run"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{state}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        f'corners=[{{id="communist-law-simulator",adapter="lawsim",game="lawsim-view",'
        f'enabled=true,duration_minutes={duration}}}]\n'
    )
    g = load_global(tmp_path, config)
    now = [time.time()]
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


def _lawsim_adapter(g, corner, switch, now, sleep):
    adapter = LawSimCornerAdapter(g, corner)
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


def test_lawsim_corner_runs_a_bounded_slot_then_restores_the_previous_game(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    manager = CornerRotationManager(
        g,
        clock=lambda: now[0],
        sleep=sleep,
        seed="lawsim-test",
        adapter_factory=lambda gg, corner: _lawsim_adapter(gg, corner, switch, now, sleep),
    )

    result = manager.tick()

    assert result["status"] == "ready"
    state = json.loads((Path(g.state_dir) / "lawsim_corner.json").read_text())
    assert state["status"] == "completed" and state["end_reason"] == "duration"
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["lawsim_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    assert state["restored_runtime_identity"]["game"] == "robots"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"
    assert canonical["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]


def test_lawsim_corner_manual_stop_restores_before_the_duration_bound(tmp_path):
    g, now, _factory, store, switch = _setup(tmp_path)
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 5:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "lawsim_corner.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "lawsim_corner.json", "requested_at": now[0]}))

    adapter = _lawsim_adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    assert state["lawsim_runtime_identity"]["game"] == VIEW_NAME
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "lawsim_corner.json").exists()


# --- view: payload re-derivation, fail-closed validation, identity -----------


def _stored_body(index=0):
    data = {"corner": CORNER_ID, "session_index": index,
            "session": session_for_index(index)["session"]}
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def test_view_payload_rederives_the_trace_from_the_session(tmp_path):
    path = tmp_path / "lawsim_session.json"
    path.write_bytes(_stored_body(0))
    payload = load_payload(path)
    assert payload["ok"] is True and payload["corner"] == CORNER_ID
    assert payload["sessionIndex"] == 0
    assert payload["finalStatus"] == "plan_achieved"
    assert len(payload["frames"]) == 13 and len(payload["turns"]) == 11
    assert payload["frames"][0]["kind"] == "start" and payload["frames"][-1]["kind"] == "end"
    assert payload["statusEntry"]["totalFrames"] == 13
    assert payload["caveatFooter"].strip()
    assert payload["pageSeconds"] == 12


def test_view_payload_fails_closed_on_missing_unreadable_or_tampered_files(tmp_path):
    with pytest.raises(LawSimError, match="missing"):
        load_payload(tmp_path / "absent.json")
    path = tmp_path / "broken.json"
    path.write_bytes(b"{ not json")
    with pytest.raises(LawSimError, match="unreadable"):
        load_payload(path)
    tampered = json.loads(_stored_body(0))
    tampered["session"]["laws"][2] = "not-a-law"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LawSimError, match="not-a-law"):
        load_payload(path)
    path.write_text(json.dumps({"corner": "other", "session": {}}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LawSimError):
        load_payload(path)


@pytest.fixture
def served(tmp_path):
    """One loopback view server bound to an ephemeral port."""
    path = tmp_path / "lawsim_session.json"
    path.write_bytes(_stored_body(0))
    payload = load_payload(path)
    lease = str(uuid.uuid4())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_for(payload, "g4-view", generation=4, lease_id=lease),
    )
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    worker.start()
    try:
        yield payload, server.server_port, lease
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5.0)


def _get(port, path, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Host": host} if host else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_view_serves_the_board_with_runtime_identity(served):
    payload, port, lease = served
    status, body = _get(port, "/api/lawsim")
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True and data["finalStatus"] == "plan_achieved"
    assert data["runtime_id"] == "g4-view" and data["generation"] == 4
    assert data["lease_id"] == lease
    assert len(data["frames"]) == 13
    status, body = _get(port, "/broadcast")
    assert status == 200 and "共産主義法律シミュレーター" in body


def test_view_rejects_unknown_paths_and_non_loopback_hosts(served):
    _, port, _ = served
    assert _get(port, "/api/other")[0] == 404
    assert _get(port, "/api/lawsim", host="evil.example:1")[0] == 403


def test_view_handler_rejects_invalid_runtime_identity():
    with pytest.raises(LawSimViewError, match="runtime id"):
        handler_for({}, "../etc/passwd")
    with pytest.raises(LawSimViewError, match="generation"):
        handler_for({}, "g4-view", generation=0)
    with pytest.raises(LawSimViewError, match="lease"):
        handler_for({}, "g4-view", generation=4, lease_id="not-a-uuid")


# --- program adapter: deterministic session build and payload handoff --------


def _spec(tmp_path, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game=VIEW_NAME, adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _program_adapter(tmp_path, *, generation=4):
    from types import SimpleNamespace

    g = SimpleNamespace(
        state_dir=tmp_path / "run",
        config_path=tmp_path / "docich.toml",
        repo_root=ROOT,
        display=SimpleNamespace(
            name=":98", width=1280, height=720,
            viewport_x=0, viewport_y=90,
            viewport_width=0, viewport_height=0,
        ),
    )
    return LawSimProgramViewAdapter(g, _spec(tmp_path, generation=generation))


def test_program_adapter_rotates_sessions_by_generation(tmp_path):
    for generation, index in ((1, 0), (2, 1), (3, 2), (4, 0)):
        adapter = _program_adapter(tmp_path, generation=generation)
        payload = adapter.build_session_payload()
        assert payload["session_index"] == index
        assert payload["corner"] == CORNER_ID
        # The stored session must survive the engine's own validation.
        assert parse_session(payload).laws
    config = lawsim_view_game_config()
    assert config.name == VIEW_NAME and config.adapter == "program"
    assert config.agent.enabled is False


def test_program_adapter_writes_a_payload_the_view_can_reload(tmp_path):
    adapter = _program_adapter(tmp_path, generation=1)
    adapter.write_session_payload()
    payload = load_payload(adapter.payload_path)
    assert payload["ok"] is True and payload["sessionIndex"] == 0
    assert payload["finalStatus"] == "plan_achieved"


def test_program_adapter_rejects_a_generation_outside_the_engine(tmp_path, monkeypatch):
    adapter = _program_adapter(tmp_path, generation=1)
    monkeypatch.setattr(
        "docich.adapters.lawsim_program.select_index", lambda _generation: session_count()
    )
    with pytest.raises(AdapterError, match="不正"):
        adapter.build_session_payload()
