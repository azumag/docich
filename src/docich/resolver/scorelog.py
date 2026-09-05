"""Stable identifiers for resolver strategies stored in improvement logs."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def strategy_key(strategy: Mapping[str, object]) -> str:
    """Return a deterministic content key for a resolver strategy."""
    payload = json.dumps(
        dict(strategy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
