"""Front door of the single direct-stream supervisor (#3553, ADR 0007).

``config``, ``command`` and ``status`` are read-only. ``validate`` observes the
host (it may create the private caption-socket directory). ``run`` and
``record`` start the one encoder and ``stop`` signals this supervisor's own
runner. Nothing starts this entry point yet: no systemd unit references it
(ADR 0007 stage D) and the production owner stays the legacy runner until the
explicit cutover (stage E).

Safety properties (each covered by process-level tests):

* ``run`` does nothing (exit 75, "not the owner") unless
  ``DOCICH_STREAM_OWNER=docich``; the default keeps the legacy runner as the
  owner. While ``tmp/state/docich_stream.paused`` exists it idles without
  starting an encoder or taking a lock. It then waits for the canonical
  PulseAudio (never starting it) before validating the host.
* ``run``/``record`` take the fixed host-wide lease and then
  ``state_dir/direct_stream.lock`` before any child starts; a legacy runner
  holding either one makes this refuse. A host without the provisioned lock
  directory refuses to start.
* ``stop`` only signals a ``docich.stream_supervisor ... run`` process.
* The TwiCa FFmpeg wrapper substitution that ``direct_stream.sh`` performs is
  done here too (``prepare_environment``), because systemd runs this module
  directly rather than the shell wrapper. Loading ``.env`` is *not* done here:
  the unit's ``EnvironmentFile`` supplies it (stage D gate).

Environment is read once at the process boundary and passed down explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Callable, Mapping, Sequence

from .stream_config import load_stream_config, public_stream_config
from .stream_encoder_command import build_direct_stream_ffmpeg_command
from .stream_host_checks import RuntimeCheckError, prepare_caption_runtime, validate_runtime
from .stream_prereqs import PrerequisiteError, ensure_pulse_ready, wait_seconds
from .stream_pause import wait_while_paused
from .stream_runner import AlreadyRunningError, run_supervised, stop_runner
from .stream_state_files import read_stream_status
from .stream_supervision_policy import load_audio_watchdog_policy, load_reconnect_policy

DEFAULT_ROOT = "/home/ubuntu/soren"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
TWICA_WRAPPER_RELATIVE = "bin/docich-twica-ffmpeg"
TWICA_KEYS = ("DOCICH_TWICA_REAL_FFMPEG", "SOREN_DIRECT_STREAM_FFMPEG_BIN")
OWNERS = ("soren", "docich")
# Exit status of ``run`` when the legacy runner owns the encoder. The unit lists
# it in SuccessExitStatus/RestartPreventExitStatus so Restart=always (needed for
# the operator stop/start cycle) does not respawn a process with nothing to do.
NOT_OWNER_EXIT = 75


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
    validate = sub.add_parser("validate", help="validate config and host prerequisites")
    validate.add_argument("--mode", choices=("live", "record"), default="live")
    sub.add_parser("run", help="run the loopback RTMP publisher in the foreground")
    record = sub.add_parser("record", help="make a bounded local MKV PoC")
    record.add_argument("--output", type=Path, required=True)
    record.add_argument("--duration", type=_positive_duration, default=60)
    sub.add_parser("status", help="print redacted status JSON")
    sub.add_parser("stop", help="stop a runner started by this entry point")
    return parser


def stream_owner(env: Mapping[str, str]) -> str:
    """Who owns the single encoder: ``soren`` (default, legacy) or ``docich``.

    Both start paths read the same flag. The host-wide lock remains the final
    safety net if the flag is ever wrong.
    """
    # Empty means unset (shell ``${VAR:-soren}`` convention used by the legacy
    # flags). No trimming: the shell side compares the exact string, so both
    # sides must reject padded values rather than disagree about them.
    raw = str(env.get("DOCICH_STREAM_OWNER") or "soren")
    if raw not in OWNERS:
        raise ValueError("DOCICH_STREAM_OWNER must be soren or docich")
    return raw


def _default_prepare_pulse(env: Mapping[str, str], config: Mapping[str, object],
                           watchdog: Mapping[str, object]) -> None:
    ensure_pulse_ready(
        pulse_server=str(watchdog["pulse_server"]),
        pulse_source=str(config["pulse_source"]),
        wait_sec=wait_seconds(env),
        base_env=env,
    )


def twica_pipeline_enabled() -> bool:
    """Same decision as ``python -m docich.twica_operator pipeline-enabled``."""
    try:
        from .twica_operator import main as operator_main

        return operator_main(["pipeline-enabled"]) == 0
    except Exception:
        return False


def prepare_environment(
    env: Mapping[str, str],
    *,
    docich_root: Path,
    pipeline_enabled: Callable[[], bool] = twica_pipeline_enabled,
    is_executable: Callable[[Path], bool] = lambda path: path.is_file() and os.access(path, os.X_OK),
) -> dict[str, str]:
    """Return ``env`` with the TwiCa FFmpeg wrapper substituted when armed.

    Mirrors ``direct_stream.sh``: when the common TwiCa pipeline is armed, the
    configured FFmpeg becomes ``DOCICH_TWICA_REAL_FFMPEG`` and the wrapper takes
    its place. Nothing changes when the wrapper is absent or the pipeline off.
    """
    result = dict(env)
    wrapper = Path(docich_root) / TWICA_WRAPPER_RELATIVE
    if is_executable(wrapper) and pipeline_enabled():
        result["DOCICH_TWICA_REAL_FFMPEG"] = env.get("SOREN_DIRECT_STREAM_FFMPEG_BIN") or "ffmpeg"
        result["SOREN_DIRECT_STREAM_FFMPEG_BIN"] = str(wrapper)
    return result


def _command_for(config: Mapping[str, object], *, mode: str, captions_active: bool,
                 output: Path | None, duration: int | None) -> list[str]:
    return build_direct_stream_ffmpeg_command(
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
        mode=mode,
        closed_captions_active=captions_active,
        closed_captions_socket=str(config["closed_captions_socket"]),
        output_path=None if output is None else str(output),
        duration_sec=duration,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    validate: Callable[[Mapping[str, object], str], bool] | None = None,
    twica_enabled: Callable[[], bool] = twica_pipeline_enabled,
    canonical_opener: Callable[[], object] | None = None,
    prepare_pulse: Callable[[Mapping[str, str], Mapping[str, object], Mapping[str, object]], None] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    process_env = environ is None
    base_env = dict(os.environ if process_env else environ)
    docich_root = Path(base_env.get("DOCICH_ROOT") or PACKAGE_ROOT)
    env = prepare_environment(base_env, docich_root=docich_root, pipeline_enabled=twica_enabled)
    if process_env:
        # The encoder child inherits this process's environment: the wrapper
        # must see DOCICH_TWICA_REAL_FFMPEG and its own path.
        for key in TWICA_KEYS:
            if key in env and env.get(key) != base_env.get(key):
                os.environ[key] = env[key]
    root = args.root or Path(env.get("SOREN_RUNTIME_REPO") or DEFAULT_ROOT)
    if args.action == "run":
        try:
            owner = stream_owner(env)
        except ValueError as exc:
            print(f"stream_supervisor: {exc}", file=sys.stderr)
            return 2
        if owner != "docich":
            print("stream_supervisor: DOCICH_STREAM_OWNER is not 'docich'; not starting "
                  "(the legacy runner owns the encoder)", file=sys.stderr)
            return NOT_OWNER_EXIT
        # Operator intent wins: idle (holding no lock) while the marker exists.
        wait_while_paused(root, announce=lambda text: print(text, file=sys.stderr))
    pulse = prepare_pulse or _default_prepare_pulse
    check = validate or (lambda cfg, mode: validate_runtime(cfg, mode=mode))
    extra = {} if canonical_opener is None else {"canonical_opener": canonical_opener}
    try:
        config = load_stream_config(env, repo_root=root, runtime_uid=os.geteuid())
        public = public_stream_config(config)
        if args.action == "config":
            print(json.dumps(public, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "status":
            print(json.dumps(read_stream_status(Path(str(config["state_dir"])) / "status.json"),
                             ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "stop":
            return stop_runner(config)
        if args.action == "command":
            if args.mode == "record" and args.output is None:
                raise ValueError("record command requires --output")
            command = _command_for(config, mode=args.mode, captions_active=bool(config["closed_captions_enabled"]),
                                   output=args.output, duration=args.duration)
            print(json.dumps(command, ensure_ascii=False))
            return 0
        if args.action == "validate":
            active = check(config, args.mode)
            if active:
                active = prepare_caption_runtime(config)
            print(json.dumps({"ok": True, "mode": args.mode, "config": public, "closed_captions_active": active},
                             ensure_ascii=False))
            return 0
        if args.action == "run":
            reconnect = load_reconnect_policy(env)
            watchdog = load_audio_watchdog_policy(env, uid=os.getuid())
            pulse(env, config, watchdog)
            active = check(config, "live") and prepare_caption_runtime(config)
            command = _command_for(config, mode="live", captions_active=active, output=None, duration=None)
            return run_supervised(config, public, command, mode="live", captions_active=active,
                                  reconnect=reconnect, audio_watchdog=watchdog, cwd=root, **extra)
        if args.action == "record":
            active = check(config, "record") and prepare_caption_runtime(config)
            output = args.output.resolve()
            command = _command_for(config, mode="record", captions_active=active, output=output,
                                   duration=args.duration)
            return run_supervised(config, public, command, mode="record", captions_active=active,
                                  reconnect=None, audio_watchdog=None, output_dir=output.parent, cwd=root, **extra)
        raise AssertionError(f"unhandled action: {args.action}")
    except (ValueError, RuntimeCheckError, AlreadyRunningError, PrerequisiteError) as exc:
        print(f"stream_supervisor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
