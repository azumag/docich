import io
import json
import sys

import pytest

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


# These exercise only the process boundary. The script core has its own suite;
# no real classifier, collector, provider, speech or queue is invoked here.


@pytest.mark.parametrize("raw", [
    '{"topic":"first","topic":"second","queries":[],"agents":"a"}',
    '{"topic":"x","queries":[],"queries":["x"],"agents":"a"}',
    '{"topic":"x","queries":[],"agents":"first","agents":"second"}',
    '{"topic":NaN,"topic":"x","queries":[],"agents":"a"}',
    '{"topic":"x","queries":[Infinity],"agents":"a"}',
    '{"topic":"x","queries":[],"agents":-Infinity}',
    '{"topic":"\\ud800","queries":[],"agents":"a"}',
    '{"topic":"x","queries":["\\udfff"],"agents":"a"}',
    '{"topic":"x","queries":[],"agents":"\\ud800"}',
    '{"topic":"x","queries":' + '[' * 1500 + '0' + ']' * 1500 + ',"agents":"a"}',
])
def test_strict_request_rejected_before_core(monkeypatch, raw):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        return ScriptResult("disabled")

    monkeypatch.setattr(consumer, "generate_script", forbidden)
    stdout = io.StringIO()
    rc = consumer.main([], stdin=io.StringIO(raw), stdout=stdout, env={})
    assert rc == 2
    assert calls == []
    assert json.loads(stdout.getvalue()) == {"status": "invalid_request", "scope": "unknown"}


def test_binary_input_is_strict_utf8_even_with_replace_text_wrapper(monkeypatch):
    calls = []
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: calls.append(1) or ScriptResult("disabled"))
    raw = b'{"topic":"bad\xff","queries":[],"agents":"a"}'
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace")
    stdout = io.StringIO()
    rc = consumer.main([], stdin=stdin, stdout=stdout, env={})
    assert rc == 2
    assert calls == []
    assert json.loads(stdout.getvalue())["status"] == "invalid_request"


def test_binary_read_is_byte_bounded(monkeypatch):
    reads = []

    class Input(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    raw = request(topic="界" * consumer.MAX_REQUEST_BYTES).encode("utf-8")
    stdin = io.TextIOWrapper(Input(raw), encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: pytest.fail("core called"))
    assert consumer.main([], stdin=stdin, stdout=stdout, env={}) == 2
    assert reads == [consumer.MAX_REQUEST_BYTES + 1]


@pytest.mark.parametrize("extra", [0, 1])
def test_exact_request_utf8_byte_limit(monkeypatch, extra):
    raw = request(topic="界")
    raw += " " * (consumer.MAX_REQUEST_BYTES - len(raw.encode("utf-8")) + extra)
    calls = []
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: calls.append(1) or ScriptResult("disabled"))
    stdout = io.StringIO()
    rc = consumer.main([], stdin=io.StringIO(raw), stdout=stdout, env={})
    assert rc == (2 if extra else 0)
    assert calls == ([] if extra else [1])


@pytest.mark.parametrize("status", ["disabled", "low_confidence", "timeout", "generation_failed"])
def test_typed_failures_never_return_script_or_retry(monkeypatch, status):
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return ScriptResult(status, "web", ParsedScript("must not be spoken", "must not be stored"))

    monkeypatch.setattr(consumer, "generate_script", generate)
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 0
    assert json.loads(stdout.getvalue()) == {"status": status, "scope": "web"}
    assert calls == [1]


@pytest.mark.parametrize("status,scope", [("ok", "api_only"), ("ok", "web"), ("partial", "web")])
def test_success_envelope_and_selection_are_unchanged(monkeypatch, status, scope):
    script = ParsedScript("公開トピックを紹介します。" * 12, "要約です。", "選択ニュース")
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: ScriptResult(status, scope, script))
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 0
    assert json.loads(stdout.getvalue()) == {
        "status": status, "scope": scope, "body": script.body,
        "summary": script.summary, "selected_news": script.selected_news,
    }


@pytest.mark.parametrize("script", [None, ParsedScript("", "summary"), ParsedScript("body", ""), ParsedScript("body", "summary", None)])
def test_malformed_success_cannot_cross_process_boundary(monkeypatch, script):
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: ScriptResult("ok", "web", script))
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 1
    assert json.loads(stdout.getvalue()) == {"status": "bridge_error", "scope": "unknown"}


