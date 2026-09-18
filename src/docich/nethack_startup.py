"""Bounded character-creation gate, separate from the P3b gameplay policy.

Only returns Actions; never sends keys itself or bypasses the agent lease fence.
No save/restore, recovery, gameplay prompt, or new-run restart automation lives here.
"""
from __future__ import annotations

import re
import sys
import time
from typing import Callable

from .actions import Action
from .nethack_observation import NethackObservation


class NethackStartup:
    MAX_OBSERVATIONS = 40
    MAX_ACTIONS = 12
    TIMEOUT_S = 60.0

    def __init__(self, *, enabled: bool = False, clock: Callable[[], float] = time.monotonic):
        self.enabled = enabled
        self.clock = clock
        self.state = "pending" if enabled else "disabled"
        self.started_at: float | None = None
        self.observations = 0
        self.actions = 0
        self._last_answered = ""
        self._creation_seen = False

    def _state(self, state: str) -> None:
        if self.state != state:
            self.state = state
            # Fixed enum only: never log raw TTY/prompt text.
            print(f'[nethack-startup] state={state}', file=sys.stderr)

    def consider(self, obs: NethackObservation) -> list[Action] | None:
        """None delegates to P3b; [] holds without falling through to policy."""
        if self.state in {"disabled", "gameplay"}:
            return None
        if self.state == "exhausted":
            return []
        now = self.clock()
        if self.started_at is None:
            self.started_at = now
        if now - self.started_at >= self.TIMEOUT_S or self.observations >= self.MAX_OBSERVATIONS:
            self._state("exhausted")
            return []
        self.observations += 1

        # Require a whole visible question, not a substring in unrelated text.
        # Whitespace may wrap at the normal 80-column terminal width.
        text = " ".join(obs.raw_text.lower().split())
        key = None
        # NetHack 5.0 wording varies by options: role/race order, an Oxford
        # comma before the final item, and a "(y)" default.  Match the reviewed
        # phrase plus a y/n prompt instead of one exact sentence.  "shall i
        # pick a character" deliberately does not match unrelated prompts such
        # as "Shall I pick your weapon? [yn]".
        patterns = (
            (r"do you want a tutorial\?\s*\[yn", "n"),
            (r"shall i pick (?:a )?character[^?]*\?\s*\[yn", "y"),
            (r"pick a character\?\s*\[yn", "y"),
            (r"is this ok\?\s*\[yn", "y"),
        )
        # Only the initial selection/confirmation screens can consume y/n.
        # A tutorial may overlay an already drawn map before the first turn.
        has_gameplay = obs.player is not None and obs.vitals.hp is not None
        for pattern, answer in patterns:
            if has_gameplay and answer != "n":
                continue
            if re.search(pattern, text):
                key = answer
                self._creation_seen = True
                break
        if key is None and has_gameplay:
            self._state("gameplay")
            return None
        if key is None and self._creation_seen and text.endswith("--more--"):
            key = " "
        if key is None:
            self._state("unknown")
            return []
        if self.actions >= self.MAX_ACTIONS:
            self._state("exhausted")
            return []
        if text == self._last_answered:
            self._state("waiting")
            return []
        self._last_answered = text
        self.actions += 1
        self._state("answering")
        return [Action(type="text", text=key)]
