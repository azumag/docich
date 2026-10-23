from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from docich import trpg_generation as gen, trpg_session
from docich.trpg_corner import TrpgCornerError


def _story(*, next1=None, next2=None, extra=None):
    scenes = [
        {"id": "s1", "text": "導入", "choices": ["調べる", "聞く"],
         "next": {"1": next1, "2": next1}},
    ]
    if next1 is not None:
        scenes.append({"id": next1, "text": "中盤", "choices": ["進む", "戻る"],
                       "next": {"1": next2, "2": next2}})
    if next2 is not None:
        scenes.append({"id": next2, "text": "終盤", "choices": ["確認", "比較"],
                       "next": {"1": None, "2": None}})
    if extra is not None:
        scenes.append(extra)
    return {
        "id": "generated-case",
        "title": "生成事件",
        "genre": "both",
        "duration_minutes": 8,
        "scenes": scenes,
        "climax": {"suspects": ["A", "B"], "answer": "B", "trick": "時刻表",
                    "window_seconds": 120},
    }


def test_deterministic_fallback_is_reproducible_and_bounded():
    first = gen.deterministic_scenario("same-seed")
    second = gen.deterministic_scenario("same-seed")
    assert first == second
    assert len(first["scenes"]) == 3
    assert first["climax"]["answer"] in first["climax"]["suspects"]
    assert gen.validate_generated_scenario(first) == first


def test_generated_graph_rejects_cycles_and_unreachable_scenes():
    cycle = _story(next1="s2")
    cycle["scenes"][1]["next"] = {"1": "s1", "2": None}
    with pytest.raises(TrpgCornerError, match="cycle"):
        gen.validate_generated_scenario(cycle)

    unreachable = _story(extra={
        "id": "orphan", "text": "孤立", "choices": ["A", "B"],
        "next": {"1": None, "2": None},
    })
    with pytest.raises(TrpgCornerError, match="unreachable"):
        gen.validate_generated_scenario(unreachable)


def test_generated_graph_rejects_a_path_that_cannot_fit_the_slot():
    raw = _story(next1="s2", next2="s3")
    raw["climax"]["window_seconds"] = 600
    with pytest.raises(TrpgCornerError, match="slot budget"):
        gen.validate_generated_scenario(raw)


def test_generate_uses_ai_candidate_once_and_records_auditable_history(tmp_path):
    g = SimpleNamespace(state_dir=tmp_path / "state")
    spec = SimpleNamespace(runtime_id="g7-aabbccdd", generation=7,
                           lease_id="11111111-1111-4111-8111-111111111111")
    candidate = _story(next1="s2", next2="s3")
    calls = []

    def fake_generator(_g, **kwargs):
        calls.append(kwargs)
        assert kwargs["env"]["DOCICH_ALLOW_REAL_AI"] == "1"
        return SimpleNamespace(ok=True, output=json.dumps(candidate, ensure_ascii=False),
                               last_agent="codex:test", failure_kind="", detail="")

    story, meta = gen.generate_scenario(
        g, spec, {"generation_agents": "codex:test"},
        generator=fake_generator, env={},
    )
    assert story == gen.validate_generated_scenario(candidate)
    assert len(calls) == 1
    assert meta["status"] == "generated" and meta["source"] == "codex:test"
    assert meta["scenario_sha256"] == gen.scenario_hash(story)

    rows = (g.state_dir / "trpg" / gen.HISTORY_FILE).read_text().splitlines()
    assert len(rows) == 1
    recorded = json.loads(rows[0])
    assert recorded["scenario_sha256"] == meta["scenario_sha256"]
    assert recorded["scenario"] == story