@pytest.mark.parametrize("extra", [0, 1])
def test_response_limit_counts_json_escaping_and_newline(monkeypatch, extra):
    limit = getattr(consumer, "MAX_RESPONSE_BYTES", 131072)
    body = '界"\\\n' * 20
    payload = {"status": "ok", "scope": "web", "body": body, "summary": "summary", "selected_news": ""}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    body += "x" * (limit - len(encoded.encode("utf-8")) + extra)
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: ScriptResult("ok", "web", ParsedScript(body, "summary")))
    stdout = io.StringIO()
    rc = consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={})
    if extra:
        assert rc == 1
        assert json.loads(stdout.getvalue()) == {"status": "bridge_error", "scope": "unknown"}
    else:
        assert rc == 0
        assert len(stdout.getvalue().encode("utf-8")) == limit
        assert json.loads(stdout.getvalue())["body"] == body


def test_unencodable_output_becomes_fixed_error(monkeypatch):
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: ScriptResult("ok", "web", ParsedScript("bad\ud800", "summary")))
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 1
    assert json.loads(stdout.getvalue()) == {"status": "bridge_error", "scope": "unknown"}


def test_python_diagnostics_do_not_corrupt_protocol_or_leak(monkeypatch, capsys):
    def noisy(*args, **kwargs):
        print("synthetic-secret stdout")
        print("synthetic-secret stderr", file=sys.stderr)
        raise RuntimeError("synthetic-secret exception")

    monkeypatch.setattr(consumer, "generate_script", noisy)
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 1
    assert json.loads(stdout.getvalue()) == {"status": "bridge_error", "scope": "unknown"}
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert "synthetic-secret" not in stdout.getvalue()


def test_read_failure_is_fixed_error(monkeypatch):
    class BrokenInput:
        def read(self, size):
            raise OSError("synthetic-secret read error")

    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: pytest.fail("core called"))
    stdout = io.StringIO()
    assert consumer.main([], stdin=BrokenInput(), stdout=stdout, env={}) == 1
    assert json.loads(stdout.getvalue()) == {"status": "bridge_error", "scope": "unknown"}


@pytest.mark.parametrize("method", ["write", "flush"])
def test_output_failure_returns_nonzero_without_exception(monkeypatch, method):
    class BrokenOutput(io.StringIO):
        def write(self, text):
            if method == "write":
                raise BrokenPipeError("synthetic-secret output error")
            return super().write(text)

        def flush(self):
            if method == "flush":
                raise OSError("synthetic-secret flush error")
            return super().flush()

    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: ScriptResult("disabled"))
    assert consumer.main([], stdin=io.StringIO(request()), stdout=BrokenOutput(), env={}) == 1


@pytest.mark.parametrize("result", [
    None,
    ScriptResult("raw provider failure: synthetic-secret"),
    ScriptResult("disabled", "synthetic-secret"),
    ScriptResult("ok", "unknown", ParsedScript("body", "summary")),
])
def test_invalid_result_metadata_is_fixed_error(monkeypatch, result):
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: result)
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 1
    assert json.loads(stdout.getvalue()) == {"status": "bridge_error", "scope": "unknown"}


def test_real_output_is_utf8_independent_of_text_wrapper_encoding(monkeypatch):
    script = ParsedScript("公開トピックです。" * 12, "要約です。")
    monkeypatch.setattr(consumer, "generate_script", lambda *a, **k: ScriptResult("ok", "web", script))
    buffer = io.BytesIO()
    stdout = io.TextIOWrapper(buffer, encoding="ascii")
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 0
    assert json.loads(buffer.getvalue().decode("utf-8"))["body"] == script.body


def test_success_discards_incidental_prints_and_restores_streams(monkeypatch, capsys):
    previous = sys.stdout, sys.stderr

    def noisy(*args, **kwargs):
        print("synthetic-secret stdout")
        print("synthetic-secret stderr", file=sys.stderr)
        return ScriptResult("ok", "web", ParsedScript("body", "summary"))

    monkeypatch.setattr(consumer, "generate_script", noisy)
    stdout = io.StringIO()
    assert consumer.main([], stdin=io.StringIO(request()), stdout=stdout, env={}) == 0
    assert json.loads(stdout.getvalue())["body"] == "body"
    assert (sys.stdout, sys.stderr) == previous
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
