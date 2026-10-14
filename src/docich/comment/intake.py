"""Normalized intake helpers for the native comment pipeline (#829 PR-3e)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .contracts import CommentIntake, CommentMessage


def comments_from_lines(lines: Iterable[str], *, source: str = "fixture",
                        batch_id: str = "", host_mode: str = "main") -> CommentIntake:
    messages = []
    for index, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if ": " in line:
            author, text = line.split(": ", 1)
        else:
            author, text = "unknown", line
        messages.append(CommentMessage(author=author.strip() or "unknown", text=text.strip(), source=source,
                                      stable_id=f"{source}:{index}"))
    return CommentIntake(
        comments=tuple(messages),
        source=source,
        batch_id=batch_id or f"{source}_{len(messages)}",
        host_mode="soren91" if host_mode == "soren91" else "main",
    )


def comments_from_text(text: str, **kwargs) -> CommentIntake:
    return comments_from_lines(text.splitlines(), **kwargs)


def comments_from_file(path: str | Path, **kwargs) -> CommentIntake:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return comments_from_text(text, batch_id=kwargs.pop("batch_id", path.stem), **kwargs)


def empty_intake(*, source: str = "fixture", batch_id: str = "empty") -> CommentIntake:
    return CommentIntake(comments=(), source=source, batch_id=batch_id, host_mode="main")
