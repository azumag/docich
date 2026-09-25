"""Runtime-local RetroArch input gate and explicit saved-boundary receipts.

This is not a game-over detector. A reviewed caller must explicitly confirm a
paused checkpoint; merely requesting a switch leaves gameplay running.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import time

from .adapters.base import AdapterError
from .game_switch import DeadlineExceededError

BOUNDARY_FILE = "retroarch_boundary.json"
MANUAL_SAVE_FILE = "hanjuku_manual_save.json"


def read_record(path: Path) -> dict:
    try:
        metadata = path.stat()
        if path.is_symlink() or not path.is_file() or metadata.st_size > 16384:
            raise AdapterError("RetroArch runtime record is not a bounded regular record")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise AdapterError("RetroArch runtime record is unreadable") from exc
    if not isinstance(data, dict):
        raise AdapterError("RetroArch runtime record must be an object")
    return data


@contextmanager
def input_gate(runtime_dir: Path, deadline: float, cancel=None):
    """Serialize boundary confirmation with *all* game input, not the wait."""
    with (runtime_dir / "retroarch_input.lock").open("a") as lock:
        while True:
            if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                raise DeadlineExceededError("RetroArch input gate timed out/cancelled")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def require_input_open(runtime_dir: Path) -> None:
    record = read_record(runtime_dir / BOUNDARY_FILE)
    if record and record.get("status") not in {"waiting", "cancelled"}:
        raise AdapterError("RetroArch safe boundary holds input; explicit recovery required")


def identity(spec, request_id: str) -> dict:
    return dict(schema=1, runtime_id=spec.runtime_id, generation=spec.generation,
                game=spec.game, lease_id=spec.lease_id, request_id=request_id)


def matches(record: dict, spec, request_id: str) -> bool:
    return all(record.get(key) == value for key, value in identity(spec, request_id).items())


def checkpoint_digest(path: Path, deadline: float | None = None, cancel=None) -> str:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise AdapterError("RetroArch checkpoint is missing, empty or a symlink")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > 64 * 1024 * 1024:
                raise AdapterError("RetroArch checkpoint exceeds size bound")
            if (deadline is not None and time.monotonic() >= deadline) or (cancel is not None and cancel.is_set()):
                raise DeadlineExceededError("RetroArch checkpoint verification timed out/cancelled")
            digest.update(chunk)
    return digest.hexdigest()
