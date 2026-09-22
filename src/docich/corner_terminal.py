"""Read-only terminal observations shared by scheduling and slot ownership."""
import datetime as dt
import math

from .game_switch import GameSwitchStore


def _bounded_timestamp(value):
    """Finite non-negative epoch seconds, or None for anything else."""
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except (ValueError, OverflowError, OSError):
            return None
        if parsed.tzinfo is None:
            return None
        try:
            value = parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def failed_improvement_is_terminal(status, state):
    """Whether a ``failed`` improvement record proves its child already exited.

    The detached improvement child writes ``running`` before its work and a
    terminal record before it exits while it holds the single-flight lock.  A
    ``failed`` record that is fresh for this corner run is therefore durable
    terminal evidence once the caller has observed a free lock.  Missing,
    stale, malformed or recovery-required records stay fail-closed: a
    SIGKILLed child leaves ``running`` metadata behind, and an old record can
    belong to a replacement job that never started.
    """
    if status.get("recovery_required") is True:
        return False
    started = _bounded_timestamp(status.get("started_at"))
    completed = _bounded_timestamp(status.get("completed_at"))
    corner_completed = _bounded_timestamp(state.get("completed_at"))
    if started is None or completed is None or corner_completed is None:
        return False
    return started >= corner_completed and completed >= started


def normalize_terminal_paper_failure(state_dir, state, target_game="paper-view"):
    """Treat a failed PAPER record as terminal only after canonical proof.

    A manual PAPER run can record ``failed`` after its restore attempt even
    though a later owner has already returned the canonical display to the
    recorded previous game.  The record is intentionally kept for diagnosis;
    it must not, by itself, pin the unified scheduler forever.  Any missing or
    unstable canonical evidence remains fail-closed.
    """
    if (state.get("status") != "failed"
            or state.get("recovery_required", False) is not False
            or state.get("last_error_code") == "recovery_required"
            or state.get("completed_at") is None
            or state.get("game") not in (None, target_game)):
        return state
    if "previous_game" not in state:
        return state
    # Match the rotation ledger's timestamp contract also when program_lock
    # calls this observation directly, without passing through that ledger.
    if _bounded_timestamp(state.get("completed_at")) is None:
        return state
    previous = state.get("previous_game")
    if previous == target_game or (previous is not None and not isinstance(previous, str)):
        return state
    try:
        canonical, missing = GameSwitchStore(state_dir).canonical.load()
    except Exception:
        return state
    if missing:
        return state
    phase = canonical.get("phase")
    active = canonical.get("active")
    if previous is None:
        terminal = phase == "idle" and active is None
    else:
        terminal = (
            phase == "ready"
            and isinstance(active, dict)
            and active.get("game") == previous
        )
    if not terminal:
        return state
    normalized = dict(state)
    normalized["status"] = "completed"
    return normalized
