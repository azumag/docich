"""Bounded, test-only notes for an isolated NetHack GUI startup failure."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

MAX_SOURCE_BYTES = 4096
MAX_NOTE_CHARS = 1024
_MANIFEST_STATUSES = frozenset({"starting", "tiles_active", "fallback_starting", "fallback_tty", "failed", "cleanup_failed", "stopped"})
_PRESENTATION_STATUSES = frozenset({"starting", "ready", "presentation_failed", "cleanup_failed", "stopped"})
_REASONS = frozenset({"browser_start_failed", "browser_exited", "browser_window_missing", "server_start_failed", "server_stopped", "reader_failed", "frame_stale", "fallback_start_failed", "fallback_window_missing", "startup_failed"})


def _read_object(path: Path) -> tuple[str, dict]:
    """Never follow a link or block on a non-regular fixture state file."""
    if not getattr(os, "O_NOFOLLOW", 0):
        return "unavailable", {}
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "unavailable", {}
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            return "oversize", {}
        value = json.loads(raw)
        return ("ok", value) if isinstance(value, dict) else ("invalid", {})
    except (OSError, ValueError, UnicodeError):
        return "unavailable", {}
    finally:
        if fd is not None:
            os.close(fd)


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def _window_count(probe, display):
    if not isinstance(display, str) or not re.fullmatch(r":[0-9]{1,5}", display):
        return {"available": False, "visible_count": None}
    value = probe(display)
    if not isinstance(value, dict):
        return {"available": False, "visible_count": None}
    count = value.get("visible_count")
    return {
        "available": value.get("available") is True,
        "visible_count": min(max(count, 0), 16) if type(count) is int else None,
    }


def _snapshot(runtime_dir, presentation_path, outer_display, window_probe):
    manifest_read, manifest = _read_object(Path(runtime_dir) / "nethack_tiles.json")
    presentation_read, presentation = _read_object(Path(presentation_path))
    return {
        "manifest": {
            "read": manifest_read,
            "status": _enum(manifest.get("status"), _MANIFEST_STATUSES),
            "mode": _enum(manifest.get("mode"), {"tiles", "tty"}),
            "reason": _enum(manifest.get("reason"), _REASONS),
            "browser_present": type(manifest.get("browser_pid")) is int and manifest["browser_pid"] > 1,
            "tty_present": type(manifest.get("tty_pid")) is int and manifest["tty_pid"] > 1,
            "frame_port_present": type(manifest.get("frame_port")) is int and 1 <= manifest["frame_port"] <= 65535,
        },
        "presentation": {
            "read": presentation_read,
            "status": _enum(presentation.get("status"), _PRESENTATION_STATUSES),
            "window_present": bool(presentation.get("window")),
        },
        "x11": {
            "private": _window_count(window_probe, presentation.get("display")),
            "outer": _window_count(window_probe, outer_display),
        },
    }


def readiness_with_diagnostics(adapter, deadline, cancel, runtime_dir, outer_display, window_probe):
    """Keep the original call/result/exception; annotate only its failure."""
    try:
        return adapter.readiness(deadline, cancel)
    except Exception as error:
        try:
            note = "NetHack GUI readiness: " + json.dumps(
                _snapshot(runtime_dir, adapter._presentation_path(), outer_display, window_probe),
                sort_keys=True, separators=(",", ":"),
            )
        except Exception:
            note = "NetHack GUI readiness: diagnostic unavailable"
        try:
            error.add_note(note[:MAX_NOTE_CHARS])
        except Exception:
            pass
        raise
