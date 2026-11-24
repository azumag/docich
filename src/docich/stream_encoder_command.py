"""Pure FFmpeg command construction for the single direct-stream encoder.

Ownership: docich streaming infrastructure (#3553). This module only builds an
argv list and parses/classifies FFmpeg output. It never imports the Soviet game,
spawns a process, opens a socket or file, reads credentials, or touches a live
service. Inputs are explicit keyword arguments (no game config object), and the
output URL is always checked by the loopback-only guard so an external publish
URL or a stream key can never reach argv.

The legacy Soren caller computes its own argv first and adopts this result only
when it is byte-for-byte identical.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
from typing import Sequence

STREAM_ENCODER_COMMAND_CONTRACT_VERSION = 1

_MODES = frozenset({"live", "record"})


def _loopback_guard():
    # Sibling file load so the module also works when a legacy caller imports
    # it by file path (no package context) from a staged docich checkout.
    here = Path(__file__).resolve()
    spec = importlib.util.spec_from_file_location(
        "docich_stream_output_guard_for_encoder", here.with_name("stream_output_guard.py")
    )
    if not spec or not spec.loader:
        raise ValueError("loopback output guard is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_loopback_rtmp_url


def validate_caption_socket_path(raw: str) -> str:
    """Restrict the caption socket to a filter-safe absolute path.

    The value is embedded into ``-vf docichcc=socket=...``; any character
    outside this subset could inject another filter or option.
    """
    if not isinstance(raw, str) or not raw or any(c in raw for c in "\r\n\x00"):
        raise ValueError("DOCICH_CC_SOCKET is invalid")
    if not re.fullmatch(r"/[A-Za-z0-9_./@+-]+", raw):
        raise ValueError("DOCICH_CC_SOCKET contains unsupported characters")
    path = Path(raw)
    if (
        not path.is_absolute()
        or path.name in {"", ".", ".."}
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("DOCICH_CC_SOCKET must be an absolute Unix socket path")
    if len(os.fsencode(path)) >= 104:
        raise ValueError("DOCICH_CC_SOCKET must be shorter than 104 bytes")
    return raw


def build_direct_stream_ffmpeg_command(
    *,
    ffmpeg_bin: str,
    display: str,
    size: str,
    fps: int,
    video_kbps: int,
    audio_kbps: int,
    audio_delay_ms: int,
    pulse_source: str,
    pulse_sample_rate: int,
    pulse_channels: int,
    local_rtmp_url: str,
    mode: str,
    closed_captions_active: bool,
    closed_captions_socket: str = "",
    output_path: str | None = None,
    duration_sec: int | None = None,
) -> list[str]:
    """Return the argv for one x11grab+pulse -> libx264/aac encoder run."""
    if mode not in _MODES:
        raise ValueError("mode must be live or record")
    if mode == "record" and output_path is None:
        raise ValueError("record mode requires an output path")
    if duration_sec is not None and not 1 <= duration_sec <= 86400:
        raise ValueError("record duration must be between 1 and 86400 seconds")
    if mode == "live":
        # Never allow an external destination or credentials into argv.
        _loopback_guard()(local_rtmp_url)
    if closed_captions_active:
        validate_caption_socket_path(closed_captions_socket)

    fps_text = str(fps)
    gop = str(fps * 2)
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats_period",
        "1",
        "-thread_queue_size",
        "1024",
        "-f",
        "x11grab",
        "-draw_mouse",
        "0",
        "-framerate",
        fps_text,
        "-video_size",
        size,
        "-i",
        f"{display}+0,0",
        "-thread_queue_size",
        "2048",
        # Both devices use the host clock. Keep Pulse's later capture start
        # relative to X11 instead of independently shifting both inputs to zero.
        "-isync",
        "0",
        "-f",
        "pulse",
        "-sample_rate",
        str(pulse_sample_rate),
        "-channels",
        str(pulse_channels),
        "-i",
        pulse_source,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-r",
        fps_text,
        "-fps_mode",
        "cfr",
        "-g",
        gop,
        "-keyint_min",
        gop,
        "-sc_threshold",
        "0",
        "-b:v",
        f"{video_kbps}k",
        "-minrate",
        f"{video_kbps}k",
        "-maxrate",
        f"{video_kbps}k",
        "-bufsize",
        f"{video_kbps * 2}k",
        "-af",
        f"adelay={audio_delay_ms}:all=1,aresample=async=1:first_pts=0",
        "-c:a",
        "aac",
        "-ar",
        str(pulse_sample_rate),
        "-ac",
        str(pulse_channels),
        "-b:a",
        f"{audio_kbps}k",
        "-progress",
        "pipe:1",
    ]
    if closed_captions_active:
        video_output_index = command.index("-c:v")
        command[video_output_index:video_output_index] = [
            "-vf",
            f"docichcc=socket={closed_captions_socket}",
        ]
        command[command.index("-preset"):command.index("-preset")] = ["-a53cc", "1"]
    if duration_sec is not None:
        command.extend(["-t", str(duration_sec)])
    if mode == "live":
        command.extend(["-flvflags", "no_duration_filesize", "-f", "flv", local_rtmp_url])
    else:
        command.extend(["-y", "-f", "matroska", str(output_path)])
    return command


def parse_progress_lines(lines: Sequence[str]) -> dict[str, object]:
    """Parse FFmpeg ``-progress pipe:1`` key=value lines."""
    parsed: dict[str, object] = {}
    integer_keys = {"frame", "total_size", "out_time_us", "out_time_ms", "dup_frames", "drop_frames"}
    for raw in lines:
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in integer_keys:
            try:
                parsed[key] = int(float(value))
            except ValueError:
                parsed[key] = value
        elif key in {"fps", "speed"}:
            try:
                parsed[key] = float(value.removesuffix("x"))
            except ValueError:
                parsed[key] = value
        else:
            parsed[key] = value
    return parsed


def classify_ffmpeg_exit(return_code: int, *, stopping: bool) -> tuple[int, str]:
    """Map an operator-requested stop to a successful runner outcome.

    FFmpeg normally reports 255 after receiving SIGINT. That raw value remains
    useful for diagnostics, but a requested stop is not a stream failure.
    """
    if stopping:
        return 0, "stopped"
    return return_code, "completed" if return_code == 0 else "failed"
