"""Place (never start) docich-stream.service, and report cutover preconditions.

ADR 0007 stage E needs ``docich-stream.service`` to exist on the host. Placing
the unit is a separate, reviewed step from the cutover itself, so this tool does
exactly two mutations and nothing else:

* ``--install --confirm-unit-placement``: copy the reviewed unit to
  ``/etc/systemd/system`` (root:root 0644) and ``systemctl daemon-reload``.
  It never runs ``enable``, ``start``, ``restart``, ``stop`` or ``disable``,
  never edits ``.env``, never touches PulseAudio, the encoder, the relay or the
  legacy supervisor.
* ``--provision-lock-dir --confirm-lock-dir``: create the private (0700) fixed
  lock directory the host-wide encoder lease lives in. An empty directory does
  not enable the lease (``DOCICH_STREAM_CANONICAL_LOCK_ENABLED`` stays untouched).

``--status`` and ``--print-config`` are read-only and print only enums,
booleans and hashes (no environment dump, no URLs, no credentials). All external
effects go through injectable callables so the tests need neither root nor
systemd.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Callable, Mapping, Sequence

STREAM_INSTALL_CONTRACT_VERSION = 1
UNIT_NAME = "docich-stream.service"
DEFAULT_HOME = Path("/home/ubuntu")
READONLY_SYSTEMCTL = frozenset({"is-enabled", "is-active", "show", "cat"})
# The only systemctl verbs this tool may ever send through sudo.
MUTATING_SYSTEMCTL = frozenset({"daemon-reload"})
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class InstallRefused(RuntimeError):
    """A precondition failed; nothing was changed."""


class Paths:
    def __init__(self, home: Path = DEFAULT_HOME, unit_dir: Path = Path("/etc/systemd/system")) -> None:
        self.home = Path(home)
        self.docich_root = self.home / "docich"
        self.soren_root = self.home / "soren"
        self.unit_source = self.docich_root / "deploy/docich-stream" / UNIT_NAME
        self.unit_target = Path(unit_dir) / UNIT_NAME
        self.wrapper = self.docich_root / "bin/docich-stream-run"
        self.lock_dir = self.docich_root / "run-soren-live/locks"
        self.runtime_uid_path = f"/run/user/{os.getuid()}/pulse/native"


def sha256_of(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _run(argv: Sequence[str], run: Runner) -> "subprocess.CompletedProcess[str] | None":
    try:
        return run(list(argv), capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _systemctl_value(verb: str, run: Runner) -> str:
    if verb not in READONLY_SYSTEMCTL:
        raise AssertionError(verb)
    done = _run(["systemctl", verb, UNIT_NAME], run)
    if done is None:
        return "unavailable"
    return (done.stdout or "").strip().splitlines()[0] if (done.stdout or "").strip() else "unknown"


def lock_dir_state(path: Path) -> str:
    """``missing`` | ``ready`` | ``unsafe`` (exists but not a private, owned, real directory)."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        return "unsafe"
    return "ready"


_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?{key}=(.*?)\s*$")


