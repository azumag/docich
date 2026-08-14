"""Adapter contract: lifecycle + AI I/O for a docich game (architecture.md SS3)."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..actions import Action
from ..config import GameConfig, GlobalConfig
from ..state import State
from ..tmux import Tmux
from ..xkit import XKit


@dataclass
class AdapterContext:
    g: GlobalConfig
    game: GameConfig
    state: State
    tmux: Tmux
    xkit: XKit


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

    @abstractmethod
    def command(self) -> list[str]:
        """Return the argv used to launch the game process."""

    def env(self) -> dict:
        # PULSE_SINK により既定 sink を変更せずに docich 配下の音声だけを
        # docich_sink へルーティングする (soren 共存。architecture.md SS0)。
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
