import json
from pathlib import Path
import stat
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.llm.contracts import DispatchRequest  # noqa: E402
from docich.llm.policy import parse_agents  # noqa: E402
from docich.llm.providers import call_agent  # noqa: E402


def _request(spec):
    return DispatchRequest(label="COMMENT:test", prompt="safe prompt", agents=(spec,))


def _script(root: Path, body: str) -> Path:
    path = root / "provider.py"
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_codex_spec_is_rejected_before_any_process():
    import pytest
    from docich.llm.contracts import AgentSpec, LlmError
    with pytest.raises(LlmError): parse_agents("codex:fixture")
    spec = AgentSpec(raw="codex:fixture", provider="codex", model="fixture")
    with mock.patch("docich.llm.providers._process") as process:
        assert call_agent(spec, _request(spec), timeout=5, env={}).failure_kind == "provider_removed"
        process.assert_not_called()


def test_opencode_retries_transient_failure_but_returns_clean_output(tmp_path):
    count_file = tmp_path / "count"
    script = _script(
        tmp_path,
        """
import os, pathlib, sys
path = pathlib.Path(os.environ['COUNT_FILE'])
count = int(path.read_text() or '0') + 1 if path.exists() else 1
path.write_text(str(count))
if count == 1:
    sys.stderr.write('temporary provider failure')
    raise SystemExit(1)
print('<analysis>hidden</analysis><final>usable</final>')
""",
    )
    spec = parse_agents("opencode:fixture")[0]
    env = {
        "OPENCODE_BIN": str(script),
        "COUNT_FILE": str(count_file),
        "OPENCODE_ABORT_RETRY": "1",
        "OPENCODE_ABORT_RETRY_WAIT_SEC": "0",
    }
    result = call_agent(spec, _request(spec), timeout=5, env=env)

    assert result.returncode == 0
    assert result.output == "usable"
    assert count_file.read_text(encoding="utf-8") == "2"


def test_opencode_uses_fixed_session_title_without_persisting_raw_label():
    spec = parse_agents("opencode:fixture")[0]
    request = DispatchRequest(
        label="COMMENT:private-topic",
        prompt="safe prompt",
        agents=(spec,),
    )
    with mock.patch(
        "docich.llm.providers._process",
        return_value=(0, "<final>usable</final>", ""),
    ) as process:
        result = call_agent(
            spec,
            request,
            timeout=5,
            env={"OPENCODE_BIN": "/tmp/fake-opencode", "OPENCODE_ABORT_RETRY": "0"},
        )

    assert result.returncode == 0
    command = process.call_args.args[0]
    title_index = command.index("--title")
    assert command[title_index + 1] == "docich:comment"
    assert "COMMENT:private-topic" not in command
    assert command[-1] == "safe prompt"


def test_opencode_session_title_buckets_are_fixed():
    from docich.llm.providers import _opencode_session_title

    cases = {
        "COMMENT:user-provided": "docich:comment",
        "IMPROVEMENT:secret": "docich:improvement",
        "PROBE:slot-1": "docich:probe",
        "RADIO:RESEARCH:topic": "docich:radio_prepass",
        "JIJI:news": "docich:radio_main",
        "UNKNOWN:private": "docich:other",
    }
    for label, expected in cases.items():
        assert _opencode_session_title(label) == expected


def test_provider_rate_limit_is_normalized_to_rc_79(tmp_path):
    script = _script(
        tmp_path,
        """
import sys
sys.stderr.write('429 rate limit exceeded')
raise SystemExit(7)
""",
    )
    spec = parse_agents("opencode:fixture")[0]
    result = call_agent(
        spec,
        _request(spec),
        timeout=5,
        env={"OPENCODE_BIN": str(script), "OPENCODE_ABORT_RETRY": "1"},
    )

    assert result.returncode == 79
    assert result.failure_kind == "rate_limit"
    assert result.detail == "rate_limit"


def test_local_adapter_posts_fixed_openai_compatible_schema_without_proxy():
    spec = parse_agents("local:fixture")[0]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit):
            return b'{"choices":[{"message":{"content":"local answer"}}]}'

    class Opener:
        def __init__(self):
            self.request = None

        def open(self, request, timeout):
            self.request = request
            assert timeout == 5
            return Response()

    opener = Opener()
    with mock.patch("docich.llm.providers.build_opener", return_value=opener):
        result = call_agent(
            spec,
            _request(spec),
            timeout=5,
            env={"LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434"},
        )

    assert result.returncode == 0
    assert result.output == "local answer"
    payload = json.loads(opener.request.data.decode("utf-8"))
    assert payload["model"] == "fixture"
    assert payload["messages"] == [{"role": "user", "content": "safe prompt"}]
    assert payload["stream"] is False
    assert opener.request.full_url == "http://127.0.0.1:11434/v1/chat/completions"
