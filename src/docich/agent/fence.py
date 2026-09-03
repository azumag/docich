"""Agent activation fence (design v2 §6).

An agent worker is only allowed to observe/act when its fixed identity
tuple ``(game, runtime_id, generation, lease_id)`` fully matches the
canonical active runtime.  A generation alone is not enough: a parallel
rollback can reuse the game runtime while a stale agent still carries the
old lease, so the lease is the fence that rejects it.

A fence mismatch is terminal ``FenceLost``: the worker exits instead of
re-entering the supervise restart loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class FenceLost(SystemExit):
    """Terminal exit for an agent worker whose fence no longer matches.

    Carries exit code 75 so supervisors and logs can distinguish a fenced
    worker from a crashed one (which restarts) instead of respawning it.
    """

    def __init__(self, detail: str = ""):
        super().__init__(75)
        self.detail = detail


@dataclass(frozen=True)
class AgentFence:
    game: str
    runtime_id: str
    generation: int
    lease_id: str | None


def check_fence(fence: AgentFence, active: Mapping[str, object] | None) -> None:
    """Raise :class:`FenceLost` unless ``fence`` matches ``active`` exactly.

    ``active`` is the canonical active runtime dict (or None when idle).
    An unfenced worker (no runtime identity) never matches when an active
    runtime exists, and a bound identity must match on all four fields.
    """
    if not isinstance(active, dict):
        raise FenceLost("active runtime がありません")
    expected = AgentFence(
        game=str(active.get("game") or ""),
        runtime_id=str(active.get("runtime_id") or ""),
        generation=int(active.get("generation") or 0),
        lease_id=active.get("lease_id"),
    )
    if fence != expected:
        raise FenceLost(
            f"fence 不一致 (worker={fence}, active={expected})"
        )


def _identity_of(runtime: object) -> AgentFence | None:
    if not isinstance(runtime, dict):
        return None
    game = runtime.get("game")
    runtime_id = runtime.get("runtime_id")
    if not isinstance(game, str) or not game or not isinstance(runtime_id, str):
        return None
    return AgentFence(
        game=game,
        runtime_id=runtime_id,
        generation=int(runtime.get("generation") or 0),
        lease_id=runtime.get("lease_id"),
    )


def resolve_fence(fence: AgentFence, state: Mapping[str, object]) -> str:
    """Decide what a fenced worker may do from the canonical state.

    Returns ``"run"`` when the worker matches canonical active,
    ``"await"`` when it matches the not-yet-active candidate (forward
    switch) or the rollback-pending previous (alive-previous restore),
    and raises :class:`FenceLost` otherwise (design v2 §5D: a candidate
    agent awaits activation and never observes/acts before commit).
    """
    if _identity_of(state.get("active")) == fence:
        return "run"
    if _identity_of(state.get("candidate")) == fence:
        return "await"
    if _identity_of(state.get("previous")) == fence:
        return "await"
    raise FenceLost("canonical に一致する runtime がありません")


def read_canonical(state_dir: Path) -> Mapping[str, object]:
    """Return the canonical state dict.  Corruption propagates (fail closed)."""
    from ..game_switch import GameSwitchStore

    state, _ = GameSwitchStore(Path(state_dir)).canonical.load()
    return state


def shared_section(state_dir: Path, fn, *, timeout_s: float = 5.0):
    """Run ``fn`` under a short shared game-switch lock (design v2 §6).

    The coordinator holds the exclusive lock across transitions, so a short
    shared section keeps a single observe/action from racing quiesce.  Waits
    are bounded: on contention the caller gets GameSwitchBusyError instead
    of blocking forever.
    """
    import time

    from ..game_switch import GameSwitchStore, GameSwitchBusyError

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with GameSwitchStore(Path(state_dir)).lock(exclusive=False):
                return fn()
        except GameSwitchBusyError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def active_fence(state_dir: Path) -> Mapping[str, object] | None:
    """Return the canonical active runtime dict, or None when idle.

    State corruption propagates instead of being guessed at (fail closed).
    """
    from ..game_switch import GameSwitchStore

    state, _ = GameSwitchStore(Path(state_dir)).canonical.load()
    active = state.get("active")
    if not isinstance(active, dict):
        return None
    return dict(active)
