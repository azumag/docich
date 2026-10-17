"""Normalized intake helpers for the native comment pipeline (#829 PR-3e)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .contracts import CommentIntake, CommentMessage


def _bot_login_from_env() -> str:
    import os

    return (os.environ.get("DOCICH_BOT_LOGIN")
            or os.environ.get("DOCICH_SELF_LOGIN")
            or "dociai").strip().lower()


def comments_from_lines(lines: Iterable[str], *, source: str = "fixture",
                        batch_id: str = "", host_mode: str = "main",
                        bot_login: str = "") -> CommentIntake:
    if not bot_login:
        bot_login = _bot_login_from_env()
    bot_login = bot_login.strip().lower()
    messages = []
    for index, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if ": " in line:
            author, text = line.split(": ", 1)
        else:
            author, text = "unknown", line
        author = author.strip() or "unknown"
        # author_login tracks the actual poster identity (Twitch login).
        # When the input line carries only a display name, we treat the
        # lowercased author as the login for backward compatibility.
        author_login = author.lower()
        messages.append(CommentMessage(author=author, text=text.strip(), source=source,
                                      stable_id=f"{source}:{index}",
                                      author_login=author_login))
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


def is_self_comment(message: CommentMessage, bot_login: str = "") -> bool:
    """True only when the comment's actual login matches the bot's configured login."""
    if not bot_login:
        bot_login = _bot_login_from_env()
    login = (getattr(message, "author_login", "") or message.author).strip().lower()
    return bool(login) and login == bot_login.strip().lower()
