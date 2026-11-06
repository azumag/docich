"""Private state-file primitives for the single direct-stream encoder (#3553).

Ownership: docich streaming infrastructure. Filesystem effects are limited to
what the caller passes in: a state directory it names, one JSON file it names,
and signal 0 liveness probes. It never starts, signals-to-terminate, or
restarts a process, never opens a network socket, never reads credentials, and
never imports the Soviet game.

The legacy Soren caller keeps its own implementation as the mandatory fallback
and verifies every shared write/read before relying on it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable, Mapping

STREAM_STATE_FILES_CONTRACT_VERSION = 1


def pid_alive(pid: object) -> bool:
    """True when ``pid`` names a live process (signal 0; never terminates)."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def ensure_private_directory(path: Path) -> None:
    """Create a directory tree that never carries group/other write bits.

    Every missing level is created explicitly with mode 0o700 (``parents=True``
    would honour the umask). An existing level keeps its identity; when this
    process owns it, any group/other write bit is cleared so a previously
    relaxed directory self-heals instead of failing fixed-source diagnostics.
    """
    current = Path(path)
    missing = []
    probe = current
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    for level in reversed(missing):
        try:
            level.mkdir(mode=0o700)
        except FileExistsError:
            pass
    try:
        metadata = current.lstat()
    except OSError:
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise FileExistsError(f"private directory path is not a directory: {current.name}")
    if metadata.st_uid == os.geteuid() and metadata.st_mode & 0o022:
        os.chmod(current, metadata.st_mode & ~0o022)


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write ``payload`` as sorted JSON via a same-directory temp + rename."""
    path = Path(path)
    ensure_private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_stream_status(
    state_file: Path,
    *,
    alive: Callable[[object], bool] = pid_alive,
) -> dict[str, object]:
    """Read the runner status file without exposing an output URL.

    ``running`` is only true while the supervisor or encoder pid is alive, so a
    stale file left by a crashed runner never reports a live stream.
    """
    try:
        data = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"backend": "ffmpeg", "running": False, "state": "not_started"}
    if not isinstance(data, dict):
        return {"backend": "ffmpeg", "running": False, "state": "invalid_status"}
    data.pop("output_url", None)
    data["running"] = bool(data.get("running")) and (
        alive(data.get("pid")) or alive(data.get("ffmpeg_pid"))
    )
    return data
