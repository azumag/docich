"""The LIVE worker status file: one small, private, atomically replaced JSON document.

The status file is the operator's view of the worker.  It is deliberately dumb and safe:

* only an allow-listed key set is ever written (see ``ALLOWED_KEYS``); unknown keys are dropped,
* only ``str``/``int``/``list[str]``/``None`` values survive; anything else is dropped,
* there are no balances, amounts, order ids, prices or key material in it,
* it is created 0o600 with ``os.open(..., O_EXCL | O_NOFOLLOW)`` and replaced atomically after
  an fsync, so a reader never sees a partial document and a symlink is never followed,
* every failure (unwritable dir, a bad value, a failed rename) returns ``False`` and never
  raises: a status write must never change a cycle outcome or an exit code.

Nothing here imports an exchange, reaches the network, or starts a thread.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from ..live.paths import live_layout

STATUS_FILENAME = "live_status.json"
SCHEMA_VERSION = 1

# The runner's configured cycle interval, in whole milliseconds.  Milliseconds keep a
# fractional CLI interval (for example 600.5 s) exact without widening the allow-list to
# floats; the CLI's supported range is 30..3600 s.
MIN_INTERVAL_MS = 30_000
MAX_INTERVAL_MS = 3_600_000
DEFAULT_INTERVAL_MS = 60_000

PHASES = ("starting", "running", "inert", "stopping", "exited")
PHASE_SET = frozenset(PHASES)

# The complete set of keys that may appear in the status file.  ``schema_version`` and
# ``written_at`` are generated here; a caller cannot override them.
ALLOWED_KEYS = frozenset({
    "schema_version", "written_at", "started_at", "pid", "phase", "cycle", "mode",
    "outcome", "reason", "blocks", "fresh_symbols", "stale_symbols",
    "consecutive_failures", "record_failures", "ttl_deferred", "ttl_failed",
    "balance_warnings", "control_state", "control_mode", "policy_set_digest", "inert_reason",
    "exit_code", "interval_ms",
})
TRUNCATED_KEYS = frozenset({"policy_set_digest"})
ALLOWED_PHASES = PHASE_SET
_HEX = frozenset("0123456789abcdef")
_DROP = object()


def status_path(state_dir) -> Path:
    """The status file next to the LIVE ledger (the layout root is created by the ledger)."""
    return live_layout(state_dir).root / STATUS_FILENAME


def _clean_value(key: str, value: object) -> object:
    """The value to write for ``key``, or ``_DROP`` when it is not an allowed shape."""
    if value is None:
        return None
    if isinstance(value, str):
        if key == "phase":
            return value if value in ALLOWED_PHASES else _DROP
        if key in TRUNCATED_KEYS:
            short = value[:12]
            return short if short and all(ch in _HEX for ch in short) else _DROP
        if key == "control_state":
            return value if value in ("on", "off", "unknown") else _DROP
        if key == "control_mode":
            return value if value in ("shadow", "trade") else _DROP
        return value
    if type(value) is int:                       # bool is not an int here
        if key == "balance_warnings" and value < 0:
            return _DROP
        if key == "interval_ms" and not MIN_INTERVAL_MS <= value <= MAX_INTERVAL_MS:
            return _DROP
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return _DROP


def _record(fields: dict, now: float) -> dict:
    record: dict = {}
    for key, value in fields.items():
        if key not in ALLOWED_KEYS or key in ("schema_version", "written_at"):
            continue
        cleaned = _clean_value(key, value)
        if cleaned is _DROP:
            continue
        record[key] = cleaned
    record["schema_version"] = SCHEMA_VERSION
    record["written_at"] = int(now)
    return record


def write_status(state_dir, fields: dict, *, now: float) -> bool:
    """Atomically write the allow-listed ``fields``; return True only on a complete write.

    Never raises.  A symlink at the final path, a wider-than-0600 mode, a partial write, a
    failed rename or an unreadable directory all just return ``False``.
    """
    path: Path | None = None
    tmp: Path | None = None
    fd: int | None = None
    try:
        record = _record(fields if isinstance(fields, dict) else {}, now)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        path = status_path(state_dir)
        if os.path.islink(path):
            return False
        parent = path.parent
        tmp = parent / f".{STATUS_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(tmp, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, path)
        tmp = None
        dir_fd = os.open(str(parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except Exception:
        return False
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
