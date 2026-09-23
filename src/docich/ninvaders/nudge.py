"""Driver-level workaround shared by the live player and the evaluator.

Measured on the real game: after the first game in a nInvaders process, every
new game starts with the cannon NOT drawn - ncurses only paints it once a key
arrives.  A policy that sends nothing while it cannot see the cannon then waits
forever and scores 0 until the aliens invade (seen as a 0-point second match
that lasted ~92 s).  A genuine cannon explosion also hides the cannon, but only
for ~0.5 s, so once the cannon has been missing longer than that we press Space
(fires + repaints).  This belongs in the trusted driver, not in policy code, so
every candidate policy benefits and evaluation matches production.
"""
from __future__ import annotations


class Nudge:
    def __init__(self, after_ticks: int = 8, every: int = 3):
        self.after_ticks = int(after_ticks)  # 0.8 s at 10 Hz > the ~0.5 s explosion
        self.every = int(every)
        self.blind = 0

    def keys(self, obs: dict) -> list[str]:
        if obs.get("kind") != "play" or obs.get("player") is not None:
            self.blind = 0
            return []
        self.blind += 1
        if self.blind >= self.after_ticks and (self.blind - self.after_ticks) % self.every == 0:
            return ["Space"]
        return []
