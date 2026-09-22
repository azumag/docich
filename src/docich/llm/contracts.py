"""Native LLM dispatch contracts for #829 PR-1.

Golden source: ``games/soviet_now/lib/ai_generate.sh`` (+
``ai_generate_policy.sh`` / ``ai_prepass_budget.sh``) at gitlink
``630aa07c``.  This module owns the value-level contract: return codes,
failure kinds, agent-spec validation, purpose scopes, timeout resolution,
settings defaults, and the ``ai_stats`` telemetry schema.  No shell, no
network, no secrets here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as _dt
import json
import os
from pathlib import Path
import re
import time

# Return codes.  Meanings are frozen by the legacy dispatch and its callers
# (broadcast/comment.sh, radio_engine.sh, radio_news.sh, strategy/ai.sh):
# 79 = explicit provider rate-limit only, 91 = improve-gate give-up (no model
# call, no attempt/fail accounting), 92 = lane max-wait give-up (no model
# call), 2 = invalid agent spec (skipped without backoff), 124 = internal
# timeout marker (callers treat as generic provider failure).
RC_OK = 0
RC_FAILED = 1
RC_INVALID_SPEC = 2
RC_TIMEOUT = 124
RC_RATE_LIMIT = 79
RC_GATE_GIVEUP = 91
RC_QUEUE_GIVEUP = 92

# failure_kind file enum.  Written only by the list-level runner, never by a
# single dispatch.
FAILURE_KINDS = ("gate_giveup", "queue_giveup", "rate_limit", "failed")

# Agent specs the legacy ``_ai_agent_spec_valid`` accepts.  ``claude`` /
# ``ollama`` backends exist in the shell file but are unreachable from the
# dispatch, so the native port rejects them the same way (rc 2).
_RE_SPEC = re.compile(
    r"^(codex|codex:.+|opencode:.+|opencode-go:.+|openrouter:.+"
    r"|vercel:.+|amd:.+|local|local:.+)$"
)
# The legacy MiniMax deny-list short-circuits to rc 1 (provider failure),
# not rc 2.
_RE_MINIMAX = re.compile(
    r"^(minimax.*|codex:.*minimax.*|opencode:minimax.*"
    r"|opencode-go:minimax.*|opencode/minimax.*|opencode-go/minimax.*)$"
)

# Purpose scopes for queue lanes and scoped backoff.  Mirrors
# ``_ai_queue_lock_scope`` / ``_ai_failure_backoff_scope``.
SCOPES = ("comment", "radio", "improve", "local", "remote", "other")
RADIO_FAMILY = ("RADIO", "NEWS", "JIJI", "CELEBRATION")


def scope_of(label: str) -> str:
    """Map a dispatch label to its queue/backoff scope."""
    head = (label or "").split(":", 1)[0]
    if head == "COMMENT":
        return "comment"
    if head in RADIO_FAMILY:
        return "radio"
    if head in ("IMPROVE", "IMPROVEMENT"):
        return "improve"
    if head == "LOCAL":
        return "local"
    if head == "REMOTE":
        return "remote"
    return "other"


def failure_scope_of(label: str) -> str:
    """Scoped backoff bucket: radio_prepass / radio_main / default."""
    if (label or "").startswith("RADIO:") and "prepass" in label:
        return "radio_prepass"
    if scope_of(label) == "radio":
        return "radio_main"
    return "default"


def lane_of(label: str) -> str | None:
    """Queue lane, or None when the label bypasses the queue entirely."""
    scope = scope_of(label)
    if scope in ("comment", "radio", "improve"):
        return scope
    return None


# Lane priority: comment(0) > radio(10) > improve(20).
LANE_PRIORITY = {"comment": 0, "radio": 10, "improve": 20}


def is_radio_family(label: str) -> bool:
    return (label or "").split(":", 1)[0] in RADIO_FAMILY


def validate_agent_spec(spec: str, vercel_free_agents: str = "") -> bool:
    """Mirror ``_ai_agent_spec_valid``: True, or False (caller maps to rc 2).

    ``vercel:*`` is only valid while ``VERCEL_FREE_AGENTS`` is non-empty.
    """
    if not spec or not _RE_SPEC.match(spec):
        return False
    if spec.startswith("vercel:") and not vercel_free_agents.strip():
        return False
    return True


def is_minimax_denied(spec: str) -> bool:
    """Specs the legacy deny-list turns into rc 1 without a model call."""
    return bool(_RE_MINIMAX.match(spec or ""))


@dataclass(frozen=True)
class LlmSettings:
    """Golden defaults mirrored from ``core/config.sh`` + dispatch fallbacks.

    Every field is overridable by the same environment variable name the
    legacy shell reads, so production ``.env`` overrides keep working when
    they are passed through.  No soviet_now paths are referenced.
    """

    comment_timeout: int = 90
    radio_timeout: int = 240
    radio_timeout_minimum: int = 60
    vercel_other_timeout: int = 45
    codex_timeout: int = 300
    codex_model: str = "amd-token-factory-deepseek-v4-flash"
    codex_bin: str = "codex"
    local_base_url: str = "http://100.112.104.102:11434"
    local_model: str = "gemma4:12b"
    local_timeout: int = 180
    local_temperature: float = 0.7
    local_num_predict: int = 1600
    opencode_bin: str = "/snap/bin/opencode"
    opencode_bin_fallback: str = "opencode"
    opencode_timeout: int = 90
    opencode_abort_retry: bool = True
    opencode_abort_retry_wait_sec: int = 2
    vercel_free_agents: str = ""
    agent_backoff_sec: int = 600
    comment_agent_backoff_sec: int = 18000
    radio_agent_backoff_sec: int = 18000
    backoff_sec_items: str = (
        "deepseek-v4-flash-free:86400 muse-spark-1.3-contributor-free:86400 "
        "muse-spark-1.2-contributor-free:86400 vercel/poolside/laguna-s-2.1-free:300 "
        "vercel/inclusionai/ling-3.0-flash-fin:300 vercel/zai/glm-5.3-flash:300 "
        "vercel/xiaomi/mimo-v2.5:300 vercel/xiaomi/mimo-v2.5-pro:300 "
        "vercel/alibaba/qwen3.8-flash:300 amd-token-factory-deepseek-v4-flash:86400 "
        "openrouter/free:86400 local:1800 deepseek-v4-flash:18000 "
        "deepseek-v4.1-flash:18000 muse-spark-1.3-contributor:86400 "
        "muse-spark-1.2-contributor:86400"
    )
    failure_backoff_sec: int = 300
    failure_streak_max_backoff_sec: int = 3600
    vercel_family_backoff_sec: int = 60
    queue_enabled: bool = True
    queue_wait_sec: int = 2
    queue_stale_sec: int = 900
    queue_max_wait_sec: int = 0
    queue_max_wait_hard_cap: bool = False
    radio_queue_max_wait_sec: int = 300
    improve_queue_max_wait_sec: int = 300
    comment_lane_lock: bool = True
    radio_lane_lock: bool = True
    improve_gate_enabled: bool = True
    improve_gate_wait_max_sec: int = 1200
    opencode_run_lock_wait_sec: int = 2
    opencode_run_lock_stale_sec: int = 1800
    opencode_run_lock_max_wait_sec: int = 0
    rotation_gate_wait_sec: int = 120
    rotation_gate_enabled: bool = True

    @classmethod
    def from_env(cls, env: dict | None = None) -> "LlmSettings":
        """Build settings, honoring the legacy variable names as overrides."""
        src = dict(os.environ) if env is None else dict(env)

        def _int(name: str, default: int) -> int:
            try:
                return int(str(src.get(name, default)))
            except (ValueError, TypeError):
                return default

        def _bool(name: str, default: bool) -> bool:
            raw = src.get(name)
            if raw is None:
                return default
            return str(raw).strip() not in ("", "0", "false", "False", "no")

        return cls(
            comment_timeout=_int("COMMENT_CODEX_TIMEOUT", 90),
            radio_timeout=_int("RADIO_CODEX_TIMEOUT", 240),
            vercel_other_timeout=_int("VERCEL_OPENCODE_TIMEOUT", 45),
            codex_timeout=_int("CODEX_TIMEOUT", 300),
            codex_model=str(src.get("CODEX_MODEL", cls.codex_model)),
            codex_bin=str(src.get("CODEX_BIN", cls.codex_bin)),
            local_base_url=str(src.get("LOCAL_LLM_BASE_URL", cls.local_base_url)),
            local_model=str(src.get("LOCAL_LLM_MODEL", cls.local_model)),
            local_timeout=_int("LOCAL_LLM_TIMEOUT", 180),
            opencode_bin=str(src.get("OPENCODE_BIN", cls.opencode_bin)),
            opencode_abort_retry=_bool("OPENCODE_ABORT_RETRY", True),
            opencode_abort_retry_wait_sec=_int("OPENCODE_ABORT_RETRY_WAIT_SEC", 2),
            vercel_free_agents=str(src.get("VERCEL_FREE_AGENTS", "")),
            agent_backoff_sec=_int("AI_AGENT_BACKOFF_SEC", 600),
            comment_agent_backoff_sec=_int("COMMENT_AGENT_BACKOFF_SEC", 18000),
            radio_agent_backoff_sec=_int("RADIO_AGENT_BACKOFF_SEC", 18000),
            backoff_sec_items=str(src.get("AI_BACKOFF_SEC_ITEMS", cls.backoff_sec_items)),
            failure_backoff_sec=_int("AI_BACKOFF_FAILURE_SEC", 300),
            failure_streak_max_backoff_sec=_int(
                "AI_FAILURE_STREAK_MAX_BACKOFF_SEC", 3600
            ),
            vercel_family_backoff_sec=_int("AI_VERCEL_FAMILY_BACKOFF_SEC", 60),
            queue_enabled=_bool("AI_GENERATION_QUEUE_ENABLED", True),
            queue_wait_sec=_int("AI_GENERATION_QUEUE_WAIT_SEC", 2),
            queue_stale_sec=_int("AI_GENERATION_QUEUE_STALE_SEC", 900),
            queue_max_wait_sec=_int("AI_GENERATION_QUEUE_MAX_WAIT_SEC", 0),
            radio_queue_max_wait_sec=_int("AI_RADIO_QUEUE_MAX_WAIT_SEC", 300),
            improve_queue_max_wait_sec=_int("AI_IMPROVE_QUEUE_MAX_WAIT_SEC", 300),
            comment_lane_lock=_bool("AI_COMMENT_LANE_LOCK", True),
            radio_lane_lock=_bool("AI_RADIO_LANE_LOCK", True),
            improve_gate_enabled=_bool("AI_RADIO_IMPROVE_GATE", True),
            improve_gate_wait_max_sec=_int("AI_RADIO_IMPROVE_WAIT_MAX_SEC", 1200),
            opencode_run_lock_wait_sec=_int("OPENCODE_RUN_LOCK_WAIT_SEC", 2),
            opencode_run_lock_stale_sec=_int("OPENCODE_RUN_LOCK_STALE_SEC", 1800),
            opencode_run_lock_max_wait_sec=_int("OPENCODE_RUN_LOCK_MAX_WAIT_SEC", 0),
            rotation_gate_wait_sec=_int("OPENCODE_ROTATION_GATE_WAIT_SEC", 120),
            rotation_gate_enabled=_bool("OPENCODE_ROTATION_GATE_ENABLED", True),
        )


def timeout_for(
    label: str, agent: str, settings: LlmSettings, override: int | None = None
) -> int:
    """Resolve the per-call provider timeout, mirroring ``_ai_dispatch``.

    An explicit call-site override always wins.  RADIO labels go through the
    radio guard (unset preserved by the caller; non-positive/small values
    fall back to the RADIO default, matching ``_normalize_radio_codex_timeout``).
    """
    if override is not None:
        if override >= settings.radio_timeout_minimum:
            return override
        if (label or "").split(":", 1)[0] == "RADIO":
            return settings.radio_timeout
        return override if override > 0 else settings.opencode_timeout
    head = (label or "").split(":", 1)[0]
    if head == "COMMENT":
        return settings.comment_timeout
    if head == "RADIO":
        return settings.radio_timeout
    if agent.startswith("vercel:"):
        return settings.vercel_other_timeout
    return settings.opencode_timeout


@dataclass(frozen=True)
class LlmRequest:
    label: str
    prompt_text: str
    agents: tuple[str, ...] = ()
    timeout: int | None = None


@dataclass
class LlmResult:
    rc: int
    text: str = ""
    winner_agent: str = ""
    failure_kind: str = ""
    resolved_models: tuple[str, ...] = ()


def record_stats(
    state_dir: Path,
    event: str,
    label: str,
    agent: str,
    rc: int | str,
    resolved_model: str = "",
    error: str = "",
) -> None:
    """Append one ``ai_stats`` JSONL line.  Never carries prompt text, raw
    bodies, credentials, or headers — only label/agent/rc/model, exactly like
    the legacy ``_ai_stats_record``."""
    try:
        day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
        line = {
            "ts": int(time.time()),
            "day": day,
            "event": event,
            "label": label,
            "agent": agent,
            "rc": str(rc),
            "resolved_model": resolved_model,
        }
        if error:
            flat = re.sub(r"\s+", " ", error).strip()
            line["error"] = flat[:90] if len(flat) > 90 else flat
        path = Path(state_dir) / "ai_stats" / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
