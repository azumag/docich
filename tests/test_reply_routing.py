"""Offline contracts. Mock choices test routing, NOT Jev's Japanese accuracy."""
import asyncio
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from docich import reply_routing as routing
from docich import reply_research as research
from docich import discord_chat as chat
from docich.discord_memory import MemoryStore
from docich.comment_classifier import jev as comment_jev
from docich.comment_classifier import reply_route as comment_route
from docich.semantic_decision.validator import dumps as strict_dumps

ENV = {routing.ENABLE_ENV: "1", "DOCICH_ALLOW_REAL_AI": "1", "TYPESAFE_API_KEY": "SYNTHETIC_KEY"}
SOURCE_CONTRASTS = json.loads((Path(__file__).parent / "fixtures/reply_evidence_source_contrasts.json").read_text())


def messages(text="SSR出た！"):
    return [{"role": "system", "content": "PERSONA_PRIVATE"},
            {"role": "user", "content": json.dumps({"author_id": "PRIVATE_ID", "name": "PRIVATE_NAME", "text": text})}]


def answer(scope="api_only", confidence=.95):
    return {"status": "ok", "data": {"answers": {"reply_evidence": {"choice": scope, "confidence": confidence}}}}


def test_live_canary_corpus_is_synthetic_and_separates_notification_from_question():
    corpus = json.loads((Path(__file__).parent / "fixtures/reply_routing_canary.json").read_text())
    by_id = {sample["id"]: sample for sample in corpus}
    assert len(corpus) == 19 and len(by_id) == len(corpus)
    assert {sample["expected"] for sample in corpus} == {*routing.CRITERIA} - {"unknown"}
    assert by_id["notification_only"]["expected"] == "api_only"
    assert by_id["notification_plus_question"]["expected"] == "code"
    assert "SSR出た！" in by_id["notification_only"]["text"]
    assert by_id["notification_only"]["text"] != by_id["notification_plus_question"]["text"]


def test_recorded_high_confidence_mixed_regression_remains_an_observed_failure():
    observed = SOURCE_CONTRASTS["observed_regression"]
    assert observed["recorded_rubric"] == "reply-evidence-v1"
    assert observed["recorded_confidence"] == .88
    assert observed["recorded_choice"] == "code" and observed["expected"] == "web_and_code"
    assert set(observed["required_sources"]) == {"web", "code"}
    # The recorded response was structurally valid. Offline mocks cannot turn
    # this historical mistake into evidence that the changed model rubric works.
    old = routing.decide(routing.project_messages(messages(observed["text"])), env=ENV,
                         transport=lambda *a, **k: answer(observed["recorded_choice"], .88))
    assert old.scope == "code" and old.status == "jev"


def test_recorded_v1_canary_is_not_claimed_as_v2_accuracy():
    record = json.loads((Path(__file__).resolve().parents[1]/"docs/evidence/reply-routing-canary-2026-10-04.json").read_text())
    assert record["discord"]["rubric"] == record["stream"]["rubric"] == "reply-evidence-v1"
    assert routing.RUBRIC_VERSION == "reply-evidence-v2"
    assert record["actual_successful_posts"] == record["max_post_count"] == 22
    assert record["retry_count"] == 0 and not record["fallback_used"]
    assert all(attempt["status"] == "ok" for attempt in record["attempts"])
    mismatch = next(row for row in record["discord"]["results"] if row["id"] == "mixed_spec_impl")
    assert mismatch["scope"] == "code" and mismatch["confidence"] == .88 and not mismatch["correct"]


def test_combined_canary_eight_cases_still_fit_one_request():
    from docich.comment_classifier import heuristic
    corpus = json.loads((Path(__file__).parent/"fixtures/reply_routing_canary.json").read_text())
    for start in range(0,len(corpus),8):
        rows = heuristic.baseline([f"case-{start+i+1}: {sample['text']}" for i,sample in enumerate(corpus[start:start+8])])
        request = comment_jev.build_request(rows,"jev-1.13.0",evidence_enabled=True)
        assert len(request["state"]["comments"]) == len(rows)
        assert len(strict_dumps(request).encode()) <= comment_jev.MAX_REQUEST_BYTES
        assert set(request["questions"]) == {f"{kind}{index}" for index in range(1,len(rows)+1) for kind in ("c","e")}


def test_combined_event_names_evidence_rubric_without_changing_category_rubric(tmp_path):
    rows=[{"index":1,"user":"fixture","comment":"公開規約と実装の差分は？","category":"general_question","is_english":False}]
    _,event=comment_jev.classify(rows,comment_jev.Config(evidence_enabled=True),ENV,tmp_path,
                                transport=_comment_transport({rows[0]["comment"]:"web_and_code"}))
    assert event["rubric_version"] == "comment-body-v1"
    assert event["evidence_rubric_version"] == routing.RUBRIC_VERSION


def _discord_canary_module():
    path=Path(__file__).resolve().parents[1]/"scripts/reply_routing_live_canary.py"
    spec=importlib.util.spec_from_file_location("discord_live_canary_contract",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discord_canary_refuses_chain_before_any_classification(monkeypatch):
    module=_discord_canary_module()
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM",module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI","1")
    monkeypatch.setenv("DOCICH_JEV_ROUTE","direct,vercel")
    monkeypatch.setattr(module,"decide",lambda *a,**k:pytest.fail("classification before single-route guard"))
    with pytest.raises(SystemExit) as error:
        module.main(["--live"])
    assert error.value.code == 2


