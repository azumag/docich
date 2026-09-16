"""Read-only Moomoo/OpenD collector for the Japanese-stock PAPER corner.

The trading runtime intentionally stays provider-neutral and continues to read the
existing file contracts under ``state_dir/market-data``.  This module is the
provider boundary: it may read quotes/scanner data from a loopback OpenD, but it
has no account, trade-context, unlock, order, or broker-write capability.

A successful cycle atomically publishes:
- market-stocks-candidates.json -- broad dynamic selector observations
- market-stocks-quotes.json     -- executable bid/ask observations for that pool

If OpenD, entitlement, freshness, market state, or schema validation fails, the
collector does *not* refresh those files. Existing files then age out and the
PAPER runtime fails closed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import math
from pathlib import Path
import re
import time
from types import ModuleType

from .core import JST, decimal, stamp
from .lab import write_json


JP_CODE = re.compile(r"JP\.([0-9A-Z]{4})\Z")
OPEN_STATES = {"MORNING", "AFTERNOON"}
SOURCE = "moomoo-opend-readonly"
MAX_AGE_S = 15
# Three screener requests every >=10s stay within Moomoo's documented
# get_stock_filter limit of 10 requests / 30 seconds.
MIN_INTERVAL_S = 10
DEFAULT_PER_SCAN = 20
MAX_PER_SCAN = 30


class MoomooMarketDataUnavailable(RuntimeError):
    pass


def _load_sdk() -> ModuleType:
    try:
        return importlib.import_module("moomoo")
    except (ImportError, ModuleNotFoundError):
        raise MoomooMarketDataUnavailable("Moomoo quote SDK unavailable") from None


def _validate_endpoint(host: str, port: int) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Moomoo OpenD collector is loopback-only")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid OpenD port")


def _ret_ok(sdk: ModuleType, value) -> bool:
    try:
        return value == sdk.RET_OK
    except Exception:
        return False


def _records(data) -> list[dict]:
    try:
        rows = data.to_dict("records")
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows[:400] if isinstance(row, dict)]


def _symbol(value: object) -> str | None:
    try:
        text = str(value).strip().upper()
    except Exception:
        return None
    match = JP_CODE.fullmatch(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[0-9A-Z]{4}", text):
        return text
    return None


def _state_name(value: object) -> str:
    try:
        text = str(value).strip().upper()
    except Exception:
        return ""
    for name in OPEN_STATES:
        if text == name or text.endswith("." + name):
            return name
    return ""


def _number(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean market value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite market value")
    return result


def _timestamp(row: dict) -> float:
    value = row.get("update_timestamp")
    try:
        if not isinstance(value, bool):
            parsed = float(value)
            if math.isfinite(parsed) and parsed > 0:
                return stamp(parsed)
    except (TypeError, ValueError, OverflowError):
        pass
    # Moomoo documents update_time as the market-local quote update time.
    # This collector only accepts JP.* securities, so interpret the fallback
    # explicitly as JST rather than using the VM timezone.
    text = row.get("update_time")
    if not isinstance(text, str):
        raise ValueError("missing snapshot timestamp")
    parsed = dt.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    return stamp(parsed.timestamp())


def _spread_bps(bid: float, ask: float) -> float:
    if not 0 < bid <= ask:
        raise ValueError("crossed/empty stock quote")
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10000


def _metric(item, filter_obj) -> float:
    try:
        return _number(item[filter_obj])
    except Exception:
        raise MoomooMarketDataUnavailable("invalid screener metric") from None


def _filters(sdk: ModuleType, sort_field: str, sort_direction):
    fields = {
        "momentum": sdk.StockField.CHANGE_RATE_5MIN,
        "volume": sdk.StockField.VOLUME_RATIO,
        "turnover": sdk.StockField.TURNOVER,
        "volatility": sdk.StockField.AMPLITUDE,
    }
    result, by_name = [], {}
    for name, field in fields.items():
        item = sdk.SimpleFilter()
        item.stock_field = field
        # Request the metric without excluding values; only the named scan key
        # controls ordering. Hard liquidity/spread gates remain in SelectorPolicy.
        item.is_no_filter = True
        item.sort = sdk.SortDir.NONE
        result.append(item)
        by_name[name] = item
    by_name[sort_field].sort = sort_direction
    return result, by_name


def _scan(ctx, sdk: ModuleType, *, sort_field: str, direction, count: int) -> dict[str, dict]:
    filters, by_name = _filters(sdk, sort_field, direction)
    try:
        response = ctx.get_stock_filter(
            market=sdk.Market.JP,
            filter_list=filters,
            begin=0,
            num=count,
        )
    except Exception:
        raise MoomooMarketDataUnavailable("Moomoo stock screener unavailable") from None
    if not isinstance(response, tuple) or len(response) != 2 or not _ret_ok(sdk, response[0]):
        raise MoomooMarketDataUnavailable("Moomoo stock screener unavailable")
    page = response[1]
    if not isinstance(page, tuple) or len(page) != 3 or not isinstance(page[2], list):
        raise MoomooMarketDataUnavailable("invalid Moomoo screener response")
    result: dict[str, dict] = {}
    for item in page[2][:count]:
        symbol = _symbol(getattr(item, "stock_code", None))
        if not symbol:
            continue
        result[symbol] = {
            "momentum_pct": _metric(item, by_name["momentum"]),
            "volume_ratio": _metric(item, by_name["volume"]),
            "turnover": _metric(item, by_name["turnover"]),
            "amplitude_pct": _metric(item, by_name["volatility"]),
        }
    return result


def _broad_scan(ctx, sdk: ModuleType, count: int) -> dict[str, dict]:
    # Three complementary lists catch sudden gainers, sudden decliners and
    # abnormal volume. 3 calls / 10s = 9 / 30s, below the documented 10 / 30s
    # stock-filter limit. Turnover and amplitude are returned on every scan and
    # feed the deterministic selector score, so a fourth request is unnecessary.
    scans = (
        ("momentum", sdk.SortDir.DESCEND),
        ("momentum", sdk.SortDir.ASCEND),
        ("volume", sdk.SortDir.DESCEND),
    )
    merged: dict[str, dict] = {}
    for field, direction in scans:
        merged.update(_scan(ctx, sdk, sort_field=field, direction=direction, count=count))
    return merged


def _snapshot(ctx, sdk: ModuleType, codes: list[str]) -> dict[str, dict]:
    try:
        response = ctx.get_market_snapshot(codes)
    except Exception:
        raise MoomooMarketDataUnavailable("Moomoo snapshot unavailable") from None
    if not isinstance(response, tuple) or len(response) != 2 or not _ret_ok(sdk, response[0]):
        raise MoomooMarketDataUnavailable("Moomoo snapshot unavailable")
    result = {}
    for row in _records(response[1]):
        symbol = _symbol(row.get("code"))
        if symbol:
            result[symbol] = row
    return result


def _market_states(ctx, sdk: ModuleType, codes: list[str]) -> dict[str, str]:
    try:
        response = ctx.get_market_state(codes)
    except Exception:
        raise MoomooMarketDataUnavailable("Moomoo market state unavailable") from None
    if not isinstance(response, tuple) or len(response) != 2 or not _ret_ok(sdk, response[0]):
        raise MoomooMarketDataUnavailable("Moomoo market state unavailable")
    result = {}
    for row in _records(response[1]):
        symbol = _symbol(row.get("code"))
        state = _state_name(row.get("market_state"))
        if symbol and state:
            result[symbol] = state
    return result


def _rows(metrics: dict[str, dict], snapshots: dict[str, dict], states: dict[str, str], now: float):
    candidates, quotes = [], []
    for symbol in sorted(metrics):
        row = snapshots.get(symbol)
        if not isinstance(row, dict) or states.get(symbol) not in OPEN_STATES:
            continue
        try:
            ts = _timestamp(row)
            age = stamp(now) - ts
            if not 0 <= age <= MAX_AGE_S:
                continue
            # Missing suspension data fails closed. numpy/pandas booleans are
            # safely normalized by bool(); a missing value defaults to blocked.
            if "suspension" not in row or bool(row.get("suspension")):
                continue
            last = _number(row.get("last_price"))
            bid = _number(row.get("bid_price"))
            ask = _number(row.get("ask_price"))
            bid_vol = _number(row.get("bid_vol"))
            ask_vol = _number(row.get("ask_vol"))
            turnover = _number(row.get("turnover"))
            spread = _spread_bps(bid, ask)
            if last <= 0 or bid_vol <= 0 or ask_vol <= 0 or turnover < 0 or spread > 1000:
                continue
            metric = metrics[symbol]
            momentum_bps = max(-5000.0, min(5000.0, _number(metric["momentum_pct"]) * 100))
            volume_accel = max(0.0, min(100.0, _number(metric["volume_ratio"])))
            volatility_bps = max(0.0, min(5000.0, _number(metric["amplitude_pct"]) * 100))
            # Decimal conversion is an additional finite/schema guard and gives
            # canonical strings accepted by Candidate/Quote without float NaN.
            candidate = {
                "symbol": symbol,
                "ts": ts,
                "price": str(decimal(last)),
                "turnover_jpy": str(decimal(turnover)),
                "momentum_bps": str(decimal(momentum_bps)),
                "volume_accel": str(decimal(volume_accel)),
                "volatility_bps": str(decimal(volatility_bps)),
                "spread_bps": str(decimal(spread)),
                "tradeable": True,
            }
            quote = {
                "symbol": symbol,
                "ts": ts,
                "bid": str(decimal(bid)),
                "ask": str(decimal(ask)),
                "bid_size": str(decimal(bid_vol)),
                "ask_size": str(decimal(ask_vol)),
                "tradeable": True,
                "source": SOURCE,
                "currency": "JPY",
            }
        except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
            continue
        candidates.append(candidate)
        quotes.append(quote)
    return candidates, quotes


def collect_once(output_dir: Path, *, now: float | None = None, host: str = "127.0.0.1",
                 port: int = 11111, per_scan: int = DEFAULT_PER_SCAN,
                 sdk: ModuleType | None = None, context_factory=None) -> dict:
    """Collect one bounded read-only JP market-data snapshot.

    The returned/persisted health object intentionally contains no prices,
    symbols, names, provider errors, account data or credentials.
    """
    _validate_endpoint(host, port)
    if type(per_scan) is not int or not 1 <= per_scan <= MAX_PER_SCAN:
        raise ValueError("invalid Moomoo per-scan count")
    now = time.time() if now is None else stamp(now)
    module = sdk or _load_sdk()
    factory = context_factory or module.OpenQuoteContext
    ctx = None
    try:
        try:
            ctx = factory(host=host, port=port)
        except Exception:
            raise MoomooMarketDataUnavailable("OpenD unavailable") from None
        metrics = _broad_scan(ctx, module, per_scan)
        if not metrics:
            raise MoomooMarketDataUnavailable("no stock screener candidates")
        codes = [f"JP.{symbol}" for symbol in sorted(metrics)[: 3 * per_scan]]
        snapshots = _snapshot(ctx, module, codes)
        states = _market_states(ctx, module, codes)
        candidates, quotes = _rows(metrics, snapshots, states, now)
        if not candidates or not quotes:
            raise MoomooMarketDataUnavailable("no fresh tradeable JP quotes")
        as_of = max(float(row["ts"]) for row in candidates)
        # Publish quotes first. If the process stops between the two atomic
        # replaces, a new selector snapshot cannot point at a not-yet-published
        # quote set. Any old pair naturally fails freshness checks.
        write_json(output_dir / "market-stocks-quotes.json", {
            "market": "stocks",
            "realtime": True,
            "quotes": quotes,
        })
        write_json(output_dir / "market-stocks-candidates.json", {
            "market": "stocks",
            "realtime": True,
            "as_of": as_of,
            "candidates": candidates,
        })
        health = {
            "provider": "moomoo",
            "mode": "read_only_quote_collector",
            "status": "ok",
            "candidate_count": len(candidates),
            "quote_count": len(quotes),
            "market_open": True,
            "as_of": as_of,
            "live_order_capability": False,
        }
        write_json(output_dir / "market-stocks-provider-health.json", health)
        return health
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _collection_window(now: float) -> bool:
    local = dt.datetime.fromtimestamp(stamp(now), JST)
    # One minute of pre-open observation lets the selector warm up without
    # placing entries before the 09:00 show/trading window.
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and 8 * 60 + 59 <= minute < 10 * 60


def _health_failure(output_dir: Path, now: float, exc: Exception) -> None:
    write_json(output_dir / "market-stocks-provider-health.json", {
        "provider": "moomoo",
        "mode": "read_only_quote_collector",
        "status": "unavailable",
        "error": type(exc).__name__,
        "candidate_count": 0,
        "quote_count": 0,
        "market_open": False,
        "as_of": stamp(now),
        "live_order_capability": False,
    })


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="read-only Moomoo JP market-data collector")
    parser.add_argument("command", choices=("once", "worker"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--interval", type=int, default=MIN_INTERVAL_S)
    parser.add_argument("--per-scan", type=int, default=DEFAULT_PER_SCAN)
    args = parser.parse_args(argv)
    _validate_endpoint(args.host, args.port)
    if type(args.interval) is not int or args.interval < MIN_INTERVAL_S:
        parser.error(f"--interval must be >= {MIN_INTERVAL_S}")

    from ...config import load_global

    root = Path(__file__).resolve().parents[4]
    g = load_global(root, args.config or root / "config" / "docich.soren-live.toml")
    output_dir = g.state_dir / "market-data"

    if args.command == "once":
        now = time.time()
        try:
            collect_once(output_dir, now=now, host=args.host, port=args.port, per_scan=args.per_scan)
            return 0
        except (MoomooMarketDataUnavailable, ValueError, OSError) as exc:
            _health_failure(output_dir, now, exc)
            return 1

    while True:
        now = time.time()
        if _collection_window(now):
            try:
                collect_once(output_dir, now=now, host=args.host, port=args.port, per_scan=args.per_scan)
            except (MoomooMarketDataUnavailable, ValueError, OSError) as exc:
                _health_failure(output_dir, now, exc)
            time.sleep(args.interval)
        else:
            time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
