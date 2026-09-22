"""Ordered, native provider-chain dispatch for Issue #829 PR-1."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import time
from typing import Callable

from .backoff import BackoffStore, failure_backoff_seconds, model_backoff_seconds, safe_key
from .contracts import AgentSpec, DispatchRequest, DispatchResult, LlmError, ProviderResult
from .locks import FileLock, LockTimeout
from .policy import (
    MAX_PROMPT_BYTES,
    SAFE_AGENT_RE,
    SUPPORTED_PROVIDERS,
    allow_vercel,
    opencode_max_wait,
    parse_agents,
    provider_timeout,
    queue_max_wait,
    validate_label,
)
from .providers import call_agent
from .telemetry import record


ProviderCaller = Callable[[AgentSpec, DispatchRequest, float, dict[str, str]], ProviderResult]
SAFE_FAILURE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
KNOWN_FAILURE_KINDS = frozenset({
    "adapter_error",
    "backoff",
    "disabled",
    "empty_output",
    "failed",
    "gate_giveup",
    "http_error",
    "invalid_output",
    "invalid_provider",
    "invalid_response",
    "output_too_large",
    "provider_error",
    "provider_failed",
    "queue_giveup",
    "rate_limit",
    "timeout",
    "transport_error",
    "validator_failed",
})


def _state_dir(g, env: dict[str, str]) -> Path:
    configured = env.get("DOCICH_LLM_STATE_DIR")
    if configured:
        return Path(configured).resolve()
    repo_root = getattr(g, "repo_root", None)
    return ((Path(repo_root) if repo_root else Path.cwd()) / "tmp" / "state" / "llm").resolve()


def _store(state_dir: Path, env: dict[str, str]) -> BackoffStore:
    backoff = Path(env.get("AI_BACKOFF_DIR", state_dir / "backoff"))
    streak = Path(env.get("AI_FAIL_STREAK_DIR", state_dir / "failure_streak"))
    return BackoffStore(backoff, streak)


def _queue_path(state_dir: Path, label: str, env: dict[str, str]) -> Path:
    explicit = env.get("AI_GENERATION_QUEUE_LOCK_DIR")
    if explicit:
        return Path(explicit)
    if label.startswith("COMMENT") and env.get("AI_COMMENT_LANE_LOCK", "1") != "1":
        return state_dir / "generation_queue" / safe_key(label)
    if label.startswith("RADIO") and env.get("AI_RADIO_LANE_LOCK", "1") != "1":
        return state_dir / "generation_queue" / safe_key(label)
    lane = "comment" if label.startswith("COMMENT") else "radio" if label.startswith("RADIO") else "other"
    return state_dir / "generation_queue" / lane


def _opencode_path(state_dir: Path, spec: AgentSpec, env: dict[str, str]) -> Path:
    explicit = env.get("OPENCODE_RUN_LOCK_DIR")
    if explicit:
        return Path(explicit)
    scope = "local" if spec.provider == "local" else f"remote-opencode-{safe_key(spec.raw)}"
    return state_dir / "opencode_locks" / scope


def _write_sidecar(path: Path | None, value: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n" if value else "", encoding="utf-8")
    except OSError:
        # Sidecars are diagnostic compatibility outputs, not a reason to abort
        # a successful generation or expose an I/O exception to a worker.
        return


def _safe_failure_kind(value: object, fallback: str) -> str:
    if isinstance(value, str) and value in KNOWN_FAILURE_KINDS and SAFE_FAILURE_RE.fullmatch(value):
        return value
    return fallback


def _positive_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _request_checks(request: DispatchRequest, env: dict[str, str]) -> None:
    try:
        validate_label(request.label)
    except (LlmError, TypeError) as exc:
        raise LlmError("不正なdispatch labelです") from exc
    if not isinstance(request.prompt, str) or not request.prompt:
        raise LlmError("プロンプトが空です")
    try:
        prompt_bytes = len(request.prompt.encode("utf-8"))
    except UnicodeError as exc:
        raise LlmError("プロンプトの文字コードが不正です") from exc
    if prompt_bytes > MAX_PROMPT_BYTES:
        raise LlmError("プロンプトが大きすぎます")
    if not request.agents:
        raise LlmError("agents は空にできません")
    for spec in request.agents:
        if not isinstance(spec, AgentSpec):
            raise LlmError("agents の要素型が不正です")
        if not isinstance(spec.raw, str) or not SAFE_AGENT_RE.fullmatch(spec.raw):
            raise LlmError("agents に安全でない識別子が含まれています")
        if spec.provider not in SUPPORTED_PROVIDERS:
            raise LlmError("未許可のproviderです")
        try:
            parsed = parse_agents(spec.raw, env)
        except LlmError as exc:
            raise LlmError("agents のprovider/model policyに違反しています") from exc
        if parsed[0].provider != spec.provider:
            raise LlmError("agents のprovider/modelが一致しません")
    if request.validator is not None and not callable(request.validator):
        raise LlmError("validator は呼び出し可能である必要があります")


def _improve_job_active(env: dict[str, str]) -> bool:
    state_file = Path(
        env.get("IMPROVE_STATE_FILE")
        or Path(env.get("TMP_STATE_DIR", "tmp/state")) / "improve_state.json"
    )
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(data, dict) or data.get("status") != "running":
        return False
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        started = float(data.get("started_at") or 0)
    except (TypeError, ValueError):
        started = 0
    return not started or time.time() - started <= 7200


def _radio_improve_gate(request: DispatchRequest, env: dict[str, str], deadline: float | None) -> bool:
    """Keep the legacy radio/improvement exclusion without blocking self-improve."""

    if not request.label.startswith("RADIO") or "retro-improve" in request.label.lower():
        return True
    if env.get("AI_GENERATION_QUEUE_ENABLED", "1") != "1":
        return True
    if env.get("AI_RADIO_IMPROVE_GATE", "1") != "1" or not _improve_job_active(env):
        return True
    wait_sec = max(_nonnegative_int(env.get("AI_GENERATION_QUEUE_WAIT_SEC", "2"), 2), 1)
    max_wait = _nonnegative_int(env.get("AI_RADIO_IMPROVE_WAIT_MAX_SEC", "1200"), 1200)
    started = time.monotonic()
    while _improve_job_active(env):
        waited = time.monotonic() - started
        if (max_wait and waited >= max_wait) or (deadline is not None and time.monotonic() >= deadline):
            return False
        sleep_for = float(wait_sec)
        if max_wait:
            sleep_for = min(sleep_for, max(max_wait - waited, 0.05))
        if deadline is not None:
            sleep_for = min(sleep_for, max(deadline - time.monotonic(), 0.05))
        time.sleep(sleep_for)
    return True


class Dispatcher:
    def __init__(
        self,
        *,
        g=None,
        env: dict[str, str] | None = None,
        provider_caller: ProviderCaller | None = None,
    ) -> None:
        self.g = g
        self.env = dict(os.environ if env is None else env)
        self.state_dir = _state_dir(g, self.env)
        self.store = _store(self.state_dir, self.env)
        self.provider_caller = provider_caller or (
            lambda spec, request, timeout, provider_env: call_agent(
                spec, request, timeout=timeout, env=provider_env
            )
        )
        self.telemetry_dir = Path(
            self.env.get("DOCICH_LLM_STATS_DIR", self.state_dir / "stats")
        )

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        overall_timeout_sec: float | None = None,
        last_agent_file: Path | None = None,
        failure_kind_file: Path | None = None,
    ) -> DispatchResult:
        _request_checks(request, self.env)
        _write_sidecar(last_agent_file, "")
        _write_sidecar(failure_kind_file, "")
        deadline = None
        if overall_timeout_sec is not None:
            budget = _positive_float(overall_timeout_sec, 0.0)
            if budget <= 0:
                raise LlmError("overall timeout は正の数である必要があります")
            deadline = time.monotonic() + budget
        if not _radio_improve_gate(request, self.env, deadline):
            _write_sidecar(failure_kind_file, "gate_giveup")
            record(self.telemetry_dir, event="failure", label=request.label, returncode=91, failure_kind="gate_giveup")
            return DispatchResult(91, failure_kind="gate_giveup", detail="gate_giveup")

        skipped = 0
        attempted = 0
        saw_rate_limit = False

        for spec in request.agents:
            if not allow_vercel(spec, self.env):
                skipped += 1
                record(self.telemetry_dir, event="skipped", label=request.label, spec=spec, failure_kind="disabled")
                continue
            remaining = self.store.remaining(spec)
            if remaining > 0:
                skipped += 1
                saw_rate_limit = True
                record(self.telemetry_dir, event="skipped", label=request.label, spec=spec, failure_kind="backoff")
                continue
            if deadline is not None and time.monotonic() >= deadline:
                _write_sidecar(failure_kind_file, "timeout")
                return DispatchResult(124, failure_kind="timeout", attempted=attempted, skipped=skipped)

            timeout = provider_timeout(request.label, spec, request.timeout_sec, self.env)
            if deadline is not None:
                timeout = min(timeout, max(deadline - time.monotonic(), 0.1))
            queue_lock = None
            if self.env.get("AI_GENERATION_QUEUE_ENABLED", "1") == "1":
                queue_lock = FileLock(
                    _queue_path(self.state_dir, request.label, self.env),
                    label=request.label,
                    wait_sec=_positive_float(self.env.get("AI_GENERATION_QUEUE_WAIT_SEC", "2"), 2),
                    max_wait_sec=queue_max_wait(request.label, self.env),
                )
            started = time.monotonic()
            provider_lock = None
            try:
                if queue_lock is not None:
                    try:
                        queue_lock.acquire(deadline=deadline)
                    except LockTimeout:
                        result = ProviderResult(92, failure_kind="queue_giveup", detail="queue_giveup")
                    else:
                        result = None
                else:
                    result = None

                if result is None:
                    if (
                        spec.provider in {"amd", "openrouter", "opencode", "opencode-go", "vercel"}
                        and self.env.get("OPENCODE_RUN_LOCK_ENABLED", "1") == "1"
                    ):
                        provider_lock = FileLock(
                            _opencode_path(self.state_dir, spec, self.env),
                            label=request.label,
                            wait_sec=_positive_float(self.env.get("OPENCODE_RUN_LOCK_WAIT_SEC", "2"), 2),
                            stale_sec=max(_nonnegative_int(self.env.get("OPENCODE_RUN_LOCK_STALE_SEC", "1800"), 1800), 60),
                            max_wait_sec=opencode_max_wait(self.env),
                        )
                        try:
                            provider_lock.acquire(deadline=deadline)
                        except LockTimeout:
                            result = ProviderResult(124, failure_kind="timeout", detail="timeout")
                    if result is None:
                        attempted += 1
                        record(self.telemetry_dir, event="attempt", label=request.label, spec=spec)
                        result = self.provider_caller(spec, request, timeout, self.env)
            except Exception:
                # Adapter bugs and unavailable binaries are a normal fallback
                # condition; the exception body must never reach telemetry.
                result = ProviderResult(1, failure_kind="provider_failed", detail="adapter_error")
            finally:
                if provider_lock is not None:
                    provider_lock.release()
                if queue_lock is not None:
                    queue_lock.release()

            if not isinstance(result, ProviderResult):
                result = ProviderResult(1, failure_kind="provider_failed", detail="adapter_error")
            try:
                returncode = int(result.returncode)
            except (TypeError, ValueError):
                returncode = 1
            output = result.output if isinstance(result.output, str) else ""
            latency_ms = int((time.monotonic() - started) * 1000)
            if returncode == 92:
                record(self.telemetry_dir, event="queue_giveup", label=request.label, spec=spec, returncode=92, failure_kind="queue_giveup", latency_ms=latency_ms)
                _write_sidecar(failure_kind_file, "queue_giveup")
                return DispatchResult(92, failure_kind="queue_giveup", attempted=attempted, skipped=skipped)

            valid = returncode == 0 and bool(output)
            validator_failed = False
            if valid and request.validator is not None:
                try:
                    valid = bool(request.validator(output))
                except Exception:
                    valid = False
                validator_failed = not valid
            if valid:
                self.store.clear_failure(spec)
                _write_sidecar(last_agent_file, spec.raw)
                record(self.telemetry_dir, event="success", label=request.label, spec=spec, latency_ms=latency_ms)
                return DispatchResult(
                    0,
                    output=output,
                    last_agent=spec.raw,
                    attempted=attempted,
                    skipped=skipped,
                )

            fallback_kind = "invalid_output" if returncode == 0 else "provider_failed"
            failure_kind = "validator_failed" if validator_failed else _safe_failure_kind(result.failure_kind, fallback_kind)
            if returncode == 79 or failure_kind == "rate_limit":
                saw_rate_limit = True
                try:
                    self.store.set(spec, model_backoff_seconds(spec, request.label, self.env))
                except OSError:
                    pass
            elif returncode != 0:
                try:
                    streak = self.store.record_failure(spec)
                    self.store.set(spec, failure_backoff_seconds(self.env, streak))
                except OSError:
                    pass
            record(
                self.telemetry_dir,
                event="failure",
                label=request.label,
                spec=spec,
                returncode=returncode,
                failure_kind=failure_kind,
                latency_ms=latency_ms,
            )

        final_kind = "rate_limit" if saw_rate_limit else "failed"
        _write_sidecar(failure_kind_file, final_kind)
        record(self.telemetry_dir, event="failure", label=request.label, returncode=1, failure_kind=final_kind)
        return DispatchResult(
            1,
            failure_kind=final_kind,
            attempted=attempted,
            skipped=skipped,
            detail="all_agents_failed",
        )
