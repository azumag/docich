#!/usr/bin/env python3
"""Enable the Jev comment classifier through the owner-only VM operation.

The API key is supplied only through the process environment by the reviewed
control plane.  This script never prints or stores the key outside Soren's
intended .env file and its protected rollback backup.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import sys
import tempfile
import time


MANAGED_KEYS = (
    "COMMENT_CLASSIFIER_BACKEND",
    "COMMENT_CLASSIFIER_JEV_MODEL",
    "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS",
    "COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE",
    "COMMENT_CLASSIFIER_JEV_LOG_ENABLED",
    "TYPESAFE_API_KEY",
)
MODEL = "jev-1.13.0"
WORKER_PID_FILE = Path("tmp/state/chat_worker.pid")
WORKER_PATTERN = re.compile(r"(?:^|[/ ])workers/chat_worker\.sh(?:[ \t]|$)")
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class ConfigureError(RuntimeError):
    """A fixed, secret-free configuration failure."""


def validate_key(value: str) -> None:
    if not value or len(value) > 4096 or not value.isascii():
        raise ConfigureError("invalid_api_key")
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ConfigureError("invalid_api_key")


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ConfigureError(f"{label}_missing") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConfigureError(f"{label}_not_regular")
    return info


def _write_atomic(path: Path, data: bytes, info: os.stat_result) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.jev-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, stat.S_IMODE(info.st_mode))
        try:
            os.fchown(fd, info.st_uid, info.st_gid)
        except PermissionError:
            if os.geteuid() == 0:
                raise
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _backup_env(env_file: Path, data: bytes, info: os.stat_result) -> Path:
    backup_dir = env_file.parent / ".codex_deploy" / "comment-classifier-jev"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fd, temporary = tempfile.mkstemp(prefix=f".env.before-jev-{timestamp}-", dir=backup_dir)
    backup = Path(temporary)
    completed = False
    try:
        os.fchmod(fd, stat.S_IMODE(info.st_mode))
        try:
            os.fchown(fd, info.st_uid, info.st_gid)
        except PermissionError:
            if os.geteuid() == 0:
                raise
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        completed = True
        return backup
    finally:
        if not completed and backup.exists():
            backup.unlink()


def _rewrite_env(env_file: Path, managed_lines: list[str]) -> Path:
    """Atomically replace only classifier assignments and return the backup."""
    info = _regular_file(env_file, "env")
    try:
        original = env_file.read_bytes()
        text = original.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigureError("env_unreadable") from exc

    kept = []
    for line in text.splitlines(keepends=True):
        match = ASSIGNMENT.match(line)
        if match and match.group(1) in MANAGED_KEYS:
            continue
        kept.append(line)
    if kept and not kept[-1].endswith(("\n", "\r")):
        kept.append("\n")
    kept.extend(managed_lines)
    new_data = "".join(kept).encode("utf-8")

    backup = _backup_env(env_file, original, info)
    _write_atomic(env_file, new_data, info)
    return backup


def configure_env(env_file: Path, api_key: str) -> Path:
    """Enable Jev and atomically replace only the classifier assignments."""
    validate_key(api_key)
    return _rewrite_env(
        env_file,
        [
            "COMMENT_CLASSIFIER_BACKEND=jev\n",
            f"COMMENT_CLASSIFIER_JEV_MODEL={MODEL}\n",
            "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=1500\n",
            "COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE=0.70\n",
            "COMMENT_CLASSIFIER_JEV_LOG_ENABLED=1\n",
            # .env is sourced by the worker; quote the secret as a shell value
            # so printable punctuation cannot become shell syntax.
            f"TYPESAFE_API_KEY={shlex.quote(api_key)}\n",
        ],
    )


def disable_env(env_file: Path) -> Path:
    """Disable Jev explicitly and remove its secret from the runtime env."""
    return _rewrite_env(env_file, ["COMMENT_CLASSIFIER_BACKEND=\n"])


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline(pid: int) -> str:
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except (FileNotFoundError, OSError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _process_env(pid: int) -> dict[str, str]:
    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
    except (FileNotFoundError, OSError):
        return {}
    result = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return result


def _is_worker(pid: int, soren_root: Path) -> bool:
    return (
        _pid_alive(pid)
        and _cwd(pid) == str(soren_root)
        and bool(WORKER_PATTERN.search(_cmdline(pid)))
    )


def _read_worker_pid(pid_file: Path) -> int | None:
    try:
        value = pid_file.read_text(encoding="ascii").strip()
        pid = int(value)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return pid if pid > 1 else None


def _wait_until(predicate, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def restart_chat_worker_verified(soren_root: Path, *, verify, timeout: float = 90.0) -> tuple[int, int]:
    """Stop the current chat_worker, wait for its replacement, then verify it.

    ``verify(runtime_env)`` receives the replacement worker's own environ
    (never argv/cwd/other processes) and must raise ``ConfigureError`` with a
    fixed, secret-free reason on any mismatch. This is the shared restart
    primitive: #678's own env keys and #882's docich-canonical route keys
    each get their own ``verify`` callback (see ``restart_chat_worker`` below
    and ``configure_jev_route.py``) rather than a second copy of the
    PID/cmdline/environ handling.
    """
    pid_file = soren_root / WORKER_PID_FILE
    old_pid = _read_worker_pid(pid_file)
    if old_pid is None or not _is_worker(old_pid, soren_root):
        raise ConfigureError("chat_worker_pid_invalid")
    try:
        os.kill(old_pid, signal.SIGTERM)
    except OSError as exc:
        raise ConfigureError("chat_worker_stop_failed") from exc
    if not _wait_until(lambda: not _pid_alive(old_pid), timeout):
        raise ConfigureError("chat_worker_old_pid_alive")

    def replacement_ready() -> bool:
        new_pid = _read_worker_pid(pid_file)
        return new_pid is not None and new_pid != old_pid and _is_worker(new_pid, soren_root)

    if not _wait_until(replacement_ready, timeout):
        raise ConfigureError("chat_worker_replacement_missing")
    new_pid = _read_worker_pid(pid_file)
    if new_pid is None or new_pid == old_pid:
        raise ConfigureError("chat_worker_replacement_invalid")
    verify(_process_env(new_pid))
    return old_pid, new_pid


def _verify_comment_classifier_jev(expect_jev: bool):
    """The #678-owned verification: COMMENT_CLASSIFIER_BACKEND/TYPESAFE_API_KEY/model."""
    def verify(runtime_env: dict[str, str]) -> None:
        if expect_jev:
            if runtime_env.get("COMMENT_CLASSIFIER_BACKEND") != "jev":
                raise ConfigureError("chat_worker_backend_not_jev")
            if not runtime_env.get("TYPESAFE_API_KEY"):
                raise ConfigureError("chat_worker_api_key_missing")
            if runtime_env.get("COMMENT_CLASSIFIER_JEV_MODEL") != MODEL:
                raise ConfigureError("chat_worker_model_mismatch")
        else:
            if runtime_env.get("COMMENT_CLASSIFIER_BACKEND") == "jev":
                raise ConfigureError("chat_worker_backend_still_jev")
            if runtime_env.get("TYPESAFE_API_KEY"):
                raise ConfigureError("chat_worker_api_key_still_present")
    return verify


def restart_chat_worker(soren_root: Path, *, expect_jev: bool, timeout: float = 90.0) -> tuple[int, int]:
    return restart_chat_worker_verified(
        soren_root, verify=_verify_comment_classifier_jev(expect_jev), timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soren-root", type=Path, default=Path("/home/ubuntu/soren"))
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if args.disable:
        backup = disable_env(args.soren_root / ".env")
    else:
        api_key = os.environ.get("TYPESAFE_API_KEY", "")
        backup = configure_env(args.soren_root / ".env", api_key)
    try:
        old_pid, new_pid = restart_chat_worker(args.soren_root, expect_jev=not args.disable)
    except Exception:
        # Keep the explicit backup for operator recovery. Do not attempt a
        # second restart here: a supervisor may already be replacing the
        # worker, and a failed recovery must remain visible and fail closed.
        raise
    print(
        json.dumps(
            {
                "status": "disabled" if args.disable else "configured",
                "backend": "" if args.disable else "jev",
                "model": None if args.disable else MODEL,
                "api_key": "absent" if args.disable else "present",
                "old_pid": old_pid,
                "new_pid": new_pid,
                "backup": str(backup),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigureError as exc:
        print(f"configuration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
