"""Shared provider runtime for the native LLM dispatch (#829 PR-1).

Every provider runs a fixed argv via :func:`run_fixed` — no shell, its own
process group, wall-timeout kill with descendant reaping.  Text classifiers
mirror the legacy ``_ai_rate_limit_text_detected`` /
``_contains_provider_error_text`` / ``_ai_strip_reasoning_blocks`` exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shutil
import signal
import subprocess

from ..contracts import RC_FAILED, RC_TIMEOUT

# Mirrors ``_ai_rate_limit_text_detected`` (ai_generate.sh).
_RATE_LIMIT_RE = re.compile(
    r"(^|[^0-9A-Za-z])429([^0-9A-Za-z]|$)"
    r"|too[\s_-]*many[\s_-]*requests"
    r"|rate[\s_-]*limit([\s_-]*(ed|exceeded))?"
    r"|quota[\s_-]*(exceeded|exhausted|limit)"
    r"|resource[\s_-]*exhausted"
    r"|usage[\s_-]*limit",
    re.IGNORECASE,
)

# Mirrors ``_contains_provider_error_text`` (core/helpers.sh).
_PROVIDER_ERROR_RE = re.compile(
    r"invalid bearer token|authentication_error|failed to authenticat(e|ed)"
    r"|api error[: ]|bad request|request_id|invalid error token|invalid token"
    r"|not logged in|please run /login|unexpected error, check log file"
    r"|failed to run the query|pragma wal_checkpoint|insufficient balance"
    r"|no resource package|rate limit exceeded|freeusagelimiterror"
    r"|degraded function cannot be invoked|function id .*degraded"
    r"|providermodelnotfounderror|model not found|no such model|modelid"
    r"|providerid|agent [\" ]*[^\" ]+[\" ]* not found"
    r"|free tier users do not have access to this model"
    r"|potentially unsafe or sensitive content"
    r"|avoid using prompts that may generate sensitive content"
    r"|unsafe or sensitive content in input or generation"
    r"|content policy|safety policy"
    r"|(^|[^0-9A-Za-z])error:[\s]*gone|status[\s]*:[\s]*410"
    r"|reached its end of life|is no longer available"
    r"|unknownerror|unexpected server error",
    re.IGNORECASE,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x0d\x0e-\x1f]")


@dataclass
class ProviderResult:
    rc: int
    stdout: str = ""
    stderr: str = ""
    resolved_model: str = ""
    timed_out: bool = False


def rate_limit_detected(text: str) -> bool:
    return bool(_RATE_LIMIT_RE.search(text or ""))


def provider_error_detected(text: str) -> bool:
    return bool(_PROVIDER_ERROR_RE.search(text or ""))


def clean_text(text: str) -> str:
    """Strip ANSI/control chars and reasoning blocks, like the legacy
    ``_strip_reasoning_blocks`` + ``_strip_ansi`` pipeline."""
    out = _ANSI_RE.sub("", text or "")
    out = _CONTROL_RE.sub("", out).replace("\r", "")
    for tag in ("think", "analysis"):
        out = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
            "",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
        out = re.sub(
            rf"<{tag}\b[^>]*>.*\Z", "", out, flags=re.IGNORECASE | re.DOTALL
        )
    for tag in ("final", "assistant_response"):
        out = re.sub(
            rf"</?{tag}\s*>", "", out, flags=re.IGNORECASE
        )
    return out.strip()


def resolve_binary(preferred: str, fallback: str) -> str | None:
    """Resolve a provider binary: preferred path, then fallback on PATH."""
    if preferred and os.path.isfile(preferred) and os.access(preferred, os.X_OK):
        return preferred
    if preferred and "/" not in preferred:
        found = shutil.which(preferred)
        if found:
            return found
    if fallback:
        if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
            return fallback
        found = shutil.which(fallback)
        if found:
            return found
    return None


def run_fixed(
    argv: list[str],
    timeout: int,
    stdin_data: bytes | None = None,
    env_extra: dict[str, str] | None = None,
) -> ProviderResult:
    """Run a fixed argv with a wall timeout.  No shell is ever involved.

    Timeout maps to rc 124 (internal marker); callers translate it to a
    generic provider failure unless rate-limit text is also present.
    """
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL if stdin_data is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return ProviderResult(rc=RC_FAILED, stderr="spawn_failed")
    try:
        try:
            out, err = proc.communicate(stdin_data, timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                out, err = proc.communicate(timeout=10)
            except Exception:
                out, err = b"", b""
            return ProviderResult(
                rc=RC_TIMEOUT,
                stdout=out.decode("utf-8", "replace"),
                stderr=err.decode("utf-8", "replace"),
                timed_out=True,
            )
        return ProviderResult(
            rc=proc.returncode,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
        )
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
