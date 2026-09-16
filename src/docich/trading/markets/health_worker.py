"""Resident PAPER worker wrapper that preserves last accepted market-data time.

`PaperBook.process()` deliberately reports accepted/rejected quotes for the current
5-second tick.  Once a file/provider quote becomes stale, the current tick has
`market_as_of=0`; overwriting health.json with that value loses the timestamp of
the last actually accepted quote and diagnostics can only report `-1`.

This wrapper keeps those two facts separate:
- accepted_quotes/rejected_quotes remain the current tick's truth;
- market_as_of remains the most recent *accepted* quote timestamp.

No order/broker path is introduced here.  Runtime remains PAPER-only.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import time

from .__main__ import Runtime
from .lab import write_json


def _positive_timestamp(value) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def preserve_last_market_as_of(result: dict, previous: float) -> tuple[dict, float]:
    """Return health payload plus the most recent accepted quote timestamp.

    Disabled workers intentionally keep the historical behaviour of not creating
    fresh health state merely because a unit is running.
    """
    if not isinstance(result, dict):
        raise TypeError("worker result must be a dict")
    previous = _positive_timestamp(previous)
    if result.get("status") == "disabled":
        return result, previous
    current = _positive_timestamp(result.get("market_as_of"))
    if current:
        return result, max(previous, current)
    if previous:
        result = dict(result)
        result["market_as_of"] = previous
    return result, previous


def _read_previous(path: Path) -> float:
    try:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 0.0
    return _positive_timestamp(raw.get("market_as_of")) if isinstance(raw, dict) else 0.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="docich resident PAPER market worker")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--markets-config", type=Path)
    parser.add_argument("--market", required=True, choices=("stocks", "fx"))
    parser.add_argument("command", choices=("worker",))
    args = parser.parse_args(argv)

    from ...config import load_global

    root = Path(__file__).resolve().parents[4]
    g = load_global(root, args.config)
    runtime = Runtime(g, args.market, args.markets_config or root / "config/market-paper.toml")
    health_path = runtime.root / "health.json"
    last_market_as_of = _read_previous(health_path)
    try:
        while True:
            try:
                result = runtime.tick()
            except (OSError, ValueError, RuntimeError) as exc:
                result = {
                    "status": "failed",
                    "error": type(exc).__name__,
                    "as_of": time.time(),
                    "mode": "paper",
                }
            result, last_market_as_of = preserve_last_market_as_of(result, last_market_as_of)
            if result.get("status") != "disabled":
                # Runtime.tick already wrote health for the normal enabled path;
                # rewrite atomically only to preserve the last accepted timestamp
                # (or to mirror the original worker's bounded failure health).
                write_json(health_path, result)
            time.sleep(5)
    finally:
        runtime.book.close()


if __name__ == "__main__":
    raise SystemExit(main())