def test_discord_canary_stops_at_first_overload_without_retry(monkeypatch,capsys):
    module=_discord_canary_module()
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM",module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI","1")
    monkeypatch.setenv("DOCICH_JEV_ROUTE","direct")
    monkeypatch.setenv("TYPESAFE_API_KEY","SYNTHETIC_NOT_REAL")
    calls=[]
    monkeypatch.setattr(module,"decide",lambda *a,**k:calls.append(1) or routing.Decision(status="overloaded"))
    assert module.main(["--live"]) == 2
    result=json.loads(capsys.readouterr().out)
    assert len(calls)==result["requests_attempted"]==1 and result["status"]=="overloaded"
    assert result["rubric"]==routing.RUBRIC_VERSION and result["low_confidence_rate"]==0
    assert result["terminal_failure"] == "overloaded"
    assert result["requests_succeeded"] == result["cases_measured"] == 0


@pytest.mark.parametrize("failure", ["overloaded", "timeout", "invalid_response"])
def test_discord_canary_last_case_failure_is_incomplete(monkeypatch, capsys, failure):
    module = _discord_canary_module()
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM", module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setenv("DOCICH_JEV_ROUTE", "direct")
    monkeypatch.setenv("TYPESAFE_API_KEY", "SYNTHETIC_KEY")
    corpus = json.loads(module.CORPUS.read_text())
    decisions = iter([routing.Decision(row["expected"], "jev", .95) for row in corpus[:-1]]
                     + [routing.Decision(status=failure)])
    calls = []
    def fake_decide(*args, **kwargs):
        calls.append(1)
        return next(decisions)
    monkeypatch.setattr(module, "decide", fake_decide)
    assert module.main(["--live"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert len(calls) == output["requests_attempted"] == 19
    assert output["requests_succeeded"] == output["cases_measured"] == 18
    assert output["status"] == output["terminal_failure"] == failure
    assert output["results"][-1]["status"] == failure


@pytest.mark.parametrize("status", ["jev", "low_confidence"])
def test_discord_canary_completed_measurement_counts_low_confidence(monkeypatch, capsys, status):
    module = _discord_canary_module()
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM", module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setenv("DOCICH_JEV_ROUTE", "direct")
    monkeypatch.setenv("TYPESAFE_API_KEY", "SYNTHETIC_KEY")
    monkeypatch.setattr(module, "decide", lambda *a, **k: routing.Decision(
        "api_only" if status == "jev" else None, status, .95 if status == "jev" else .4))
    assert module.main(["--live"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "complete" and output["terminal_failure"] is None
    assert output["requests_attempted"] == output["requests_succeeded"] == output["cases_measured"] == 19
    assert output["low_confidence_rate"] == (1 if status == "low_confidence" else 0)


@pytest.mark.parametrize("sample", SOURCE_CONTRASTS["cases"], ids=lambda sample: sample["id"])
def test_source_contrasts_share_semantic_rubric_and_route_contract(sample):
    expected_sources = {"web": {"web"}, "code": {"code"}, "web_and_code": {"web", "code"}, "unknown": None}
    assert (set(sample["required_sources"]) if sample["required_sources"] is not None else None) == expected_sources[sample["expected"]]
    requests = []
    def mock(request, **kwargs):
        requests.append(request)
        return answer(sample["expected"])
    turns = [{"role": "user", "text": sample["text"]}]
    decision = routing.decide(turns, env=ENV, transport=mock)
    assert decision.scope == sample["expected"] and decision.status == "jev"
    assert not decision.api_only
    discord = requests[0]["questions"]["reply_evidence"]
    combined = comment_jev.build_request([{"comment": sample["text"]}], "jev-1.13.0", evidence_enabled=True)["questions"]["e1"]
    assert discord["criteria"] == combined["criteria"] == routing.CRITERIA
    assert routing.EVIDENCE_INSTRUCTIONS in discord["instructions"]
    assert routing.EVIDENCE_INSTRUCTIONS in combined["instructions"]
    assert "public plus implementation means web_and_code" in routing.EVIDENCE_INSTRUCTIONS
    assert "Only actual project-source" in discord["criteria"]["code"]
    assert "Only external public-source" in discord["criteria"]["web"]
    assert "Each side needs its own evidence" in discord["criteria"]["web_and_code"]
    # The classifier receives the unchanged question. No lexical override or
    # example-specific route selector is introduced in the production path.
    assert requests[0]["state"]["turns"] == turns
    assert sample["text"] not in routing.EVIDENCE_INSTRUCTIONS
    research_calls = []
    result = routing.complete(messages(sample["text"]), env=ENV,
        api=lambda *a: pytest.fail("external/unknown contrast reached API-only"),
        transport=lambda *a, **k: answer(sample["expected"]),
        researcher=lambda turns, scope, **kw: research_calls.append(scope) or research.Evidence())
    assert research_calls == ([] if sample["expected"] == "unknown" else [sample["expected"]])
    assert result == (routing.CLARIFY_REPLY if sample["expected"] == "unknown" else routing.UNAVAILABLE_REPLY)


@pytest.mark.parametrize("scope", ["web", "code", "web_and_code", "unknown"])
def test_source_contrast_low_confidence_never_researches_or_becomes_api(scope):
    calls = []
    result = routing.complete(messages("公開資料と実装を確認して。"), env=ENV,
        api=lambda *a: calls.append("api"), researcher=lambda *a, **k: calls.append("research"),
        transport=lambda *a, **k: answer(scope, .799))
    assert result == routing.UNAVAILABLE_REPLY and calls == []


def _comment_choice_answer(criteria, choice, confidence=.95):
    labels = list(criteria)
    rest = (1 - confidence) / (len(labels) - 1)
    return {"type": "choice", "choice": choice, "confidence": confidence,
            "probabilities": {label: confidence if label == choice else rest for label in labels}}


def _comment_transport(scope_for_text, *, confidence=.95, calls=None):
    def transport(request, config, env):
        if calls is not None:
            calls.append(request)
        comments = request["state"]["comments"]
        answers = {}
        for index, item in enumerate(comments, 1):
            for key, question in request["questions"].items():
                if not key.endswith(str(index)):
                    continue
                if key.startswith("c"):
                    choice = "general_question"
                elif key.startswith("e"):
                    choice = scope_for_text[item["text"]]
                else:
                    choice = next(iter(question["criteria"]))
                answers[key] = _comment_choice_answer(question["criteria"], choice, confidence)
        return {"status": "ok", "data": {"model": request["model"], "usage": {
            "input_tokens": 100, "output_tokens": 0}, "answers": answers}}
    return transport


def _comment_env(tmp_path):
    return {"COMMENT_CLASSIFIER_BACKEND": "jev", routing.ENABLE_ENV: "1",
            "COMMENT_CLASSIFIER_JEV_LOG_ENABLED": "0", "COMMENT_CLASSIFIER_JEV_STATE_DIR": str(tmp_path),
            "TYPESAFE_API_KEY": "SYNTHETIC_KEY", "DOCICH_ALLOW_REAL_AI": "1"}


def test_comment_evidence_question_is_combined_with_category_and_projects_body_only():
    request = comment_jev.build_request([{
        "index": 1, "user": "PRIVATE_NAME", "comment": "Reximって誰？",
        "persona": "PRIVATE_PERSONA", "history": "PRIVATE_HISTORY", "user_id": "PRIVATE_ID"}],
        "jev-1.13.0", evidence_enabled=True)
    assert set(request["questions"]) == {"c1", "e1"}
    assert request["state"] == {"comments": [{"index": 1, "text": "Reximって誰？"}]}
    assert set(request["questions"]["e1"]["criteria"]) == set(routing.CRITERIA)
    assert "PRIVATE" not in strict_dumps(request)
    combined = comment_jev.build_request([{"index": 1, "user": "viewer", "comment": "画面は？"}],
                                         "jev-1.13.0", screen_enabled=True, evidence_enabled=True)
    assert set(combined["questions"]) == {"c1", "s1", "e1"}
    with pytest.raises(ValueError, match="private_input"):
        comment_jev.build_request([{"index": 1, "user": "viewer",
                                   "comment": "token: abcdefghijklmnopqrstuvwxyz"}],
                                 "jev-1.13.0", evidence_enabled=True)


def test_stream_batch_routes_notification_and_question_separately(tmp_path):
    source = tmp_path / "comments.txt"
    source.write_text("Nightbot: SSR出た！\nviewer: SSR出た！このガチャの確率どうなってる？\n", encoding="utf-8")
    calls, research_calls = [], []
    notification = "SSR出た！"
    question = "SSR出た！このガチャの確率どうなってる？"

    def researcher(turns, scope, *, env, timeout_sec, comment_scopes):
        research_calls.append((turns, scope, timeout_sec))
        return research.Evidence("ok", "合成証拠", ("https://example.org/spec",), (1,))

    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"),
        transport=_comment_transport({notification: "api_only", question: "code"}, calls=calls),
        researcher=researcher)
    assert result["routing"]["status"] == "ready"
    assert result["routing"]["scope"] == "code"
    assert [c["text"] for c in calls[0]["state"]["comments"]] == [notification, question]
    assert "Nightbot" not in strict_dumps(calls[0]["state"])
    assert "viewer" not in strict_dumps(calls[0]["state"])
    assert research_calls == [([{"role": "user", "content": question}], "code", 45.0)]
    assert result["routing"]["sources"] == ["https://example.org/spec"]


def test_viewer_notification_reaction_is_api_only_only_after_confident_jev(tmp_path):
    source = tmp_path / "comments.txt"
    text = "SSR出た！"
    source.write_text(f"viewer: {text}\n", encoding="utf-8")
    calls = []
    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"),
        transport=_comment_transport({text: "api_only"}, calls=calls),
        researcher=lambda *args, **kwargs: pytest.fail("api_only reaction triggered research"))
    assert result["routing"]["status"] == "ready"
    assert result["routing"]["scope"] == "api_only"
    assert result["routing"]["confidence"] == .95
    assert len(calls) == 1


def test_notification_display_name_does_not_bypass_confident_jev(tmp_path):
    source = tmp_path / "comments.txt"
    source.write_text("Nightbot: SSR出た！\n", encoding="utf-8")
    calls = []
    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"),
        transport=_comment_transport({"SSR出た！": "api_only"}, calls=calls),
        researcher=lambda *args, **kwargs: pytest.fail("notification triggered research"))
    assert result["routing"]["status"] == "ready"
    assert result["routing"]["scope"] == "api_only"
    assert result["routing"]["confidence"] == .95
    assert len(calls) == 1
    assert "Nightbot" not in strict_dumps(calls[0]["state"])


def test_display_name_cannot_hide_runtime_or_secret_rows_in_mixed_batch(tmp_path):
    source = tmp_path / "comments.txt"
    for sensitive, label, expected_scope in (
            ("今Botが止まってる原因は？", "runtime", "runtime"),
            ('token: "SYNTHETIC_PRIVATE_VALUE_1234"', "secret", "unknown"),
            ('"token": "SYNTHETIC_PRIVATE_VALUE_1234"', "quoted-secret", "unknown")):
        source.write_text(f"Nightbot: {sensitive}\nviewer: こんにちは\n", encoding="utf-8")
        calls = []
        result = comment_route.classify_file(
            source, env=_comment_env(tmp_path / f"state-{label}"),
            transport=_comment_transport({
                sensitive: expected_scope, "こんにちは": "api_only"}, calls=calls),
            researcher=lambda *a, **k: pytest.fail("mixed unverified batch reached research"))
        assert result["routing"]["status"] == "hold"
        assert result["routing"]["scope"] == expected_scope
        assert len(calls) == 1
        request_text = strict_dumps(calls[0])
        assert "Nightbot" not in strict_dumps(calls[0]["state"])
        assert "viewer" not in strict_dumps(calls[0]["state"])
        if label == "secret":
            assert sensitive not in request_text


def test_repeated_display_name_rows_are_all_independently_judged(tmp_path):
    source = tmp_path / "comments.txt"
    texts = ["SSR出た！", "やったー", "今Botが止まってる原因は？"]
    source.write_text("".join(f"Nightbot: {text}\n" for text in texts), encoding="utf-8")
    calls = []
    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"),
        transport=_comment_transport({
            texts[0]: "api_only", texts[1]: "api_only", texts[2]: "runtime"}, calls=calls),
        researcher=lambda *a, **k: pytest.fail("runtime row reached research"))
    assert result["routing"]["status"] == "hold"
    assert result["routing"]["scope"] == "runtime"
    assert [item["text"] for item in calls[0]["state"]["comments"]] == texts
    assert "Nightbot" not in strict_dumps(calls[0]["state"])


