"""Pure supervision policy for the single direct-stream encoder (#3553).

Ownership: docich streaming infrastructure. This module only parses an explicit
environment mapping into validated policy dicts and classifies a PulseAudio
``source-outputs`` listing text. It never reads ``os.environ``, runs ``pactl``,
probes Twitch, restarts FFmpeg, or touches any live service. Callers pass the
environment and the already-captured listing; supervision itself stays with the
caller during the staged migration.

The legacy Soren caller computes its own result first and adopts this one only
when it is identical.
"""
from __future__ import annotations

import re
from typing import Mapping

STREAM_SUPERVISION_POLICY_CONTRACT_VERSION = 1


def _strict_int(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(env.get(key, default)).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError(f"{key} must be an integer")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _strict_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean")


# Public aliases so sibling stream modules reuse one strict parser.
strict_int = _strict_int
strict_bool = _strict_bool


def load_reconnect_policy(env: Mapping[str, str]) -> dict[str, object]:
    """Viewer-side reconnect policy (probe the public channel, restart on a
    confirmed offline streak). The channel is restricted to ``[A-Za-z0-9_]``
    because it is later embedded into a GraphQL query."""
    channel = str(
        env.get("SOREN_DIRECT_STREAM_TWITCH_CHANNEL")
        or env.get("TWITCH_CHANNEL")
        or ""
    ).strip()
    if channel and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", channel):
        raise ValueError("SOREN_DIRECT_STREAM_TWITCH_CHANNEL is invalid")
    return {
        "enabled": _strict_bool(env, "SOREN_DIRECT_STREAM_RECONNECT_ENABLED", True),
        "twitch_channel": channel,
        "probe_interval_sec": _strict_int(env, "SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC", 60, 10, 600),
        "offline_threshold": _strict_int(env, "SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD", 2, 1, 10),
        "backoff_sec": _strict_int(env, "SOREN_DIRECT_STREAM_RECONNECT_BACKOFF_SEC", 10, 1, 300),
        "max_consecutive_restarts": _strict_int(
            env, "SOREN_DIRECT_STREAM_RECONNECT_MAX_CONSECUTIVE", 10, 1, 100
        ),
        "reload_relay": _strict_bool(env, "SOREN_DIRECT_STREAM_RECONNECT_RELOAD_RELAY", True),
    }


def load_audio_watchdog_policy(env: Mapping[str, str], *, uid: int) -> dict[str, object]:
    """Pulse capture watchdog policy. The daemon must be a local unix socket."""
    pulse_server = str(env.get("PULSE_SERVER") or f"unix:/run/user/{uid}/pulse/native").strip()
    if not re.fullmatch(r"unix:/[A-Za-z0-9._/-]+", pulse_server) or ".." in pulse_server:
        raise ValueError("PULSE_SERVER must be a local unix socket path")
    return {
        "enabled": _strict_bool(env, "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED", True),
        "probe_interval_sec": _strict_int(env, "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_PROBE_SEC", 10, 5, 120),
        "loss_threshold": _strict_int(env, "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_LOSS_THRESHOLD", 2, 1, 10),
        "pulse_server": pulse_server,
    }


def classify_source_output_listing(listing: str, ffmpeg_pid: int) -> str:
    """Classify ``pactl list source-outputs`` text for one FFmpeg process.

    ``ok``       this FFmpeg owns a listed source output.
    ``missing``  streams were described (or none at all) and every one was
                 attributable to another process.
    ``unknown``  the listing was not understood, or at least one stream exposes
                 no process id so it might be ours.

    Only ``missing`` may restart FFmpeg; anything uncertain must stay
    ``unknown`` so a healthy encoder is never taken down (docich#2008).
    """
    if listing.strip() and not re.search(r"^Source Output #[0-9]+", listing, re.MULTILINE):
        return "unknown"
    blocks = re.split(r"^Source Output #\d+", listing, flags=re.MULTILINE)[1:]
    if not blocks:
        return "missing"
    unattributable = False
    for block in blocks:
        match = re.search(r'application\.process\.id = "([0-9]+)"', block)
        if match is None:
            unattributable = True
            continue
        if int(match.group(1)) == int(ffmpeg_pid):
            return "ok"
    return "unknown" if unattributable else "missing"
