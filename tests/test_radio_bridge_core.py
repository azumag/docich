"""Bridge -> #1852 core -> guarded script, with synthetic API transports only."""
from functools import partial
import hashlib
import io
import json

import pytest

from docich.llm.contracts import DispatchResult
from docich.radio import bridge, parser, script
from docich.web_material import VerifiedWebBundle, VerifiedWebMaterial

ENV = {"DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "1",
       "DOCICH_RADIO_RESEARCH_ROUTING_ENABLED": "1", "DOCICH_ALLOW_REAL_AI": "1"}
AGENT = "openrouter-api:openai/gpt-4.1-nano"
BODY = "今日は、架空の街での散歩を楽しむお話です。景色を眺めながら、ゆっくり歩いてみましょう。" * 4
OUTPUT = "ON_AIR_SCRIPT_START\n" + BODY + "\n===SUMMARY===\n架空の散歩です。"


def invoke(monkeypatch, *, scope="web", queries=None, confidence=.95, agents=AGENT):
    calls = {"classify": 0, "collect": 0, "generate": 0}
    excerpt = "Synthetic public source body."
    sha = hashlib.sha256(excerpt.encode()).hexdigest()
    material = VerifiedWebMaterial("https://example.com/source", sha, sha, sha, excerpt, (0,))
    def transport(request, **kwargs):
        calls["classify"] += 1
        return {"status": "ok", "data": {"answers": {
            "radio_evidence": {"choice": scope, "confidence": confidence}}}}
    def collect(normalized_queries, **kwargs):
        calls["collect"] += 1
        return VerifiedWebBundle("ok", (material,), normalized_queries)
    def generate(_config, **kwargs):
        calls["generate"] += 1
        assert kwargs["label"] == "RADIO:native-script"
        assert kwargs["agents"] == AGENT
        assert 0 < kwargs["timeout_sec"] <= 45
        return DispatchResult(0, output=OUTPUT)
    # Keep the real planner, verification, direct allowlist and script parser.
    monkeypatch.setattr(script, "generate_script", partial(
        script.generate_script, transport=transport,
        material_collector=collect, generator=generate))
    payload = {"schema": bridge.REQUEST_SCHEMA, "request_id": "synthetic-core-01",
               "topic": "架空の街を歩くつなぎ話", "queries": ["caller query"] if queries is None else queries}
    out = io.BytesIO()
    rc = bridge.main(["--agents", agents], stdin=io.BytesIO(json.dumps(payload).encode()),
                     stdout=out, env=ENV)
    return rc, json.loads(out.getvalue()), calls, material


def test_default_bridge_calls_native_core_and_preserves_legacy_parser_contract(monkeypatch):
    rc, reply, calls, material = invoke(monkeypatch)
    assert rc == 0 and reply["status"] == "ok" and reply["scope"] == "web"
    assert calls == {"classify": 1, "collect": 1, "generate": 1}
    assert reply["materials"] == [material.wire()]
    assert reply["script"]["body"] == BODY
    frame = "ON_AIR_SCRIPT_START\n" + reply["script"]["body"] + "\n===SUMMARY===\n" + reply["script"]["summary"]
    parsed = parser.parse_script(frame)
    assert parsed.body == BODY and parsed.summary == reply["script"]["summary"]
    assert reply["delivery"] == "not_requested"


def test_api_only_bridge_never_collects(monkeypatch):
    rc, reply, calls, _ = invoke(monkeypatch, scope="api_only", queries=[])
    assert rc == 0 and reply["scope"] == "api_only" and reply["materials"] == []
    assert calls == {"classify": 1, "collect": 0, "generate": 1}


def test_partial_wire_result_is_not_promoted_to_complete_or_delivered(monkeypatch):
    rc, reply, calls, material = invoke(monkeypatch, queries=["first", "second"])
    assert rc == 0 and reply["status"] == "partial"
    assert reply["materials"] == [material.wire()]
    assert reply["delivery"] == "not_requested"
    assert calls == {"classify": 1, "collect": 1, "generate": 1}


def test_low_confidence_emits_no_script_or_material(monkeypatch):
    rc, reply, calls, _ = invoke(monkeypatch, confidence=.79)
    assert rc == 3 and reply["status"] == "low_confidence"
    assert reply["script"] is None and reply["materials"] == []
    assert calls == {"classify": 1, "collect": 0, "generate": 0}


@pytest.mark.parametrize("agents", ["opencode:example", AGENT + ",local:example"])
def test_bridge_cannot_inherit_a_legacy_or_mixed_fallback(monkeypatch, agents):
    rc, reply, calls, _ = invoke(monkeypatch, agents=agents)
    assert rc == 3 and reply["status"] == "invalid_input"
    assert reply["script"] is None
    assert calls == {"classify": 0, "collect": 0, "generate": 0}


def test_malformed_success_from_core_is_not_a_success_receipt(monkeypatch):
    monkeypatch.setattr(script, "generate_script",
                        lambda *a, **kw: script.ScriptResult("ok", "web"))
    payload = {"schema": bridge.REQUEST_SCHEMA, "request_id": "synthetic-core-02",
               "topic": "Public topic", "queries": ["caller query"]}
    reply = bridge.execute(json.dumps(payload).encode(), agents=AGENT, env=ENV)
    assert reply["status"] == "invalid_response"
    assert reply["script"] is None and reply["delivery"] == "not_requested"
