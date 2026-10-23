#!/usr/bin/env python3
"""Installed dispatcher for fixed evidence and read-only VM gateway operations."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

READ_ONLY_OPS = {"status", "diagnostics", "github_auth_diagnostics"}
STREAM_PROFILE_KEYS = {
    "SOREN_DIRECT_STREAM_SIZE": "size",
    "SOREN_DIRECT_STREAM_FPS": "fps",
    "SOREN_DIRECT_STREAM_VIDEO_KBPS": "video_kbps",
}
STREAM_PROFILE_MAX_BYTES = 256 * 1024
_STREAM_SIZE_RE = re.compile(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}\Z")
_STREAM_INT_RE = re.compile(r"[0-9]{1,6}\Z")


def _read_bounded_regular(path: Path):
    """Read one fixed regular file without following a final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > STREAM_PROFILE_MAX_BYTES:
            return None
        raw = b""
        while len(raw) <= STREAM_PROFILE_MAX_BYTES:
            chunk = os.read(fd, min(65536, STREAM_PROFILE_MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > STREAM_PROFILE_MAX_BYTES:
            return None
        return raw.decode("utf-8", "strict")
    except (OSError, UnicodeError):
        return None
    finally:
        os.close(fd)


def _shell_scalar(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value or any(ch in value for ch in "\r\n\x00"):
        return None
    return value


def _stream_value(key: str, raw: str):
    value = _shell_scalar(raw)
    if value is None:
        return None
    if key == "SOREN_DIRECT_STREAM_SIZE":
        return value if _STREAM_SIZE_RE.fullmatch(value) else None
    if _STREAM_INT_RE.fullmatch(value) is None:
        return None
    number = int(value)
    if key == "SOREN_DIRECT_STREAM_FPS":
        return number if 1 <= number <= 240 else None
    if key == "SOREN_DIRECT_STREAM_VIDEO_KBPS":
        return number if 1 <= number <= 100000 else None
    return None


def _stream_env_values(text: str):
    values = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if match is None or match.group(1) not in STREAM_PROFILE_KEYS:
            continue
        value = _stream_value(match.group(1), match.group(2))
        if value is not None:
            values[STREAM_PROFILE_KEYS[match.group(1)]] = value
    return values


def _stream_profile_values(text: str):
    values = {}
    for line in text.splitlines():
        match = re.match(
            r"^\s*set_env_value\s+"
            r"(SOREN_DIRECT_STREAM_SIZE|SOREN_DIRECT_STREAM_FPS|SOREN_DIRECT_STREAM_VIDEO_KBPS)"
            r"\s+(.+?)\s*$",
            line,
        )
        if match is None:
            continue
        value = _stream_value(match.group(1), match.group(2))
        if value is not None:
            values[STREAM_PROFILE_KEYS[match.group(1)]] = value
    return values


def _stream_profile_diagnostics(cfg, repo):
    """Project only three non-secret stream settings and their profile drift."""
    destination = cfg["repos"][repo].get("projections", {}).get("games/soviet_now")
    if not destination:
        return {
            "status": "unavailable",
            "effective": {},
            "profile_default": {},
            "drift": None,
            "drift_fields": [],
            "warning": "stream_profile_comparison_unavailable",
            "reason": "projection_missing",
        }
    root = Path(destination)
    env_text = _read_bounded_regular(root / ".env")
    profile_text = _read_bounded_regular(root / "configure_linux_stream_profile.sh")
    effective = _stream_env_values(env_text) if env_text is not None else {}
    defaults = _stream_profile_values(profile_text) if profile_text is not None else {}
    fields = ("size", "fps", "video_kbps")
    missing = [
        f"{side}.{field}"
        for side, values in (("effective", effective), ("profile_default", defaults))
        for field in fields
        if field not in values
    ]
    if missing:
        return {
            "status": "unavailable",
            "effective": effective,
            "profile_default": defaults,
            "drift": None,
            "drift_fields": [],
            "warning": "stream_profile_comparison_unavailable",
            "reason": "missing_values",
            "missing": missing,
        }
    drift_fields = [field for field in fields if effective[field] != defaults[field]]
    drift = bool(drift_fields)
    result = {
        "status": "warn" if drift else "ok",
        "effective": effective,
        "profile_default": defaults,
        "drift": drift,
        "drift_fields": drift_fields,
    }
    if drift:
        result["warning"] = "stream_profile_drift"
    return result


def _augment_stream_profile(result, cfg, repo, gateway):
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return result
    stream = _stream_profile_diagnostics(cfg, repo)
    diagnostics["stream"] = stream
    if stream["status"] != "ok" and diagnostics.get("status") == "ok":
        diagnostics["status"] = "warn"
    if len(json.dumps(diagnostics, separators=(",", ":")).encode()) > gateway.DIAGNOSTICS_JSON_MAX:
        raise ValueError("diagnostics output too large")
    return result


def _read_only_main(gateway, config_path: str, command: str):
    """Run fixed read-only operations under a shared non-blocking VM lock.

    Writers keep the legacy gateway's blocking exclusive lock. A read arriving
    while a writer owns that lock must fail closed with a fixed public reason
    rather than occupying an Actions runner for minutes. Holding the shared
    lock for the complete read keeps the snapshot mutually exclusive with a
    deployment while allowing independent readers to coexist.
    """
    parts = command.split()
    if not parts or parts[0] not in READ_ONLY_OPS:
        return None

    # Preserve the legacy gateway's own config/missing-file rejection path.
    # The installed path always exists; this mainly keeps bootstrap/test error
    # handling identical instead of inventing a second missing-config contract.
    config = Path(config_path)
    if not config.is_file():
        return None

    cfg = None
    try:
        cfg = gateway.load_config(config)
        op, repo, target, sha = gateway.parse_command(cfg)
        if op not in READ_ONLY_OPS:
            return None
        lock = gateway.state_root(cfg) / "vm-operations.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                gateway.die("VM operation rejected: gateway_busy")
            if op == "diagnostics":
                result = gateway.diagnostics_result(cfg, repo, target, sha)
                result = _augment_stream_profile(result, cfg, repo, gateway)
            elif op == "github_auth_diagnostics":
                result = gateway.github_auth_diagnostics(cfg, repo, target, sha)
            else:
                result = gateway.status_result(cfg, repo, target, sha)
    except ValueError as exc:
        gateway.die("VM operation rejected: %s" % gateway.reason_code(exc))
    except Exception:
        gateway._log_unexpected_traceback(cfg)
        gateway.die("VM operation rejected: operation_rejected")

    print(json.dumps(result, separators=(",", ":")))
    if result.get("exit_code", 0):
        raise SystemExit(result["exit_code"])
    return 0


def main():
    import gateway
    if len(sys.argv) != 2:
        gateway.die()
    command = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if command.split()[:1] in (["hanjuku_evidence"], ["hanjuku_evidence_query"]):
        import hanjuku_evidence_gateway
        return hanjuku_evidence_gateway.main(gateway, sys.argv[1])
    read_only = _read_only_main(gateway, sys.argv[1], command)
    if read_only is not None:
        return read_only
    # Mutating operations retain the legacy parser, blocking exclusive lock,
    # allowlist, stdin handling, and output/error behavior unchanged.
    gateway.main()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("VM operation rejected", file=sys.stderr)
        raise SystemExit(1)
