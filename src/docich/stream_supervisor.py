"""Read-only front door of the future single direct-stream supervisor (#3553).

Stage B of ADR 0007: only ``config``, ``command`` and ``status`` exist, and all
three are read-only. ``run``, ``stop``, ``record`` and ``validate`` are parsed
but refused with exit code 2 so nothing can start, signal or probe an encoder or
host service through this entry point yet. It acquires no lock, starts no
process, writes no file and never prints the loopback output URL's credentials
(there are none by contract) in ``config`` or ``status``.

Environment is read once here, at the process boundary, and passed down as an
explicit mapping to the pure modules.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from .stream_config import load_stream_config, public_stream_config
from .stream_encoder_command import build_direct_stream_ffmpeg_command
from .stream_state_files import read_stream_status

DEFAULT_ROOT = "/home/ubuntu/soren"
NOT_IMPLEMENTED = ("run", "stop", "record", "validate")


def _positive_duration(raw: str) -> int:
    if not raw.isdecimal() or not raw.isascii():
        raise argparse.ArgumentTypeError("duration must be an integer")
    value = int(raw)
    if not 1 <= value <= 86400:
        raise argparse.ArgumentTypeError("duration must be between 1 and 86400")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich.stream_supervisor", description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None,
                        help="game runtime root for relative state/log paths "
                             "(default: $SOREN_RUNTIME_REPO or " + DEFAULT_ROOT + ")")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("config", help="print redacted normalized config")
    command = sub.add_parser("command", help="print FFmpeg argv as JSON")
    command.add_argument("--mode", choices=("live", "record"), default="live")
    command.add_argument("--output", type=Path)
    command.add_argument("--duration", type=_positive_duration)
    sub.add_parser("status", help="print redacted status JSON")
    for name in NOT_IMPLEMENTED:
        sub.add_parser(name, help="not implemented in the read-only stage")
    return parser


def main(argv: Sequence[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action in NOT_IMPLEMENTED:
        print(f"stream_supervisor: '{args.action}' is not implemented in the read-only stage", file=sys.stderr)
        return 2
    env = dict(os.environ if environ is None else environ)
    root = args.root or Path(env.get("SOREN_RUNTIME_REPO") or DEFAULT_ROOT)
    try:
        config = load_stream_config(env, repo_root=root, runtime_uid=os.geteuid())
        if args.action == "config":
            print(json.dumps(public_stream_config(config), ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "status":
            print(json.dumps(read_stream_status(Path(str(config["state_dir"])) / "status.json"),
                             ensure_ascii=False, sort_keys=True))
            return 0
        if args.mode == "record" and args.output is None:
            raise ValueError("record command requires --output")
        command = build_direct_stream_ffmpeg_command(
            ffmpeg_bin=str(config["ffmpeg_bin"]),
            display=str(config["display"]),
            size=f"{config['width']}x{config['height']}",
            fps=int(config["fps"]),
            video_kbps=int(config["video_kbps"]),
            audio_kbps=int(config["audio_kbps"]),
            audio_delay_ms=int(config["audio_delay_ms"]),
            pulse_source=str(config["pulse_source"]),
            pulse_sample_rate=int(config["pulse_sample_rate"]),
            pulse_channels=int(config["pulse_channels"]),
            local_rtmp_url=str(config["local_rtmp_url"]),
            mode=args.mode,
            closed_captions_active=bool(config["closed_captions_enabled"]),
            closed_captions_socket=str(config["closed_captions_socket"]),
            output_path=None if args.output is None else str(args.output),
            duration_sec=args.duration,
        )
        print(json.dumps(command, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(f"stream_supervisor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
