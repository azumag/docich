"""docich-owned comment classifier (#882 / #829): heuristic parity, Jev purpose,
entry points. Keyless and mock-only; no provider request is made."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich import comment_classifier as cc  # noqa: E402
from docich.comment_classifier import __main__ as cli  # noqa: E402
from docich.comment_classifier import heuristic, jev, report  # noqa: E402
from docich.semantic_decision import transport as core  # noqa: E402
from docich.semantic_decision.validator import dumps  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/fixtures/comment_classifier_heuristic_golden.json").read_text())
LEGACY_REQUEST = json.loads((ROOT / "tests/fixtures/semantic_choice_direct.json").read_text())


def row(text="BGM聞こえない？", category="chitchat", index=1, user="viewer", is_english=False):
    return {"index": index, "user": user, "comment": text, "category": category, "is_english": is_english}


def response(request, choices=None, confidence=.9, model=None):
    choices = choices or ["stream_bug_report"] * len(request["questions"])
    return {"model": model or request["model"], "usage": {"input_tokens": 1000, "output_tokens": 0},
            "answers": {key: {"type": "choice", "choice": choice, "confidence": confidence,
                              "probabilities": {c: .95 if c == choice else .05 / 13 for c in jev.CRITERIA}}
                        for key, choice in zip(request["questions"], choices)}}


def ok_transport(choices=None, confidence=.9):
    return Mock(side_effect=lambda req, config, env: {"status": "ok", "data": response(req, choices, confidence)})


KEY_ENV = {"TYPESAFE_API_KEY": "test-key"}


def classify(rows, tmp_path, transport, env=KEY_ENV, config=None):
    return jev.classify(rows, config or jev.Config(), env, tmp_path, transport=transport)


# ---------------------------------------------------------------- heuristic


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_heuristic_is_byte_identical_to_soviet_now_golden(case):
    assert GOLDEN["provenance"]["soviet_now_commit"] == "f2c20234487c9d70953181d5ef6f32c7ff9d2091"
    assert dumps(heuristic.baseline(case["lines"])) == dumps(case["expected"])


def test_heuristic_reads_the_batch_file_contract(tmp_path):
    source = tmp_path / "comments.txt"
    source.write_text("viewer: a\n\n   \nviewer: b\n", encoding="utf-8")
    assert [r["index"] for r in heuristic.baseline(heuristic.read_comment_lines(source))] == [1, 2]


def test_empty_batch_is_unusable():
    with pytest.raises(ValueError):
        heuristic.baseline([])


def test_english_safety_restores_source_fields_and_clears_noise():
    rows = [{"index": 1, "user": "X", "comment": "echoed", "category": "chitchat", "is_english": True}]
    out = heuristic.enforce_english_safety(rows, ["viewer: LUL LUL LUL"])
    assert out == [{"index": 1, "user": "viewer", "comment": "LUL LUL LUL",
                    "category": "chitchat", "is_english": False}]
    with pytest.raises(ValueError):
        heuristic.enforce_english_safety([{"index": 2}], ["viewer: x"])


# ---------------------------------------------------------------- Jev purpose


def test_direct_request_projection_matches_the_legacy_golden():
    rows = [dict(r) for r in LEGACY_REQUEST["rows"][:2]]
    assert jev.build_request(rows, jev.Config().model) == LEGACY_REQUEST["request"]


def test_projection_is_body_only_not_context():
    source = row("nextNext: ソ連ゲームについて？", user="PRIVATE_NAME")
    source.update(persona="PRIVATE_PERSONA", game="PRIVATE_GAME", history="PRIVATE_HISTORY")
    request = jev.build_request([source], "jev-1.13.0")
    assert request["state"] == {"comments": [{"index": 1, "text": source["comment"]}]}
    assert "PRIVATE" not in dumps(request)
    assert "index=1" in request["questions"]["c1"]["instructions"]


def test_criteria_match_the_heuristic_category_space():
    categories = {r["category"] for c in GOLDEN["cases"] for r in c["expected"]}
    assert categories <= set(jev.CRITERIA)
    assert jev.NOTIFICATIONS == {"card_gacha", "raid", "subscription", "stream_goal", "bits"}


def test_model_is_fixed_by_route_and_legacy_model_env_is_ignored():
    assert jev.Config.from_env({"COMMENT_CLASSIFIER_JEV_MODEL": "jev-latest"}).model == "jev-1.13.0"
    vercel = jev.Config.from_env({"DOCICH_JEV_ROUTE": "vercel"})
    assert (vercel.route, vercel.model) == ("vercel", "typesafe-ai/jev")
    for bad in ({"DOCICH_JEV_ROUTE": "auto"}, {"COMMENT_CLASSIFIER_JEV_TIMEOUT_MS": "0"},
                {"COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE": "nan"}):
        with pytest.raises(ValueError):
            jev.Config.from_env(bad)


def test_success_only_changes_category(tmp_path):
    rows = [row()]
    output, event = classify(rows, tmp_path, ok_transport())
    assert output == [{**rows[0], "category": "stream_bug_report"}]
    assert rows[0]["category"] == "chitchat"
    assert event["rows"][0]["status"] == "jev"
    assert event["route"] == "direct" and event["requested_model"] == "jev-1.13.0"
    assert event["estimated_usd"] == pytest.approx(.000042)
    assert "BGM" not in dumps(event) and "test-key" not in dumps(event)


def test_per_row_low_confidence_falls_back(tmp_path):
    rows = [row(index=1), row("右に置いた方がよくない？", index=2)]

    def transport(req, config, env):
        value = response(req, ["stream_bug_report", "strategy_advice"])
        value["answers"]["c2"]["confidence"] = .2
        return {"status": "ok", "data": value}

    output, event = classify(rows, tmp_path, transport)
    assert output[0]["category"] == "stream_bug_report"
    assert output[1] == rows[1]
    assert event["rows"][1]["status"] == "low_confidence"


def test_local_notifications_are_not_sent_or_overridden(tmp_path):
    rows = [row(category="card_gacha", user="PRIVATE_BOT"), row(index=2, user="Nightbot"), row(index=3)]
    transport = ok_transport()
    output, event = classify(rows, tmp_path, transport)
    assert output[:2] == rows[:2]
    assert output[2]["category"] == "stream_bug_report"
    sent = transport.call_args.args[0]
    assert len(sent["state"]["comments"]) == 1 and "PRIVATE_BOT" not in dumps(sent)
    assert event["rows"][0]["status"] == "local_notification"


@pytest.mark.parametrize("category", sorted(jev.NOTIFICATIONS))
def test_model_cannot_manufacture_a_platform_notification(tmp_path, category):
    rows = [row()]
    output, event = classify(rows, tmp_path, ok_transport([category]))
    assert output == rows
    assert event["rows"][0]["status"] == "unconfirmed_notification"


@pytest.mark.parametrize("env", [{}, {"TYPESAFE_API_KEY": ""}, {"TYPESAFE_API_KEY": "bad\nkey"},
                                 {"DOCICH_JEV_VERCEL_API_KEY": "wrong-route-key"}])
def test_missing_or_other_route_key_means_no_network(tmp_path, env):
    transport = ok_transport()
    output, event = classify([row()], tmp_path, transport, env=env)
    assert output == [row()] and event["status"] == "missing_key" and not event["attempted"]
    transport.assert_not_called()


def test_vercel_route_uses_its_own_credential(tmp_path):
    config = jev.Config(route="vercel")
    transport = Mock(side_effect=lambda req, cfg, env: {"status": "ok", "data": response(req)})
    output, event = classify([row()], tmp_path, transport, env={"DOCICH_JEV_VERCEL_API_KEY": "v-key"}, config=config)
    assert transport.call_args.args[0]["model"] == "typesafe-ai/jev"
    assert event["route"] == "vercel" and event["estimated_usd"] is None  # cost unknown, never 0


# ------------------------------------------------------------ route failover

BOTH_KEYS = {"TYPESAFE_API_KEY": "test-key", "DOCICH_JEV_VERCEL_API_KEY": "v-key"}
CHAIN = jev.Config(route="direct", fallback="vercel")


def routed(outcomes):
    """Per-route scripted transport: outcomes[route] -> status (or 'ok')."""
    calls = []

    def transport(req, config, env):
        calls.append((config.route, req["model"], config.fallback))
        status = outcomes[config.route]
        return {"status": "ok", "data": response(req)} if status == "ok" else {"status": status}
    return transport, calls


def test_route_chain_is_parsed_strictly_from_the_one_route_key():
    assert jev.Config.from_env({"DOCICH_JEV_ROUTE": "direct,vercel"}).chain == ("direct", "vercel")
    assert jev.Config.from_env({}).chain == ("direct",)
    for bad in ("direct,direct", "direct, vercel", "direct,vercel,direct", "direct,", "x,vercel"):
        with pytest.raises(ValueError):
            jev.Config.from_env({"DOCICH_JEV_ROUTE": bad})


def test_healthy_primary_never_touches_the_fallback(tmp_path):
    transport, calls = routed({"direct": "ok", "vercel": "ok"})
    _, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    assert calls == [("direct", "jev-1.13.0", None)]
    assert event["route"] == "direct" and event["failover"] is False
    assert event["attempts"] == [{"route": "direct", "status": "ok", "attempted": True}]
    assert not (tmp_path / "gate-vercel.json").exists()


@pytest.mark.parametrize("reason", sorted(jev.FAILOVER_STATUSES - {"cooldown", "missing_key"}))
def test_fast_primary_failure_fails_over_once_with_the_fallbacks_own_model(tmp_path, reason):
    transport, calls = routed({"direct": reason, "vercel": "ok"})
    output, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    # One request per route, the fallback leg carrying no further fallback.
    assert calls == [("direct", "jev-1.13.0", None), ("vercel", "typesafe-ai/jev", None)]
    assert output[0]["category"] == "stream_bug_report"
    assert event["status"] == "ok" and event["route"] == "vercel" and event["failover"] is True
    assert event["requested_model"] == "typesafe-ai/jev" and event["estimated_usd"] is None
    assert [a["route"] for a in event["attempts"]] == ["direct", "vercel"]
    # The failed primary is cooled down on its own gate only.
    assert json.loads((tmp_path / "gate.json").read_text())["until"] > time.time()
    assert json.loads((tmp_path / "gate-vercel.json").read_text())["until"] == 0


def test_primary_timeout_does_not_spend_a_second_budget_in_the_same_batch(tmp_path):
    transport, calls = routed({"direct": "timeout", "vercel": "ok"})
    output, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    assert calls == [("direct", "jev-1.13.0", None)]
    assert output == [row()] and event["status"] == "timeout" and event["failover"] is False
    # ...but the primary's cooldown sends the next batch straight to the fallback.
    transport, calls = routed({"direct": "ok", "vercel": "ok"})
    _, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    assert calls == [("vercel", "typesafe-ai/jev", None)]
    assert event["attempts"][0] == {"route": "direct", "status": "cooldown", "attempted": False}
    assert event["status"] == "ok" and event["route"] == "vercel"


def test_missing_primary_key_uses_the_fallback_without_a_primary_request(tmp_path):
    transport, calls = routed({"direct": "ok", "vercel": "ok"})
    _, event = classify([row()], tmp_path, transport, env={"DOCICH_JEV_VERCEL_API_KEY": "v-key"}, config=CHAIN)
    assert calls == [("vercel", "typesafe-ai/jev", None)]
    assert event["attempts"][0] == {"route": "direct", "status": "missing_key", "attempted": False}


def test_both_routes_failing_keeps_the_heuristic_and_reports_the_last_leg(tmp_path):
    transport, calls = routed({"direct": "server_error", "vercel": "rate_limited"})
    output, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    assert len(calls) == 2 and output == [row()]
    assert event["status"] == "rate_limited" and event["route"] == "vercel" and event["attempted"]


@pytest.mark.parametrize("status", ["input_limit", "invalid_config"])
def test_non_health_outcomes_do_not_fail_over(tmp_path, status):
    transport, calls = routed({"direct": status, "vercel": "ok"})
    _, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    assert [c[0] for c in calls] == ["direct"] and event["failover"] is False


def test_a_busy_primary_gate_does_not_double_the_traffic_onto_the_fallback(tmp_path):
    transport, calls = routed({"direct": "ok", "vercel": "ok"})
    with jev.locked_file(jev.gate_path(tmp_path, "direct")):
        _, event = classify([row()], tmp_path, transport, env=BOTH_KEYS, config=CHAIN)
    assert calls == [] and event["status"] == "busy" and event["failover"] is False


def test_single_route_config_never_fails_over(tmp_path):
    transport, calls = routed({"direct": "server_error", "vercel": "ok"})
    _, event = classify([row()], tmp_path, transport, env=BOTH_KEYS)
    assert [c[0] for c in calls] == ["direct"] and event["route_chain"] == ["direct"]


@pytest.mark.parametrize("reason", sorted(jev.COOLDOWNS))
def test_provider_failures_fall_back_with_unknown_usage(tmp_path, reason):
    output, event = classify([row()], tmp_path, lambda *_: {"status": reason})
    assert output == [row()] and event["status"] == reason and event["attempted"]
    assert event["usage"] is None and event["estimated_usd"] is None


@pytest.mark.parametrize("status", sorted(jev.NO_COOLDOWN_STATUSES))
def test_config_and_input_outcomes_are_recorded_without_cooldown(tmp_path, status):
    _, event = classify([row()], tmp_path, lambda *_: {"status": status})
    assert event["status"] == status
    assert json.loads((tmp_path / "gate.json").read_text())["until"] == 0


def test_bad_mapping_falls_back_the_whole_batch(tmp_path):
    def transport(req, config, env):
        value = response(req)
        value["answers"]["wrong"] = value["answers"].pop("c1")
        return {"status": "ok", "data": value}

    output, event = classify([row()], tmp_path, transport)
    assert output == [row()] and event["status"] == "invalid_response"


def test_limits_keep_excess_on_the_heuristic(tmp_path):
    rows = [row(index=i) for i in range(1, 12)]
    rows[0]["comment"] = "あ" * 5000
    transport = ok_transport()
    output, event = classify(rows, tmp_path, transport)
    assert output[0] == rows[0] and output[-1] == rows[-1]
    assert event["eligible_count"] == 8
    assert len(dumps(transport.call_args.args[0]).encode()) <= jev.MAX_REQUEST_BYTES


def test_cooldown_survives_a_new_call(tmp_path):
    classify([row()], tmp_path, Mock(return_value={"status": "rate_limited", "retry_after": 120}))
    transport = ok_transport()
    output, event = classify([row()], tmp_path, transport)
    assert output == [row()] and event["status"] == "cooldown" and not event["attempted"]
    transport.assert_not_called()
    assert json.loads((tmp_path / "gate.json").read_text())["until"] > time.time() + 100


def test_busy_gate_is_nonblocking(tmp_path):
    transport = ok_transport()
    with jev.locked_file(tmp_path / "gate.json"):
        _, event = classify([row()], tmp_path, transport)
    assert event["status"] == "busy"
    transport.assert_not_called()


def test_state_symlink_is_not_followed(tmp_path):
    target = tmp_path / "target"
    target.write_text("untouched")
    (tmp_path / "gate.json").symlink_to(target)
    transport = ok_transport()
    _, event = classify([row()], tmp_path, transport)
    assert event["status"] == "state_unavailable" and target.read_text() == "untouched"
    transport.assert_not_called()


def test_cooldown_write_failure_does_not_hide_an_attempt(tmp_path):
    stream = io.StringIO("")
    stream.write = Mock(side_effect=OSError("SECRET"))

    @contextmanager
    def locked(_):
        yield stream

    with patch.object(jev, "locked_file", locked):
        _, event = classify([row()], tmp_path, ok_transport())
    assert event["attempted"] and event["status"] == "ok" and event["usage"] is not None


def test_metrics_rotation_retention_and_redaction(tmp_path):
    _, event = classify([row("PRIVATE_TEXT", user="PRIVATE_USER")], tmp_path, ok_transport())
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "metrics-2000-01-01.jsonl"
    old.write_text("{}\n")
    with patch.object(jev, "LOG_BYTES", len(dumps(event).encode()) + 2):
        for _ in range(3):
            jev.append_metrics(event, logs)
    assert not old.exists()
    assert len(list(logs.glob("metrics-*.jsonl.1"))) == 1
    assert "PRIVATE" not in next(logs.glob("metrics-*.jsonl")).read_text()
    with patch.object(jev, "locked_file", side_effect=OSError("SECRET")):
        jev.append_metrics(event, logs)  # best effort, never raises


def test_the_only_http_path_is_the_reviewed_core(tmp_path):
    """No transport of our own: the default path spawns exactly one core child."""
    rows = [row()]
    request = jev.build_request(rows, "jev-1.13.0")
    raw = dumps({"status": "ok", "data": response(request)}).encode()
    with patch.object(core, "_bounded_process", return_value=raw) as spawn:
        output, event = classify(rows, tmp_path, jev.docich_transport, env={"TYPESAFE_API_KEY": "PRIVATE_KEY"})
    assert output[0]["category"] == "stream_bug_report" and event["status"] == "ok"
    assert spawn.call_count == 1
    args, kwargs = spawn.call_args
    assert json.loads(kwargs["data"]) == request
    assert kwargs["env"] == {"TYPESAFE_API_KEY": "PRIVATE_KEY", "LANG": "C.UTF-8"}
    assert "PRIVATE_KEY" not in repr(args) and b"PRIVATE_KEY" not in kwargs["data"]
    assert not hasattr(jev, "ENDPOINT") and not hasattr(jev, "http_worker")


# ---------------------------------------------------------------- entry points


def _batch(tmp_path, text="viewer: BGM聞こえない？\nNightbot: raid incoming\n"):
    source = tmp_path / "comments.txt"
    source.write_text(text, encoding="utf-8")
    return source


def test_backend_unset_is_heuristic_only_and_never_touches_the_network(tmp_path):
    transport = ok_transport()
    rows, event = cc.classify_file(_batch(tmp_path), env={"TYPESAFE_API_KEY": "k"}, transport=transport)
    assert event is None and rows == heuristic.baseline(heuristic.read_comment_lines(_batch(tmp_path)))
    transport.assert_not_called()


def test_backend_jev_replaces_categories_and_writes_sanitised_metrics(tmp_path):
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k",
           "COMMENT_CLASSIFIER_JEV_STATE_DIR": str(tmp_path / "state"),
           "COMMENT_CLASSIFIER_JEV_METRICS_DIR": str(tmp_path / "metrics")}
    rows, event = cc.classify_file(_batch(tmp_path), env=env, transport=ok_transport(["general_question"]))
    assert rows[0]["category"] == "general_question" and rows[1]["category"] == "raid"
    assert event["implementation_sha256"] and "heuristic_ms" in event and "classification_ms" in event
    written = next((tmp_path / "metrics").glob("metrics-*.jsonl")).read_text()
    assert "BGM" not in written and '"k"' not in written


def test_invalid_jev_config_never_makes_a_request(tmp_path):
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k",
           "COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE": "PRIVATE_INVALID",
           "COMMENT_CLASSIFIER_JEV_LOG_ENABLED": "0"}
    transport = ok_transport()
    rows, event = cc.classify_file(_batch(tmp_path), env=env, transport=transport)
    assert event["status"] == "invalid_config" and "PRIVATE_INVALID" not in dumps(event)
    transport.assert_not_called()


def test_a_jev_defect_never_costs_the_batch_its_classification(tmp_path):
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k"}
    with patch.object(jev, "run_jev", side_effect=RuntimeError("boom")):
        rows, event = cc.classify_file(_batch(tmp_path), env=env)
    assert event is None and rows[1]["category"] == "raid"


def test_cli_prints_only_the_json_contract(tmp_path, capsys):
    assert cli.main([str(_batch(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)[1] == {"index": 2, "user": "Nightbot", "comment": "raid incoming",
                                  "category": "raid", "is_english": False}
    assert cli.main([str(tmp_path / "missing.txt")]) == 1
    empty = _batch(tmp_path, "\n  \n")
    assert cli.main([str(empty)]) == 1
    assert capsys.readouterr().out == ""


def test_launcher_is_not_shadowed_by_the_callers_cwd_or_pythonpath(tmp_path):
    shadow = tmp_path / "caller"
    (shadow / "docich" / "comment_classifier").mkdir(parents=True)
    (shadow / "docich" / "__init__.py").write_text("")
    (shadow / "docich" / "comment_classifier" / "__init__.py").write_text("raise SystemExit('SHADOWED')")
    source = _batch(shadow)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMMENT_CLASSIFIER", "DOCICH_JEV", "TYPESAFE"))}
    env["PYTHONPATH"] = str(shadow)
    result = subprocess.run([str(ROOT / "bin/docich-comment-classify"), str(source)], cwd=shadow,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[1]["category"] == "raid"
    assert "SHADOWED" not in result.stdout + result.stderr


# ---------------------------------------------------------------- report


def test_report_denominators_and_abstentions(tmp_path):
    _, success = classify([row()], tmp_path / "a", ok_transport())
    _, missing = classify([row()], tmp_path / "b", ok_transport(), env={})
    success.update(heuristic_ms=1, classification_ms=20)
    missing.update(heuristic_ms=2, classification_ms=2)
    result = report.summarize([success, missing])
    assert result["comments"] == 2 and result["requests_attempted"] == 1
    assert result["jev_coverage"] == .5 and result["agreement_sample_count"] == 1
    assert result["route_counts"] == {"direct": 2} and result["failover_batches"] == 0
    assert result["latency_ms"]["classification_ms"]["p95"] == 20
    transport, _ = routed({"direct": "server_error", "vercel": "ok"})
    _, failed_over = classify([row()], tmp_path / "c", transport, env=BOTH_KEYS, config=CHAIN)
    result = report.summarize([success, failed_over])
    assert result["route_counts"] == {"direct": 1, "vercel": 1} and result["failover_batches"] == 1
    scored = report.score_predictions([("chitchat", "chitchat"), ("stream_bug_report", None)])
    assert scored["coverage"] == .5 and scored["correct_fraction_all"] == .5
    assert scored["accuracy_on_available"] == 1


def test_report_evaluation_requires_explicit_api_permission(tmp_path, capsys):
    with pytest.raises(SystemExit):
        report.main(["--evaluate", str(tmp_path / "not-read.jsonl")])
    assert "requires --allow-api" in capsys.readouterr().err


def test_report_evaluation_uses_the_selected_route_key_only(tmp_path):
    corpus = tmp_path / "labelled.jsonl"
    corpus.write_text(json.dumps({"text": "BGM聞こえない？", "category": "stream_bug_report"}) + "\n")
    with pytest.raises(ValueError):
        report.evaluate(corpus, env={"DOCICH_JEV_ROUTE": "vercel", "TYPESAFE_API_KEY": "direct-only"})
    result = report.evaluate(corpus, env={"TYPESAFE_API_KEY": "k"}, transport=ok_transport())
    assert result["scores"]["jev"]["accuracy_on_available"] == 1
