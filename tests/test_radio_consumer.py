import io
import json

from docich.radio import consumer
from docich.radio.parser import ParsedScript
from docich.radio.script import ScriptResult


def request(topic="公開トピック", queries=None, agents="openrouter-api:openai/gpt-4.1-nano"):
    return json.dumps({
        "topic": topic,
        "queries": ["公開トピック"] if queries is None else queries,
        "agents": agents,
    }, ensure_ascii=False)


def test_disabled_result_has_no_delivery_payload(monkeypatch):
    seen = {}

    def fake_generate(topic, queries, *, agents, env):
        seen.update(topic=topic, queries=queries, agents=agents, env=env)
        return ScriptResult("disabled")

    monkeypatch.setattr(consumer, "generate_script", fake_generate)
    payload = consumer.run_request(request(), env={"SAFE": "1"})

    assert payload == {"status": "disabled", "scope": "unknown"}
    assert seen == {
        "topic": "公開トピック",
        "queries": ("公開トピック",),
        "agents": "openrouter-api:openai/gpt-4.1-nano",
        "env": {"SAFE": "1"},
    }


def test_success_returns_only_typed_script_fields(monkeypatch):
    body = "これは公開素材に基づく十分な長さの日本語ラジオ本文です。" * 6
    script = ParsedScript(body, "要約です。", "ニュース題名")

    def fake_generate(*args, **kwargs):
        return ScriptResult("ok", "web", script)

    monkeypatch.setattr(consumer, "generate_script", fake_generate)
    payload = consumer.run_request(request(), env={})

    assert payload == {
        "status": "ok",
        "scope": "web",
        "body": body,
        "summary": "要約です。",
        "selected_news": "ニュース題名",
    }
    assert "materials" not in payload


def test_main_invalid_request_is_fixed_and_does_not_call_generator(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("generator must not be called")

    monkeypatch.setattr(consumer, "generate_script", fail)
    stdout = io.StringIO()
    rc = consumer.main(
        [],
        stdin=io.StringIO('{"topic":"x","queries":[],"agents":"a","secret":"no"}'),
        stdout=stdout,
        env={},
    )

    assert rc == 2
    assert json.loads(stdout.getvalue()) == {
        "status": "invalid_request",
        "scope": "unknown",
    }


def test_main_sanitizes_unexpected_exception(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("OPENROUTER_API_KEY=synthetic-secret")

    monkeypatch.setattr(consumer, "generate_script", explode)
    stdout = io.StringIO()
    rc = consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={})

    assert rc == 1
    assert json.loads(stdout.getvalue()) == {
        "status": "bridge_error",
        "scope": "unknown",
    }
    assert "synthetic-secret" not in stdout.getvalue()


def test_request_byte_limit_is_checked_before_generation(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("generator must not be called")

    monkeypatch.setattr(consumer, "generate_script", fail)
    oversized = request(topic="界" * consumer.MAX_REQUEST_BYTES)
    stdout = io.StringIO()
    rc = consumer.main([], stdin=io.StringIO(oversized), stdout=stdout, env={})

    assert rc == 2
    assert json.loads(stdout.getvalue())["status"] == "invalid_request"


def test_arguments_are_rejected_without_reading_request(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("generator must not be called")

    monkeypatch.setattr(consumer, "generate_script", fail)
    stdout = io.StringIO()
    rc = consumer.main(["--unexpected"], stdin=io.StringIO(request()), stdout=stdout, env={})

    assert rc == 2
    assert stdout.getvalue() == ""