def dotenv_value(env_file: Path, key: str) -> str | None:
    """Last plain assignment of ``key`` (shell-style quotes). Only for whitelisted keys."""
    pattern = re.compile(_ASSIGNMENT.pattern.format(key=re.escape(key)))
    value = None
    try:
        lines = env_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        match = pattern.match(line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
    return value


def collect_status(paths: Paths, run: Runner = subprocess.run) -> dict[str, object]:
    """Read-only snapshot of what stage E depends on. Never prints values of secrets."""
    source_hash = sha256_of(paths.unit_source)
    installed_hash = sha256_of(paths.unit_target)
    env_file = paths.soren_root / ".env"
    backend = dotenv_value(env_file, "SOREN_STREAM_BACKEND")
    owner = dotenv_value(env_file, "DOCICH_STREAM_OWNER")
    canonical_flag = dotenv_value(env_file, "DOCICH_STREAM_CANONICAL_LOCK_ENABLED")
    pulse_env = dict(os.environ, PULSE_SERVER=f"unix:{paths.runtime_uid_path}")
    info = _run(["pactl", "info"], lambda argv, **kw: run(argv, env=pulse_env, **kw))
    sinks = _run(["pactl", "list", "short", "sinks"], lambda argv, **kw: run(argv, env=pulse_env, **kw))
    sink_names = {f.split()[1] for f in (sinks.stdout.splitlines() if sinks and sinks.returncode == 0 else []) if len(f.split()) > 1}
    state = {
        "contract": STREAM_INSTALL_CONTRACT_VERSION,
        "unit": {
            "source_sha256": source_hash,
            "installed": installed_hash is not None,
            "matches_source": installed_hash is not None and installed_hash == source_hash,
            "enabled": _systemctl_value("is-enabled", run),
            "active": _systemctl_value("is-active", run),
        },
        "wrapper_executable": paths.wrapper.is_file() and os.access(paths.wrapper, os.X_OK),
        "lock_dir": lock_dir_state(paths.lock_dir),
        "env": {
            "backend": backend if backend in {"ffmpeg", "obs"} else ("unset" if backend is None else "other"),
            "stream_owner": owner if owner in {"soren", "docich"} else ("unset" if owner is None else "invalid"),
            "canonical_lock_flag": canonical_flag if canonical_flag in {"0", "1"} else ("unset" if canonical_flag is None else "invalid"),
        },
        "pulse": {
            "server_answers": bool(info and info.returncode == 0),
            "soren_null_sink": "soren_null" in sink_names,
        },
        "markers": {
            "legacy_paused": (paths.soren_root / "tmp/state/direct_stream.paused").exists(),
            "docich_paused": (paths.soren_root / "tmp/state/docich_stream.paused").exists(),
        },
    }
    return state


def print_config(paths: Paths) -> dict[str, object]:
    return {
        "contract": STREAM_INSTALL_CONTRACT_VERSION,
        "unit_source": str(paths.unit_source),
        "unit_target": str(paths.unit_target),
        "wrapper": str(paths.wrapper),
        "lock_dir": str(paths.lock_dir),
        "mutations": {
            "--install --confirm-unit-placement": ["install unit file (root:root 0644)", "systemctl daemon-reload"],
            "--provision-lock-dir --confirm-lock-dir": ["create the private (0700) fixed lock directory"],
        },
        "never_does": ["enable", "start", "restart", "stop", "disable", "edit .env",
                       "touch PulseAudio", "touch the encoder, relay or legacy supervisor"],
    }


def check_unit_source(paths: Paths) -> str:
    """The reviewed unit must be the shape ADR 0007 defines; returns its text."""
    try:
        text = paths.unit_source.read_text(encoding="utf-8")
    except OSError:
        raise InstallRefused("unit source is missing") from None
    for forbidden in ("EnvironmentFile", "ExecStartPre", "pulseaudio", "soren-runtime.service", "rtmp://", "KillMode=control-group"):
        if forbidden in text:
            raise InstallRefused(f"unit source contains {forbidden!r}")
    exec_start = re.search(r"^ExecStart=(\S+)\s*$", text, re.M)
    expected = DEFAULT_HOME / "docich/bin/docich-stream-run"
    if not exec_start or Path(exec_start.group(1)) != expected:
        raise InstallRefused("ExecStart is not the reviewed wrapper path")
    for needle in ("Restart=always", "KillMode=mixed", "RestartPreventExitStatus=75"):
        if needle not in text:
            raise InstallRefused(f"unit source lacks {needle}")
    if not (paths.wrapper.is_file() and os.access(paths.wrapper, os.X_OK)):
        raise InstallRefused("the wrapper is not an executable file")
    return text


def install_unit(
    paths: Paths,
    *,
    confirm: bool,
    run: Runner = subprocess.run,
    sudo: Sequence[str] = ("sudo", "-n"),
    analyze: bool = True,
    require_default_home: bool = True,
) -> dict[str, object]:
    """``require_default_home=False`` exists for tests only; the CLI never sets it."""
    if not confirm:
        raise InstallRefused("--confirm-unit-placement is required")
    if require_default_home and paths.home != DEFAULT_HOME:
        raise InstallRefused("unexpected install root")
    check_unit_source(paths)
    source_hash = sha256_of(paths.unit_source)
    installed_hash = sha256_of(paths.unit_target)
    if paths.unit_target.is_symlink():
        raise InstallRefused("the installed unit path is a symlink")
    if installed_hash == source_hash:
        return {"unit": "unchanged", "daemon_reload": False}
    if installed_hash is not None:
        raise InstallRefused("a different docich-stream.service is already installed; inspect it first")
    if analyze:
        verify = _run(["systemd-analyze", "verify", str(paths.unit_source)], run)
        if verify is not None and verify.returncode != 0:
            raise InstallRefused("systemd-analyze verify rejected the unit")
    for argv in (
        [*sudo, "install", "-o", "root", "-g", "root", "-m", "0644", str(paths.unit_source), str(paths.unit_target)],
        [*sudo, "systemctl", "daemon-reload"],
    ):
        if argv[len(sudo)] == "systemctl" and argv[len(sudo) + 1] not in MUTATING_SYSTEMCTL:
            raise AssertionError(argv)
        done = _run(argv, run)
        if done is None or done.returncode != 0:
            raise InstallRefused(f"{argv[len(sudo)]} failed")
    if sha256_of(paths.unit_target) != source_hash:
        raise InstallRefused("the installed unit does not match the source after install")
    return {"unit": "installed", "daemon_reload": True, "enabled": False, "started": False}


def provision_lock_dir(paths: Paths, *, confirm: bool) -> dict[str, object]:
    if not confirm:
        raise InstallRefused("--confirm-lock-dir is required")
    state = lock_dir_state(paths.lock_dir)
    if state == "ready":
        return {"lock_dir": "unchanged"}
    if state == "unsafe":
        raise InstallRefused("the lock directory exists but is not a private directory owned by this user")
    if not paths.docich_root.is_dir():
        raise InstallRefused("docich root does not exist")
    previous = os.umask(0o077)
    try:
        paths.lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    finally:
        os.umask(previous)
    if lock_dir_state(paths.lock_dir) != "ready":
        raise InstallRefused("the lock directory is not private after creation")
    return {"lock_dir": "created"}


def main(argv: Sequence[str] | None = None, *, paths: Paths | None = None, run: Runner = subprocess.run,
         environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich.stream_install", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="read-only snapshot of the cutover preconditions")
    group.add_argument("--print-config", action="store_true", help="print paths and the exact mutations (read-only)")
    group.add_argument("--install", action="store_true", help="place the unit file and daemon-reload (never start)")
    group.add_argument("--provision-lock-dir", action="store_true", help="create the private fixed lock directory")
    parser.add_argument("--confirm-unit-placement", action="store_true")
    parser.add_argument("--confirm-lock-dir", action="store_true")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    use = paths or Paths(unit_dir=Path(env.get("DOCICH_STREAM_UNIT_DIR") or "/etc/systemd/system"))
    sudo = tuple((env.get("DOCICH_STREAM_SUDO") or "sudo -n").split())
    try:
        if args.status:
            result = collect_status(use, run)
        elif args.print_config:
            result = print_config(use)
        elif args.install:
            result = install_unit(use, confirm=args.confirm_unit_placement, run=run, sudo=sudo)
        else:
            result = provision_lock_dir(use, confirm=args.confirm_lock_dir)
    except InstallRefused as exc:
        print(f"stream_install: refused: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
