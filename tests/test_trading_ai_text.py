"""Regression tests for model-output JSON extraction (9/17 AI script outage)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ai_text import AiTextError, extract_json_object, generate_text  # noqa: E402
from docich.llm.contracts import DispatchResult  # noqa: E402


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

    def _fake_dispatch(g, *, label, agents, prompt_text, timeout, timeout_sec):
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
