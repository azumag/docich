"""Regression tests for model-output JSON extraction (9/17 AI script outage)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ai_text import AiTextError, extract_json_object, generate_text  # noqa: E402


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


class _FakeInvocation:
    def __init__(self, kind_text: str = "", agent_text: str = ""):
        self.argv = ["true"]
        self.cwd = "."
        self.env = {}
        self._kind_text = kind_text
        self._agent_text = agent_text

    def write_sidecars(self, last_agent_file, failure_kind_file):
        if self._agent_text:
            Path(last_agent_file).write_text(self._agent_text, encoding="utf-8")
        if self._kind_text:
            Path(failure_kind_file).write_text(self._kind_text, encoding="utf-8")


def _patch_invocation(monkeypatch, fake: _FakeInvocation):
    import docich.ai_generate as ai_generate

    def _fake_build(g, *, game_name, label, agents, prompt_file, timeout,
                     last_agent_file=None, failure_kind_file=None):
        fake.write_sidecars(last_agent_file, failure_kind_file)
        return fake

    monkeypatch.setattr(ai_generate, "build_ai_invocation", _fake_build)


def _patch_run(monkeypatch, *, returncode: int, stdout: str = ""):
    import docich.procs as procs

    class _Completed:
        pass

    def _fake_run(argv, *, cwd, env_extra, timeout, capture):
        completed = _Completed()
        completed.returncode = returncode
        completed.stdout = stdout
        return completed

    monkeypatch.setattr(procs, "run", _fake_run)


def test_generate_text_gate_disabled_kind(monkeypatch):
    monkeypatch.delenv("DOCICH_ALLOW_REAL_AI", raising=False)
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "gate-disabled"


def test_generate_text_rc_failure_reports_upstream_failure_kind(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_invocation(monkeypatch, _FakeInvocation(kind_text="rate_limit"))
    _patch_run(monkeypatch, returncode=1, stdout="")
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "rc-1:rate_limit"


def test_generate_text_rc_failure_without_upstream_kind_is_unclassified(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_invocation(monkeypatch, _FakeInvocation())
    _patch_run(monkeypatch, returncode=1, stdout="")
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "rc-1:unclassified"


def test_generate_text_empty_output_kind(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_invocation(monkeypatch, _FakeInvocation())
    _patch_run(monkeypatch, returncode=0, stdout="   ")
    try:
        generate_text(None, label="RADIO:x", agents="a", prompt_text="p")
        assert False, "expected AiTextError"
    except AiTextError as exc:
        assert exc.kind == "empty-output"


def test_generate_text_success_returns_stripped_output(monkeypatch):
    monkeypatch.setenv("DOCICH_ALLOW_REAL_AI", "1")
    _patch_invocation(monkeypatch, _FakeInvocation())
    _patch_run(monkeypatch, returncode=0, stdout="  hello  \n")
    assert generate_text(None, label="RADIO:x", agents="a", prompt_text="p") == "hello"
