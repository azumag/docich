"""Pulse prerequisites for the docich-owned encoder (#3553, ADR 0007 stage D).

Ported from the PulseAudio part of soviet_now ``wait_soren_runtime_prereqs.sh``
(0971b36). It deliberately does *less* than that script:

* It never starts, restarts or spawns PulseAudio. The canonical server belongs
  to the ``ubuntu`` user manager (``pulseaudio.socket``); if it does not answer
  within the deadline this fails and the supervisor does not start, so a
  silent (video-only) broadcast is never begun (docich#2008).
* It never sets the default sink/source; the encoder addresses its capture
  source explicitly.
* The only mutation is loading ``module-null-sink`` for the configured sink
  when it is missing, with the same arguments as the legacy script.

All external effects are injectable.
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Callable, Mapping

STREAM_PREREQS_CONTRACT_VERSION = 1
SINK_NAME = re.compile(r"[A-Za-z0-9_.-]+")


class PrerequisiteError(RuntimeError):
    """A runtime prerequisite is not ready."""


def sink_for_source(pulse_source: str) -> str | None:
    """``soren_null.monitor`` -> ``soren_null``; anything else -> None."""
    if pulse_source.endswith(".monitor"):
        sink = pulse_source[: -len(".monitor")]
        if SINK_NAME.fullmatch(sink):
            return sink
    return None


def wait_seconds(env: Mapping[str, str]) -> int:
    raw = str(env.get("SOREN_RUNTIME_PREREQ_WAIT_SEC", "90")).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError("runtime prerequisite timeout is invalid")
    value = int(raw)
    if not 5 <= value <= 300:
        raise ValueError("runtime prerequisite timeout must be between 5 and 300 seconds")
    return value


def ensure_pulse_ready(
    *,
    pulse_server: str,
    pulse_source: str,
    wait_sec: int,
    base_env: Mapping[str, str],
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Wait for the canonical Pulse server; create the null sink if missing."""
    env = dict(base_env)
    env["PULSE_SERVER"] = pulse_server

    def pactl(*args: str) -> "subprocess.CompletedProcess[str]":
        try:
            return run(["pactl", *args], env=env, text=True, capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PrerequisiteError(f"pactl could not be run: {exc.__class__.__name__}") from exc

    deadline = monotonic() + wait_sec
    while pactl("info").returncode != 0:
        if monotonic() >= deadline:
            raise PrerequisiteError("PulseAudio did not become ready before the runtime deadline")
        sleep(1)

    sink = sink_for_source(pulse_source)
    if sink is None:
        return
    listing = pactl("list", "short", "sinks")
    if listing.returncode != 0:
        raise PrerequisiteError("PulseAudio sinks could not be listed")
    names = {fields[1] for line in listing.stdout.splitlines() if len(fields := line.split()) >= 2}
    if sink in names:
        return
    created = pactl("load-module", "module-null-sink", f"sink_name={sink}",
                    f"sink_properties=device.description={sink}")
    if created.returncode != 0:
        raise PrerequisiteError(f"PulseAudio sink could not be created: {sink}")
