"""Operator pause marker of the docich-owned encoder (#3553, ADR 0007 D2).

``tmp/state/docich_stream.paused`` is the docich counterpart of the legacy
supervisor's ``tmp/state/direct_stream.paused``: while it exists the supervisor
does not start an encoder. The two are deliberately different files so that
turning the stream off or on never makes the legacy supervisor respawn (or log
about) a worker it no longer owns, and vice versa.

Only file existence is meaningful; the JSON payload mirrors the legacy marker.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Callable

STREAM_PAUSE_CONTRACT_VERSION = 1
MARKER_RELATIVE = "tmp/state/docich_stream.paused"


def marker_path(root: str | Path) -> Path:
    return Path(root) / MARKER_RELATIVE


def is_paused(root: str | Path) -> bool:
    try:
        return marker_path(root).is_file()
    except OSError:
        return False


def set_paused(root: str | Path, paused: bool, *, source: str = "operator") -> None:
    marker = marker_path(root)
    if not paused:
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"paused": True, "ts": int(time.time()), "source": source}, ensure_ascii=False)
    temporary = marker.with_name(marker.name + f".tmp{os.getpid()}")
    temporary.write_text(payload + "\n", encoding="utf-8")
    os.replace(temporary, marker)
    try:
        os.chmod(marker, 0o644)
    except OSError:
        pass


def wait_while_paused(
    root: str | Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_sec: float = 1.0,
    announce: Callable[[str], None] | None = None,
) -> bool:
    """Block until the marker is gone. Returns True when it had to wait."""
    if not is_paused(root):
        return False
    if announce is not None:
        announce("stream_supervisor: paused by operator marker; not starting the encoder")
    while is_paused(root):
        sleep(poll_sec)
    if announce is not None:
        announce("stream_supervisor: pause marker removed; starting")
    return True