def test_nine_and_ten_row_fetch_batches_are_classified_in_bounded_chunks(tmp_path):
    for count in (9, 10):
        source = tmp_path / f"comments-{count}.txt"
        texts = [f"こんにちは {index}" for index in range(count)]
        source.write_text("".join(f"viewer{index}: {text}\n" for index, text in enumerate(texts)),
                          encoding="utf-8")
        calls = []
        result = comment_route.classify_file(
            source, env=_comment_env(tmp_path / f"state-{count}"),
            transport=_comment_transport({text: "api_only" for text in texts}, calls=calls),
            researcher=lambda *a, **k: pytest.fail("api_only batch triggered research"))
        assert result["routing"]["status"] == "ready"
        assert result["routing"]["scope"] == "api_only"
        assert result["routing"]["confidence"] == .95
        assert [len(call["state"]["comments"]) for call in calls] == [8, count - 8]
        assert [row["index"] for row in result["rows"]] == list(range(1, count + 1))
        assert [row["comment"] for row in result["rows"]] == texts


def test_second_chunk_provider_failure_holds_the_whole_batch(tmp_path):
    source = tmp_path / "comments.txt"
    texts = [f"こんにちは {index}" for index in range(10)]
    source.write_text("".join(f"viewer{index}: {text}\n" for index, text in enumerate(texts)),
                      encoding="utf-8")
    calls = []
    success = _comment_transport({text: "api_only" for text in texts})

    def transport(request, config, env):
        calls.append(request)
        if len(calls) == 2:
            return {"status": "timeout"}
        return success(request, config, env)

    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"), transport=transport,
        researcher=lambda *a, **k: pytest.fail("partial batch reached research"))
    assert result["routing"]["status"] == "hold"
    assert result["routing"]["scope"] == "unknown"
    assert result["routing"]["reason"] == "classification_unavailable"
    assert len(result["rows"]) == 10
    assert [len(call["state"]["comments"]) for call in calls] == [8, 2]


