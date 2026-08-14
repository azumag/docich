"""ffmpeg command construction for the docich stream pipeline (architecture.md §5)."""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import procs
from .config import GlobalConfig

MASK = "***"


class StreamKeyError(Exception):
    """Raised when the configured stream key environment variable is missing."""


class CaptionSocketDirectoryError(Exception):
    """Raised when the caption socket parent is not private and user-owned."""


@dataclass(frozen=True)
class StreamRuntime:
    command: list[str]
    captions_active: bool
    caption_detail: str


def _resolve_binary(binary: str) -> str | None:
    if "/" in binary:
        path = Path(binary)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return procs.which(binary)


def caption_capability(binary: str) -> tuple[bool, str]:
    """Return whether ``binary`` has docichcc and libx264 A/53 support."""
    resolved = _resolve_binary(binary)
    if resolved is None:
        return False, f"FFmpegが見つかりません: {binary}"
    try:
        filters = procs.run([resolved, "-hide_banner", "-filters"], timeout=8)
        encoder = procs.run(
            [resolved, "-hide_banner", "-h", "encoder=libx264"], timeout=8
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"FFmpeg機能確認に失敗しました: {exc}"
    filter_text = f"{filters.stdout}\n{filters.stderr}"
    encoder_text = f"{encoder.stdout}\n{encoder.stderr}"
    if filters.returncode != 0 or " docichcc " not in filter_text:
        return False, "docichcc filterがありません"
    if encoder.returncode != 0 or "a53cc" not in encoder_text:
        return False, "libx264のa53cc optionがありません"
    return True, "docichcc + libx264 a53cc"


def ensure_caption_socket_parent(socket_path: str) -> None:
    """Create and verify the private directory used by the FFmpeg listener.

    The filter owns the socket itself. The Python runtime owns only the parent
    directory and refuses symlinks, foreign ownership, or group/other access.
    """
    parent = Path(socket_path).parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(parent)
    except OSError as exc:
        raise CaptionSocketDirectoryError(
            f"字幕socket directoryを準備できません: {parent} ({exc})"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise CaptionSocketDirectoryError(
            f"字幕socket parentは実directoryである必要があります: {parent}"
        )
    if metadata.st_uid != os.geteuid():
        raise CaptionSocketDirectoryError(
            f"字幕socket directoryの所有者が実行userではありません: {parent}"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CaptionSocketDirectoryError(
            f"字幕socket directoryはowner専用(0700)である必要があります: {parent}"
        )


def caption_socket_ready(socket_path: str) -> tuple[bool, str]:
    """Inspect the live filter socket without creating or connecting to it."""
    path = Path(socket_path)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False, "caption socketは未準備です"
    except OSError as exc:
        return False, f"caption socketを確認できません: {exc}"
    if not stat.S_ISSOCK(metadata.st_mode):
        return False, "caption socket pathがUnix socketではありません"
    if metadata.st_uid != os.geteuid():
        return False, "caption socketの所有者が実行userではありません"
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        return False, "caption socketはowner専用(0600)ではありません"
    return True, "caption socket ready"


def build_ffmpeg_cmd(
    g: GlobalConfig,
    *,
    mask_key: bool = False,
    captions_enabled: bool | None = None,
    ffmpeg_bin: str | None = None,
) -> list[str]:
    d = g.display
    s = g.stream

    use_captions = g.captions.enabled if captions_enabled is None else captions_enabled
    cmd = [ffmpeg_bin or s.ffmpeg_bin]

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

    if use_captions:
        cmd += ["-vf", f"docichcc=socket={g.captions.socket_path}"]

    gop = s.framerate * s.gop_seconds
    cmd += [
        "-c:v", "libx264", "-preset", s.preset, "-tune", "zerolatency",
        "-b:v", s.video_bitrate, "-maxrate", s.maxrate, "-bufsize", s.bufsize,
        "-pix_fmt", "yuv420p", "-g", str(gop),
        "-c:a", "aac", "-b:a", s.audio_bitrate, "-ar", "44100",
    ]
    if use_captions:
        # libx264 consumes AV_FRAME_DATA_A53_CC attached by docichcc and writes
        # the registered-user-data SEI carried by Twitch native captions.
        video_codec_index = cmd.index("-c:v") + 2
        cmd[video_codec_index:video_codec_index] = ["-a53cc", "1"]

    if s.mode == "rtmp":
        key = os.environ.get(s.stream_key_env)
        if not key:
            raise StreamKeyError(
                f"環境変数 {s.stream_key_env} が設定されていません (RTMP 配信にはストリームキーが必要です)"
            )
        shown_key = MASK if mask_key else key
        cmd += ["-f", "flv", f"{s.rtmp_url}/{shown_key}"]
    elif s.mode == "file":
        # tmux window の cwd に依存しないよう、相対パスは repo_root 基準で絶対化する。
        file_path = Path(s.file_path)
        if not file_path.is_absolute():
            file_path = g.repo_root / file_path
        cmd += ["-y", "-f", "flv", str(file_path)]
    else:
        cmd += ["-f", "null", "-"]

    return cmd


def resolve_runtime(g: GlobalConfig, *, mask_key: bool = False) -> StreamRuntime:
    """Resolve the actual stream command, failing open to captionless video."""
    requested = g.stream.ffmpeg_bin
    resolved = _resolve_binary(requested)
    if resolved is None:
        fallback = _resolve_binary("ffmpeg")
        binary = fallback or requested
        if g.captions.enabled:
            return StreamRuntime(
                build_ffmpeg_cmd(
                    g,
                    mask_key=mask_key,
                    captions_enabled=False,
                    ffmpeg_bin=binary,
                ),
                False,
                f"fail-open: 設定FFmpegが見つかりません: {requested}",
            )
        return StreamRuntime(
            build_ffmpeg_cmd(
                g,
                mask_key=mask_key,
                captions_enabled=False,
                ffmpeg_bin=binary,
            ),
            False,
            "disabled",
        )

    if not g.captions.enabled:
        return StreamRuntime(
            build_ffmpeg_cmd(
                g,
                mask_key=mask_key,
                captions_enabled=False,
                ffmpeg_bin=resolved,
            ),
            False,
            "disabled",
        )

    supported, detail = caption_capability(resolved)
    return StreamRuntime(
        build_ffmpeg_cmd(
            g,
            mask_key=mask_key,
            captions_enabled=supported,
            ffmpeg_bin=resolved,
        ),
        supported,
        detail if supported else f"fail-open: {detail}",
    )


def redact_stream_command(command: list[str], g: GlobalConfig) -> list[str]:
    """Mask the RTMP key before a command is written to status or logs."""
    secret = os.environ.get(g.stream.stream_key_env, "")
    if not secret:
        return list(command)
    return [part.replace(secret, MASK) for part in command]
