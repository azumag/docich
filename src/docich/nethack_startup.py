"""Bounded character-creation gate, separate from the P3b gameplay policy.

Only returns Actions; never sends keys itself or bypasses the agent lease fence.
No save/restore, recovery, or new-run restart automation lives here.
Resume pagers can hide the player before the first complete map. Advance a
normalized ``more`` only with visible living-game vitals or a previously
accepted exact restore banner, within the existing startup budgets. The
legacy restore-banner and character-creation paths remain separate.
"""
from __future__ import annotations

import re
import sys
import time
from typing import Callable

from .actions import Action
from .nethack_observation import NethackObservation

# A saved run being resumed (the program boundary's normal save, restored by the
# next start) opens with "Restoring save file...--More--" above the copyright
# banner.  The game cannot draw the map until this is dismissed, and the
# character-creation prompts that arm ``_creation_seen`` never appear on a
# resume, so without this the gate held the frame until it was exhausted.
# Deliberately specific: only this fixed NetHack message, only before gameplay.
_RESTORE_MORE_RE = re.compile(r"restoring save file\.*\s*--more--")
# The tutorial y/n can be drawn on a lower row over the first map.
_TUTORIAL_RE = re.compile(r"do you want a tutorial\?\s*\[yn")


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
        self._restore_seen = False

    def _state(self, state: str) -> None:
        if self.state != state:
            self.state = state
            # Fixed enum only: never log raw TTY/prompt text.
            print(f'[nethack-startup] state={state}', file=sys.stderr)

    def consider(self, obs: NethackObservation) -> list[Action] | None:
        """None delegates to P3b; [] holds without falling through to policy."""
        if self.state in {"disabled", "gameplay"}:
            return None
        # Budgets bound startup answers, not recognition that startup finished.
        # A delayed redraw (or an externally cleared overlay) can reveal a
        # complete gameplay frame after a timeout. Delegate it to the normal
        # policy instead of latching [] forever. This sends no key, does not
        # replenish any budget, and cannot rearm character-creation answers.
        # Prompts, ambiguous players, missing vitals and death remain blocked.
        # obs.prompt reads only row 0, so a tutorial question drawn on a lower
        # row must still reach the startup answer below, not the policy.
        if (
            obs.prompt == "none"
            and not _TUTORIAL_RE.search(" ".join(obs.raw_text.lower().split()))
            and obs.player is not None
            and obs.vitals.dungeon_level is not None
            and obs.vitals.hp is not None and obs.vitals.hp > 0
            and obs.vitals.hp_max is not None and obs.vitals.hp_max > 0
        ):
            self._state("gameplay")
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
            (_TUTORIAL_RE.pattern, "n"),
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
        restore_more = not has_gameplay and bool(_RESTORE_MORE_RE.search(text))
        if key is None and restore_more:
            key = " "
        if key is None and obs.prompt == "more" and (
            self._restore_seen
            or (
                obs.vitals.dungeon_level is not None
                and obs.vitals.hp is not None and obs.vitals.hp > 0
                and obs.vitals.hp_max is not None and obs.vitals.hp_max > 0
            )
        ):
            # A resumed game's text can cover '@' without character creation
            # in this session. Only recognized pagers with resume evidence
            # qualify; arbitrary pre-game '--More--' remains blocked. Common
            # budgets and duplicate-frame suppression still apply below.
            key = " "
        if key is None and self._creation_seen and "--more--" in text:
            # The intro/--More-- screen can show the status lines below the
            # prompt, so it is not the last line of the frame.
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
        if restore_more and key == " ":
            # The brain rolls this evidence back if fresh-frame validation
            # rejects the action before transport (startup checkpoint).
            self._restore_seen = True
        self._last_answered = text
        self.actions += 1
        self._state("answering")
        return [Action(type="text", text=key)]
