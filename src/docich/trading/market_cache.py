"""Persistent per-market public-data cache for the paper worker.

The cache keeps the last successful frame batch per symbol so a failed or
over-budget cycle never erases the last good observation. Cached frames are
for display/history only: trading decisions always use freshly fetched
frames from the current cycle.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

# Cached entries older than this are pruned (daily retention).
CACHE_MAX_AGE_S = 7 * 24 * 3600
# Hard cap on cached symbols as a backstop against unbounded growth.
CACHE_MAX_SYMBOLS = 512
# Stored closes per symbol (contract is 24 bars; the cap guards future callers).
CACHE_MAX_CLOSES = 512


def cache_path(state_dir: Path) -> Path:
    return Path(state_dir) / "trading" / "market_cache.json"


def load_cache(path: Path) -> dict[str, dict[str, object]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    symbols = raw.get("symbols")
    if not isinstance(symbols, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for symbol, entry in symbols.items():
        if isinstance(symbol, str) and symbol and isinstance(entry, dict):
            fetched = entry.get("fetched_at")
            if isinstance(fetched, bool):
                continue
            try:
                moment_check = float(fetched)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if not math.isfinite(moment_check):
                continue
            out[symbol] = dict(entry)
    return out


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise


def store_frames(
    cache: dict[str, dict[str, object]],
    symbol: str,
    *,
    fetched_at: float,
    data_as_of: float,
    last_bar_start: float | None,
    timeframe_s: int,
    last_close: str,
    closes: list[str],
    now: float | None = None,
) -> None:
    closes = [str(value) for value in closes][:CACHE_MAX_CLOSES]
    cache[str(symbol)] = {
        "fetched_at": float(fetched_at),
        "data_as_of": float(data_as_of),
        "last_bar_start": last_bar_start,
        "timeframe_s": int(timeframe_s),
        "last_close": str(last_close),
        "closes": closes,
    }
    prune_cache(cache, now=now)


def prune_cache(cache: dict[str, dict[str, object]], *, now: float | None = None) -> None:
    moment = float(now) if now is not None else time.time()
    stale = [
        symbol for symbol, entry in cache.items()
        if not isinstance(entry.get("fetched_at"), (int, float))
        or isinstance(entry.get("fetched_at"), bool)
        or float(entry["fetched_at"]) < moment - CACHE_MAX_AGE_S
    ]
    for symbol in stale:
        del cache[symbol]
    if len(cache) > CACHE_MAX_SYMBOLS:
        ordered = sorted(
            cache, key=lambda s: float(cache[s].get("fetched_at") or 0.0)
        )
        for symbol in ordered[: len(cache) - CACHE_MAX_SYMBOLS]:
            del cache[symbol]


def save_cache(path: Path, cache: dict[str, dict[str, object]]) -> None:
    _atomic_write_json(path, {"schema_version": 1, "symbols": cache})
