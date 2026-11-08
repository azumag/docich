"""Host prerequisite checks for the direct-stream encoder (#3553, ADR 0007 C1).

Ported from soviet_now ``lib/direct_stream.py`` at 0971b36 (``_checked``,
``validate_local_relay``, ``caption_capabilities_available``,
``prepare_caption_runtime``, ``validate_runtime``). Re-diff against the then-
current soviet_now before the production cutover (ADR 0007 stage E).

The checks only *observe* the host (run ``ffmpeg -devices``, ``xdpyinfo``,
``pactl list``, connect to the loopback relay). Every external effect is an
injectable callable so tests run without ffmpeg, X11 or PulseAudio. The one
write is ``prepare_caption_runtime``'s private caption-socket directory.
Nothing here starts an encoder, signals a process or touches a lock.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

STREAM_HOST_CHECKS_CONTRACT_VERSION = 1


class RuntimeCheckError(RuntimeError):
    """A host prerequisite is missing or unhealthy."""


def checked(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> str:
    try:
        result = run(
            list(command),
            env=None if env is None else dict(env),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeCheckError(f"failed to run prerequisite check: {command[0]}") from exc
    if result.returncode != 0:
        raise RuntimeCheckError(f"prerequisite check failed: {command[0]}")
    return result.stdout + result.stderr


def validate_local_relay(
    local_rtmp_url: str,
    *,
    connect: Callable[..., object] = socket.create_connection,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> None:
    parsed = urlsplit(local_rtmp_url)
    host = parsed.hostname
    port = parsed.port or 1935
    if host is None:
        raise RuntimeCheckError("loopback RTMP relay host is missing")
    try:
        connection = connect((host, port), timeout=2)
    except OSError as exc:
        # nginx master may be running outside systemd (manual
        # `nginx -c /etc/soren-rtmp/nginx.conf`); treat that as healthy so a
        # boot race does not report a false `not reachable`.
        try:
            probe = run(
                ["pgrep", "-f", r"nginx: master.*soren-rtmp"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise RuntimeCheckError("loopback RTMP relay is not reachable") from exc
    connection.close()  # type: ignore[attr-defined]


def caption_capabilities_available(
    config: Mapping[str, object],
    *,
    check: Callable[..., str] = checked,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Whether this FFmpeg can inject and encode A/53 captions.

    Caption support is additive and fail-open: a stock FFmpeg must never
    prevent the audio/video stream from starting.
    """
    emit = log or (lambda text: print(text, file=sys.stderr))
    if not config["closed_captions_enabled"]:
        return False
    ffmpeg_bin = str(config["ffmpeg_bin"])
    try:
        filters = check([ffmpeg_bin, "-hide_banner", "-filters"])
        encoder_help = check([ffmpeg_bin, "-hide_banner", "-h", "encoder=libx264"])
    except RuntimeCheckError as exc:
        emit(f"direct_stream: closed captions disabled ({exc})")
        return False
    if not re.search(r"^\s*\.\S*\s+docichcc\s", filters, re.MULTILINE):
        emit("direct_stream: closed captions disabled (docichcc filter is unavailable)")
        return False
    if not re.search(r"(?:^|\s)-?a53cc(?:\s|$)", encoder_help):
        emit("direct_stream: closed captions disabled (libx264 a53cc is unavailable)")
        return False
    return True


def prepare_caption_runtime(
    config: Mapping[str, object],
    *,
    geteuid: Callable[[], int] = os.geteuid,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Create a private socket directory, or disable captions without failing."""
    emit = log or (lambda text: print(text, file=sys.stderr))
    if not config["closed_captions_enabled"]:
        return False
    parent = Path(str(config["closed_captions_socket"])).parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError("socket parent is not a real directory")
        if metadata.st_uid != geteuid():
            raise OSError("socket parent is not owned by this user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            parent.chmod(0o700)
    except OSError as exc:
        emit(f"direct_stream: closed captions disabled (socket directory: {exc})")
        return False
    return True


def validate_runtime(
    config: Mapping[str, object],
    *,
    mode: str,
    check: Callable[..., str] = checked,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[str], bool] = lambda value: Path(value).is_file(),
    connect: Callable[..., object] = socket.create_connection,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Validate the host for ``mode``; return whether captions are usable."""
    if platform != "linux":
        raise RuntimeCheckError("FFmpeg direct streaming is Linux-only")
    if mode == "live" and config["backend"] != "ffmpeg":
        raise RuntimeCheckError("live direct streaming requires SOREN_STREAM_BACKEND=ffmpeg")
    if mode == "live":
        validate_local_relay(str(config["local_rtmp_url"]), connect=connect, run=run)

    ffmpeg_bin = str(config["ffmpeg_bin"])
    if which(ffmpeg_bin) is None and not is_file(ffmpeg_bin):
        raise RuntimeCheckError("FFmpeg executable was not found")
    devices = check([ffmpeg_bin, "-hide_banner", "-devices"])
    if not re.search(r"^\s*D\S*\s+x11grab\s", devices, re.MULTILINE):
        raise RuntimeCheckError("FFmpeg does not provide x11grab input")
    if not re.search(r"^\s*D\S*\s+pulse\s", devices, re.MULTILINE):
        raise RuntimeCheckError("FFmpeg does not provide PulseAudio input")
    encoders = check([ffmpeg_bin, "-hide_banner", "-encoders"])
    if not re.search(r"^\s*V\S*\s+libx264\s", encoders, re.MULTILINE):
        raise RuntimeCheckError("FFmpeg does not provide libx264")

    if which("xdpyinfo") is None:
        raise RuntimeCheckError("xdpyinfo was not found")
    display_info = check(["xdpyinfo", "-display", str(config["display"])])
    match = re.search(r"dimensions:\s*([0-9]+)x([0-9]+)", display_info)
    if not match:
        raise RuntimeCheckError("could not determine X11 display dimensions")
    display_width, display_height = int(match.group(1)), int(match.group(2))
    if display_width < int(config["width"]) or display_height < int(config["height"]):
        raise RuntimeCheckError(
            f"X11 display is {display_width}x{display_height}, smaller than requested capture"
        )

    if which("pactl") is None:
        raise RuntimeCheckError("pactl was not found")
    sources = check(["pactl", "list", "short", "sources"])
    source_names = {
        fields[1]
        for line in sources.splitlines()
        if len(fields := line.split()) >= 2
    }
    if str(config["pulse_source"]) not in source_names:
        raise RuntimeCheckError(f"PulseAudio source was not found: {config['pulse_source']}")
    return caption_capabilities_available(config, check=check, log=log)
