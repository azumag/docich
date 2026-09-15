"""Sanitized, read-only market-data provider probes.

The Moomoo probe is deliberately quote-only: it creates OpenQuoteContext and
never creates/imports a trading context, places orders, unlocks trading, or
reads account state. Output is a fixed allowlist of booleans/counters/timing
buckets; prices, names, raw provider errors and credentials are never returned.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import math
import re
import time
from types import ModuleType
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
JP_SYMBOL = re.compile(r"JP\.[0-9A-Z]{4}\Z")
OPEN_STATES = {"MORNING", "AFTERNOON"}


def _base() -> dict:
    return {
        "provider": "moomoo",
        "mode": "read_only_quote_probe",
        "sdk_available": False,
        "opend_reachable": False,
        "market_state_ok": False,
        "market_state_known": False,
        "market_open": False,
        "snapshot_ok": False,
        "snapshot_symbol_count": 0,
        "quote_subscribe_ok": False,
        "quote_ok": False,
        "quote_symbol_count": 0,
        "quote_timestamp_known": False,
        "fresh_quote_count": 0,
        "quote_age_bucket": "unknown",
        "snapshot_roundtrip_bucket": "unknown",
        "quote_roundtrip_bucket": "unknown",
        "jp_quote_entitled": False,
        "realtime_ready": False,
        "status": "sdk_unavailable",
    }


def _bucket_ms(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return "unknown"
    ms = seconds * 1000
    if ms <= 100:
        return "le_100ms"
    if ms <= 500:
        return "le_500ms"
    if ms <= 2000:
        return "le_2s"
    if ms <= 5000:
        return "le_5s"
    return "gt_5s"


def _bucket_age(age: float | None) -> str:
    if age is None or not math.isfinite(age) or age < 0:
        return "unknown"
    if age <= 2:
        return "le_2s"
    if age <= 5:
        return "le_5s"
    if age <= 15:
        return "le_15s"
    if age <= 60:
        return "le_60s"
    return "gt_60s"


def _load_sdk() -> ModuleType:
    # The current Moomoo API Python package installs the `moomoo` module.
    # Keep this optional: normal docich runtime must not depend on it until the
    # production entitlement/latency probe has succeeded.
    return importlib.import_module("moomoo")


def _records(data) -> list[dict]:
    """Convert a provider DataFrame-like result without exposing repr/errors."""
    try:
        rows = data.to_dict("records")
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows[:400] if isinstance(row, dict)]


def _ret_ok(module: ModuleType, value) -> bool:
    try:
        return value == module.RET_OK
    except Exception:
        return False


def _state_name(value) -> str:
    # Enum/string representations vary across SDK releases. Only compare a
    # short uppercase suffix; never publish the raw representation.
    try:
        text = str(value).strip().upper()
    except Exception:
        return ""
    for name in OPEN_STATES:
        if text == name or text.endswith("." + name):
            return name
    return "KNOWN" if text and len(text) <= 64 else ""


def _jp_timestamp(row: dict) -> float | None:
    date = row.get("data_date")
    clock = row.get("data_time")
    if not isinstance(date, str) or not isinstance(clock, str):
        combined = row.get("update_time")
        if not isinstance(combined, str):
            return None
        text = combined.strip()
    else:
        text = f"{date.strip()} {clock.strip()}"
    try:
        parsed = dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _validate_args(host: str, port: int, symbols: list[str]) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Moomoo OpenD probe is loopback-only")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid OpenD port")
    if not symbols or len(symbols) > 20 or len(set(symbols)) != len(symbols):
        raise ValueError("configure 1..20 unique JP symbols")
    if any(not isinstance(symbol, str) or not JP_SYMBOL.fullmatch(symbol) for symbol in symbols):
        raise ValueError("probe accepts JP.<4-char-symbol> only")


def probe_moomoo(symbols: list[str], *, host: str = "127.0.0.1", port: int = 11111,
                 now: float | None = None, sdk: ModuleType | None = None,
                 monotonic=time.monotonic) -> dict:
    """Probe JP quote entitlement through local OpenD, without trade authority."""
    _validate_args(host, port, symbols)
    result = _base()
    now = time.time() if now is None else float(now)
    if not math.isfinite(now) or now <= 0:
        raise ValueError("invalid probe clock")
    try:
        module = sdk or _load_sdk()
    except (ImportError, ModuleNotFoundError):
        return result
    result["sdk_available"] = True

    ctx = None
    quote_rows: list[dict] = []
    try:
        ctx = module.OpenQuoteContext(host=host, port=port)
    except Exception:
        result["status"] = "opend_unreachable"
        return result

    def call(method: str, *args, **kwargs):
        started = monotonic()
        try:
            value = getattr(ctx, method)(*args, **kwargs)
        except Exception:
            return None, monotonic() - started
        result["opend_reachable"] = True
        return value, monotonic() - started

    try:
        response, _ = call("get_market_state", symbols)
        if isinstance(response, tuple) and len(response) == 2:
            ret, data = response
            if _ret_ok(module, ret):
                market_rows = _records(data)
                covered = {str(row.get("code")) for row in market_rows}
                result["market_state_ok"] = any(symbol in covered for symbol in symbols)
                names = [
                    _state_name(row.get("market_state"))
                    for row in market_rows if str(row.get("code")) in symbols
                ]
                result["market_state_known"] = result["market_state_ok"] and bool(names) and all(names)
                result["market_open"] = any(name in OPEN_STATES for name in names)

        response, elapsed = call("get_market_snapshot", symbols)
        result["snapshot_roundtrip_bucket"] = _bucket_ms(elapsed)
        if isinstance(response, tuple) and len(response) == 2:
            ret, data = response
            if _ret_ok(module, ret):
                snapshot_rows = _records(data)
                covered = {str(row.get("code")) for row in snapshot_rows}
                result["snapshot_symbol_count"] = sum(symbol in covered for symbol in symbols)
                result["snapshot_ok"] = result["snapshot_symbol_count"] > 0

        subtype = None
        try:
            subtype = module.SubType.QUOTE
        except Exception:
            pass
        if subtype is not None:
            response, _ = call("subscribe", symbols, [subtype], subscribe_push=False)
            if isinstance(response, tuple) and len(response) == 2:
                result["quote_subscribe_ok"] = _ret_ok(module, response[0])

        if result["quote_subscribe_ok"]:
            response, elapsed = call("get_stock_quote", symbols)
            result["quote_roundtrip_bucket"] = _bucket_ms(elapsed)
            if isinstance(response, tuple) and len(response) == 2:
                ret, data = response
                if _ret_ok(module, ret):
                    quote_rows = _records(data)
                    covered = {str(row.get("code")) for row in quote_rows}
                    result["quote_symbol_count"] = sum(symbol in covered for symbol in symbols)
                    result["quote_ok"] = result["quote_symbol_count"] > 0

        ages = []
        for row in quote_rows:
            if str(row.get("code")) not in symbols:
                continue
            ts = _jp_timestamp(row)
            if ts is None:
                continue
            age = now - ts
            if math.isfinite(age) and age >= 0:
                ages.append(age)
        result["quote_timestamp_known"] = bool(ages)
        result["fresh_quote_count"] = sum(age <= 15 for age in ages)
        result["quote_age_bucket"] = _bucket_age(max(ages) if ages else None)
        result["jp_quote_entitled"] = result["quote_subscribe_ok"] and result["quote_ok"]
        result["realtime_ready"] = (
            result["jp_quote_entitled"]
            and result["market_open"]
            and result["fresh_quote_count"] == result["quote_symbol_count"]
            and result["quote_symbol_count"] > 0
        )
        if result["realtime_ready"]:
            result["status"] = "realtime_ready"
        elif result["jp_quote_entitled"]:
            result["status"] = "entitled_not_realtime_ready"
        elif result["opend_reachable"]:
            result["status"] = "jp_quote_unavailable"
        else:
            result["status"] = "opend_unreachable"
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="read-only stock market-data provider probe")
    parser.add_argument("--provider", choices=("moomoo",), default="moomoo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--symbols", default="JP.7203,JP.7974")
    args = parser.parse_args(argv)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        result = probe_moomoo(symbols, host=args.host, port=args.port)
    except ValueError:
        result = _base()
        result["status"] = "invalid_probe_request"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
