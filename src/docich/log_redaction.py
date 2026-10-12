"""Shared event-detail redaction, applied to complete input before budgeting."""

import re


_AUTH_HEADER_CREDENTIAL = re.compile(
    r"(?i)\b(?P<header>authorization\s*[:=]\s*)"
    r"(?:bearer|basic|digest|negotiate)\s+\S+"
)
_STANDALONE_AUTH_CREDENTIAL = re.compile(
    r"(?i)(?<![\w\-])(?:bearer|basic|digest|negotiate)\s+\S+"
)
_SECRET_KEY_VALUE = re.compile(
    r"(?i)\b(?P<key>token|api[_-]?key|password|passwd|pwd|secret|"
    r"stream[_-]?key|auth|authorization|bearer|session[_-]?key|"
    r"private[_-]?key|client[_-]?secret)\b(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|\S+)"
)
_ARGV_EXPR = re.compile(
    # Exception strings have no reliable command grammar or terminator.
    # Quotes, escapes, nested containers and upstream clipping must never
    # expose a trailing argument: conservatively redact the whole remainder.
    r"(?is)\b(?:argv|command|cmd|args)\s*[:=].*"
)
_URL_WHOLE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s\"']+")
_LONG_OPAQUE_TOKEN = re.compile(r"\b(?:[0-9a-f]{32,}|[0-9A-Za-z+/]{24,}={0,2})\b")


def redact_log_detail(detail: str) -> str:
    """Redact secrets from an event-log detail (design v2 §9).

    The log contract records no URLs, tokens, or argv: command expressions
    and whole URLs are replaced outright, credential key=value pairs keep
    only a redacted value, and leftover long opaque tokens are replaced.
    Hosts are not preserved (a URL is a URL).  Game/window names,
    generations, request ids and error codes survive.
    """
    text = str(detail).replace("\n", " ")
    # Detect commands before another mask can remove their marker (e.g. a
    # standalone Bearer mask consuming the first token of an argv expression).
    text = _ARGV_EXPR.sub("<redacted>", text)
    text = _AUTH_HEADER_CREDENTIAL.sub(lambda m: f"{m['header']}<redacted>", text)
    text = _STANDALONE_AUTH_CREDENTIAL.sub("<redacted>", text)
    text = _URL_WHOLE.sub("<redacted-url>", text)
    text = _SECRET_KEY_VALUE.sub(lambda m: f"{m['key']}{m['sep']}<redacted>", text)
    text = _LONG_OPAQUE_TOKEN.sub("<redacted>", text)
    return text

