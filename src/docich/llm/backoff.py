"""Backoff stores for the native LLM dispatch (#829 PR-1).

Three independent stores mirror the legacy layout, relocated under the
docich state dir (never the soviet_now tree):

- explicit agent backoff (cross-purpose, 429 only),
- scoped generic-failure backoff with streak multiplier,
- vercel family breaker (only after >=2 distinct vercel agents 429).

Files hold a single integer epoch.  Checks delete expired entries.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
import time


def _read_until(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def _write_until(path: Path, until: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(int(until)), encoding="utf-8")
    except OSError:
        pass


def _check(path: Path, now: int) -> int:
    """Return remaining seconds, 0 when clear.  Deletes expired entries."""
    until = _read_until(path)
    if until <= 0:
        return 0
    if now >= until:
        try:
            path.unlink()
        except OSError:
            pass
        return 0
    return until - now


def _sanitize(value: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value
    )[:128] or "default"


def _model_after_prefix(agent: str) -> str:
    if ":" in agent:
        return agent.split(":", 1)[1]
    if "/" in agent:
        return agent.split("/", 1)[1]
    return agent


class ExplicitBackoff:
    """Cross-purpose per-agent 429 backoff (legacy ``ai_backoff`` dir)."""

    def __init__(self, state_dir: Path, settings) -> None:
        self._dir = Path(state_dir) / "ai_backoff"
        self._settings = settings

    def _path(self, agent: str) -> Path:
        return self._dir / _sanitize(agent)

    def seconds_for(self, agent: str, label: str) -> int:
        """Duration for a fresh 429, mirroring ``_ai_backoff_sec_for_agent``."""
        model = _model_after_prefix(agent)
        for item in self._settings.backoff_sec_items.split():
            if ":" not in item:
                continue
            key, _, raw = item.partition(":")
            if key and (model == key or model.endswith("/" + key)):
                try:
                    value = int(raw)
                except ValueError:
                    continue
                if agent.startswith("opencode:muse-spark-1.") and value >= 86400:
                    now = int(time.time())
                    day = 86400 - (now % 86400)
                    return min(value, day) if day > 0 else value
                return value
        if (label or "").startswith("COMMENT"):
            return self._settings.comment_agent_backoff_sec
        if (label or "").startswith("RADIO"):
            return self._settings.radio_agent_backoff_sec
        return self._settings.agent_backoff_sec

    def set(self, agent: str, label: str, now: int | None = None) -> int:
        at = int(time.time()) if now is None else now
        seconds = self.seconds_for(agent, label)
        _write_until(self._path(agent), at + seconds)
        return seconds

    def check(self, agent: str, now: int | None = None) -> int:
        at = int(time.time()) if now is None else now
        return _check(self._path(agent), at)


class FailureBackoff:
    """Scoped generic-failure backoff with streak multiplier (legacy
    ``ai_failure_backoff`` + ``ai_fail_streak`` dirs)."""

    def __init__(self, state_dir: Path, settings) -> None:
        from .contracts import failure_scope_of

        self._dir = Path(state_dir) / "ai_failure_backoff"
        self._streak_dir = Path(state_dir) / "ai_fail_streak"
        self._settings = settings
        self._scope_of = failure_scope_of

    def _path(self, label: str, agent: str) -> Path:
        return (
            self._dir / self._scope_of(label) / _sanitize(agent)
        )

    def _streak_path(self, label: str, agent: str) -> Path:
        return (
            self._streak_dir
            / f"generic__{self._scope_of(label)}__{_sanitize(agent)}"
        )

    def check(self, label: str, agent: str, now: int | None = None) -> int:
        at = int(time.time()) if now is None else now
        return _check(self._path(label, agent), at)

    def record_failure(
        self, label: str, agent: str, now: int | None = None
    ) -> int:
        """Record one generic failure; return the backoff seconds applied."""
        at = int(time.time()) if now is None else now
        streak_path = self._streak_path(label, agent)
        streak = _read_until(streak_path) + 1
        _write_until(streak_path, streak)
        shift = min(streak - 1, 3)
        seconds = self._settings.failure_backoff_sec * (2**shift)
        seconds = min(seconds, self._settings.failure_streak_max_backoff_sec)
        _write_until(self._path(label, agent), at + seconds)
        return seconds

    def record_success(self, label: str, agent: str) -> None:
        try:
            self._streak_path(label, agent).unlink()
        except OSError:
            pass

    def streak(self, label: str, agent: str) -> int:
        return _read_until(self._streak_path(label, agent))


class FamilyBreaker:
    """Vercel-family breaker: suppresses all vercel agents only after >=2
    distinct vercel agents 429 within one chain."""

    def __init__(self, state_dir: Path, settings) -> None:
        self._dir = Path(state_dir) / "ai_family_backoff"
        self._settings = settings

    def _path(self, family: str = "vercel") -> Path:
        return self._dir / _sanitize(family)

    def check(self, now: int | None = None) -> int:
        at = int(time.time()) if now is None else now
        return _check(self._path(), at)

    def trip(self, now: int | None = None) -> int:
        at = int(time.time()) if now is None else now
        seconds = max(1, min(300, self._settings.vercel_family_backoff_sec))
        _write_until(self._path(), at + seconds)
        return seconds
