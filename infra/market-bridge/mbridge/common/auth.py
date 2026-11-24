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
REPLAY_CACHE_LIMIT = 4096  # fail closed rather than grow without bound under authenticated floods
HEADER = "X-Docich-Bridge"
KEY_ID_HEADER = "X-Docich-Key-Id"

# Server-side replay cache: (key_id, nonce) -> expiry timestamp.
# An accepted nonce is remembered through ``signed_ts + max_skew_s`` (the last
# instant its signature can still pass the skew check). Entries are lazily evicted after expiry
# and the cache has a hard cardinality limit; once full, new nonces fail closed
# until expired entries are removed. Guarded by a lock for concurrent handlers.
_REPLAY_LOCK = threading.Lock()
_REPLAY_CACHE: dict[tuple[str, str], float] = {}


def reset_replay_cache() -> None:
    """Test-only: forget every remembered nonce."""
    with _REPLAY_LOCK:
        _REPLAY_CACHE.clear()


def _record_nonce(key_id: str, nonce: str, signed_ts: int, max_skew_s: float) -> str:
    """Return a fixed reason code; capacity exhaustion fails closed.

    Only called after the HMAC signature has been verified, so unauthenticated
    attempts never pollute the cache.  The entry expires at ``now +
    max_skew_s`` and is lazily evicted, so a nonce may legitimately be reused
    once the skew window has elapsed.
    """
    with _REPLAY_LOCK:
        expiry = signed_ts + max_skew_s
        # verify_headers accepts the exact skew boundary, so keep the nonce at
        # e == now and evict only once its signature has become invalid.
        stale = [k for k, e in _REPLAY_CACHE.items() if e < now]
        for k in stale:
            del _REPLAY_CACHE[k]
        if (key_id, nonce) in _REPLAY_CACHE:
            return "replay_detected"
        if len(_REPLAY_CACHE) >= REPLAY_CACHE_LIMIT:
            return "replay_cache_full"
        _REPLAY_CACHE[(key_id, nonce)] = expiry
        return "ok"


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
    replay_reason = _record_nonce(key_id, parts[1], ts, max_skew_s)
    if replay_reason != "ok":
        return False, replay_reason
    return True, "ok"
