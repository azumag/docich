"""ffmpeg command construction for the docich stream pipeline (architecture.md SS5)."""
from __future__ import annotations

import os

from .config import GlobalConfig

MASK = "***"


class StreamKeyError(Exception):
    """Raised when the configured stream key environment variable is missing."""


def build_ffmpeg_cmd(g: GlobalConfig, *, mask_key: bool = False) -> list[str]:
    d = g.display
    s = g.stream

    cmd = ["ffmpeg"]

    # video input: -draw_mouse 0 is required, otherwise the cursor is captured mid-screen.
    cmd += [
        "-f", "x11grab", "-draw_mouse", "0",
        "-framerate", str(s.framerate),
        "-video_size", f"{d.width}x{d.height}",
        "-i", d.name,
    ]

    # audio input
    if g.audio.enabled:
        cmd += ["-f", "pulse", "-i", f"{g.audio.sink_name}.monitor"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    gop = s.framerate * s.gop_seconds
    cmd += [
        "-c:v", "libx264", "-preset", s.preset, "-tune", "zerolatency",
        "-b:v", s.video_bitrate, "-maxrate", s.maxrate, "-bufsize", s.bufsize,
        "-pix_fmt", "yuv420p", "-g", str(gop),
        "-c:a", "aac", "-b:a", s.audio_bitrate, "-ar", "44100",
    ]

    if s.mode == "rtmp":
        key = os.environ.get(s.stream_key_env)
        if not key:
            raise StreamKeyError(
                f"環境変数 {s.stream_key_env} が設定されていません (RTMP 配信にはストリームキーが必要です)"
            )
        shown_key = MASK if mask_key else key
        cmd += ["-f", "flv", f"{s.rtmp_url}/{shown_key}"]
    elif s.mode == "file":
        cmd += ["-y", "-f", "flv", s.file_path]
    else:
        cmd += ["-f", "null", "-"]

    return cmd
