"""Bounded agent-cycle telemetry for #2832 (Stage 1).

Gated by the single env flag ``DOCICH_AGENT_TELEMETRY`` (production default
off). When the flag is off every method is a no-op and no file is written, so
the observe -> decide -> act order, cadence and fence contract are unchanged.

When on, only fixed numeric counters are accumulated (durations in seconds and
counts). Frame images, prompts, action payloads and secrets are never recorded.
The snapshot is one atomically replaced JSON state file per game and role; the
one-shot profiler reads it. There is no resident profiler and no new timer: the
file is rewritten at most once per ``FLUSH_INTERVAL_S`` from the existing cycle.

#3287 wires the adapter/corner side: ``XKit.screenshot`` counts ffmpeg/x11grab
spawns, capture invocations and capture wall time; ``CommandBrain`` counts brain
subprocess spawns. The agent and the corner monitor are separate processes, so
the corner writes ``<game>.corner.json`` instead of overwriting the agent file.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

ENV_FLAG = "DOCICH_AGENT_TELEMETRY"
SCHEMA = "docich.agent_telemetry.v1"
FLUSH_INTERVAL_S = 10.0
_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})

_COUNTERS = (
    "cycles",
    "awaits",
    "observes",
    "decides",
    "acts",
    "waits",
    "actions_completed",
    "hotload_publish_failures",
    # #3287: adapter/corner-side spawn and capture wiring. Counts only; the
    # adapter never records argv, frames, prompts or payloads.
    "captures",
    "capture_spawns",
    "brain_spawns",
)
_TIMERS = (
    "fence_lock_s",
    "observe_s",
    "decide_s",
    "act_s",
    "hotload_publish_s",
    "capture_s",
)
ROLES = ("agent", "corner")
# Public, fixed allowlists for readers (the profiler projects only these keys).
COUNTERS = _COUNTERS
TIMERS = _TIMERS


def enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV_FLAG, "")).strip().lower() in _TRUE_VALUES


class AgentTelemetry:
    """Fixed-field accumulator. Construct via :meth:`from_env`."""

    def __init__(self, path: Path | None, game: str, brain: str, *, role: str = "agent",
                 clock=time.monotonic):
        self._path = path
        self._clock = clock
        self._game = game
        self._brain = brain
        self._role = role if role in ROLES else "agent"
        self._counters = {key: 0 for key in _COUNTERS}
        self._timers = {key: 0.0 for key in _TIMERS}
        self._last_flush = None
        self._dirty = False

    @classmethod
    def from_env(cls, state_dir, game: str, brain: str, *, role: str = "agent",
                 environ=None):
        if not enabled(environ) or state_dir is None:
            return DISABLED
        # The agent and the corner monitor are separate processes. A shared
        # file would let one overwrite the other's snapshot, so the corner
        # role gets its own fixed path while the agent path stays #3151's.
        suffix = "" if role == "agent" else f".{role if role in ROLES else 'agent'}"
        path = Path(state_dir) / "agent_telemetry" / f"{game}{suffix}.json"
        return cls(path, game, brain, role=role)

    @property
    def active(self) -> bool:
        return self._path is not None

    def now(self) -> float:
        return self._clock() if self.active else 0.0

    def count(self, key: str, amount: int = 1) -> None:
        if self.active:
            self._counters[key] += amount
            self._dirty = True

    def add_time(self, key: str, start: float) -> None:
        if self.active:
            self._timers[key] += max(self._clock() - start, 0.0)
            self._dirty = True

    def snapshot(self) -> dict:
        return {
            "schema": SCHEMA,
            "game": self._game,
            "brain": self._brain,
            "role": self._role,
            "counters": dict(self._counters),
            "seconds": {key: round(value, 6) for key, value in self._timers.items()},
            "written_at": time.time(),
        }

    def flush(self, *, force: bool = False) -> None:
        """Rewrite the state file, at most once per FLUSH_INTERVAL_S unless forced."""
        if not self.active or not self._dirty:
            return
        now = self._clock()
        if not force and self._last_flush is not None and now - self._last_flush < FLUSH_INTERVAL_S:
            return
        from ..game_switch import atomic_write_json

        atomic_write_json(self._path, self.snapshot())
        self._last_flush = now
        self._dirty = False


class _DisabledTelemetry(AgentTelemetry):
    def __init__(self):
        super().__init__(None, "", "")


DISABLED = _DisabledTelemetry()

# The adapter/corner code runs deep below the agent loop, so the current
# process's telemetry is bound once instead of threading it through every
# adapter signature. Each process has its own module state; when the flag is
# off this is DISABLED and every call remains a no-op.
_ACTIVE = DISABLED


def set_active(tel) -> None:
    global _ACTIVE
    _ACTIVE = DISABLED if tel is None else tel


def active() -> AgentTelemetry:
    return _ACTIVE


def flush_active(*, force: bool = False) -> None:
    """Flush the bound telemetry; a state-write failure must never stop play."""
    try:
        active().flush(force=force)
    except OSError as exc:
        print(f"[telemetry] 警告: telemetry の書き出しに失敗しました: {exc}", flush=True)


def load_snapshot(path) -> dict | None:
    """Read a telemetry snapshot for the profiler; None when absent or foreign."""
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data
