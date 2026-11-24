"""Where the shared Soren comment/audio queue lives, and how full it is.

The audio worker consumes one queue: ``soviet_now`` ``lib/outbound_queue.sh``
writes an item as ``comment_*.txt`` (queued), ``broadcast/comment.sh`` renames it
to ``comment_*.playing`` before the playback attempt and removes it afterwards
(``get_comment_backlog_counts``). Viewer comments, the radio host and every
corner announcement (including 予想王's intro/ranking) share that one queue.

The shell sink resolves the directory as
``${COMMENT_QUEUE_DIR:-tmp/.comment_queue}`` with the Soren root as cwd, after
sourcing ``<soren_root>/.env``. This module is the single Python resolver of the
same directory so a corner never gates on (or counts markers in) a directory the
sink does not write to:

1. ``COMMENT_QUEUE_DIR`` in the process environment,
2. ``COMMENT_QUEUE_DIR`` in ``<soren_root>/.env`` (what the shell actually sources),
3. ``tmp/.comment_queue`` below the Soren root.

A relative value is resolved against the Soren root, exactly like the shell.
"""
from __future__ import annotations

import os
from pathlib import Path

QUEUE_DEFAULT = "tmp/.comment_queue"
ITEM_PREFIX = "comment_"
QUEUED_SUFFIX = ".txt"
PLAYING_SUFFIX = ".playing"


def _dotenv_value(root: Path, name: str) -> str:
    """One value from ``<root>/.env``; empty when absent or unreadable.

    The parsing rules match the shell sink (``KEY=value``, optional matching
    quotes, ``" #"`` inline comment) and the WebUI's own dotenv reader.
    """
    try:
        lines = (root / ".env").read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        return value
    return ""


def comment_queue_dir(soren_root: Path) -> Path:
    """The comment/audio queue directory the audio worker consumes."""
    root = Path(soren_root)
    raw = os.environ.get("COMMENT_QUEUE_DIR", "") or _dotenv_value(root, "COMMENT_QUEUE_DIR")
    path = Path(raw) if raw else Path(QUEUE_DEFAULT)
    return path if path.is_absolute() else root / path


def backlog_counts(directory: Path) -> tuple[int, int] | None:
    """``(queued, playing)`` items, or ``None`` when the directory is unreadable.

    A missing queue is not an error: it means nothing is queued. ``None`` keeps
    that distinction available to read-only diagnostics while callers that must
    not block on a broken queue treat both as "nothing to wait for".
    """
    queued = playing = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return None
    for entry in entries:
        name = entry.name
        if not name.startswith(ITEM_PREFIX):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if name.endswith(QUEUED_SUFFIX):
            queued += 1
        elif name.endswith(PLAYING_SUFFIX):
            playing += 1
    return queued, playing


def is_quiet(directory: Path) -> bool:
    """True when nothing is queued and nothing is playing.

    An absent or unreadable queue is quiet: a broken queue must never block
    every corner, which is the same fail-open direction the prediction worker
    rule already takes.
    """
    counts = backlog_counts(directory)
    return counts is None or counts == (0, 0)
