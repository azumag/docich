from pathlib import Path
import stat
import sys

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


def test_codex_success_body_rate_limit_is_rc_79(tmp_path):
    script = _script(
        tmp_path,
        """
import pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(args[args.index('-o') + 1])
out.write_text('Error: rate limit exceeded', encoding='utf-8')
""",
    )
    spec = parse_agents("codex:fixture")[0]
    result = call_agent(
        spec,
        _request(spec),
        timeout=5,
        env={"CODEX_BIN": str(script)},
    )

    assert result.returncode == 79
    assert result.failure_kind == "rate_limit"
    assert result.detail == "rate_limit"


def test_opencode_success_stdout_rate_limit_is_rc_79(tmp_path):
    script = _script(tmp_path, "print('429 Too Many Requests')\n")
    spec = parse_agents("opencode:fixture")[0]
    result = call_agent(
        spec,
        _request(spec),
        timeout=5,
        env={"OPENCODE_BIN": str(script), "OPENCODE_ABORT_RETRY": "0"},
    )

    assert result.returncode == 79
    assert result.failure_kind == "rate_limit"
    assert result.detail == "rate_limit"
