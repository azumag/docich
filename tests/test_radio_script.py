import hashlib
import json
from pathlib import Path

import pytest

from docich.llm.contracts import DispatchResult, ProviderResult
from docich.discord_chat import ChatBackend, DIRECT_CHAT_MODELS, Settings, direct_chat_options
from docich.radio import parser, script
from docich.web_material import VerifiedWebBundle, VerifiedWebMaterial

AGENT = "openrouter-api:openai/gpt-4.1-nano"
BODY = "今日は、架空の街での散歩を楽しむお話です。景色を眺めながら、ゆっくり歩いてみましょう。" * 4
OUTPUT = "ON_AIR_SCRIPT_START\n" + BODY + "\n===SUMMARY===\n架空の散歩です。"
ENV = {"DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "1",
       "DOCICH_RADIO_RESEARCH_ROUTING_ENABLED": "1", "DOCICH_ALLOW_REAL_AI": "1"}
GOLDEN = json.loads((Path(__file__).parent / "fixtures/radio_parser_golden.json").read_text())


def decision(scope="web", confidence=.95):
    return {"status": "ok", "data": {"answers": {
        "radio_evidence": {"choice": scope, "confidence": confidence}}}}


def material(excerpt="Public source body only.", indexes=(0,)):
    sha = hashlib.sha256(excerpt.encode()).hexdigest()
    return VerifiedWebMaterial("https://example.com/source", sha, sha, sha, excerpt, indexes)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["name"])
def test_required_parser_exact_reference_parity(case):
    if case["returncode"]:
        with pytest.raises(parser.RadioParseError, match=case["error"]):
            parser.parse_script(case["raw"])
    else:
        parsed = parser.parse_script(case["raw"])
        assert (parsed.body, parsed.summary, parsed.selected_news) == (
            case["body"], case["summary"], case["selected_news"])


@pytest.mark.parametrize("raw", [None, 1, "", "\ud800", "x" * 65537])
def test_parser_bounds_and_fixed_failure(raw):
    with pytest.raises(parser.RadioParseError) as caught:
        parser.parse_script(raw)
    assert str(caught.value) in {"invalid_output", "output_limit"}


def fail_call(*args, **kwargs):
    pytest.fail("disabled/hold path must not invoke IO")


