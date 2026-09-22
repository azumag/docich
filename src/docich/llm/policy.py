"""Input and timeout policy for the native provider chain."""

from __future__ import annotations

import re
import os

from .contracts import AgentSpec, LlmError


SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SUPPORTED_PROVIDERS = {
    "amd",
    "codex",
    "local",
    "openrouter",
    "opencode",
    "opencode-go",
    "vercel",
}
RETIRED_PROVIDER_RE = re.compile(r"(^|[:/])minimax([:/-]|$)", re.IGNORECASE)
MAX_PROMPT_BYTES = 1024 * 1024


def validate_label(label: str) -> str:
    label = str(label or "").strip()
    if not SAFE_LABEL_RE.fullmatch(label):
        raise LlmError("label は英数字・._:- の安全な識別子に限定されます")
    if not (label.startswith("COMMENT") or label.startswith("RADIO")):
        raise LlmError("label は COMMENT または RADIO で始める必要があります")
    return label


def _parse_one(raw: str) -> AgentSpec:
    if not SAFE_AGENT_RE.fullmatch(raw):
        raise LlmError("agents に安全でない識別子が含まれています")
    if RETIRED_PROVIDER_RE.search(raw) or raw.lower().startswith("minimax"):
        raise LlmError("退役済みのMiniMax providerは使用できません")
    if ":" not in raw:
        if raw != "codex" and raw != "local":
            raise LlmError("agent は provider:model 形式で指定してください")
        return AgentSpec(raw=raw, provider=raw)
    provider, model = raw.split(":", 1)
    if provider not in SUPPORTED_PROVIDERS or not model:
        raise LlmError("未許可または空のprovider/modelです")
    return AgentSpec(raw=raw, provider=provider, model=model)


def parse_agents(raw: str, env: dict[str, str] | None = None) -> tuple[AgentSpec, ...]:
    """Parse the ordered chain without resolving credentials or calling a provider."""

    effective_env = os.environ if env is None else env
    if not isinstance(raw, str) or not raw.strip():
        raise LlmError("agents は空にできません")
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        raise LlmError("agents に空の要素があります")
    specs = []
    for part in parts:
        spec = _parse_one(part)
        # Bare legacy names resolve to the same operator-selected defaults as
        # the shell dispatcher.  Environment values are still constrained to
        # the same safe model alphabet before they become argv/telemetry.
        if spec.provider == "codex" and spec.model is None:
            model = str(effective_env.get("CODEX_MODEL", "") or "").strip()
            if model:
                if not SAFE_AGENT_RE.fullmatch(model):
                    raise LlmError("CODEX_MODEL は安全なモデル識別子に限定されます")
                spec = AgentSpec(raw=spec.raw, provider=spec.provider, model=model)
        elif spec.provider == "local" and spec.model is None:
            model = str(effective_env.get("LOCAL_LLM_MODEL", "") or "").strip()
            if model:
                if not SAFE_AGENT_RE.fullmatch(model):
                    raise LlmError("LOCAL_LLM_MODEL は安全なモデル識別子に限定されます")
                spec = AgentSpec(raw=spec.raw, provider=spec.provider, model=model)
        specs.append(spec)
    specs = tuple(specs)
    if not specs:
        raise LlmError("agents は空にできません")
    return specs


def _positive_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not str(raw).isdigit() or int(raw) < 1:
        return default
    return int(raw)


def provider_timeout(
    label: str,
    spec: AgentSpec,
    override: int | None,
    env: dict[str, str],
) -> int:
    """Resolve the legacy COMMENT/RADIO timeout defaults deterministically."""

    if override is not None:
        if type(override) is not int or override < 1:
            raise LlmError("timeout は1以上の整数である必要があります")
        return override
    if label.startswith("COMMENT"):
        return _positive_int(env, "COMMENT_CODEX_TIMEOUT", 90)
    if label.startswith("RADIO"):
        return _positive_int(env, "RADIO_CODEX_TIMEOUT", 240)
    if spec.provider == "vercel":
        return _positive_int(env, "VERCEL_OPENCODE_TIMEOUT", 45)
    if spec.provider == "local":
        return _positive_int(env, "LOCAL_LLM_TIMEOUT", 180)
    return _positive_int(env, "CODEX_TIMEOUT", 300)


def queue_max_wait(label: str, env: dict[str, str]) -> int:
    """Return the queue wait cap; zero retains the legacy unbounded default."""

    name = "AI_GENERATION_QUEUE_MAX_WAIT_SEC"
    if name in env:
        raw = env[name]
        return int(raw) if str(raw).isdigit() and int(raw) >= 0 else 0
    if label.startswith("RADIO"):
        raw = env.get("AI_RADIO_QUEUE_MAX_WAIT_SEC", "300")
        return int(raw) if str(raw).isdigit() and int(raw) >= 0 else 300
    return 0


def opencode_max_wait(env: dict[str, str]) -> int:
    raw = env.get("OPENCODE_RUN_LOCK_MAX_WAIT_SEC", "0")
    return int(raw) if str(raw).isdigit() and int(raw) >= 0 else 0


def allow_vercel(spec: AgentSpec, env: dict[str, str]) -> bool:
    """Honor the existing emergency disable switch without exposing its value."""

    return spec.provider != "vercel" or bool(env.get("VERCEL_FREE_AGENTS", "").strip())
