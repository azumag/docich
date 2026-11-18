"""Owner's LIVE on/off control file.  Reads one file; writes only on request; never raises on read.

The file is the owner's plain switch.  ``state`` says whether the bot may do anything at all
(``on``/``off``); ``mode`` says whether an ``on`` bot may send orders (``trade``) or only watch
(``shadow``).  There is no expiry, no policy digest and no code revision in it: switching off is
not a safety trip, and switching back on needs no human safety clear.

Reading is fail-closed: a missing, unreadable, over-large, loosely-permissioned, corrupt or
schema-invalid file reads as OFF with the matching reason.  ``read_control`` never raises.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
from pathlib import Path

from .canonical import sha256_hex
from .enable import EnableReason, EnableResult
from .paths import live_layout

MAX_CONTROL_BYTES = 4096
_KEYS = {"schema_version", "kind", "state", "mode", "updated_at", "updated_by"}
_STATES = ("on", "off")
_MODES = ("shadow", "trade")
MAX_UPDATED_BY = 80


def _no(*reasons: EnableReason) -> EnableResult:
    return EnableResult(False, tuple(reasons))


def _dir_ok(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and (info.st_mode & 0o777) == 0o700)


def _aware_text(value: object) -> str | None:
    """The ISO timestamp when it carries a timezone, else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return value


def _inspect(state_dir, *, now: float) -> tuple[EnableResult, dict | None]:
    """One reading: (result, the parsed document only when it is schema-valid).

    ``now`` is accepted for the gate's uniform signature; the control file has no expiry.
    """
    try:
        layout = live_layout(state_dir)
        if not _dir_ok(layout.root):
            return _no(EnableReason.ROOT_INVALID if layout.root.exists() else EnableReason.FILE_MISSING), None
        if not _dir_ok(layout.owner_dir):
            return _no(EnableReason.OWNER_DIR_INVALID if layout.owner_dir.exists()
                       else EnableReason.FILE_MISSING), None
        try:
            fd = os.open(layout.control_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return _no(EnableReason.FILE_MISSING), None
        except OSError:
            return _no(EnableReason.NOT_REGULAR), None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return _no(EnableReason.NOT_REGULAR), None
            if info.st_uid != os.geteuid():
                return _no(EnableReason.OWNER_MISMATCH), None
            if (info.st_mode & 0o777) != 0o600:
                return _no(EnableReason.MODE_NOT_0600), None
            if info.st_size > MAX_CONTROL_BYTES:
                return _no(EnableReason.TOO_LARGE), None
            raw = os.read(fd, MAX_CONTROL_BYTES + 1)
        finally:
            os.close(fd)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _no(EnableReason.CORRUPT_JSON), None
        if not isinstance(data, dict):
            return _no(EnableReason.SCHEMA_INVALID), None
        if set(data) - _KEYS:
            return _no(EnableReason.UNKNOWN_KEYS), None
        if (not _KEYS <= set(data)
                or type(data["schema_version"]) is not int
                or data["schema_version"] != 1
                or data["kind"] != "docich-live-control"
                or data["state"] not in _STATES
                or data["mode"] not in _MODES
                or _aware_text(data["updated_at"]) is None
                or not isinstance(data["updated_by"], str)
                or not data["updated_by"]
                or len(data["updated_by"]) > MAX_UPDATED_BY):
            return _no(EnableReason.SCHEMA_INVALID), None
        if data["state"] == "off":
            return EnableResult(False, (EnableReason.SWITCHED_OFF,), mode=data["mode"]), data
        return (EnableResult(True, (), mode=data["mode"], file_sha256=sha256_hex(raw)), data)
    except Exception:  # fail closed, never raise
        return _no(EnableReason.CHECK_ERROR), None


def read_control(state_dir, *, now: float) -> EnableResult:
    """Read the owner's switch.  Missing or invalid always reads as OFF, never raises."""
    return _inspect(state_dir, now=now)[0]


def describe_control(state_dir, *, now: float) -> dict:
    """The allow-listed view for status/UI: no raw content, no file bytes."""
    try:
        result, data = _inspect(state_dir, now=now)
    except Exception:  # belt and braces: describe must never raise either
        result, data = _no(EnableReason.CHECK_ERROR), None
    if result.enabled:
        state = "on"
    elif EnableReason.SWITCHED_OFF in result.reasons:
        state = "off"
    else:
        state = "unknown"
    return {
        "state": state,
        "mode": result.mode,
        "updated_at": data.get("updated_at") if data else None,
        "updated_by": data.get("updated_by") if data else None,
        "reasons": [reason.value for reason in result.reasons],
    }


def _ensure_dir(path: Path) -> None:
    """Create one private directory, or refuse an existing one that is not ours and 0700."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
            info = os.lstat(path)
        except OSError as exc:
            raise ValueError(f"cannot create {path.name}: {type(exc).__name__}") from exc
    except OSError as exc:
        raise ValueError(f"cannot inspect {path.name}: {type(exc).__name__}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{path.name} is not a directory")
    if info.st_uid != os.geteuid():
        raise ValueError(f"{path.name} is not owned by this user")
    if (info.st_mode & 0o777) != 0o700:
        raise ValueError(f"{path.name} must be mode 0700")


def write_control(state_dir, *, state: str, mode: str, updated_by: str, now: float) -> None:
    """Atomically replace the control file (0600) after validating every input."""
    if state not in _STATES:
        raise ValueError("state must be 'on' or 'off'")
    if mode not in _MODES:
        raise ValueError("mode must be 'shadow' or 'trade'")
    if not isinstance(updated_by, str) or not updated_by or len(updated_by) > MAX_UPDATED_BY:
        raise ValueError(f"updated_by must be 1..{MAX_UPDATED_BY} characters")
    try:
        moment = dt.datetime.fromtimestamp(float(now), tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError("now must be an epoch timestamp") from exc

    layout = live_layout(state_dir)
    _ensure_dir(layout.root)
    _ensure_dir(layout.owner_dir)

    payload = json.dumps({
        "schema_version": 1,
        "kind": "docich-live-control",
        "state": state,
        "mode": mode,
        "updated_at": moment.isoformat(),
        "updated_by": updated_by,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tmp: Path | None = layout.owner_dir / f".live_control.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(tmp, flags, 0o600)
        os.fchmod(fd, 0o600)                      # mode 0600 is not subject to the umask
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, layout.control_path)
        tmp = None
        dir_fd = os.open(str(layout.owner_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