def test_rotation_request_owns_private_stage_and_retry_never_regenerates(tmp_path):
    g = SimpleNamespace(state_dir=tmp_path / "state")
    request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    g.state_dir.mkdir(parents=True)
    (g.state_dir / gen.PROGRAM_STATE_FILENAME).write_text(json.dumps({
        "schema_version": 1,
        "status": "starting",
        "rotation_request_id": request_id,
        "duration_minutes": 8,
    }))
    spec = SimpleNamespace(runtime_id="g10-feedface", generation=10,
                           lease_id="44444444-4444-4444-8444-444444444444")
    candidate = _story(next1="s2", next2="s3")
    calls = []

    def fake_generator(_g, **_kwargs):
        calls.append(1)
        return SimpleNamespace(ok=True, output=json.dumps(candidate, ensure_ascii=False),
                               last_agent="codex:test", failure_kind="", detail="")

    first, first_meta = gen.generate_scenario(
        g, spec, {"generation_agents": "codex:test"}, generator=fake_generator, env={})
    second, second_meta = gen.generate_scenario(
        g, spec, {"generation_agents": "codex:test"}, generator=fake_generator, env={})

    assert calls == [1]
    assert second == first and second_meta == first_meta
    assert first_meta["request_id"] == request_id
    stage = g.state_dir / "trpg" / gen.STAGE_DIR / f"{request_id}.json"
    history = g.state_dir / "trpg" / gen.HISTORY_FILE
    assert stage.stat().st_mode & 0o777 == 0o600
    assert history.stat().st_mode & 0o777 == 0o600
    assert json.loads(stage.read_text())["metadata"]["scenario_sha256"] == gen.scenario_hash(first)
    assert len(history.read_text().splitlines()) == 1


def test_invalid_ai_output_fails_closed_to_seeded_fallback(tmp_path):
    g = SimpleNamespace(state_dir=tmp_path / "state")
    spec = SimpleNamespace(runtime_id="g8-deadbeef", generation=8,
                           lease_id="22222222-2222-4222-8222-222222222222")

    def bad_generator(_g, **_kwargs):
        return SimpleNamespace(ok=True, output="not-json", last_agent="codex:test",
                               failure_kind="", detail="")

    story, meta = gen.generate_scenario(
        g, spec, {"generation_agents": "codex:test"},
        generator=bad_generator, env={},
    )
    assert meta["status"] == "fallback"
    assert meta["source"] == "procedural-fallback"
    assert meta["failure"] == "JSONDecodeError"
    assert gen.validate_generated_scenario(story) == story


def test_missing_scenario_file_now_means_auto_generation_preview_not_ineligible(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{tmp_path / "state"}"\n'
        '[trpg_corner]\ngeneration_agents=""\nchat_file="chat.log"\n'
    )
    g = SimpleNamespace(config_path=config, repo_root=tmp_path,
                        state_dir=tmp_path / "state")
    story = trpg_session.scenario(g)
    assert story["id"].startswith("auto-")
    assert story["duration_minutes"] == 8


def test_prepare_materializes_one_generated_story_and_persists_metadata(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{tmp_path / "state"}"\n'
        '[trpg_corner]\ngeneration_agents="codex:test"\nchat_file="chat.log"\n'
    )
    g = SimpleNamespace(config_path=config, repo_root=tmp_path,
                        state_dir=tmp_path / "state")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    spec = SimpleNamespace(game="trpg-view", runtime_id="g9-cafebabe", generation=9,
                           lease_id="33333333-3333-4333-8333-333333333333",
                           runtime_dir=runtime)
    generated = gen.deterministic_scenario("materialized")
    calls = []

    def fake_generate(_g, _spec, _settings):
        calls.append(_spec.runtime_id)
        return generated, {"schema": 1, "source": "test", "status": "generated",
                           "scenario_sha256": gen.scenario_hash(generated)}

    monkeypatch.setattr(gen, "generate_scenario", fake_generate)
    data = trpg_session.prepare(g, spec)
    assert calls == [spec.runtime_id]
    assert data["scenario"] == generated
    assert data["scenario_meta"]["source"] == "test"
    saved = json.loads((runtime / trpg_session.SESSION_FILE).read_text())
    assert saved["scenario"] == generated
    assert saved["scenario_meta"] == data["scenario_meta"]