def test_incomplete_classifier_rows_cannot_produce_a_ready_route(tmp_path, monkeypatch):
    source = tmp_path / "comments.txt"
    source.write_text("viewer: こんにちは\nviewer2: やったー\n", encoding="utf-8")
    row = {"index": 1, "user": "viewer", "comment": "こんにちは",
           "category": "chitchat", "is_english": False}
    detail = {"evidence_status": "jev", "evidence_scope": "api_only",
              "evidence_confidence": .95}
    incomplete_results = [
        ([row], {"rows": [detail]}),
        ([row, {**row, "index": 2}], {"rows": [detail, detail]}),
    ]
    for index, malformed in enumerate(incomplete_results):
        monkeypatch.setattr(comment_route, "classify_comments_file",
                            lambda *a, _malformed=malformed, **k: _malformed)
        result = comment_route.classify_file(
            source, env=_comment_env(tmp_path / f"state-{index}"),
            researcher=lambda *a, **k: pytest.fail("partial result reached research"))
        assert result["routing"]["status"] == "hold"
        assert result["routing"]["reason"] == "classifier_unavailable"


def test_eleven_row_batch_holds_instead_of_omitting_the_tail(tmp_path):
    source = tmp_path / "comments.txt"
    source.write_text("".join(f"viewer{index}: こんにちは {index}\n" for index in range(11)),
                      encoding="utf-8")
    calls = []
    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"),
        transport=_comment_transport({"こんにちは 0": "api_only"}, calls=calls),
        researcher=lambda *a, **k: pytest.fail("oversized input reached research"))
    assert result["routing"]["status"] == "hold"
    assert result["routing"]["reason"] == "input_limit"
    assert calls == []


