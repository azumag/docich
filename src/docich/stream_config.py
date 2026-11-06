"""Pure configuration loader for the single direct-stream encoder (#3553).

Ownership: docich streaming infrastructure. It maps an explicit environment
mapping plus explicit ``repo_root`` / ``runtime_uid`` to a validated plain dict.
It never reads ``os.environ`` (the only indirect read is ``~`` expansion of a
path value), touches the filesystem, probes a host binary,
opens a socket or starts a process, and never imports the Soviet game.

Only the ``ffmpeg`` backend exists: OBS was retired (ADR 0007), so
``SOREN_STREAM_BACKEND=obs`` is refused here and a legacy caller keeps its own
loader for that value.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from typing import Mapping

STREAM_CONFIG_CONTRACT_VERSION = 1


def _sibling(name: str):
    # Load by sibling file path so the module works both as a package member
    # and when a legacy caller loads it by file path from a staged checkout.
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"docich_stream_config_{name}", path)
    if not spec or not spec.loader:
        raise ValueError(f"{name} is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_path(raw: str, default: str, repo_root: Path) -> Path:
    path = Path(raw or default).expanduser()
    return path if path.is_absolute() else Path(repo_root) / path


def load_stream_config(
    env: Mapping[str, str],
    *,
    repo_root: Path,
    runtime_uid: int,
) -> dict[str, object]:
    """Return the normalised encoder configuration (includes the loopback URL).

    Use :func:`public_stream_config` for anything that is displayed or stored.
    """
    policy = _sibling("stream_supervision_policy")
    command = _sibling("stream_encoder_command")
    guard = _sibling("stream_output_guard")
    strict_int, strict_bool = policy.strict_int, policy.strict_bool

    backend = str(env.get("SOREN_STREAM_BACKEND", "ffmpeg")).strip().lower()
    if backend != "ffmpeg":
        raise ValueError("SOREN_STREAM_BACKEND must be ffmpeg")

    size_raw = str(env.get("SOREN_DIRECT_STREAM_SIZE", "1280x720")).strip().lower()
    match = re.fullmatch(r"([0-9]{2,5})x([0-9]{2,5})", size_raw)
    if not match:
        raise ValueError("SOREN_DIRECT_STREAM_SIZE must use WIDTHxHEIGHT")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 320 or width > 3840 or height < 180 or height > 2160:
        raise ValueError("SOREN_DIRECT_STREAM_SIZE is outside the supported range")
    if width % 2 or height % 2:
        raise ValueError("SOREN_DIRECT_STREAM_SIZE dimensions must be even")

    display = str(env.get("SOREN_DIRECT_STREAM_DISPLAY", env.get("DISPLAY", ":99.0"))).strip()
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display):
        raise ValueError("SOREN_DIRECT_STREAM_DISPLAY must look like :99.0")

    pulse_source = str(env.get("SOREN_DIRECT_STREAM_PULSE_SOURCE", "soren_null.monitor")).strip()
    if not pulse_source or not re.fullmatch(r"[A-Za-z0-9_.:@+-]+", pulse_source):
        raise ValueError("SOREN_DIRECT_STREAM_PULSE_SOURCE contains unsupported characters")

    local_url = guard.validate_loopback_rtmp_url(
        str(env.get("SOREN_DIRECT_STREAM_LOCAL_URL", "rtmp://127.0.0.1:1935/soren/live")).strip()
    )

    ffmpeg_bin = str(env.get("SOREN_DIRECT_STREAM_FFMPEG_BIN", "ffmpeg")).strip()
    if not ffmpeg_bin or any(ch in ffmpeg_bin for ch in "\r\n\x00"):
        raise ValueError("SOREN_DIRECT_STREAM_FFMPEG_BIN is invalid")

    runtime_dir = str(env.get("XDG_RUNTIME_DIR") or f"/run/user/{runtime_uid}").rstrip("/")

    return {
        "backend": backend,
        "display": display,
        "width": width,
        "height": height,
        "fps": strict_int(env, "SOREN_DIRECT_STREAM_FPS", 30, 1, 60),
        "video_kbps": strict_int(env, "SOREN_DIRECT_STREAM_VIDEO_KBPS", 4500, 500, 6000),
        "audio_kbps": strict_int(env, "SOREN_DIRECT_STREAM_AUDIO_KBPS", 160, 64, 320),
        "audio_delay_ms": strict_int(env, "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", 0, 0, 2000),
        "pulse_source": pulse_source,
        "pulse_sample_rate": strict_int(env, "SOREN_DIRECT_STREAM_AUDIO_HZ", 48000, 8000, 192000),
        "pulse_channels": strict_int(env, "SOREN_DIRECT_STREAM_AUDIO_CHANNELS", 2, 1, 2),
        "local_rtmp_url": local_url,
        "ffmpeg_bin": ffmpeg_bin,
        "closed_captions_enabled": strict_bool(env, "DOCICH_CC_ENABLED", False),
        "closed_captions_socket": command.validate_caption_socket_path(
            str(env.get("DOCICH_CC_SOCKET", f"{runtime_dir}/docich/ffmpeg-cc.sock")).strip()
        ),
        "state_dir": str(_resolve_path(
            str(env.get("SOREN_DIRECT_STREAM_STATE_DIR", "")), "tmp/state/direct_stream", repo_root)),
        "log_file": str(_resolve_path(
            str(env.get("SOREN_DIRECT_STREAM_LOG_FILE", "")), "logs/direct_stream.log", repo_root)),
    }


def public_stream_config(config: Mapping[str, object]) -> dict[str, object]:
    """Config safe to print or persist: never includes the output URL."""
    public = dict(config)
    public.pop("local_rtmp_url", None)
    return public
