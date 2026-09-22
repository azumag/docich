"""File-backed, secret-free provider backoff state."""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .contracts import AgentSpec


_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._:-]+")


def safe_key(value: str) -> str:
    return _SAFE_KEY_RE.sub("_", str(value))[:160] or "unknown"


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".llm-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


@dataclass
class BackoffStore:
    backoff_dir: Path
    failure_streak_dir: Path

    def _path(self, root: Path, agent: AgentSpec | str) -> Path:
        raw = agent.raw if isinstance(agent, AgentSpec) else str(agent)
        return root / safe_key(raw)

    def remaining(self, agent: AgentSpec, *, now: int | None = None) -> int:
        path = self._path(self.backoff_dir, agent)
        try:
            until = int(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return 0
        remaining = until - int(time.time() if now is None else now)
        if remaining <= 0:
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                pass
            return 0
        return remaining

    def set(self, agent: AgentSpec, seconds: int, *, now: int | None = None) -> None:
        if seconds < 1:
            return
        current = int(time.time() if now is None else now)
        _atomic_text(self._path(self.backoff_dir, agent), f"{current + seconds}\n")

    def record_failure(self, agent: AgentSpec) -> int:
        path = self._path(self.failure_streak_dir, agent)
        try:
            previous = int(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            previous = 0
        current = previous + 1
        _atomic_text(path, f"{current}\n")
        return current

    def clear_failure(self, agent: AgentSpec) -> None:
        try:
            self._path(self.failure_streak_dir, agent).unlink()
        except (FileNotFoundError, OSError):
            pass


def model_backoff_seconds(
    spec: AgentSpec,
    label: str,
    env: dict[str, str],
    *,
    now: int | None = None,
) -> int:
    """Resolve explicit rate-limit backoff using the shell-compatible names."""

    model_keys = {spec.resolved_model}
    if spec.model:
        # The legacy config historically keys OpenCode quota entries by the
        # model suffix (for example ``muse-spark-...``), while Vercel entries
        # commonly use the resolved ``vercel/...`` form. Accept both so the
        # native dispatcher consumes the same operator policy.
        model_keys.add(spec.model)
    for item in env.get("AI_BACKOFF_SEC_ITEMS", "").split():
        name, separator, seconds = item.partition(":")
        if separator and name in model_keys and seconds.isdigit() and int(seconds) > 0:
            configured = int(seconds)
            # Zen Muse free is an IP daily bucket reset at UTC midnight, not a
            # rolling 24-hour lockout. Preserve shorter explicit overrides.
            if spec.raw in {
                "opencode:muse-spark-1.2-contributor-free",
                "opencode:muse-spark-1.3-contributor-free",
            }:
                current = int(time.time() if now is None else now)
                return min(configured, 86400 - current % 86400)
            return configured
    name = "COMMENT_AGENT_BACKOFF_SEC" if label.startswith("COMMENT") else "RADIO_AGENT_BACKOFF_SEC"
    default = 18000 if label.startswith(("COMMENT", "RADIO")) else 600
    raw = env.get(name, str(default))
    return int(raw) if str(raw).isdigit() and int(raw) > 0 else default


def failure_backoff_seconds(env: dict[str, str], streak: int) -> int:
    raw = env.get("AI_BACKOFF_FAILURE_SEC", "300")
    base = int(raw) if str(raw).isdigit() and int(raw) > 0 else 300
    cap_raw = env.get("AI_FAILURE_STREAK_MAX_BACKOFF_SEC", "3600")
    cap = int(cap_raw) if str(cap_raw).isdigit() and int(cap_raw) > 0 else 3600
    multiplier = 2 ** min(max(streak - 1, 0), 3)
    return min(base * multiplier, cap)