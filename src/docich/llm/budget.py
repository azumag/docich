"""Chain wall-budget for the native LLM dispatch (#829 PR-1).

Mirrors ``lib/ai_prepass_budget.sh``: a chain carries an absolute monotonic
deadline.  When the deadline passes, the chain ends as an *empty success*
(rc 0, no text) with a ``budget_exhausted`` telemetry event — never as a
provider failure, and never recording failure backoff.
"""

from __future__ import annotations

import time


class ChainBudget:
    """Absolute monotonic deadline for one provider chain."""

    def __init__(self, total_sec: float | None = None) -> None:
        self._deadline: float | None = None
        if total_sec is not None and total_sec > 0:
            self._deadline = time.monotonic() + total_sec

    @classmethod
    def from_deadline(cls, deadline: float) -> "ChainBudget":
        obj = cls()
        obj._deadline = deadline
        return obj

    def remaining(self, now: float | None = None) -> float | None:
        if self._deadline is None:
            return None
        at = time.monotonic() if now is None else now
        return max(0.0, self._deadline - at)

    def exhausted(self, now: float | None = None) -> bool:
        if self._deadline is None:
            return False
        at = time.monotonic() if now is None else now
        return at >= self._deadline

    def clamp_timeout(self, timeout: int, now: float | None = None) -> int:
        """Cap a per-call timeout to the remaining chain budget."""
        remaining = self.remaining(now)
        if remaining is None:
            return timeout
        return max(1, min(timeout, int(remaining)))
