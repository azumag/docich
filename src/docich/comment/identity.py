"""Self-authored chat identity (#2373), mirrored from the soviet_now shell.

Until the #829 comment cutover the live reply path is still the soviet_now
shell (``twitch_chat.sh`` / ``twitch_chat_daemon.sh`` / ``broadcast/comment.sh``
via ``docich chat``), so these helpers only pin the contract that the native
pipeline has to reproduce:

* self identity is the IRC ``login`` compared exactly against the configured
  account names (``TWITCH_SELF_AUTHORS``; ``TWITCH_IGNORE_AUTHORS`` stays
  readable for compatibility) — never a display name and never a
  self-declaration inside the message body;
* an unknown or missing identity is not self;
* a line already carrying the ingestion ``self`` flag (identity metadata) is
  presented as the AI's own past statement instead of a viewer comment.
"""
from __future__ import annotations

import os
from typing import Iterable, Mapping

SELF_AUTHORS_ENV = "TWITCH_SELF_AUTHORS"
LEGACY_SELF_AUTHORS_ENV = "TWITCH_IGNORE_AUTHORS"
DEFAULT_SELF_AUTHORS = ("dociai", "azumagdev")


def self_author_logins(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The configured self logins, lowercased, in declaration order."""
    env = os.environ if env is None else env
    raw = (env.get(SELF_AUTHORS_ENV) or env.get(LEGACY_SELF_AUTHORS_ENV) or "").strip()
    if not raw:
        return DEFAULT_SELF_AUTHORS
    return tuple(item.lower() for item in raw.split() if item)


def is_self_author_login(login: str, env: Mapping[str, str] | None = None) -> bool:
    """Exact (case-insensitive) login match; display names are never consulted.

    Whitespace is not trimmed, matching the shell's ``tr`` based comparison:
    only the sanitized IRC login can equal a configured name.
    """
    candidate = (login or "").lower()
    if not candidate.strip():
        return False
    return candidate in self_author_logins(env)


def is_self_comment(entry: Mapping[str, object] | None) -> bool:
    """Whether ingestion marked this metadata entry as the AI's own post.

    Only the collector-assigned ``self`` flag counts. Missing metadata (an
    unknown poster) is never treated as self.
    """
    if not isinstance(entry, Mapping):
        return False
    flags = entry.get("flags")
    if not isinstance(flags, (list, tuple)):
        return False
    return any(flag == "self" for flag in flags)


def split_self_comments(
    lines: Iterable[str], entries: Iterable[Mapping[str, object] | None]
) -> tuple[list[str], list[str]]:
    """Split a batch into (third-party comment lines, own-post lines).

    Metadata is positional, exactly like the shell side: a line without a
    matching entry keeps its place in the reply list.
    """
    reply_targets: list[str] = []
    own_posts: list[str] = []
    entries = list(entries)
    for index, line in enumerate(lines):
        entry = entries[index] if index < len(entries) else None
        (own_posts if is_self_comment(entry) else reply_targets).append(line)
    return reply_targets, own_posts


__all__ = ["DEFAULT_SELF_AUTHORS", "LEGACY_SELF_AUTHORS_ENV", "SELF_AUTHORS_ENV",
           "is_self_author_login", "is_self_comment", "self_author_logins", "split_self_comments"]
