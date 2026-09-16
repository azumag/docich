"""Read-only OANDA practice pricing collector for the FX PAPER corner.

The PAPER runtime continues to consume the provider-neutral file contract.  This
module is the credential/provider boundary: it only reuses the existing OANDA
practice ``GET /pricing`` adapter and atomically publishes validated JPY-cross
quotes.  It has no order/trade/account mutation surface.

Credentials are read by ``feeds.read_quotes`` from the process environment:
``DOCICH_OANDA_ACCOUNT_ID`` and ``DOCICH_OANDA_TOKEN``.  They are never written
to provider health, quote files, logs, or command-line arguments.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import re
import time

from .core import Quote, decimal, fx_week_open, stamp
from .feeds import FeedUnavailable, read_quotes
from .lab import write_json

SOURCE = "oanda-practice-pricing"
DEFAULT_SYMBOLS = ("USD_JPY", "EUR_JPY")
MAX_SYMBOLS = 10
MAX_AGE_S = 15
MIN_INTERVAL_S = 5


class OandaMarketDataUnavailable(RuntimeError):
    pass


def _validate_symbols(symbols) -> list[str]:
    if not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= MAX_SYMBOLS:
        raise ValueError("configure 1..10 JPY crosses")
    result = []
    for symbol in symbols:
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z]{3}_JPY", symbol):
            raise ValueError("only JPY-quoted FX crosses are supported")
        if symbol not in result:
            result.append(symbol)
    if not result:
        raise ValueError("empty FX symbol set")
    return result


def _validated_quote(row: Quote, now: float, allowed: set[str]) -> Quote:
    if not isinstance(row, Quote) or row.symbol not in allowed:
        raise ValueError("unexpected OANDA quote")
    if row.source != SOURCE or row.currency != "JPY" or row.tradeable is not True:
        raise ValueError("untradeable or unexpected OANDA quote")
    age = stamp(now) - stamp(row.ts)
    bid, ask = decimal(row.bid), decimal(row.ask)
    if not 0 <= age <= MAX_AGE_S or not 0 < bid <= ask:
        raise ValueError("stale/future/crossed OANDA quote")
    if min(decimal(row.bid_size), decimal(row.ask_size)) <= 0:
        raise ValueError("missing OANDA executable liquidity")
    return row


def collect_once(output_dir: Path, *, symbols=DEFAULT_SYMBOLS, now: float | None = None,
                 reader=read_quotes) -> dict:
    """Fetch one bounded OANDA-practice pricing snapshot and publish it.

    A failed/open-market partial collection never refreshes the quote file.  The
    previous file therefore ages out and the normal PAPER freshness gate stops
    entries instead of silently trading stale data.
    """
    now = time.time() if now is None else stamp(now)
    symbols = _validate_symbols(symbols)
    if not fx_week_open(now):
        raise OandaMarketDataUnavailable("FX market closed")
    try:
        rows = reader({"feed": "oanda", "symbols": symbols}, "fx", output_dir, now)
    except (FeedUnavailable, OSError, ValueError, KeyError, TypeError, IndexError):
        raise OandaMarketDataUnavailable("OANDA practice pricing unavailable") from None
    allowed = set(symbols)
    valid = []
    for row in rows:
        try:
            valid.append(_validated_quote(row, now, allowed))
        except (ValueError, TypeError, ArithmeticError, OverflowError):
            continue
    if not valid:
        raise OandaMarketDataUnavailable("no fresh tradeable OANDA practice quotes")

    as_of = max(stamp(row.ts) for row in valid)
    payload = {
        "market": "fx",
        "realtime": True,
        "quotes": [asdict(row) for row in valid],
    }
    write_json(output_dir / "market-fx-quotes.json", payload)
    health = {
        "provider": "oanda-practice",
        "mode": "read_only_pricing_collector",
        "status": "ok",
        "quote_count": len(valid),
        "market_open": True,
        "as_of": as_of,
        "live_order_capability": False,
    }
    write_json(output_dir / "market-fx-provider-health.json", health)
    return health


def _health(output_dir: Path, now: float, status: str, exc: Exception | None = None) -> None:
    body = {
        "provider": "oanda-practice",
        "mode": "read_only_pricing_collector",
        "status": status,
        "quote_count": 0,
        "market_open": status != "closed",
        "as_of": stamp(now),
        "live_order_capability": False,
    }
    if exc is not None:
        # Type only: never persist token/account/request URL/provider body.
        body["error"] = type(exc).__name__
    write_json(output_dir / "market-fx-provider-health.json", body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="read-only OANDA practice FX pricing collector")
    parser.add_argument("command", choices=("once", "worker"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--interval", type=int, default=MIN_INTERVAL_S)
    args = parser.parse_args(argv)
    symbols = _validate_symbols([part.strip().upper() for part in args.symbols.split(",") if part.strip()])
    if type(args.interval) is not int or args.interval < MIN_INTERVAL_S:
        parser.error(f"--interval must be >= {MIN_INTERVAL_S}")

    from ...config import load_global

    root = Path(__file__).resolve().parents[4]
    g = load_global(root, args.config or root / "config" / "docich.soren-live.toml")
    output_dir = g.state_dir / "market-data"

    if args.command == "once":
        now = time.time()
        if not fx_week_open(now):
            _health(output_dir, now, "closed")
            return 0
        try:
            collect_once(output_dir, symbols=symbols, now=now)
            return 0
        except (OandaMarketDataUnavailable, ValueError, OSError) as exc:
            _health(output_dir, now, "unavailable", exc)
            return 1

    last_closed_health = 0.0
    while True:
        now = time.time()
        if not fx_week_open(now):
            if now - last_closed_health >= 300:
                _health(output_dir, now, "closed")
                last_closed_health = now
            time.sleep(60)
            continue
        try:
            collect_once(output_dir, symbols=symbols, now=now)
        except (OandaMarketDataUnavailable, ValueError, OSError) as exc:
            _health(output_dir, now, "unavailable", exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
