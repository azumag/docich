"""Adapter contract: lifecycle + AI I/O for a docich game (architecture.md §3)."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..actions import Action
from ..config import GameConfig, GlobalConfig
from ..state import State
from ..tmux import Tmux
from ..xkit import XKit

if TYPE_CHECKING:  # avoid a hard import cycle at runtime
    from ..agent.fence import AgentFence


@dataclass
class AdapterContext:
    g: GlobalConfig
    game: GameConfig
    state: State
    tmux: Tmux
    xkit: XKit
    # P3 activation fence (design v2 §6).  None means the legacy unfenced
    # mode: no fence check is performed.
    fence: "AgentFence | None" = None


@dataclass
class Observation:
    game: str
    title: str
    adapter: str
    ts: float
    kind: str  # "text" | "screenshot" | "both"
    text: str | None = None
    screenshot: str | None = None
    meta: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "game": self.game,
                "title": self.title,
                "adapter": self.adapter,
                "ts": self.ts,
                "kind": self.kind,
                "text": self.text,
                "screenshot": self.screenshot,
                "meta": self.meta,
            },
            ensure_ascii=False,
        )


class AdapterError(Exception):
    """Raised for adapter resolution/lifecycle failures."""


class Adapter(ABC):
    name: str = ""

    def __init__(self, ctx: AdapterContext):
        self.ctx = ctx

    def _check_fence(self) -> None:
        """Validate the activation fence against canonical active (P3).

        No-op when the context carries no fence (legacy mode).  The
        coordinator-owned paths always bind a fence; stale workers raise
        FenceLost here as well as in the agent loop.
        """
        from ..agent.fence import active_fence, check_fence

        fence = getattr(self.ctx, "fence", None)
        if fence is None:
            return
        check_fence(fence, active_fence(self.ctx.g.state_dir))

    @abstractmethod
    def command(self) -> list[str]:
        """Return the argv used to launch the game process."""

    def env(self) -> dict:
        # PULSE_SINK により既定 sink を変更せずに docich 配下の音声だけを
        # docich_sink へルーティングする (soren 共存。architecture.md §0)。
        e = {"DISPLAY": self.ctx.g.display.name}
        if self.ctx.g.audio.enabled:
            e["PULSE_SINK"] = self.ctx.g.audio.sink_name
        return e

    def prepare(self) -> None:
        """Idempotent pre-launch hook (cfg generation, ROM checks, ...).

        Called by supervise on every respawn, so it must be safe to call
        repeatedly.
        """

    def cleanup(self) -> None:
        """Post-stop hook. Only called by `docich stop`."""

    @abstractmethod
    def observe(self) -> Observation:
        """Return the current Observation for the brain loop."""

    @abstractmethod
    def act(self, action: Action) -> None:
        """Inject a single Action into the running game."""
