"""Allowlisted provider adapters for the first native migration step."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .contracts import AgentSpec, DispatchRequest, ProviderResult


RATE_LIMIT_RE = re.compile(
    r"(?:rate[ -]?limit|too many requests|429|quota|resource[ -]?exhausted|usage[ -]?limit)",
    re.IGNORECASE,
)
PROVIDER_ERROR_RE = re.compile(
    r"(?:authentication failed|unauthorized|forbidden|invalid api key|provider error|api key required)",
    re.IGNORECASE,
)
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024


def _rate_limited(text: str) -> bool:
    return bool(RATE_LIMIT_RE.search(text or ""))


def _clean_model_output(text: str) -> str:
    import re as _re

    value = str(text or "")
    for tag in ("think", "analysis"):
        value = _re.sub(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", "", value, flags=_re.I | _re.S)
        value = _re.sub(rf"<{tag}\b[^>]*>.*\Z", "", value, flags=_re.I | _re.S)
    value = _re.sub(r"</?(?:final|assistant_response)\b[^>]*>", "", value, flags=_re.I)
    return value.strip()


def _process(
    command: list[str], *, timeout: float, env: dict[str, str], capture_stdout: bool = True
) -> tuple[int, str, str]:
    """Run one fixed argv without a shell and reap the whole process group.

    Temporary files keep a provider that emits an unexpectedly large response
    from consuming unbounded Python heap memory. Callers still enforce the
    one-megabyte model-output contract after reading a bounded prefix.
    """

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file if capture_stdout else subprocess.DEVNULL,
            stderr=stderr_file,
            env={**os.environ, **env},
            start_new_session=True,
        )

        timed_out = False
        try:
            process.communicate(timeout=max(float(timeout), 0.1))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.kill()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
                process.communicate()

        def read_prefix(handle, limit: int) -> str:
            handle.seek(0)
            return handle.read(limit + 1).decode("utf-8", "replace")

        stdout = read_prefix(stdout_file, MAX_OUTPUT_BYTES) if capture_stdout else ""
        stderr = read_prefix(stderr_file, MAX_STDERR_BYTES)
        if timed_out:
            return 124, stdout, stderr
        return process.returncode, stdout, stderr


def _failed(returncode: int, stderr: str = "", *, detail: str = "provider_failed") -> ProviderResult:
    if returncode == 124:
        return ProviderResult(124, failure_kind="timeout", detail="timeout")
    if _rate_limited(stderr) or returncode == 429:
        return ProviderResult(79, failure_kind="rate_limit", detail="rate_limit")
    return ProviderResult(returncode or 1, failure_kind="provider_failed", detail=detail)


def _retry_wait(env: dict[str, str]) -> float:
    try:
        value = float(env.get("OPENCODE_ABORT_RETRY_WAIT_SEC", "2") or 2)
    except (TypeError, ValueError):
        return 2.0
    return value if value >= 0 else 2.0


def _codex(spec: AgentSpec, request: DispatchRequest, timeout: float, env: dict[str, str]) -> ProviderResult:
    binary = env.get("CODEX_BIN", "codex")
    model = spec.model or env.get("CODEX_MODEL", "amd-token-factory-deepseek-v4-flash")
    with tempfile.TemporaryDirectory(prefix="docich-llm-codex-") as tmp:
        output_file = Path(tmp) / "output.txt"
        command = [
            binary,
            "exec",
            "--skip-git-repo-check",
            "-m",
            model,
            "-o",
            str(output_file),
            request.prompt,
        ]
        rc, stdout, stderr = _process(command, timeout=timeout, env=env)
        if rc != 0:
            return _failed(rc, stderr)
        try:
            output = output_file.read_text(encoding="utf-8")
        except OSError:
            output = stdout
        output = _clean_model_output(output)
        if not output:
            return ProviderResult(1, failure_kind="empty_output", detail="empty_output")
        if _rate_limited(output):
            return ProviderResult(79, failure_kind="rate_limit", detail="rate_limit")
        if PROVIDER_ERROR_RE.search(output):
            return ProviderResult(1, failure_kind="provider_failed", detail="provider_error")
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return ProviderResult(1, failure_kind="output_too_large", detail="output_too_large")
        return ProviderResult(0, output=output)


def _opencode_model(spec: AgentSpec) -> str:
    if spec.provider == "openrouter":
        return f"openrouter/{spec.model}"
    if spec.provider == "vercel":
        return f"vercel/{spec.model}"
    if spec.provider == "amd":
        return f"amd-token-factory/{spec.model}"
    if spec.provider == "opencode-go":
        return f"opencode-go/{spec.model}"
    return f"opencode/{spec.model}"


def _opencode(spec: AgentSpec, request: DispatchRequest, timeout: float, env: dict[str, str]) -> ProviderResult:
    binary = env.get("OPENCODE_BIN") or (
        "/snap/bin/opencode" if Path("/snap/bin/opencode").is_file() else "opencode"
    )
    base_command = [binary, "run"]
    if spec.provider in {"vercel", "amd"}:
        role = "soren-research" if "RESEARCH" in request.label.upper() or "PREPASS" in request.label.upper() else "soren-lite"
        base_command += ["--agent", role]
    base_command += ["--model", _opencode_model(spec), request.prompt]
    attempts = 2 if env.get("OPENCODE_ABORT_RETRY", "1") == "1" and spec.provider != "vercel" else 1
    started = time.monotonic()
    last_rc, last_stdout, last_stderr = 1, "", ""
    for attempt in range(attempts):
        remaining = float(timeout) - (time.monotonic() - started)
        if remaining <= 0:
            return ProviderResult(124, failure_kind="timeout", detail="timeout")
        last_rc, last_stdout, last_stderr = _process(
            base_command, timeout=remaining, env=env
        )
        if last_rc == 124 or (last_rc != 0 and _rate_limited(last_stderr)):
            break
        if last_rc == 0:
            output = _clean_model_output(last_stdout)
            if output and _rate_limited(output):
                return ProviderResult(79, failure_kind="rate_limit", detail="rate_limit")
            if output and not PROVIDER_ERROR_RE.search(output):
                if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
                    return ProviderResult(1, failure_kind="output_too_large", detail="output_too_large")
                return ProviderResult(0, output=output)
        if attempt + 1 < attempts:
            wait_sec = _retry_wait(env)
            remaining = float(timeout) - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(wait_sec, remaining))
    if last_rc != 0:
        return _failed(last_rc, last_stderr)
    output = _clean_model_output(last_stdout)
    if not output:
        return ProviderResult(1, failure_kind="empty_output", detail="empty_output")
    if _rate_limited(output):
        return ProviderResult(79, failure_kind="rate_limit", detail="rate_limit")
    if PROVIDER_ERROR_RE.search(output):
        return ProviderResult(1, failure_kind="provider_failed", detail="provider_error")
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        return ProviderResult(1, failure_kind="output_too_large", detail="output_too_large")
    return ProviderResult(0, output=output)


def _local(spec: AgentSpec, request: DispatchRequest, timeout: float, env: dict[str, str]) -> ProviderResult:
    model = spec.model or env.get("LOCAL_LLM_MODEL", "gemma4:12b")
    base_url = env.get("LOCAL_LLM_BASE_URL", "http://100.112.104.102:11434").rstrip("/")
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": False,
            "temperature": 0.7,
            "num_predict": 1600,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(req, timeout=max(float(timeout), 0.1)) as response:
            raw = response.read(MAX_OUTPUT_BYTES + 1)
    except HTTPError as exc:
        return _failed(exc.code, "rate limit" if exc.code == 429 else "http error", detail="http_error")
    except (URLError, TimeoutError, OSError):
        return ProviderResult(1, failure_kind="provider_failed", detail="transport_error")
    if len(raw) > MAX_OUTPUT_BYTES:
        return ProviderResult(1, failure_kind="output_too_large", detail="output_too_large")
    try:
        data = json.loads(raw.decode("utf-8"))
        output = data["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ProviderResult(1, failure_kind="invalid_response", detail="invalid_response")
    output = _clean_model_output(output)
    if not output:
        return ProviderResult(1, failure_kind="empty_output", detail="empty_output")
    if PROVIDER_ERROR_RE.search(output):
        return ProviderResult(1, failure_kind="provider_failed", detail="provider_error")
    return ProviderResult(0, output=output)


def call_agent(
    spec: AgentSpec,
    request: DispatchRequest,
    *,
    timeout: float,
    env: dict[str, str],
) -> ProviderResult:
    if spec.provider == "codex":
        return _codex(spec, request, timeout, env)
    if spec.provider in {"amd", "openrouter", "opencode", "opencode-go", "vercel"}:
        return _opencode(spec, request, timeout, env)
    if spec.provider == "local":
        return _local(spec, request, timeout, env)
    return ProviderResult(2, failure_kind="invalid_provider", detail="invalid_provider")
