"""#1233 PR-1: conditional screen decisions, no capture and no live API.

Synthetic model replies test routing/contracts, not Japanese model accuracy.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import time
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich import comment_classifier as cc  # noqa: E402
from docich.comment_classifier import heuristic, jev, screen  # noqa: E402
from docich.semantic_decision import transport as core  # noqa: E402
from docich.semantic_decision.routes import resolve_route  # noqa: E402
from docich.semantic_decision.validator import dumps, encode_request, validate_response  # noqa: E402

KEYS = {"TYPESAFE_API_KEY": "test-key", "DOCICH_JEV_VERCEL_API_KEY": "test-v-key"}


@pytest.fixture(autouse=True)
def no_live_process(monkeypatch):
    blocked = Mock(side_effect=AssertionError("no live API process in these tests"))
    monkeypatch.setattr(core, "_bounded_process", blocked)
    return blocked


def rows(*texts):
    return [{"index": i, "user": "viewer", "comment": text,
             "category": "chitchat", "is_english": False}
            for i, text in enumerate(texts or ("右上の赤いやつ何？",), 1)]


def reply(request, choices=None, confidence=None):
    choices, confidence = choices or {}, confidence or {}
    answers = {}
    for key, question in request["questions"].items():
        choice = choices.get(key, "required" if key.startswith("s") else "general_question")
        labels = question["criteria"]
        answers[key] = {"type": "choice", "choice": choice,
                        "confidence": confidence.get(key, 0.9),
                        "probabilities": {label: 0.9 if label == choice else 0.1 / (len(labels) - 1)
                                          for label in labels}}
    return {"model": request["model"], "answers": answers,
            "usage": {"input_tokens": 1000, "output_tokens": 100}}


def run(batch, tmp_path, *, choices=None, confidence=None, env=None, config=None, status=None):
    def respond(request, cfg, env):
        if status:
            return {"status": status}
        data = validate_response(reply(request, choices, confidence), request, resolve_route(cfg.route))
        return {"status": "ok", "data": data}
    mocked = Mock(side_effect=respond)
    output, event = jev.classify(batch, config or jev.Config(screen_enabled=True),
                                 KEYS if env is None else env, tmp_path, transport=mocked)
    return output, event, mocked


@pytest.mark.parametrize("count,expected", [
    (1, "c3572430f107a0ace104e8296d21e269b06bc36e450b7acb3ce6bc9803047d0c"),
    (8, "3b37bc9cf6446948177862139391b7d5d63414d425509ffd4cb6bad415922766"),
])
def test_disabled_request_is_byte_identical_to_2e26a357(count, expected):
    value = jev.build_request([{"comment": "こんにちは"}] * count, "jev-1.13.0")
    assert hashlib.sha256(dumps(value).encode()).hexdigest() == expected


def test_disabled_rows_and_category_telemetry_have_no_screen_fields(tmp_path):
    batch = rows()
    out, event, called = run(batch, tmp_path, config=jev.Config())
    assert out == [{**batch[0], "category": "general_question"}]
    assert set(called.call_args.args[0]["questions"]) == {"c1"}
    assert not any(k.startswith("screen_") for k in event)
    assert not any(k.startswith("screen_") for k in event["rows"][0])


def test_two_independent_questions_per_body_in_one_bounded_request(tmp_path):
    batch = rows(*(["右上の赤いやつ何？"] * 8))
    batch[0].update(user="PRIVATE_NAME", persona="PRIVATE_PERSONA", game="PRIVATE_GAME",
                    screenshot="PRIVATE_PATH", screen_need="required")
    out, event, called = run(batch, tmp_path)
    called.assert_called_once()
    sent = called.call_args.args[0]
    assert set(sent["questions"]) == {f"{prefix}{i}" for prefix in "cs" for i in range(1, 9)}
    assert len(encode_request(sent, resolve_route("direct"))) <= jev.MAX_REQUEST_BYTES
    assert sent["state"] == {"comments": [{"index": i, "text": row["comment"]}
                                           for i, row in enumerate(batch, 1)]}
    assert "PRIVATE" not in dumps(sent)
    for i in range(1, 9):
        assert f"index={i}" in sent["questions"][f"s{i}"]["instructions"]
    assert [row["screen_need"] for row in out] == ["required"] * 8
    assert event["screen_rubric_version"] == screen.RUBRIC_VERSION


@pytest.mark.parametrize("choice", ["required", "not_required", "uncertain"])
@pytest.mark.parametrize("category", ["chitchat", "game_question", "stream_bug_report"])
def test_screen_need_is_not_a_category_switch(tmp_path, choice, category):
    out, _, _ = run(rows(), tmp_path, choices={"c1": category, "s1": choice})
    assert out[0]["category"] == category
    assert out[0]["screen_need"] == choice
    assert out[0]["screen_status"] == "jev"


@pytest.mark.parametrize("category_conf,screen_conf,category,need", [
    (0.2, 0.9, "chitchat", "required"),
    (0.9, 0.2, "general_question", "uncertain"),
    (0.7, 0.7, "general_question", "required"),
])
def test_confidence_is_independent_for_category_and_screen(tmp_path, category_conf, screen_conf, category, need):
    out, event, _ = run(rows(), tmp_path, confidence={"c1": category_conf, "s1": screen_conf})
    assert out[0]["category"] == category
    assert out[0]["screen_need"] == need
    assert out[0]["screen_confidence"] == screen_conf
    assert event["rows"][0]["screen_candidate"] == "required"
    assert event["rows"][0]["screen_status"] == ("low_confidence" if screen_conf < 0.7 else "jev")


def test_notifications_and_eligible_position_mapping(tmp_path):
    batch = rows("通知", "通知", "ルールを教えて", "右上の赤いやつ何？")
    batch[0]["category"] = "card_gacha"
    batch[1]["user"] = "Nightbot"
    original = copy.deepcopy(batch)
    out, event, called = run(batch, tmp_path, choices={"s1": "not_required", "s2": "required"})
    assert len(called.call_args.args[0]["state"]["comments"]) == 2
    assert [row["screen_need"] for row in out] == ["not_required", "not_required", "not_required", "required"]
    assert [row["screen_status"] for row in out[:2]] == ["local_notification"] * 2
    assert [row["index"] for row in out] == [1, 2, 3, 4]
    assert batch == original
    assert [r["screen_need"] for r in event["rows"]] == [r["screen_need"] for r in out]


def test_only_notifications_do_not_call_jev(tmp_path):
    batch = rows("通知")
    batch[0]["category"] = "raid"
    out, event, called = run(batch, tmp_path)
    called.assert_not_called()
    assert out[0]["screen_need"] == "not_required"
    assert event["status"] == "no_candidates"


def test_excess_and_oversize_rows_remain_uncertain_not_silently_false(tmp_path):
    batch = rows(*(["こんにちは"] * 10))
    batch[0]["comment"] = "あ" * 1500
    out, _, called = run(batch, tmp_path)
    assert len(out) == 10
    for pos in (0, 9):
        assert out[pos]["screen_need"] == "uncertain"
        assert out[pos]["screen_status"] == "input_limit"
        assert out[pos]["category"] == "chitchat"
    assert len(called.call_args.args[0]["questions"]) == 16


def test_request_byte_limit_is_not_raised_to_fit_screen_questions(tmp_path):
    batch = rows(*(["x" * 4096] * 8))
    with pytest.raises(ValueError, match="input_limit"):
        jev.build_request(batch, "jev-1.13.0", screen_enabled=True)
    out, _, called = run(batch, tmp_path)
    sent = called.call_args.args[0]
    assert len(encode_request(sent, resolve_route("direct"))) <= 32768
    assert len(out) == 8
    assert any(row["screen_status"] == "input_limit" for row in out)


@pytest.mark.parametrize("reason", sorted(jev.COOLDOWNS | {"input_limit": 0, "invalid_config": 0}))
def test_provider_failure_preserves_category_and_marks_unknown(tmp_path, reason):
    out, event, called = run(rows(), tmp_path, status=reason)
    called.assert_called_once()
    assert out[0]["category"] == "chitchat"
    assert out[0]["screen_need"] == "uncertain"
    assert out[0]["screen_confidence"] is None
    assert out[0]["screen_status"] == reason
    assert event["rows"][0]["screen_status"] == reason


def test_missing_key_does_not_reuse_a_previous_positive_decision(tmp_path):
    batch = rows()
    batch[0].update(screen_need="required", screen_status="jev", screen_confidence=0.99)
    out, event, called = run(batch, tmp_path, env={})
    called.assert_not_called()
    assert out[0]["screen_need"] == "uncertain"
    assert out[0]["screen_status"] == "missing_key"
    assert out[0]["screen_confidence"] is None
    assert not event["attempted"]


def test_timeout_does_not_add_a_second_request_and_cooldown_is_preserved(tmp_path):
    config = jev.Config(fallback="vercel", screen_enabled=True)
    out, _, called = run(rows(), tmp_path, config=config, status="timeout")
    called.assert_called_once()
    assert out[0]["screen_status"] == "timeout"
    out, event, called = run(rows(), tmp_path, config=config)
    called.assert_called_once()
    assert called.call_args.args[1].route == "vercel"
    assert set(called.call_args.args[0]["questions"]) == {"c1", "s1"}
    assert event["attempts"][0]["status"] == "cooldown"
    assert out[0]["screen_need"] == "required"


def test_fast_failover_rebuilds_both_questions_for_the_selected_route(tmp_path):
    calls = []
    def transport(request, config, env):
        calls.append((config.route, copy.deepcopy(request)))
        if config.route == "direct":
            return {"status": "server_error"}
        return {"status": "ok", "data": validate_response(reply(request), request, resolve_route(config.route))}
    out, _ = jev.classify(rows(), jev.Config(screen_enabled=True, fallback="vercel"),
                           KEYS, tmp_path, transport=transport)
    assert [route for route, _ in calls] == ["direct", "vercel"]
    assert calls[0][1]["questions"] == calls[1][1]["questions"]
    assert calls[1][1]["model"] == "typesafe-ai/jev"
    assert out[0]["screen_need"] == "required"


@pytest.mark.parametrize("route", ["direct", "vercel"])
def test_real_core_boundary_carries_and_validates_both_answers(tmp_path, monkeypatch, route):
    def child(argv, *, data, timeout, env):
        import json
        request = json.loads(data)
        assert set(request["questions"]) == {"c1", "s1"}
        value = reply(request)
        value["answers"]["s1"]["injected_text"] = "PRIVATE_ECHO"
        return dumps({"status": "ok", "data": value}).encode()
    process = Mock(side_effect=child)
    monkeypatch.setattr(core, "_bounded_process", process)
    out, event = jev.classify(rows(), jev.Config(route=route, screen_enabled=True), KEYS, tmp_path)
    process.assert_called_once()
    assert out[0]["screen_need"] == "required"
    assert "PRIVATE_ECHO" not in dumps(event)
    assert "PRIVATE_ECHO" not in dumps(out)


@pytest.mark.parametrize("mutation", ["missing", "wrong_choice", "wrong_type", "nan", "bool", "missing_probability"])
def test_invalid_screen_answer_rejects_the_whole_response_not_the_comment(tmp_path, monkeypatch, mutation):
    def child(argv, *, data, timeout, env):
        import json
        request = json.loads(data)
        value = reply(request)
        answer = value["answers"]["s1"]
        if mutation == "missing":
            del value["answers"]["s1"]
        elif mutation == "wrong_choice":
            answer["choice"] = "game_question"
        elif mutation == "wrong_type":
            answer["type"] = "noul"
        elif mutation == "nan":
            answer["confidence"] = float("nan")
        elif mutation == "bool":
            answer["confidence"] = True
        else:
            del answer["probabilities"]["uncertain"]
        return json.dumps({"status": "ok", "data": value}).encode()
    monkeypatch.setattr(core, "_bounded_process", Mock(side_effect=child))
    out, event = jev.classify(rows(), jev.Config(screen_enabled=True), KEYS, tmp_path)
    assert out[0]["category"] == "chitchat"
    assert out[0]["screen_need"] == "uncertain"
    assert event["status"] == "invalid_response"


@pytest.mark.parametrize("flag", ["true", "yes", "", "2", True, 1, None])
def test_invalid_enable_flag_is_never_truthy_opt_in(flag):
    with pytest.raises(ValueError, match="invalid_config"):
        jev.Config.from_env({screen.ENABLE_ENV: flag})


@pytest.mark.parametrize("threshold", ["nan", "inf", "-0.1", "1.1", True, 0.5])
def test_invalid_screen_tuning_returns_local_rows_without_a_provider_call(tmp_path, threshold):
    env = {screen.ENABLE_ENV: "1", screen.CONFIDENCE_ENV: threshold,
           "COMMENT_CLASSIFIER_BACKEND": "jev", "COMMENT_CLASSIFIER_JEV_LOG_ENABLED": "0", **KEYS}
    source = tmp_path / "comments.txt"
    source.write_text("viewer: こんにちは\n", encoding="utf-8")
    mock = Mock(side_effect=AssertionError("must not call provider"))
    out, event = cc.classify_file(source, env=env, transport=mock)
    mock.assert_not_called()
    assert out[0]["category"] == "chitchat"
    assert out[0]["screen_status"] == "invalid_config"
    assert out[0]["screen_need"] == "uncertain"
    assert event["status"] == "invalid_config"


def test_disabled_feature_ignores_screen_tuning_and_does_not_change_default_model():
    config = jev.Config.from_env({screen.CONFIDENCE_ENV: "not-a-number"})
    assert not config.screen_enabled
    assert config.model == "jev-1.13.0"


@pytest.mark.parametrize("backend", ["heuristic", "jev"])
def test_public_entry_has_explicit_unknown_on_backend_unavailable_or_defect(tmp_path, backend):
    source = tmp_path / "comments.txt"
    source.write_text("viewer: こんにちは\nNightbot: 通知\n", encoding="utf-8")
    env = {screen.ENABLE_ENV: "1", "COMMENT_CLASSIFIER_BACKEND": backend}
    with patch.object(jev, "run_jev", side_effect=RuntimeError("PRIVATE_ERROR")):
        out, event = cc.classify_file(source, env=env)
    assert event is None
    assert out[0]["screen_status"] == ("classifier_error" if backend == "jev" else "backend_unavailable")
    assert out[0]["screen_need"] == "uncertain"
    assert out[1]["screen_need"] == "not_required"
    assert "PRIVATE_ERROR" not in dumps(out)


def test_public_entry_records_only_sanitized_screen_metadata(tmp_path):
    source = tmp_path / "comments.txt"
    source.write_text("PRIVATE_NAME: PRIVATE_BODY\n", encoding="utf-8")
    env = {screen.ENABLE_ENV: "1", "COMMENT_CLASSIFIER_BACKEND": "jev", **KEYS,
           "COMMENT_CLASSIFIER_JEV_STATE_DIR": str(tmp_path / "state"),
           "COMMENT_CLASSIFIER_JEV_METRICS_DIR": str(tmp_path / "metrics")}
    transport = Mock(side_effect=lambda request, cfg, env: {"status": "ok", "data": reply(request)})
    out, event = cc.classify_file(source, env=env, transport=transport)
    assert out[0]["screen_need"] == "required"
    text = "".join(path.read_text() for path in (tmp_path / "metrics").glob("metrics-*.jsonl"))
    assert text and "screen_need" in text
    for secret in ("PRIVATE_NAME", "PRIVATE_BODY", "test-key", "test-v-key"):
        assert secret not in text
    assert Path(screen.__file__) in jev._IMPLEMENTATION_FILES
    assert len(event["implementation_sha256"]) == 64


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_emits_opt_in_fields_and_no_diagnostic_stderr(tmp_path, capsys, monkeypatch, enabled):
    import json
    from docich.comment_classifier import __main__ as cli
    source = tmp_path / "comments.txt"
    source.write_text("viewer: こんにちは\n", encoding="utf-8")
    monkeypatch.setenv(screen.ENABLE_ENV, "1" if enabled else "0")
    monkeypatch.setenv("COMMENT_CLASSIFIER_BACKEND", "heuristic")
    assert cli.main([str(source)]) == 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    assert ("screen_need" in value[0]) is enabled
    assert captured.err == ""


def test_screen_threshold_is_configurable_without_changing_category_threshold(tmp_path):
    config = jev.Config(screen_enabled=True, min_confidence=0.95, screen_min_confidence=0.6)
    out, _, _ = run(rows(), tmp_path, config=config, confidence={"c1": 0.8, "s1": 0.6})
    assert out[0]["category"] == "chitchat"
    assert out[0]["screen_need"] == "required"
