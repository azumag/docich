"""Offline wiring contracts for the #5584 common new-corner production adapter.

Covers: the geography-world / international-politics catalog rows, the
``DOCICH_NEW_CORNER_ENABLED`` flag gate, theme-candidate selection, record
production through injected (fake) material collection + generation (the
recorded-response contract — the same ``build_draft``/``render_overlay``/``progress_frames``/
``validate_status`` validation applies to the synthetic fixture and to a
produced record), the loopback view, and the GameSwitch-integrated bounded
lifecycle. No network, no real LLM, no Twitch.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

import pytest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global  # noqa: E402
from docich.corner_adapters import CornerExecutionError, NewCornerAdapter  # noqa: E402
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog  # noqa: E402
from docich.corner_rotation import STOP_REQUEST_DIR, CornerRotationManager  # noqa: E402
from docich.game_switch import (  # noqa: E402
    GameSwitchCoordinator,
    GameSwitchStore,
    RuntimeSpec,
    StepTimeouts,
)
from docich.llm.contracts import DispatchResult  # noqa: E402
from docich.new_corner_manager import (  # noqa: E402
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    NewCornerError,
    NewCornerManager,
)
from docich.new_corner_production import (  # noqa: E402
    ENABLE_ENV,
    generated_keys,
    pick_theme_id,
    produce_record,
    production_enabled,
    validate_record,
)
from docich.new_corner_runtime import (  # noqa: E402
    CORNER_SPECS,
    load_corner_fixture,
    list_theme_candidates,
    select_theme,
)
from docich.new_corner_view import (  # noqa: E402
    NewCornerViewError,
    handler_for,
    load_payload,
    port_for,
    view_name_for,
)
from docich.adapters.new_corner_program import (  # noqa: E402
    AdapterError,
    NewCornerProgramViewAdapter,
    new_corner_view_game_config,
)
from docich.naming import runtime_names  # noqa: E402
from docich.web_material import VerifiedWebBundle, VerifiedWebMaterial  # noqa: E402

CORNER_ID = "geography-world"
VIEW_NAME = "geography-world-view"
INTERNATIONAL_ID = "international-politics"
INTERNATIONAL_VIEW = "international-politics-view"
ENGLISH_ID = "english"
ENGLISH_VIEW = "english-view"


def _global(tmp_path, config_text=""):
    config = tmp_path / "docich.toml"
    config.write_text(config_text, encoding="utf-8")
    return load_global(tmp_path, config)


# --- catalog and flag gate ----------------------------------------------------


def test_production_catalog_registers_geography_world_flag_gated():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    row = next(c for c in load_catalog(g) if c.id == CORNER_ID)
    assert (row.adapter, row.game) == ("newcorner", VIEW_NAME)
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    assert row.duration_minutes == 4 and row.audio_enabled is False
    assert row.fetch_on_start is False and row.target_matches is None


def test_production_catalog_registers_international_politics_flag_gated():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    row = next(c for c in load_catalog(g) if c.id == INTERNATIONAL_ID)
    assert (row.adapter, row.game) == ("newcorner", INTERNATIONAL_VIEW)
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    assert row.duration_minutes == 4 and row.audio_enabled is False
    assert row.fetch_on_start is False and row.target_matches is None


def test_newcorner_catalog_row_requires_a_spec_id_and_derived_view(tmp_path):
    config = tmp_path / "config.toml"

    def load(row):
        config.write_text(f"[corner_rotation]\ncorners=[{row}]\n")
        return load_catalog(load_global(tmp_path, config))

    row = load(f'{{id="{CORNER_ID}",adapter="newcorner",game="{VIEW_NAME}"}}')
    assert len(row) == 1 and row[0].duration_minutes is None
    for bad in (
        '{id="not-a-spec",adapter="newcorner",game="not-a-spec-view"}',
        f'{{id="{CORNER_ID}",adapter="newcorner",game="other-view"}}',
    ):
        with pytest.raises(CornerCatalogError, match="newcorner"):
            load(bad)
    with pytest.raises(CornerCatalogError, match="fetch_on_start"):
        load(f'{{id="{CORNER_ID}",adapter="newcorner",game="{VIEW_NAME}",fetch_on_start=true}}')
    with pytest.raises(CornerCatalogError, match="audio_enabled"):
        load(f'{{id="{CORNER_ID}",adapter="newcorner",game="{VIEW_NAME}",audio_enabled="yes"}}')
    for duration in ("0", "15", "true", "1.5"):
        with pytest.raises(CornerCatalogError, match="1-14"):
            load(f'{{id="{CORNER_ID}",adapter="newcorner",game="{VIEW_NAME}",duration_minutes={duration}}}')


def _corner(duration=None, **overrides):
    kwargs = {"duration_minutes": duration}
    kwargs.update(overrides)
    return Corner(CORNER_ID, "newcorner", VIEW_NAME, **kwargs)


def test_adapter_is_flag_gated_and_uses_the_catalog_duration(tmp_path, monkeypatch):
    g = _global(tmp_path)
    monkeypatch.delenv(ENABLE_ENV, raising=False)

    adapter = NewCornerAdapter(g, _corner(duration=5))
    assert adapter.manager.duration_minutes == 5
    assert adapter.manager.eligible() is False  # flag OFF: never in rotation
    assert adapter.state_path.name == "new_corner.json"

    monkeypatch.setenv(ENABLE_ENV, "1")
    assert adapter.manager.eligible() is True
    assert NewCornerAdapter(g, _corner()).manager.duration_minutes == DEFAULT_DURATION_MINUTES

    with pytest.raises(CornerExecutionError, match="identity"):
        NewCornerAdapter(g, Corner(CORNER_ID, "newcorner", "other-view"))
    with pytest.raises(CornerExecutionError, match="identity"):
        NewCornerAdapter(g, _corner(live_eligible=True))
    with pytest.raises(CornerExecutionError, match="audio"):
        NewCornerAdapter(g, _corner(audio_enabled=1))
    with pytest.raises(NewCornerError, match="duration"):
        NewCornerManager(g, CORNER_ID, duration_minutes=MAX_DURATION_MINUTES + 1)


def test_international_adapter_uses_the_catalog_identity_and_flag_gate(tmp_path, monkeypatch):
    g = _global(tmp_path)
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    corner = Corner(
        INTERNATIONAL_ID, "newcorner", INTERNATIONAL_VIEW,
        duration_minutes=4, audio_enabled=False,
    )
    adapter = NewCornerAdapter(g, corner)
    assert adapter.manager.corner_id == INTERNATIONAL_ID
    assert adapter.manager.view_name == INTERNATIONAL_VIEW
    assert adapter.manager.duration_minutes == 4
    assert adapter.manager.eligible() is False
    monkeypatch.setenv(ENABLE_ENV, "1")
    assert adapter.manager.eligible() is True


def test_run_rotation_fails_closed_when_the_flag_is_off(tmp_path, monkeypatch):
    g = _global(tmp_path)
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    manager = NewCornerManager(g, CORNER_ID)
    with pytest.raises(NewCornerError, match="disabled"):
        manager.run_rotation(str(uuid.uuid4()))


# --- theme candidates ---------------------------------------------------------


def test_theme_candidates_load_and_select_for_geography_world():
    candidates = list_theme_candidates(CORNER_ID)
    assert len(candidates) >= 3
    ids = [c["id"] for c in candidates]
    assert len(set(ids)) == len(ids)
    theme = select_theme(ids[0], CORNER_ID)
    assert theme["id"] == ids[0] and theme["title"].strip()
    assert pick_theme_id(CORNER_ID, generation=1) == ids[0]
    assert pick_theme_id(CORNER_ID, generation=len(ids) + 1) == ids[0]


def test_theme_candidates_load_and_select_for_international_politics():
    candidates = list_theme_candidates(INTERNATIONAL_ID)
    assert len(candidates) >= 5
    ids = [c["id"] for c in candidates]
    assert len(set(ids)) == len(ids)
    assert "maritime-boundary-dialogue" in ids
    assert "international-court-arbitration-dispute" in ids
    theme = select_theme("maritime-boundary-dialogue", INTERNATIONAL_ID)
    assert theme["fixtureId"] == "fixed-international-politics-deepdive-001"
    assert pick_theme_id(INTERNATIONAL_ID, generation=1) == ids[0]
    assert pick_theme_id(INTERNATIONAL_ID, generation=len(ids) + 1) == ids[0]


def test_production_catalog_registers_english_flag_gated():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    row = next(c for c in load_catalog(g) if c.id == ENGLISH_ID)
    assert (row.adapter, row.game) == ("newcorner", ENGLISH_VIEW)
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    assert row.duration_minutes == 4 and row.audio_enabled is False
    assert row.fetch_on_start is False and row.target_matches is None


def test_theme_candidates_load_and_select_for_english():
    candidates = list_theme_candidates(ENGLISH_ID)
    assert len(candidates) >= 3
    ids = [c["id"] for c in candidates]
    assert len(set(ids)) == len(ids)
    expected = {
        "stack-trace-reading", "deploy-rollback",
        "central-bank-rate-decision", "election-headline",
        "coffee-shop-order", "weekend-plan",
    }
    assert expected <= set(ids)
    theme = select_theme("stack-trace-reading", ENGLISH_ID)
    assert theme["id"] == "stack-trace-reading" and theme["title"].strip()
    assert pick_theme_id(ENGLISH_ID, generation=1) == ids[0]
    assert pick_theme_id(ENGLISH_ID, generation=len(ids) + 1) == ids[0]


def test_adapter_is_ineligible_for_english_when_flag_is_off(tmp_path, monkeypatch):
    g = _global(tmp_path)
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    corner = Corner(
        ENGLISH_ID, "newcorner", ENGLISH_VIEW,
        duration_minutes=4, audio_enabled=False,
    )
    adapter = NewCornerAdapter(g, corner)
    assert adapter.manager.corner_id == ENGLISH_ID
    assert adapter.manager.view_name == ENGLISH_VIEW
    assert adapter.manager.duration_minutes == 4
    # flag 未指定／OFF: adapter は rotation に入りえない（fail-closed）
    assert adapter.manager.eligible() is False


def test_english_produce_record_is_disabled_when_flag_unset(tmp_path, monkeypatch):
    g = _global(tmp_path)
    # DOCICH_NEW_CORNER_ENABLED 未指定: produce_record は disabled を返し発火しない
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    result = produce_record(
        ENGLISH_ID,
        env={},
        agents="",
        root=ROOT,
    )
    assert result.status == "disabled"
    assert result.record is None
    assert not result.ok


# --- record production (fake material + recorded generation) ------------------


def _material(
    url="https://geo.example/verified/singapore",
    excerpt="Verified excerpt about Singapore and the Strait of Malacca.",
):
    return VerifiedWebMaterial(
        url=url,
        body_sha256="a" * 64,
        text_sha256="b" * 64,
        excerpt_sha256="c" * 64,
        excerpt=excerpt,
        query_indexes=(0,),
    )


def _bundle(status="ok", items=None):
    return VerifiedWebBundle(
        status,
        tuple(items if items is not None else [_material()]),
        ("singapore malacca",),
    )


def _generation_payload(corner_id=CORNER_ID):
    return {key: ("項目 " + key) for key in generated_keys(corner_id)}


def _env(**extra):
    env = {ENABLE_ENV: "1", "DOCICH_ALLOW_REAL_AI": "1"}
    env.update(extra)
    return env


AGENTS = "openrouter-api:openai/gpt-4.1-nano,sarashina-api:sarashina3-nano"


def _generator(output=None):
    def run(_g, *, label, agents, prompt_text, timeout_sec=None, env=None):
        assert label.startswith("RADIO:")
        assert "openrouter-api" in agents
        return DispatchResult(
            returncode=0,
            output=json.dumps(output if output is not None else _generation_payload()),
        )
    return run


def _collector(bundle=None):
    def collect(queries, *, env, timeout_sec, max_sources, **kwargs):
        assert 1 <= len(queries) <= 3
        return bundle if bundle is not None else _bundle()
    return collect


def test_produce_record_is_disabled_without_the_flag():
    result = produce_record(
        CORNER_ID, env={"DOCICH_ALLOW_REAL_AI": "1"}, agents=AGENTS,
        collector=_collector(), generator=_generator(),
    )
    assert result.status == "disabled" and result.record is None


def test_produce_record_requires_the_real_ai_gate():
    result = produce_record(
        CORNER_ID, env={ENABLE_ENV: "1"}, agents=AGENTS,
        collector=_collector(), generator=_generator(),
    )
    assert result.status == "invalid_config"


def test_produce_record_rejects_a_non_direct_agent_chain():
    # non-direct providers are rejected by the chain policy
    result = produce_record(
        CORNER_ID, env=_env(), agents="opencode:some-model",
        collector=_collector(), generator=_generator(),
    )
    assert result.status == "invalid_config"


def test_produce_record_reports_unavailable_material():
    result = produce_record(
        CORNER_ID, env=_env(), agents=AGENTS,
        collector=_collector(_bundle("unavailable", items=[])),
        generator=_generator(),
    )
    assert result.status == "material_unavailable"


def test_produced_record_passes_the_same_contract_as_the_fixture():
    result = produce_record(
        CORNER_ID, env=_env(), agents=AGENTS, generation=3,
        collector=_collector(), generator=_generator(), clock=lambda: 1000.0,
    )
    assert result.status == "ok" and result.record is not None
    record = dict(result.record)
    assert record["corner"] == CORNER_ID
    assert record["isSyntheticFixture"] is False
    assert record["verifiedWebMaterial"]["status"] == "ok"
    topic = record["topic"]
    # deterministic provenance fields come from theme + bundle, never the model
    assert topic["url"] == "https://geo.example/verified/singapore"
    assert topic["primarySources"] == ["https://geo.example/verified/singapore"]
    assert topic["citationKeepUrl"] is True
    assert topic["segmentLabels"] == ["fact", "analysis", "forecast"]
    assert topic["mapFocus"] == "項目 mapFocus"  # model-provided section
    entry = validate_record(record, CORNER_ID)
    assert entry["corner"] == CORNER_ID and entry["totalFrames"] >= 3

    # Same contract over the canonical synthetic fixture.
    fixture = load_corner_fixture(CORNER_ID)
    fixture_entry = validate_record(fixture, CORNER_ID)
    assert fixture_entry["corner"] == CORNER_ID


def test_international_produced_record_passes_the_same_contract_as_the_fixture():
    material = _material(
        url="https://example.org/international-politics/verified-material",
        excerpt="Verified excerpt about maritime boundary negotiations.",
    )
    result = produce_record(
        INTERNATIONAL_ID, env=_env(), agents=AGENTS, generation=1,
        collector=_collector(_bundle(items=[material])),
        generator=_generator(output=_generation_payload(INTERNATIONAL_ID)),
        clock=lambda: 1000.0,
    )
    assert result.status == "ok" and result.record is not None
    record = dict(result.record)
    assert record["corner"] == INTERNATIONAL_ID
    assert record["isSyntheticFixture"] is False
    topic = record["topic"]
    assert topic["topicId"] == f"{INTERNATIONAL_ID}-maritime-boundary-dialogue"
    assert topic["url"] == "https://example.org/international-politics/verified-material"
    assert topic["primarySources"] == [
        "https://example.org/international-politics/verified-material"
    ]
    assert topic["citationKeepUrl"] is True
    assert topic["segmentLabels"] == ["fact", "analysis", "forecast"]
    assert topic["whatHappened"] == "項目 whatHappened"
    entry = validate_record(record, INTERNATIONAL_ID)
    assert entry["corner"] == INTERNATIONAL_ID and entry["totalFrames"] == 10

    fixture = load_corner_fixture(INTERNATIONAL_ID)
    fixture_entry = validate_record(fixture, INTERNATIONAL_ID)
    assert fixture_entry["corner"] == INTERNATIONAL_ID


def test_produce_record_rejects_bad_model_output():
    for output in (
        "not json",
        json.dumps({"region": "only-one-key"}),
        json.dumps({**_generation_payload(), "mapFocus": "see https://leak.example/x"}),
        json.dumps({**_generation_payload(), "extra": "unexpected"}),
    ):
        result = produce_record(
            CORNER_ID, env=_env(), agents=AGENTS,
            collector=_collector(), generator=_generator(output=output),
            clock=lambda: 1000.0,
        )
        assert result.status in {"invalid_output", "invalid_record"}, output[:40]


def test_produce_record_rejects_failed_generation():
    def generator(_g, **kwargs):
        return DispatchResult(returncode=1, output="")
    result = produce_record(
        CORNER_ID, env=_env(), agents=AGENTS,
        collector=_collector(), generator=generator, clock=lambda: 1000.0,
    )
    assert result.status == "generation_failed"


# --- view: payload re-derivation and fail-closed serving ---------------------


def _produced_record():
    result = produce_record(
        CORNER_ID, env=_env(), agents=AGENTS, generation=1,
        collector=_collector(), generator=_generator(), clock=lambda: 1000.0,
    )
    assert result.ok
    return dict(result.record)


def _stored_body(record=None):
    return json.dumps(
        {"corner": CORNER_ID, "record": record if record is not None else _produced_record()},
        ensure_ascii=False,
    ).encode("utf-8")


def test_view_payload_rederives_frames_from_a_produced_record(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(_stored_body())
    payload = load_payload(path)
    assert payload["ok"] is True and payload["corner"] == CORNER_ID
    assert payload["isSynthetic"] is False
    assert payload["view"] == VIEW_NAME
    assert payload["frames"][0]["kind"] == "start"
    assert payload["frames"][-1]["kind"] == "end"
    assert payload["citationUrls"] == ["https://geo.example/verified/singapore"]
    assert payload["statusEntry"]["totalFrames"] == len(payload["frames"])
    assert payload["pageSeconds"] == 12


def test_view_payload_accepts_the_synthetic_fixture_with_the_same_contract(tmp_path):
    fixture = load_corner_fixture(CORNER_ID)
    path = tmp_path / "record.json"
    path.write_bytes(_stored_body(record=fixture))
    payload = load_payload(path)
    assert payload["ok"] is True and payload["isSynthetic"] is True
    assert payload["frames"][0]["kind"] == "start"


def test_view_payload_fails_closed_on_missing_unreadable_or_tampered(tmp_path):
    with pytest.raises(NewCornerViewError, match="missing"):
        load_payload(tmp_path / "absent.json")
    path = tmp_path / "broken.json"
    path.write_bytes(b"{ not json")
    with pytest.raises(NewCornerViewError, match="unreadable"):
        load_payload(path)
    tampered = json.loads(_stored_body())
    tampered["record"]["topic"]["mapFocus"] = ""
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(NewCornerViewError):
        load_payload(path)
    path.write_text(json.dumps({"corner": "other", "record": {}}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(NewCornerViewError):
        load_payload(path)


@pytest.fixture
def served(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(_stored_body())
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
    _, port, lease = served
    status, body = _get(port, "/api/new-corner")
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True and data["corner"] == CORNER_ID
    assert data["runtime_id"] == "g4-view" and data["generation"] == 4
    assert data["lease_id"] == lease
    status, body = _get(port, "/broadcast")
    assert status == 200 and 'lang="ja"' in body


def test_view_rejects_unknown_paths_and_non_loopback_hosts(served):
    _, port, _ = served
    assert _get(port, "/api/other")[0] == 404
    assert _get(port, "/api/new-corner", host="evil.example:1")[0] == 403


def test_view_identity_helpers_and_handler_bounds():
    assert view_name_for(CORNER_ID) == VIEW_NAME
    assert port_for(CORNER_ID) == 8811
    assert view_name_for(INTERNATIONAL_ID) == INTERNATIONAL_VIEW
    assert port_for(INTERNATIONAL_ID) == 8812
    with pytest.raises(NewCornerViewError, match="runtime id"):
        handler_for({}, "../etc/passwd")
    with pytest.raises(NewCornerViewError, match="generation"):
        handler_for({}, "g4-view", generation=0)
    with pytest.raises(NewCornerViewError, match="lease"):
        handler_for({}, "g4-view", generation=4, lease_id="not-a-uuid")


# --- program adapter ----------------------------------------------------------


def _spec(tmp_path, *, generation=4, runtime_id="g4-a1b2c3d4"):
    names = runtime_names(generation)
    return RuntimeSpec(
        game=VIEW_NAME, adapter="program", generation=generation,
        runtime_id=runtime_id, lease_id=str(uuid.uuid4()),
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _program_adapter(tmp_path, *, generation=4, config_text='[new_corner]\ndraft_agents="openrouter-api:openai/gpt-4.1-nano"\n'):
    from types import SimpleNamespace

    (tmp_path / "docich.toml").write_text(config_text, encoding="utf-8")
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
    return NewCornerProgramViewAdapter(g, _spec(tmp_path, generation=generation))


def test_program_adapter_requires_draft_agents_config(tmp_path):
    adapter = _program_adapter(tmp_path, config_text="")
    with pytest.raises(AdapterError, match="draft agents"):
        adapter.build_session_payload()


def test_program_adapter_writes_a_payload_the_view_can_reload(tmp_path, monkeypatch):
    from docich.adapters import new_corner_program

    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(
        new_corner_program, "produce_record",
        lambda *a, **k: type("R", (), {"ok": True, "record": _produced_record(), "status": "ok"})(),
    )
    adapter = _program_adapter(tmp_path, generation=1)
    payload = adapter.write_session_payload()
    assert payload["corner"] == CORNER_ID
    loaded = load_payload(adapter.payload_path)
    assert loaded["ok"] is True and loaded["corner"] == CORNER_ID

    config = new_corner_view_game_config(CORNER_ID)
    assert config.name == VIEW_NAME and config.adapter == "program"
    assert config.agent.enabled is False


def test_program_adapter_fails_closed_when_production_fails(tmp_path, monkeypatch):
    from docich.adapters import new_corner_program
    from docich.new_corner_production import ProductionResult

    monkeypatch.delenv(ENABLE_ENV, raising=False)
    monkeypatch.setattr(
        new_corner_program, "produce_record",
        lambda *a, **k: ProductionResult("disabled"),
    )
    adapter = _program_adapter(tmp_path)
    with pytest.raises(AdapterError, match="unavailable"):
        adapter.build_session_payload()


# --- GameSwitch-integrated bounded lifecycle (lawsim/doushi contract) ---------


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
        f'corners=[{{id="{CORNER_ID}",adapter="newcorner",game="{VIEW_NAME}",'
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


def _newcorner_adapter(g, corner, switch, now, sleep):
    adapter = NewCornerAdapter(g, corner)
    adapter.manager.coordinator = switch
    adapter.manager.clock = lambda: now[0]
    adapter.manager.sleep = sleep
    adapter.manager.poll_s = 1.0
    return adapter


def test_new_corner_runs_a_bounded_slot_then_restores_the_previous_game(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    manager = CornerRotationManager(
        g,
        clock=lambda: now[0],
        sleep=sleep,
        seed="newcorner-test",
        adapter_factory=lambda gg, corner: _newcorner_adapter(gg, corner, switch, now, sleep),
    )

    result = manager.tick()

    assert result["status"] == "ready"
    state = json.loads((Path(g.state_dir) / "new_corner.json").read_text())
    assert state["status"] == "completed" and state["end_reason"] == "duration"
    assert state["game"] == VIEW_NAME and state["corner"] == CORNER_ID
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["new_corner_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    assert state["restored_runtime_identity"]["game"] == "robots"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"


def test_new_corner_manual_stop_restores_before_the_duration_bound(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    g, now, _factory, store, switch = _setup(tmp_path)
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 5:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "new_corner.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "new_corner.json", "requested_at": now[0]}))

    adapter = _newcorner_adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    assert state["new_corner_runtime_identity"]["game"] == VIEW_NAME
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "new_corner.json").exists()


def test_shared_state_file_blocks_a_second_newcorner_owner(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    g, now, _factory, _store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _newcorner_adapter(g, _corner(), switch, now, sleep)
    # Seed an active geography-world owner.
    state = {
        "schema_version": 1,
        "game": VIEW_NAME,
        "corner": CORNER_ID,
        "status": "active",
        "rotation_request_id": str(uuid.uuid4()),
        "new_corner_runtime_identity": {
            "game": VIEW_NAME, "runtime_id": "g4-a1b2c3d4",
            "generation": 4, "lease_id": str(uuid.uuid4()),
        },
        "ends_at": now[0] + 60,
    }
    Path(g.state_dir).mkdir(parents=True, exist_ok=True)
    adapter.state_path.write_text(json.dumps(state))
    # The other production newcorner row must not take over the shared file.
    other = NewCornerManager(
        g, INTERNATIONAL_ID,
        duration_minutes=1, coordinator=adapter.manager.coordinator,
        clock=lambda: now[0], sleep=sleep,
    )
    with pytest.raises(NewCornerError, match="owns the state"):
        other.run_rotation(str(uuid.uuid4()))
    assert other.observations() == []