def test_stream_research_failure_runtime_unknown_and_low_confidence_hold(tmp_path):
    source = tmp_path / "comments.txt"
    for text, scope, confidence, expected in (
            ("今日のニュースは？", "web", .95, "research_unavailable"),
            ("今Botが止まってる原因は？", "runtime", .95, "runtime_evidence_unavailable"),
            ("これどう？", "unknown", .95, "scope_unknown"),
            ("Reximって誰？", "web", .79, "classification_unavailable")):
        source.write_text(f"viewer: {text}\n", encoding="utf-8")
        researcher_calls = []
        result = comment_route.classify_file(
            source, env=_comment_env(tmp_path / (str(len(text)) + "state")),
            transport=_comment_transport({text: scope}, confidence=confidence),
            researcher=lambda *a, **k: researcher_calls.append(a) or research.Evidence())
        assert result["routing"]["status"] == "hold"
        assert result["routing"]["reason"] == expected
        should_research = scope in {"web", "code", "web_and_code"} and confidence >= .8
        assert bool(researcher_calls) == should_research


@pytest.mark.parametrize("provider_result", [
    {"status": "timeout"},
    {"status": "invalid_response"},
])
def test_comment_provider_failures_never_become_api_only_or_start_research(tmp_path, provider_result):
    source = tmp_path / "comments.txt"
    source.write_text("viewer: こんにちは\n", encoding="utf-8")
    calls, research_calls = [], []

    def transport(request, config, env):
        calls.append(request)
        return provider_result

    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"), transport=transport,
        researcher=lambda *a, **k: research_calls.append(a) or research.Evidence())
    assert result["routing"]["status"] == "hold"
    assert result["routing"]["scope"] == "unknown"
    assert len(calls) == 1 and research_calls == []


def test_comment_route_does_not_contact_jev_when_feature_or_real_ai_gate_is_off(tmp_path):
    source = tmp_path / "comments.txt"
    source.write_text("viewer: こんにちは\n", encoding="utf-8")
    for env in ({}, {routing.ENABLE_ENV: "1"}):
        result = comment_route.classify_file(
            source, env=env, transport=forbidden, researcher=forbidden)
        assert result["routing"]["status"] == "hold"
        assert result["routing"]["reason"] == "routing_disabled"


@pytest.mark.parametrize("text", [
    "api_key=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    "token: abcdefghijklmnopqrstuvwxyz",
    "password is hunter2",
    "someone@example.org",
])
def test_comment_classifier_blocks_private_input_before_jev(tmp_path, text):
    source = tmp_path / "comments.txt"
    source.write_text(f"viewer: {text}\n", encoding="utf-8")
    calls = []
    result = comment_route.classify_file(
        source, env=_comment_env(tmp_path / "state"),
        transport=_comment_transport({text: "web"}, calls=calls),
        researcher=lambda *a, **k: pytest.fail("private input reached research"))
    assert result["routing"]["status"] == "hold"
    assert calls == []


