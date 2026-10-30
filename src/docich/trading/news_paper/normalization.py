"""Normalization gates for news observation input (N02, #778).

Input is untrusted.  Every gate either returns a normalized value or raises
``NormalizationError`` with a fixed code.  Nothing here fills in a missing
publication time, timezone, source or asset: an unknown value stays unknown and
the observation is quarantined by the caller.

Fixture-only constants below are not approved operating values (#778
condition 3).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

NORMALIZATION_VERSION = "fixture-normalization-v1"

# Fixture-only values. Not approved TTL/quota/cadence; used by tests to pin behaviour.
FIXTURE_MAX_URL_CHARS = 2048
FIXTURE_MAX_TITLE_CHARS = 300
FIXTURE_EXCERPT_CHARS = 280
FIXTURE_FUTURE_SKEW = timedelta(seconds=60)
FIXTURE_MAX_AGE = timedelta(hours=24)
FIXTURE_SIMILARITY_THRESHOLD = 0.9

CODE_URL_INVALID = "URL_INVALID"
CODE_URL_SCHEME = "URL_SCHEME_NOT_HTTPS"
CODE_URL_USERINFO = "URL_USERINFO_PRESENT"
CODE_URL_PORT = "URL_PORT_NOT_ALLOWED"
CODE_URL_PATH = "URL_PATH_TRAVERSAL"
CODE_URL_QUERY_KEY = "URL_QUERY_KEY_NOT_ALLOWED"
CODE_TIME_INVALID = "TIME_INVALID"
CODE_TIMEZONE_MISSING = "TIMEZONE_MISSING"
CODE_TIME_FUTURE = "TIME_FUTURE"
CODE_TIME_STALE = "TIME_STALE"
CODE_TEXT_INVALID = "TEXT_INVALID"
CODE_SECRET_SUSPECTED = "SECRET_SUSPECTED"

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_SECRET_PATTERNS = (
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),  # credentials in URL
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|bearer)\b\s*[:=]?\s*\S{6,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class NormalizationError(ValueError):
    """Fixed-code rejection. ``str(exc)`` is the code and nothing else."""


def canonical_url(raw: object, *, allowed_query_keys: tuple[str, ...] = ()) -> str:
    """Return the canonical https URL, or raise. No fetch happens here."""
    if not isinstance(raw, str) or not raw or len(raw) > FIXTURE_MAX_URL_CHARS:
        raise NormalizationError(CODE_URL_INVALID)
    if _CONTROL_RE.search(raw) or any(ch.isspace() for ch in raw):
        raise NormalizationError(CODE_URL_INVALID)
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise NormalizationError(CODE_URL_INVALID) from None
    if parts.scheme.lower() != "https":
        raise NormalizationError(CODE_URL_SCHEME)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise NormalizationError(CODE_URL_USERINFO)
    if port not in (None, 443):
        raise NormalizationError(CODE_URL_PORT)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise NormalizationError(CODE_URL_INVALID)
    path = parts.path or "/"
    if any(seg in ("..", ".") for seg in path.split("/")) or "%2e" in path.lower():
        raise NormalizationError(CODE_URL_PATH)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    allowed = set(allowed_query_keys)
    if any(key not in allowed for key, _ in pairs):
        raise NormalizationError(CODE_URL_QUERY_KEY)
    query = urlencode(sorted(pairs))
    return f"https://{host}{path}" + (f"?{query}" if query else "")


def normalize_title(raw: object) -> str:
    if not isinstance(raw, str):
        raise NormalizationError(CODE_TEXT_INVALID)
    text = unicodedata.normalize("NFKC", raw)
    if _CONTROL_RE.search(text):
        raise NormalizationError(CODE_TEXT_INVALID)
    text = _SPACE_RE.sub(" ", text).strip()
    if not text or len(text) > FIXTURE_MAX_TITLE_CHARS:
        raise NormalizationError(CODE_TEXT_INVALID)
    return text


def normalize_body(raw: object) -> str:
    """Body text for digesting only. The full body is never stored."""
    if not isinstance(raw, str):
        raise NormalizationError(CODE_TEXT_INVALID)
    text = unicodedata.normalize("NFKC", raw)
    return _SPACE_RE.sub(" ", _CONTROL_RE.sub(" ", text)).strip()


def content_digest(normalized_body: str) -> str:
    return hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()


def excerpt(normalized_body: str) -> str:
    """Bounded excerpt. Only this part is persisted; the rest is digested and dropped."""
    return normalized_body[:FIXTURE_EXCERPT_CHARS]


def secret_suspected(text: str) -> bool:
    """True when text carries credential-like material. Callers store a code, not the text."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def parse_published_at(raw: object, *, now: datetime) -> datetime:
    """RFC3339 with explicit offset only. Missing offset is rejected, never assumed."""
    if not isinstance(raw, str) or not raw or len(raw) > 64:
        raise NormalizationError(CODE_TIME_INVALID)
    text = raw.strip()
    if text[-1:] in ("z", "Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise NormalizationError(CODE_TIME_INVALID) from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise NormalizationError(CODE_TIMEZONE_MISSING)
    moment = moment.astimezone(timezone.utc)
    if moment > now + FIXTURE_FUTURE_SKEW:
        raise NormalizationError(CODE_TIME_FUTURE)
    if now - moment > FIXTURE_MAX_AGE:
        raise NormalizationError(CODE_TIME_STALE)
    return moment


def require_aware(moment: object) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise NormalizationError(CODE_TIMEZONE_MISSING)
    return moment.astimezone(timezone.utc)
