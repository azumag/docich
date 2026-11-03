"""Common retire marker for completed runtime generations (docich#3122).

Game-switch teardown writes one marker into every runtime directory whose
stop it confirmed.  The marker is the completion proof for corners that never
write a per-game terminal record (the browser-profile corners), so the
browser-profile reclaim helper can recognize those generations as completed
without introducing a second deletion path.

The marker is additive and deliberately weak: it records that the switch
observed the runtime stop, nothing more.  Consumers still apply their own
gates (canonical inactive, keep-recent window, age, symlink/hardlink
rejection).  The hanjuku run record stays the stricter proof where it exists.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

from .naming import RUNTIME_ID_PATTERN

RETIRE_MARKER = "retired"
SCHEMA = 1
MAX_MARKER_BYTES = 64 * 1024
MARKER_TEMP_SUFFIX = ".tmp"

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class RetireMarkerError(ValueError):
    """Fixed-code failure; never carries paths or runtime content."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload(runtime_id: str, generation: int, retired_at: str) -> bytes:
    return (json.dumps(
        {"schema": SCHEMA, "runtime_id": runtime_id,
         "generation": generation, "retired_at": retired_at},
        sort_keys=True, separators=(",", ":"),
    ) + "\n").encode()


def write_retire_marker(state_dir, runtime, *, retired_at: str | None = None) -> bool:
    """Write the marker for one torn-down runtime, idempotently.

    Returns ``True`` when a new marker was written and ``False`` when the
    generation already carries one (the first retirement timestamp wins).
    Any unsafe or unavailable path raises :class:`RetireMarkerError`; the
    caller treats that as a warning so switch completion never depends on
    housekeeping.
    """
    runtime_id = runtime.get("runtime_id") if isinstance(runtime, dict) else None
    generation = runtime.get("generation") if isinstance(runtime, dict) else None
    match = RUNTIME_ID_PATTERN.fullmatch(runtime_id) if isinstance(runtime_id, str) else None
    if (match is None or type(generation) is not int
            or int(match.group(1)) != generation):
        raise RetireMarkerError("invalid_runtime")
    runtime_dir = Path(state_dir) / "runtimes" / runtime_id
    if runtime_dir.is_symlink():
        raise RetireMarkerError("unsafe_runtime_dir")
    try:
        dir_fd = os.open(runtime_dir, _DIR_FLAGS)
    except OSError:
        raise RetireMarkerError("runtime_dir_unavailable") from None
    try:
        try:
            existing = os.stat(RETIRE_MARKER, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        except OSError:
            raise RetireMarkerError("marker_stat_failed") from None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise RetireMarkerError("unsafe_marker")
            return False
        temp_name = f"{RETIRE_MARKER}.{os.getpid()}{MARKER_TEMP_SUFFIX}"
        data = _payload(runtime_id, generation, retired_at or _utc_now())
        try:
            out = os.open(temp_name, _FILE_FLAGS, 0o644, dir_fd=dir_fd)
        except OSError:
            raise RetireMarkerError("marker_create_failed") from None
        try:
            try:
                os.write(out, data)
                os.fsync(out)
            finally:
                os.close(out)
            os.replace(temp_name, RETIRE_MARKER, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
        except OSError:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise RetireMarkerError("marker_write_failed") from None
        return True
    finally:
        os.close(dir_fd)
