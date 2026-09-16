#!/usr/bin/env python3
"""Fail-closed startup gate for the read-only OANDA practice collector.

The systemd service starts the resident collector first, then this helper waits
for evidence written by *this* service start.  It accepts either:

- a fresh validated OANDA-practice quote snapshot; or
- a freshly written ``closed`` provider-health snapshot when the FX week is
  closed.

Credential values, provider response bodies, URLs, symbols' prices, and errors
are never printed.  Exit status is the only output contract.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import stat
import sys
import time

PROVIDER = "oanda-practice"
MODE = "read_only_pricing_collector"
SOURCE = "oanda-practice-pricing"
ALLOWED_SYMBOLS = frozenset({"USD_JPY", "EUR_JPY"})
MAX_AGE_S = 15.0
MAX_JSON_BYTES = 65536


def _read_json(path: Path):
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if not raw or len(raw) > MAX_JSON_BYTES:
            return None
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _safe_mtime(path: Path):
    try:
        st = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
        return None
    return float(st.st_mtime)


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _fresh_timestamp(value, now: float):
    ts = _finite_number(value)
    if ts is None:
        return False
    age = now - ts
    return 0.0 <= age <= MAX_AGE_S


def _quote_valid(row, now: float):
    if not isinstance(row, dict):
        return False
    if row.get("symbol") not in ALLOWED_SYMBOLS:
        return False
    if row.get("source") != SOURCE or row.get("currency") != "JPY" or row.get("tradeable") is not True:
        return False
    if not _fresh_timestamp(row.get("ts"), now):
        return False
    bid = _positive_decimal(row.get("bid"))
    ask = _positive_decimal(row.get("ask"))
    bid_size = _positive_decimal(row.get("bid_size"))
    ask_size = _positive_decimal(row.get("ask_size"))
    return None not in (bid, ask, bid_size, ask_size) and bid <= ask


def snapshot_ready(output_dir: Path, marker: Path, now: float | None = None) -> bool:
    now = time.time() if now is None else float(now)
    marker_mtime = _safe_mtime(marker)
    if marker_mtime is None:
        return False

    health_path = output_dir / "market-fx-provider-health.json"
    health_mtime = _safe_mtime(health_path)
    if health_mtime is None or health_mtime < marker_mtime:
        return False
    health = _read_json(health_path)
    if not health:
        return False
    if health.get("provider") != PROVIDER or health.get("mode") != MODE:
        return False
    if health.get("live_order_capability") is not False:
        return False

    status = health.get("status")
    if status == "closed":
        return (
            health.get("quote_count") == 0
            and health.get("market_open") is False
            and _fresh_timestamp(health.get("as_of"), now)
        )
    if status != "ok" or health.get("market_open") is not True:
        return False
    quote_count = health.get("quote_count")
    if type(quote_count) is not int or not 1 <= quote_count <= len(ALLOWED_SYMBOLS):
        return False
    if not _fresh_timestamp(health.get("as_of"), now):
        return False

    quote_path = output_dir / "market-fx-quotes.json"
    quote_mtime = _safe_mtime(quote_path)
    if quote_mtime is None or quote_mtime < marker_mtime:
        return False
    payload = _read_json(quote_path)
    if not payload or payload.get("market") != "fx" or payload.get("realtime") is not True:
        return False
    quotes = payload.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != quote_count:
        return False
    symbols = [row.get("symbol") for row in quotes if isinstance(row, dict)]
    if len(symbols) != len(quotes) or len(set(symbols)) != len(symbols):
        return False
    return all(_quote_valid(row, now) for row in quotes)


def wait_ready(output_dir: Path, marker: Path, timeout_s: int) -> bool:
    if type(timeout_s) is not int or not 1 <= timeout_s <= 60:
        raise ValueError("timeout out of bounds")
    deadline = time.monotonic() + timeout_s
    while True:
        if snapshot_ready(output_dir, marker):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    try:
        sys.path.insert(0, str(root / "src"))
        from docich.config import load_global

        g = load_global(root, args.config)
        output_dir = Path(g.state_dir) / "market-data"
        return 0 if wait_ready(output_dir, args.marker, args.timeout) else 1
    except (OSError, ValueError, TypeError, ImportError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
