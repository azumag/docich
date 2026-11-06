"""Pure restart decisions for the single direct-stream encoder (#3553).

Ownership: docich streaming infrastructure. Each function maps the previous
counter state plus one probe observation to the next counter state and a
``restart`` verdict. Nothing here sleeps, probes Twitch or PulseAudio, sends a
signal, starts a thread or touches a process: the caller owns all of that and
only asks "should this encoder be restarted now?".

The legacy Soren caller computes its own verdict first and adopts this one only
when it is identical, so this module can never add a restart of a live encoder.
"""
from __future__ import annotations

STREAM_MONITOR_POLICY_CONTRACT_VERSION = 1


def step_twitch_offline_streak(
    offline_streak: int,
    live: bool | None,
    offline_threshold: int,
) -> tuple[int, bool]:
    """Advance the viewer-side offline streak by one public-channel probe.

    ``live is None`` is a transient probe failure: it never counts toward the
    streak and never restarts. A confirmed ``True`` resets the streak. Any
    other observation is offline and restarts only once the streak reaches the
    threshold.
    """
    if live is None:
        return int(offline_streak), False
    if live is True:
        return 0, False
    streak = int(offline_streak) + 1
    return streak, streak >= offline_threshold


def step_audio_capture_loss(
    state: str,
    consecutive: int,
    seen_capture: bool,
    loss_threshold: int,
) -> tuple[int, bool, bool]:
    """Advance the Pulse capture watchdog by one classified probe.

    Returns ``(consecutive, seen_capture, restart)``. Only ``missing`` after
    this run has been seen ``ok`` may count; an unreachable daemon, an unknown
    listing or a capture never observed reset the counter and never restart,
    because restarting on a guess would take a healthy encoder down
    (docich#2008).
    """
    if state == "ok":
        seen_capture = True
        consecutive = 0
    elif state == "missing" and seen_capture:
        consecutive = int(consecutive) + 1
    else:
        consecutive = 0
    restart = state == "missing" and seen_capture and consecutive >= loss_threshold
    return consecutive, bool(seen_capture), restart