@pytest.mark.parametrize("env,expected", [
    ({}, "disabled"),
    ({**ENV, "DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "0"}, "disabled"),
    ({**ENV, "DOCICH_RADIO_SCRIPT_DIRECT_ENABLED": "true"}, "invalid_config"),
    ({**ENV, "DOCICH_ALLOW_REAL_AI": "0"}, "invalid_config"),
    ({**ENV, "DOCICH_RADIO_RESEARCH_ROUTING_ENABLED": "0"}, "disabled"),
])
def test_disabled_zero_all_calls(env, expected):
    result = script.generate_script("topic", ["topic"], agents=AGENT, env=env,
                                    transport=fail_call, material_collector=fail_call,
                                    generator=fail_call)
    assert result.status == expected and not result.ok


@pytest.mark.parametrize("agents", ["", "local:gemma4:12b", "opencode-go:test",
    "openrouter:openai/gpt-4.1-nano", "codex:foo", AGENT + ",local:gemma4",
    AGENT + ",opencode:foo", "openrouter-api:openai/gpt-4.1-nano:online",
    "vercel-api:vmc/example", "openrouter-api:openrouter/auto", ",".join([AGENT] * 9)])
def test_direct_concrete_chain_only_before_classifier(agents):
    result = script.generate_script("topic", ["topic"], agents=agents, env=ENV,
                                    transport=fail_call, material_collector=fail_call,
                                    generator=fail_call)
    assert result.status == "invalid_input"


@pytest.mark.parametrize("topic", ["", None, "x" * 4097,
    "secret: synthetic-sentinel", "OPENROUTER_API_KEY=sentinel-secret"])
def test_invalid_or_private_topic_zero_calls(topic):
    result = script.generate_script(topic, ["topic"], agents=AGENT, env=ENV,
                                    transport=fail_call, material_collector=fail_call,
                                    generator=fail_call)
    assert result.status == "invalid_input"


@pytest.mark.parametrize("response,expected", [
    (decision(confidence=.79), "low_confidence"),
    (decision("unknown"), "invalid_response"),
    ({"status": "timeout"}, "timeout"),
    ({"status": "missing_key"}, "missing_key"),
])
def test_uncertain_classifier_holds_without_web_or_generation(response, expected):
    seen = []
    def transport(request, **kwargs):
        seen.append(request)
        return response
    result = script.generate_script("Public topic", ["caller query"], agents=AGENT,
        env=ENV, transport=transport, material_collector=fail_call, generator=fail_call)
    assert result.status == expected and len(seen) == 1 and not result.ok


def test_api_only_skips_collection_and_uses_existing_envelope():
    seen = []
    def generator(g, **kwargs):
        seen.append(kwargs)
        return DispatchResult(0, output=OUTPUT)
    result = script.generate_script("架空の散歩のつなぎ話", [], agents=AGENT, env=ENV,
        transport=lambda *a, **kw: decision("api_only"), material_collector=fail_call,
        generator=generator)
    assert result.ok and result.scope == "api_only" and not result.materials
    assert result.script.body == BODY and result.script.summary == "架空の散歩です。"
    prompt = seen[0]["prompt_text"]
    assert json.loads(prompt.split("INPUT_JSON:\n", 1)[1])["materials"] == []
    assert seen[0]["label"] == "RADIO:native-script" and seen[0]["agents"] == AGENT
    assert 0 < seen[0]["timeout_sec"] <= 45
    assert "backups" not in repr(result) and BODY not in repr(result)


def test_verified_web_input_never_uses_model_query_or_search_snippet():
    seen = {}
    verified = material("FETCHED public body, unrelated to search snippet.")
    def transport(request, **kwargs):
        assert request["state"] == {"topic": "Public topic"}
        return decision()
    def collector(queries, **kwargs):
        assert queries == ("caller query",)
        seen["collector_budget"] = kwargs["timeout_sec"]
        return VerifiedWebBundle("ok", (verified,), queries)
    def generator(g, **kwargs):
        seen["data"] = json.loads(kwargs["prompt_text"].split("INPUT_JSON:\n", 1)[1])
        return DispatchResult(0, output=OUTPUT)
    result = script.generate_script("Public topic", [" caller  query "], agents=AGENT,
        env=ENV, transport=transport, material_collector=collector, generator=generator)
    assert result.ok and result.materials == (verified,)
    assert seen["data"]["materials"] == [verified.wire()]
    assert seen["data"]["material_status"] == "ok"
    assert "snippet" not in seen["data"] and "provider" not in seen["data"]
    assert 0 < seen["collector_budget"] < 45


def test_partial_coverage_stays_partial_in_generated_result():
    verified = material()
    seen = []
    def generator(g, **kwargs):
        seen.append(json.loads(kwargs["prompt_text"].split("INPUT_JSON:\n", 1)[1]))
        return DispatchResult(0, output=OUTPUT)
    result = script.generate_script("Public topic", ["first", "second"], agents=AGENT,
        env=ENV, transport=lambda *a, **kw: decision(),
        material_collector=lambda queries, **kw: VerifiedWebBundle("ok", (verified,), queries),
        generator=generator)
    assert result.ok and result.status == "partial"
    assert seen[0]["material_status"] == "partial"


@pytest.mark.parametrize("excerpt", ["x" * 8192, '"\\\n' * 2000, "界" * 2700])
def test_max_bundle_wire_limit_rejects_before_generation(excerpt):
    sha = hashlib.sha256(excerpt.encode()).hexdigest()
    sources = tuple(VerifiedWebMaterial(f"https://example.com/source{i}", sha, sha, sha,
                                       excerpt, (0,)) for i in range(4))
    result = script.generate_script("Public topic", ["caller query"], agents=AGENT,
        env=ENV, transport=lambda *a, **kw: decision(),
        material_collector=lambda queries, **kw: VerifiedWebBundle("ok", sources, queries),
        generator=fail_call)
    assert result.status == "input_limit" and not result.ok


@pytest.mark.parametrize("provider", sorted(DIRECT_CHAT_MODELS))
def test_provider_wrappers_fit_reserved_metadata_for_every_registered_model(provider):
    upstream = "x" * 64 if provider != "cloudflare" else ""
    base = {"openrouter": "https://openrouter.ai/api/v1",
            "vercel": "https://ai-gateway.vercel.sh/v1",
            "cloudflare": "https://api.cloudflare.com/client/v4/accounts/" + "a"*32 + "/ai/v1"}[provider]
    prompt = script._prompt("Public topic", "web", "ok", (material('"\\\n界' * 1000),))
    messages = [{"role": "user", "content": prompt}]
    projected_bytes = len(json.dumps({"messages": messages}, ensure_ascii=False).encode())
    for model in DIRECT_CHAT_MODELS[provider]:
        settings = Settings(base, model, "", api_key="SYNTHETIC_ONLY", provider=provider,
                            upstream=upstream, billing_mode="credits_only" if upstream else "")
        options = direct_chat_options(settings)
        wire = json.dumps({"model": model, "messages": messages, "stream": False,
                           "max_tokens": 500, **options}, ensure_ascii=False).encode()
        assert len(wire) - projected_bytes <= script.REQUEST_METADATA_RESERVE
        assert len(wire) <= script.MAX_DIRECT_REQUEST_BYTES


@pytest.mark.parametrize("bundle", [VerifiedWebBundle("unavailable"),
    {"snippet": "unverified"}, VerifiedWebBundle("ok", (material(),), ("wrong",)),
    VerifiedWebBundle("ok", (VerifiedWebMaterial("https://example.com/source", "a"*64,
        "b"*64, "c"*64, "forged body", (0,)),), ("caller query",))])
def test_missing_unverified_or_wrong_query_material_never_generates(bundle):
    result = script.generate_script("Public topic", ["caller query"], agents=AGENT,
        env=ENV, transport=lambda *a, **kw: decision(),
        material_collector=lambda *a, **kw: bundle, generator=fail_call)
    assert result.status == "material_unavailable" and not result.ok


@pytest.mark.parametrize("raw", ["plain body only", "ON_AIR_SCRIPT_START\n短いお話です。\n===SUMMARY===\n短文。",
    OUTPUT.replace("===SUMMARY===", "===OTHER==="),
    OUTPUT.replace(BODY, "not Japanese. " * 12),
    OUTPUT.replace(BODY, BODY.rstrip("。")),
    OUTPUT.replace(BODY, "<tool_call>hidden command</tool_call>\n" + BODY),
    OUTPUT.replace(BODY, "authentication_error\n" + BODY),
    OUTPUT.replace("架空の散歩です。", ""), "x" * 65537,
    "<final>" + OUTPUT + "</final>" + "outside" * 10000,
    "ON_AIR_SCRIPT_START\n===SUMMARY===\n" + BODY,
    "ON_AIR_SCRIPT_START\n===OTHER===\n===SUMMARY===\n" + BODY])
def test_invalid_generated_envelope_never_returns_script(raw):
    result = script.generate_script("Public topic", [], agents=AGENT, env=ENV,
        transport=lambda *a, **kw: decision("api_only"),
        material_collector=fail_call, generator=lambda *a, **kw: DispatchResult(0, output=raw))
    assert not result.ok and result.script is None and result.status == "invalid_script"


@pytest.mark.parametrize("output", [DispatchResult(79, failure_kind="rate_limit"),
    DispatchResult(1, detail="secret raw stderr"), "raw untyped output", None])
def test_generation_failure_fixed_status_without_detail(output):
    result = script.generate_script("Public topic", [], agents=AGENT, env=ENV,
        transport=lambda *a, **kw: decision("api_only"),
        generator=lambda *a, **kw: output)
    assert result.status == "generation_failed" and result.script is None
    assert "secret" not in repr(result)


def test_generation_exception_is_sanitized():
    def generator(*args, **kwargs):
        raise RuntimeError("secret raw exception")
    result = script.generate_script("Public topic", [], agents=AGENT, env=ENV,
        transport=lambda *a, **kw: decision("api_only"), generator=generator)
    assert result.status == "generation_failed" and "secret" not in repr(result)


class Clock:
    value = 0.
    def __call__(self):
        return self.value


def test_shared_deadline_passes_only_remaining_budget_and_rejects_late_script():
    clock = Clock()
    def transport(*args, **kwargs):
        clock.value = 1.
        return decision()
    def collector(queries, **kwargs):
        assert kwargs["timeout_sec"] == 4.
        clock.value = 3.
        return VerifiedWebBundle("ok", (material(),), queries)
    def generator(g, **kwargs):
        assert kwargs["timeout_sec"] == 2.
        clock.value = 5.
        return DispatchResult(0, output=OUTPUT)
    result = script.generate_script("Public topic", ["caller query"], agents=AGENT,
        env=ENV, transport=transport, material_collector=collector, generator=generator,
        timeout_sec=5., clock=clock)
    assert result.status == "timeout" and result.script is None


@pytest.mark.parametrize("budget", [0, -1, True, float("nan"), float("inf"), 45.1])
def test_invalid_budget_zero_calls(budget):
    result = script.generate_script("Public topic", [], agents=AGENT, env=ENV,
        transport=fail_call, generator=fail_call, timeout_sec=budget)
    assert result.status == "invalid_config"


def test_default_native_dispatch_with_explicit_direct_fallback_and_no_cli(tmp_path, monkeypatch):
    calls = []
    def provider(spec, request, *, timeout, env):
        calls.append(spec.raw)
        assert spec.provider in {"openrouter-api", "vercel-api"}
        assert request.label == script.LABEL and "INPUT_JSON:" in request.prompt
        assert 0 < timeout <= 2
        if len(calls) == 1:
            return ProviderResult(1, failure_kind="provider_failed")
        return ProviderResult(0, output=OUTPUT)
    monkeypatch.setattr("docich.llm.dispatch.call_agent", provider)
    env = {**ENV, "DOCICH_LLM_STATE_DIR": str(tmp_path / "llm"),
           "DOCICH_LLM_STATS_DIR": str(tmp_path / "stats"),
           "AI_GENERATION_QUEUE_ENABLED": "0", "AI_RADIO_IMPROVE_GATE": "0"}
    chain = AGENT + ",vercel-api:openai/gpt-4.1-nano"
    result = script.generate_script("Public topic", [], agents=chain, env=env,
        transport=lambda *a, **kw: decision("api_only"), material_collector=fail_call,
        timeout_sec=2.)
    assert result.ok and calls == chain.split(",")
