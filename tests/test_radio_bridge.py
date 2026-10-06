"""Offline wire/launcher contracts; no JEV, Web, LLM, TTS or production queue."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from docich.radio import bridge

ENV = {
    "DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "1",
    "DOCICH_RADIO_RESEARCH_ROUTING_ENABLED": "1",
    "DOCICH_ALLOW_REAL_AI": "1",
}
AGENT = "openrouter-api:openai/gpt-4.1-nano"
REQUEST = {
    "schema": bridge.REQUEST_SCHEMA, "request_id": "radio-test_01",
    "topic": "工学の公開情報", "queries": ["工学 公開資料"],
}


def raw(obj=REQUEST):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def no_prepare(*args, **kwargs):
    pytest.fail("prepare must not run")


@pytest.mark.parametrize("changes,status", [
    ({"DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "0"}, "disabled"),
    ({"DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "true"}, "invalid_config"),
    ({"DOCICH_RADIO_RESEARCH_ROUTING_ENABLED": "0"}, "invalid_config"),
    ({"DOCICH_ALLOW_REAL_AI": "0"}, "invalid_config"),
])
def test_gates_before_prepare_and_stdin(monkeypatch, changes, status):
    monkeypatch.setattr(bridge, "_prepare", no_prepare)
    class NoRead:
        def read(self, _size):
            pytest.fail("stdin must not be read")
    out = io.BytesIO()
    assert bridge.main(["--agents", AGENT], stdin=NoRead(), stdout=out,
                       env=ENV | changes) == 3
    response = json.loads(out.getvalue())
    assert response["status"] == status
    assert response["script"] is None and response["materials"] == []
    assert response["delivery"] == "not_requested"


@pytest.mark.parametrize("payload", [
    b"", b"null", b"[]", b"{}", b"true", b"\xff", b"{", b"x" * 16385,
    raw({**REQUEST, "extra": "ignored?"}),
    raw({**REQUEST, "provider": "cloudflare"}),
    raw({**REQUEST, "env": {"DOCICH_ALLOW_REAL_AI": "1"}}),
    raw({**REQUEST, "queue": "/tmp/live"}),
    raw({**REQUEST, "topic": ""}), raw({**REQUEST, "topic": 7}),
    json.dumps({**REQUEST, "topic": "\ud800"}).encode(),
    raw({**REQUEST, "queries": "not-a-list"}),
    raw({**REQUEST, "queries": [None]}),
    raw({**REQUEST, "request_id": "../state"}),
    raw({**REQUEST, "request_id": "a" * 81}),
    raw({**REQUEST, "schema": "v2"}),
    raw()[:-1] + b',"topic":"duplicate"}',
    raw()[:-1] + b',"query":NaN}',
    b"[" * 1500 + b"]" * 1500,
])
def test_invalid_request_never_prepares(monkeypatch, payload):
    monkeypatch.setattr(bridge, "_prepare", no_prepare)
    reply = bridge.execute(payload, agents=AGENT, env=ENV)
    assert reply["status"] == "invalid_input"
    assert reply["script"] is None and reply["materials"] == []


@pytest.mark.parametrize("status", ["ok", "partial"])
def test_caller_projection_and_verified_material_preserved(monkeypatch, status):
    calls = []
    material = {"url": "https://example.org/source", "excerpt": "検証済みの例",
                "receipt_id": "a" * 64, "text_sha256": "b" * 64,
                "excerpt_sha256": "c" * 64, "query_indexes": [0]}
    def prepare(topic, queries, **kwargs):
        calls.append((topic, queries, kwargs))
        result = bridge._response(status, scope="web")
        result["script"] = {"body": "読み上げ本文", "summary": "要約", "selected_news": ""}
        result["materials"] = [material]
        return result
    monkeypatch.setattr(bridge, "_prepare", prepare)
    out = io.BytesIO()
    assert bridge.main(["--agents", AGENT], stdin=io.BytesIO(raw()), stdout=out, env=ENV) == 0
    reply = json.loads(out.getvalue())
    assert len(calls) == 1
    assert calls[0] == (REQUEST["topic"], REQUEST["queries"], {"agents": AGENT, "env": ENV})
    assert reply["request_id"] == REQUEST["request_id"]
    assert reply["status"] == status
    assert reply["materials"] == [material]
    assert reply["delivery"] == "not_requested"
    assert "queued" not in reply and "played" not in reply


def test_exception_and_incidental_diagnostics_never_leak(monkeypatch, capsys):
    secret = "synthetic-provider-secret"
    def prepare(*args, **kwargs):
        print(secret)
        print(secret, file=sys.stderr)
        raise RuntimeError(secret)
    monkeypatch.setattr(bridge, "_prepare", prepare)
    out = io.BytesIO()
    assert bridge.main(["--agents", AGENT], stdin=io.BytesIO(raw()), stdout=out, env=ENV) == 3
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err + out.getvalue().decode()
    reply = json.loads(out.getvalue())
    assert reply["status"] == "generation_failed"
    assert reply["request_id"] == REQUEST["request_id"]
    assert reply["script"] is None


def test_serialized_reply_bound_discards_entire_payload(monkeypatch):
    def prepare(*args, **kwargs):
        result = bridge._response("ok", scope="web")
        result["script"] = {"body": "あ" * bridge.MAX_OUTPUT_BYTES}
        result["materials"] = [{"excerpt": "must not survive"}]
        return result
    monkeypatch.setattr(bridge, "_prepare", prepare)
    reply = bridge.execute(raw(), agents=AGENT, env=ENV)
    assert reply["status"] == "input_limit"
    assert reply["script"] is None and reply["materials"] == []
    assert len(bridge._encode(reply)) < bridge.MAX_OUTPUT_BYTES


@pytest.mark.parametrize("argv", [[], ["--agents"], ["--agents", ""],
    ["--provider", "private-value"], ["--agents", AGENT, "payload"]])
def test_cli_arguments_are_fixed_and_redacted(monkeypatch, argv):
    monkeypatch.setattr(bridge, "_prepare", no_prepare)
    out = io.BytesIO()
    assert bridge.main(argv, stdin=io.BytesIO(raw()), stdout=out, env=ENV) == 3
    assert json.loads(out.getvalue())["status"] == "invalid_input"
    assert "private-value" not in out.getvalue().decode()


def test_launcher_cannot_import_from_cwd_or_inherited_pythonpath(tmp_path):
    root = Path(__file__).resolve().parents[1]
    hostile = tmp_path / "hostile"
    (hostile / "docich" / "radio").mkdir(parents=True)
    (hostile / "docich" / "__init__.py").write_text("raise RuntimeError('SHADOWED')\n")
    (hostile / "docich" / "radio" / "bridge.py").write_text("raise RuntimeError('SHADOWED')\n")
    env = os.environ | {"PYTHONPATH": str(hostile), "DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "0"}
    result = subprocess.run([str(root / "bin/docich-radio-script")], cwd=hostile,
                            env=env, capture_output=True, timeout=5, check=False)
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "disabled"
    assert result.stderr == b""


def test_cli_read_is_bounded_and_failure_cannot_publish(monkeypatch):
    monkeypatch.setattr(bridge, "_prepare", no_prepare)
    class Input:
        def read(self, size):
            assert size == bridge.MAX_INPUT_BYTES + 1
            return b"x" * size
    out = io.BytesIO()
    assert bridge.main(["--agents", AGENT], stdin=Input(), stdout=out, env=ENV) == 3
    assert json.loads(out.getvalue())["delivery"] == "not_requested"
