"""External probes and relay reload for the direct-stream supervisor
(#3553, ADR 0007 C3).

Ported from soviet_now ``lib/direct_stream.py`` at 0971b36
(``twitch_channel_is_live``, ``_reload_relay``, ``probe_audio_capture_state``).
Re-diff against the then-current soviet_now before the production cutover
(ADR 0007 stage E).

Every external effect is an injectable callable (``urlopen``, ``run``), so the
tests never touch Twitch, ``sudo``, nginx or PulseAudio. The Twitch probe is the
public, credential-less GraphQL ``stream`` presence check. ``reload_relay`` is
best-effort and only ever runs the three fixed relay-reload argv lists.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Callable, Mapping
import urllib.error
import urllib.request

from .stream_host_checks import RuntimeCheckError, checked
from .stream_supervision_policy import classify_source_output_listing

STREAM_EXTERNAL_PROBES_CONTRACT_VERSION = 1
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

RELAY_RELOAD_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("sudo", "-n", "systemctl", "reload", "soren-rtmp-relay.service"),
    ("sudo", "-n", "nginx", "-s", "reload", "-c", "/etc/soren-rtmp/nginx.conf"),
    ("nginx", "-s", "reload", "-c", "/etc/soren-rtmp/nginx.conf"),
)


def twitch_channel_is_live(
    channel: str,
    timeout: int = 10,
    *,
    urlopen: Callable[..., object] = urllib.request.urlopen,
) -> bool | None:
    """True when publishing, False when offline, None when the probe failed.

    None is deliberately "unknown": a transient network/parse error must never
    turn into an offline streak. The channel is interpolated into the query, so
    anything outside ``[A-Za-z0-9_]{1,32}`` is refused (returns None).
    """
    if not channel or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", channel):
        return None
    payload = {"query": f"query {{ user(login: \"{channel}\") {{ stream {{ id }} }} }}"}
    request = urllib.request.Request(
        TWITCH_GQL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Client-ID": TWITCH_WEB_CLIENT_ID,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # type: ignore[operator]
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError):
        return None
    try:
        stream = body["data"]["user"]["stream"]
    except (KeyError, TypeError):
        return None
    return stream is not None


def reload_relay(
    *,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
    cwd: str | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Best-effort relay reload so nginx re-establishes its Twitch pushes."""
    emit = log or (lambda text: print(text, file=sys.stderr))
    for command in RELAY_RELOAD_COMMANDS:
        try:
            result = run(
                list(command),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            emit(f"direct_stream: relay reload skipped ({exc})")
            continue
        if result.returncode == 0:
            return True
        emit(
            f"direct_stream: relay reload failed rc={result.returncode} "
            f"{result.stderr.strip()} cmd={' '.join(command)}"
        )
    return False


def probe_audio_capture_state(
    pulse_server: str,
    ffmpeg_pid: int,
    *,
    base_env: Mapping[str, str],
    run: Callable[..., str] = checked,
) -> str:
    """Classify the encoder's Pulse capture stream (see the policy module).

    ``ok`` / ``missing`` / ``unknown`` come from the listing classification;
    ``unreachable`` means the daemon did not answer, so the cause is outside
    FFmpeg. Only ``missing`` may restart FFmpeg.
    """
    env = dict(base_env)
    env["PULSE_SERVER"] = pulse_server
    try:
        run(["pactl", "info"], env=env)
    except RuntimeCheckError:
        return "unreachable"
    try:
        listing = run(["pactl", "list", "source-outputs"], env=env)
    except RuntimeCheckError:
        return "unknown"
    return classify_source_output_listing(listing, ffmpeg_pid)
