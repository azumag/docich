"""Bridge-only credential: a market-scoped HMAC bearer token.

The broker password/token stays on Windows.  Linux holds only this derived
bridge key.  Nothing here logs or returns the secret; failure reasons are
fixed codes.  Tests must use synthetic keys only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import threading
from typing import Mapping

TS_RE = re.compile(r"\d{10,12}\Z")
NONCE_RE = re.compile(r"[0-9a-f]{8,64}\Z")
HEADER = "X-Docich-Bridge"
KEY_ID_HEADER = "X-Docich-Key-Id"

# Server-side replay cache: (key_id, nonce) -> expiry timestamp.
# An accepted nonce is remembered until ``now + max_skew_s`` so the same nonce
# cannot be reused within the skew window.  Entries are lazily evicted at their
# expiry, keeping the cache bounded by the skew window and allowing a nonce to
# be reused only after the window elapses.  Guarded by a lock because the
# bridge server handles requests on multiple threads.
_REPLAY_LOCK = threading.Lock()
_REPLAY_CACHE: dict[tuple[str, str], float] = {}


def reset_replay_cache() -> None:
    """Test-only: forget every remembered nonce."""
    with _REPLAY_LOCK:
        _REPLAY_CACHE.clear()


def _record_nonce(key_id: str, nonce: str, now: float, max_skew_s: float) -> bool:
    """Return False if the nonce is still within its skew window (a replay).

    Only called after the HMAC signature has been verified, so unauthenticated
    attempts never pollute the cache.  The entry expires at ``now +
    max_skew_s`` and is lazily evicted, so a nonce may legitimately be reused
    once the skew window has elapsed.
    """
    with _REPLAY_LOCK:
        expiry = now + max_skew_s
        stale = [k for k, e in _REPLAY_CACHE.items() if e <= now]
        for k in stale:
            del _REPLAY_CACHE[k]
        if (key_id, nonce) in _REPLAY_CACHE:
            return False
        _REPLAY_CACHE[(key_id, nonce)] = expiry
        return True


def key_id_for(secret: bytes) -> str:
    """Public identifier derived from the secret (safe to log/transmit)."""
    return "mb-" + hashlib.sha256(secret).hexdigest()[:16]


def parse_key(text: str) -> bytes:
    """Parse a base64url 32-byte bridge key.  No broker credential is accepted."""
    if not isinstance(text, str):
        raise ValueError("invalid_bridge_key")
    token = text.strip()
    if len(token) < 32 or len(token) > 128:
        raise ValueError("invalid_bridge_key")
    try:
        key_bytes = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception as exc:
        raise ValueError("invalid_bridge_key") from exc
    if len(key_bytes) != 32:
        raise ValueError("invalid_bridge_key")
    return key_bytes


def signature(secret: bytes, ts: int, nonce: str) -> str:
    message = f"{ts}\n{nonce}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def build_headers(secret: bytes, *, ts: int, nonce: str) -> dict[str, str]:
    return {
        HEADER: f"{int(ts)}.{nonce}.{signature(secret, int(ts), nonce)}",
        KEY_ID_HEADER: key_id_for(secret),
    }


def verify_headers(headers: Mapping[str, str], *, secret: bytes, now: float,
                   max_skew_s: float = 60.0) -> tuple[bool, str]:
    """Return (ok, fixed_reason).  Constant-time comparisons; no secret in output."""
    raw = headers.get(HEADER) if hasattr(headers, "get") else None
    key_id = headers.get(KEY_ID_HEADER) if hasattr(headers, "get") else None
    if not isinstance(raw, str) or not isinstance(key_id, str):
        return False, "auth_missing"
    if not hmac.compare_digest(key_id, key_id_for(secret)):
        return False, "auth_failed"
    parts = raw.split(".")
    if len(parts) != 3 or not TS_RE.fullmatch(parts[0]) or not NONCE_RE.fullmatch(parts[1]):
        return False, "auth_failed"
    try:
        ts = int(parts[0])
    except ValueError:
        return False, "auth_failed"
    if abs(now - ts) > max_skew_s:
        return False, "auth_failed"
    expected = signature(secret, ts, parts[1])
    if not hmac.compare_digest(parts[2], expected):
        return False, "auth_failed"
    # Replay guard: reject a nonce already used within the skew window.  Checked
    # only after a valid signature so invalid attempts never occupy the cache.
    if not _record_nonce(key_id, parts[1], now, max_skew_s):
        return False, "replay_detected"
    return True, "ok"
