"""Read-only terminal observations shared by scheduling and slot ownership."""
import datetime as dt
import math

from .game_switch import GameSwitchStore


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
    completed = state.get("completed_at")
    try:
        if isinstance(completed, str):
            parsed = dt.datetime.fromisoformat(completed)
            if parsed.tzinfo is None:
                return state
            completed = parsed.timestamp()
        if type(completed) not in (int, float) or not math.isfinite(completed) or completed < 0:
            return state
    except (ValueError, OverflowError, OSError):
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
