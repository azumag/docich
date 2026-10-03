"""Regression tests for model-output JSON extraction (9/17 AI script outage)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ai_text import (  # noqa: E402
    AI_FAILURE_REASON_CODES,
    AiTextError,
    ai_failure_reason_code,
    extract_json_object,
    generate_text,
)
from docich.llm.contracts import DispatchResult, ProviderResult  # noqa: E402


def test_pretty_printed_json_with_raw_newlines_parses():
    raw = '{\n  "corner": "あいう\nえお",\n  "news": "x"\n}'
    data = extract_json_object(raw)
    assert data is not None
    assert "あいう" in data["corner"] and "えお" in data["corner"]
    assert data["news"] == "x"


def test_prose_and_fences_around_json_parse():
    raw = 'はい、台本です。\n```json\n{"corner": "a",\n"news": "b"}\n```\n以上です。'
    data = extract_json_object(raw)
    assert data == {"corner": "a", "news": "b"}


def test_compact_json_still_parses():
    assert extract_json_object('{"corner": "a"}') == {"corner": "a"}


def test_no_json_returns_none():
    assert extract_json_object("ただの文章です。") is None


def test_truncated_json_returns_none():
    assert extract_json_object('{"corner": "a", "news":') is None


def test_non_dict_json_returns_none():
    assert extract_json_object("[1, 2, 3]") is None


def _patch_dispatch(monkeypatch, result: DispatchResult):
    import docich.ai_generate as ai_generate

    def _fake_dispatch(g, *, label, agents, prompt_text, timeout, timeout_sec, env=None):
        return result

    monkeypatch.setattr(ai_generate, "run_prompt", _fake_dispatch)


def test_generate_text_gate_disabled_kind(monkeypatch):
    monkeypatch.delenv("DOCICH_ALLOW_REAL_AI", raising=False)
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "gate-disabled"


def test_generate_text_rc_failure_reports_upstream_failure_kind(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_dispatch(monkeypatch, DispatchResult(1, failure_kind="rate_limit"))
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "rc-1:rate_limit"


def test_generate_text_rc_failure_without_upstream_kind_is_unclassified(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_dispatch(monkeypatch, DispatchResult(1))
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "rc-1:unclassified"


def test_generate_text_empty_output_kind(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_dispatch(monkeypatch, DispatchResult(0, output="   "))
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "empty-output"


def test_generate_text_success_returns_stripped_output(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_dispatch(monkeypatch, DispatchResult(0, output="  hello  \n"))
    assert generate_text(None, label="RADIO:x", agents="a", prompt_text="p") == "hello"


def test_generate_text_explicit_env_carries_gate_without_touching_process_env(monkeypatch):
    import os
    import docich.ai_generate as ai_generate

    monkeypatch.delenv("DOCICH_ALLOW_REAL_AI", raising=False)
    seen = {}

    def _fake_dispatch(g, *, label, agents, prompt_text, timeout, timeout_sec, env=None):
        seen["env"] = env
        return DispatchResult(0, output="hello")

    monkeypatch.setattr(ai_generate, "run_prompt", _fake_dispatch)

    env = {**os.environ, "DOCICH_ALLOW_REAL_AI": "1"}
    assert generate_text(None, label="RADIO:x", agents="a", prompt_text="p", env=env) == "hello"
    assert seen["env"]["DOCICH_ALLOW_REAL_AI"] == "1"
    assert "DOCICH_ALLOW_REAL_AI" not in os.environ


def test_generate_text_default_total_budget_is_compatible(monkeypatch):
    import docich.ai_generate as ai_generate

    seen = {}

    def dispatch(g, **kwargs):
        seen.update(kwargs)
        return DispatchResult(0, output="answer")

    monkeypatch.setattr(ai_generate, "run_prompt", dispatch)
    assert generate_text(
        None, label="RADIO:x", agents="codex", prompt_text="p", timeout=240,
        env={"DOCICH_ALLOW_REAL_AI": "1"},
    ) == "answer"
    assert seen["timeout"] == 240
    assert seen["timeout_sec"] == 300.0


@pytest.mark.parametrize("overall", [0, -1, float("nan"), float("inf"), float("-inf"), True, "660", {}, 10**400])
def test_generate_text_rejects_invalid_total_budget_before_dispatch(monkeypatch, overall):
    import docich.ai_generate as ai_generate

    def forbidden(*args, **kwargs):
        pytest.fail("invalid total budget reached dispatch")

    monkeypatch.setattr(ai_generate, "run_prompt", forbidden)
    with pytest.raises(AiTextError) as raised:
        generate_text(
            None, label="RADIO:x", agents="codex", prompt_text="p",
            timeout=180, overall_timeout_s=overall,
            env={"DOCICH_ALLOW_REAL_AI": "1"},
        )
    assert raised.value.kind == "invalid-timeout"


def _controlled_chain(monkeypatch, tmp_path, *, succeed_third):
    import docich.llm.dispatch as native_dispatch

    clock = [0.0]
    calls = []

    def provider(spec, request, *, timeout, env):
        calls.append((spec.raw, timeout))
        if succeed_third and len(calls) == 3:
            clock[0] += 1.0
            return ProviderResult(0, output="fallback answer")
        clock[0] += timeout
        return ProviderResult(124, failure_kind="timeout")

    monkeypatch.setattr(native_dispatch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(native_dispatch, "call_agent", provider)
    env = {
        "DOCICH_ALLOW_REAL_AI": "1",
        "DOCICH_LLM_STATE_DIR": str(tmp_path / "state"),
        "AI_GENERATION_QUEUE_ENABLED": "0",
        "OPENCODE_RUN_LOCK_ENABLED": "0",
        "AI_RADIO_IMPROVE_GATE": "0",
    }
    return env, calls


def test_generate_text_separate_budget_reaches_fallback_after_two_timeouts(monkeypatch, tmp_path):
    env, calls = _controlled_chain(monkeypatch, tmp_path, succeed_third=True)
    output = generate_text(
        None, label="RADIO:paper-improve",
        agents="codex:first,codex:second,codex:third", prompt_text="p",
        timeout=180, overall_timeout_s=660, env=env,
    )
    assert output == "fallback answer"
    assert calls == [("codex:first", 180.0), ("codex:second", 180.0), ("codex:third", 180.0)]


def test_generate_text_total_budget_still_bounds_the_whole_chain(monkeypatch, tmp_path):
    env, calls = _controlled_chain(monkeypatch, tmp_path, succeed_third=False)
    with pytest.raises(AiTextError) as raised:
        generate_text(
            None, label="RADIO:paper-improve",
            agents="codex:first,codex:second,codex:third,codex:fourth", prompt_text="p",
            timeout=180, overall_timeout_s=400, env=env,
        )
    assert calls == [("codex:first", 180.0), ("codex:second", 180.0), ("codex:third", 40.0)]
    assert raised.value.kind == "rc-124:timeout"
    assert ai_failure_reason_code(raised.value.kind) == "timeout"


@pytest.mark.parametrize(("kind", "expected"), [
    ("rc-124:timeout", "timeout"),
    ("rc-79:rate_limit", "rate-limit"),
    ("rc-91:gate_giveup", "gate-giveup"),
    ("rc-92:queue_giveup", "queue-giveup"),
    ("rc-1:empty_output", "empty-output"),
    ("rc-1:validator_failed", "invalid-output"),
    ("rc--15:provider_failed", "provider-failed"),
    ("rc-1:unclassified", "provider-failed"),
    ("rc-1:transport_error", "provider-failed"),
    ("gate-disabled", "gate-disabled"),
    ("invocation-error", "invocation-error"),
    ("rc-0:timeout", "unknown"),
    ("rc-124:timeout\nSECRET", "unknown"),
    ("rc-124:timeout?token=SECRET", "unknown"),
    ("rc-1:secret_model_name", "unknown"),
    ("rc-99999:timeout", "unknown"),
    ("prefix:rc-124:timeout", "unknown"),
    (None, "unknown"),
    ({"kind": "timeout"}, "unknown"),
])
def test_ai_failure_reason_code_is_a_fixed_projection(kind, expected):
    reason = ai_failure_reason_code(kind)
    assert reason == expected
    assert reason in AI_FAILURE_REASON_CODES