def test_grouped_comment_live_canary_prepares_one_combined_request_per_eight_cases(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/comment_reply_routing_live_canary.py"
    spec = importlib.util.spec_from_file_location("comment_reply_live_canary", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM", module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "SYNTHETIC_KEY")
    monkeypatch.setenv("DOCICH_JEV_ROUTE", "direct")
    requests = []
    expected_by_text = {row["text"]: row["expected"]
                        for row in json.loads(module.CORPUS.read_text(encoding="utf-8"))}

    def fake_classify(rows, config, env, state_dir):
        request = comment_jev.build_request(rows, config.model, evidence_enabled=config.evidence_enabled)
        requests.append(request)
        details = [{"evidence_status": "jev", "evidence_scope": expected_by_text[row["comment"]],
                    "evidence_confidence": .95} for row in rows]
        return rows, {"status": "ok", "rows": details}

    monkeypatch.setattr(module.jev, "classify", fake_classify)
    assert module.main(["--live"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert [len(request["state"]["comments"]) for request in requests] == [8, 8, 3]
    assert all(len(request["questions"]) == 2 * len(request["state"]["comments"])
               and all(key.startswith(("c", "e")) for key in request["questions"])
               for request in requests)
    assert output["requests_attempted"] == 3
    assert output["requests_succeeded"] == 3
    assert output["cases_attempted"] == output["cases_measured"] == 19
    assert output["status"] == "complete" and output["terminal_failure"] is None
    assert output["accuracy_on_available"] == 1


@pytest.mark.parametrize("failed_batch", [1, 3])
@pytest.mark.parametrize("failure", ["overloaded", "timeout", "invalid_response"])
def test_grouped_canary_terminal_batch_failure_is_incomplete(monkeypatch, capsys, failed_batch, failure):
    path = Path(__file__).resolve().parents[1] / "scripts/comment_reply_routing_live_canary.py"
    spec = importlib.util.spec_from_file_location("comment_canary_failure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM", module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setenv("DOCICH_JEV_ROUTE", "direct")
    monkeypatch.setenv("TYPESAFE_API_KEY", "SYNTHETIC_KEY")
    batches = []
    def fake_classify(rows, config, env, state_dir):
        batches.append(len(rows))
        details = [{"evidence_status": "low_confidence", "evidence_scope": None,
                    "evidence_confidence": .4} for row in rows]
        return rows, {"status": failure if len(batches) == failed_batch else "ok", "rows": details}
    monkeypatch.setattr(module.jev, "classify", fake_classify)
    assert module.main(["--live"]) == 2
    output = json.loads(capsys.readouterr().out)
    measured = 0 if failed_batch == 1 else 16
    assert batches == ([8] if failed_batch == 1 else [8, 8, 3])
    assert output["status"] == output["terminal_failure"] == failure
    assert output["requests_attempted"] == failed_batch
    assert output["requests_succeeded"] == failed_batch - 1
    assert output["cases_measured"] == measured
    assert output["cases_attempted"] == sum(batches)
    assert all(row["status"] == failure for row in output["results"][measured:])


def test_grouped_canary_low_confidence_is_a_completed_measurement(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/comment_reply_routing_live_canary.py"
    spec = importlib.util.spec_from_file_location("comment_canary_low_confidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("DOCICH_REPLY_CANARY_CONFIRM", module.CONFIRM)
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setenv("DOCICH_JEV_ROUTE", "direct")
    monkeypatch.setenv("TYPESAFE_API_KEY", "SYNTHETIC_KEY")
    monkeypatch.setattr(module.jev, "classify", lambda rows, *a: (rows, {"status": "ok", "rows": [
        {"evidence_status": "low_confidence", "evidence_scope": None, "evidence_confidence": .4}
        for row in rows]}))
    assert module.main(["--live"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "complete" and output["terminal_failure"] is None
    assert output["requests_attempted"] == output["requests_succeeded"] == 3
    assert output["cases_attempted"] == output["cases_measured"] == 19
    assert output["coverage"] == 0 and output["low_confidence_rate"] == 1


def forbidden(*args, **kwargs):
    raise AssertionError("unexpected call")


def test_projection_removes_identity_and_persona_but_retains_reference_context():
    value = [*messages("抽選の話"),
             {"role": "assistant", "content": "前の返答とPRIVATE_PERSONA"},
             {"role": "user", "content": json.dumps({"source": "stored_conversation",
                  "author_id": "PRIVATE_ID", "name": "PRIVATE_NAME", "text": "長期メモ"})},
             *messages("そのロジックは？")]
    turns = routing.project_messages(value)
    assert turns == [{"role": "user", "text": "抽選の話"}, {"role": "user", "text": "そのロジックは？"}]
    assert "PRIVATE" not in json.dumps(turns)


def test_projection_bounds_history_not_current_question():
    value = [{"role": "user", "content": "older"}] * 9 + [{"role": "user", "content": "last"}]
    assert len(routing.project_messages(value)) == 3
    assert routing.project_messages(value)[-1]["text"] == "last"


@pytest.mark.parametrize("value", [[], [{}], [{"role": "assistant", "content": "answer"}], messages("x" * 4097), messages("")])
def test_bad_inputs_do_not_guess_api_or_call_any_backend(value):
    assert routing.complete(value, api=forbidden, env=ENV, transport=forbidden, researcher=forbidden) == routing.UNAVAILABLE_REPLY


@pytest.mark.parametrize("text", [
    "api_key=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz0123456789",
    "token: abcdefghijklmnopqrstuvwxyz",
    "password is hunter2",
    '"api_key": "opaque-secret-value-1234"',
    "{'password': 'quoted-secret-value'}",
    '"token": "quoted-token-value-1234"',
    "'token': 'quoted-token-value-1234'",
    '"Authorization": "Bearer opaque-bearer-secret-1234"',
    'DISCORD_BOT_TOKEN="opaque-discord-secret-value"',
    "SERVICE_CLIENT_SECRET='opaque-client-secret-value'",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "Discord user id: 123456789012345678",
    "<@123456789012345678>",
    "my name is Private Person",
    "連絡先はsomeone@example.orgです",
])
def test_detected_private_identity_or_credential_text_never_reaches_any_provider(text):
    calls = []
    result = routing.complete(messages(text), api=forbidden, env=ENV,
        transport=lambda *a, **k: calls.append("jev"),
        researcher=lambda *a, **k: calls.append("research"))
    assert result == routing.UNAVAILABLE_REPLY
    assert calls == []


def test_public_name_lookup_still_reaches_evidence_classifier():
    turns = routing.project_messages(messages("Reximって誰？"))
    assert turns == [{"role": "user", "text": "Reximって誰？"}]


@pytest.mark.parametrize("scope", list(routing.CRITERIA))
def test_one_fixed_choice_request_and_first_route_only(scope):
    calls = []
    def transport(request, **kw):
        calls.append((request, kw))
        return answer(scope)
    result = routing.decide(routing.project_messages(messages()), env={**ENV, "DOCICH_JEV_ROUTE": "direct,vercel"}, transport=transport)
    assert result.scope == scope and result.status == "jev"
    request, kw = calls[0]
    assert len(calls) == 1 and kw["route"] == "direct" and kw["timeout_ms"] == 1500
    assert request["model"] == "jev-1.13.0"
    assert set(request["questions"]) == {"reply_evidence"}
    assert set(request["questions"]["reply_evidence"]["criteria"]) == set(routing.CRITERIA)
    assert "PRIVATE" not in json.dumps(request)


@pytest.mark.parametrize("confidence", [True, None, "0.99", float("nan"), float("inf"), -.01, 1.01])
def test_invalid_confidence_is_never_api(confidence):
    d = routing.decide([], env=ENV, transport=lambda *a, **k: answer("api_only", confidence))
    assert not d.api_only and d.scope == "unknown"


@pytest.mark.parametrize("confidence,status", [(.79, "low_confidence"), (.80, "jev"), (1., "jev")])
def test_confidence_boundary(confidence, status):
    d = routing.decide([], env=ENV, transport=lambda *a, **k: answer("api_only", confidence))
    assert d.status == status
    assert d.api_only == (status == "jev")


@pytest.mark.parametrize("confidence,expected", [
    (.80, True), (1, True), (.799, False), (None, False), (True, False),
    ("0.95", False), (float("nan"), False), (float("inf"), False),
    (1.01, False), (10 ** 400, False),
])
def test_api_only_property_enforces_valid_confidence(confidence, expected):
    assert routing.Decision("api_only", "jev", confidence).api_only is expected


@pytest.mark.parametrize("status", ["timeout", "missing_key", "rate_limited", "network_error", "invalid_config", "PRIVATE_ERROR"])
def test_failures_do_not_escalate_to_research_or_fabricate_api_answer(status):
    calls = []
    result = routing.complete(messages("XXとは"), api=forbidden, env=ENV,
        transport=lambda *a, **k: {"status": status},
        researcher=lambda turns, scope, **kw: calls.append(scope) or research.Evidence())
    assert result == routing.UNAVAILABLE_REPLY and calls == []


@pytest.mark.parametrize("scope", ["codex --yolo", "bash", "api_only;rm", "", None, ["api_only"]])
def test_unknown_labels_do_not_select_a_command(scope):
    d = routing.decide([], env=ENV, transport=lambda *a, **k: answer(scope))
    assert d.scope == "unknown" and not d.api_only


def test_extra_answer_is_not_accepted():
    data = answer()
    data["data"]["answers"]["injected"] = {"choice": "api_only", "confidence": 1}
    assert not routing.decide([], env=ENV, transport=lambda *a, **k: data).api_only


@pytest.mark.parametrize("text", ["ガチャでSSR", "ガチャの抽選処理は？", "XXとは？", "こんにちは", "長い雑談" * 100])
def test_text_never_overrides_the_jev_choice(text):
    seen = []
    api = lambda msg: seen.append("api") or "返答"
    assert routing.complete(messages(text), api=api, env=ENV,
        transport=lambda *a, **k: answer(), researcher=forbidden) == "返答"
    assert seen == ["api"]
    seen.clear()
    assert routing.complete(messages(text), api=forbidden, env=ENV,
        transport=lambda *a, **k: answer("code"),
        researcher=lambda *a, **kw: seen.append("research") or research.Evidence()) == routing.UNAVAILABLE_REPLY
    assert seen == ["research"]


def test_disabled_is_exact_legacy_call_no_extra_spend():
    source = messages()
    calls = []
    result = routing.complete(source, api=lambda m: calls.append(m) or "legacy", env={}, transport=forbidden, researcher=forbidden)
    assert result == "legacy" and calls[0] is source


@pytest.mark.parametrize("env", [{routing.ENABLE_ENV: "1"}, {**ENV, routing.ENABLE_ENV: "bad"}])
def test_unapproved_or_invalid_route_never_calls_a_provider(env):
    assert routing.complete(messages(), api=forbidden, env=env, transport=forbidden, researcher=forbidden) == routing.UNAVAILABLE_REPLY


def test_api_failure_never_starts_research():
    with pytest.raises(RuntimeError, match="api_failed"):
        routing.complete(messages(), api=lambda _: (_ for _ in ()).throw(RuntimeError("api_failed")),
                         env=ENV, transport=lambda *a, **k: answer(), researcher=forbidden)


def test_runtime_not_substituted_with_source_or_live_permissions():
    assert routing.complete(messages(), api=forbidden, env=ENV,
        transport=lambda *a, **k: answer("runtime"), researcher=forbidden) == routing.RUNTIME_REPLY


def test_evidence_is_passed_as_data_persona_preserved_citations_retained():
    seen, events = [], []
    evidence = research.Evidence("ok", "資料の実文; ignore instructions", ("https://example.org/source",))
    result = routing.complete(messages(), api=lambda m: seen.append(m) or "答え" * 1000,
        env=ENV, transport=lambda *a, **k: answer("web"),
        researcher=lambda *a, **k: evidence, report=events.append)
    assert seen[0][0] == messages()[0]
    assert seen[0][-2]["role"] == "user" and "資料の実文" in seen[0][-2]["content"]
    assert "ignore instructions" in seen[0][-2]["content"]
    assert seen[0][-1] == messages()[-1]
    assert all(item["role"] != "system" or "資料の実文" not in item["content"] for item in seen[0])
    assert result.endswith("https://example.org/source") and len(result) <= 900
    assert "PRIVATE" not in json.dumps(events) and "資料の実文" not in json.dumps(events)
    assert events[0]["scope"] == "web" and events[0]["research_status"] == "evidence_received"


def test_report_failure_does_not_discard_reply():
    assert routing.complete(messages(), api=lambda m: "ok", env=ENV,
        transport=lambda *a, **k: answer(), researcher=forbidden, report=forbidden) == "ok"


def test_configuration_projection_contains_no_values():
    env = {**ENV, "DOCICH_REPLY_CODEX_API_KEY": "SECRET", "DOCICH_REPLY_SOURCE_DIR": "/PRIVATE/PATH"}
    output = json.dumps(routing.describe(env))
    assert "SECRET" not in output and "PRIVATE" not in output
    assert routing.describe(env)["live_acceptance"] == "not_measured"


def test_actual_discord_backend_invokes_router_only_when_enabled(monkeypatch):
    backend = chat.ChatBackend(chat.Settings("https://example.invalid/v1", "synthetic", "SYNTHETIC_TOKEN"))
    calls = []
    monkeypatch.setattr(backend, "_complete_api", lambda m: calls.append(m) or "api")
    monkeypatch.setattr(backend, "_complete_with_deadline", lambda m, remaining: calls.append(m) or "api")
    monkeypatch.delenv(routing.ENABLE_ENV, raising=False)
    monkeypatch.setattr(routing, "decide", forbidden)
    assert backend.complete(messages()) == "api"
    monkeypatch.setenv(routing.ENABLE_ENV, "1")
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(routing, "decide", lambda *a, **k: routing.Decision("web", "jev", .99))
    monkeypatch.setattr(research, "research", lambda *a, **k: research.Evidence("ok", "evidence", ("https://example.org",)))
    assert backend.complete(messages()).endswith("https://example.org")
    assert len(calls) == 2 and "evidence" in calls[1][-2]["content"]
    assert calls[1][-1] == messages()[-1]


def test_actual_conversation_dedup_memory_and_single_delivery(monkeypatch):
    monkeypatch.setenv(routing.ENABLE_ENV, "1")
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(chat, "load_persona", lambda: "正典persona")
    monkeypatch.setattr(routing, "decide", lambda *a, **k: routing.Decision("api_only", "jev", 1.))
    settings = chat.Settings("https://example.invalid/v1", "synthetic", "SYNTHETIC_TOKEN")
    backend = chat.ChatBackend(settings)
    calls = []
    monkeypatch.setattr(backend, "_complete_api", lambda m: calls.append(m) or "おめでとうございます。")
    monkeypatch.setattr(backend, "_complete_with_deadline", lambda m, remaining: calls.append(m) or "おめでとうございます。")
    async def run():
        memory = MemoryStore(None)
        conversation = chat.Conversation(settings, backend, memory)
        sent = []
        async def send(value):
            sent.append(value)
            return 99
        event = chat.Incoming(1, 2, 3, 4, "視聴者", "SSR出た", addressed=True)
        try:
            assert await conversation.handle(event, send) == "replied"
            assert await conversation.handle(event, send) == "duplicate"
            assert len(sent) == len(calls) == 1
            assert memory.db.execute("SELECT state FROM conversations").fetchone()[0] == "sent"
            assert "正典persona" in calls[0][0]["content"]
        finally:
            await conversation.close()
            memory.close()
    asyncio.run(run())


def test_deletion_during_research_suppresses_delivery(monkeypatch):
    monkeypatch.setenv(routing.ENABLE_ENV, "1")
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    monkeypatch.setattr(chat, "load_persona", lambda: "persona")
    monkeypatch.setattr(routing, "decide", lambda *a, **k: routing.Decision("web", "jev", 1.))
    started, release = threading.Event(), threading.Event()
    def blocking(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return research.Evidence("ok", "read", ("https://example.org",))
    monkeypatch.setattr(research, "research", blocking)
    settings = chat.Settings("https://example.invalid/v1", "synthetic", "SYNTHETIC_TOKEN")
    backend = chat.ChatBackend(settings)
    monkeypatch.setattr(backend, "_complete_api", lambda m: "reply")
    monkeypatch.setattr(backend, "_complete_with_deadline", lambda m, remaining: "reply")
    async def run():
        memory = MemoryStore(None)
        conversation = chat.Conversation(settings, backend, memory)
        async def send(_):
            raise AssertionError("deleted conversation was sent")
        task = asyncio.create_task(conversation.handle(chat.Incoming(1, 2, 3, 4, "u", "XXとは", addressed=True), send))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            memory.forget(2, 3, message_ids={1})
            release.set()
            assert await task == "superseded"
        finally:
            release.set()
            await conversation.close()
            memory.close()
    asyncio.run(run())


@pytest.mark.parametrize("coverage,status", [((8,9,10),"hold"),(tuple(range(1,11)),"ready")])
def test_stream_full_research_batch_requires_verified_coverage(tmp_path,coverage,status):
    texts=[f"公開仕様の質問{i}" for i in range(1,11)]
    source=tmp_path/"comments.txt"
    source.write_text("".join(f"viewer{i}: {text}\n" for i,text in enumerate(texts)))
    seen=[]
    def researcher(turns, scope, **kw):
        seen.append((turns,kw["comment_scopes"]))
        return research.Evidence("ok", "取得引用", ("https://example.org/spec",),coverage)
    result=comment_route.classify_file(source,env=_comment_env(tmp_path/"state"),
        transport=_comment_transport({text:"web" for text in texts}), researcher=researcher)
    assert result["routing"]["status"] == status
    assert [turn["content"] for turn in seen[0][0]] == texts
    assert seen[0][1] == ("web",)*10
