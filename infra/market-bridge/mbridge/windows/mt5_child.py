"""Isolated MT5 initialize/tick child.  One shot, bounded, sanitized output.

Only a fixed allowlist of MetaTrader5 read functions is used.  The child never
imports an order API, never returns account balances/broker errors, and
publishes ``trade_allowed`` only as an ignored diagnostic boolean.
"""
from __future__ import annotations

import json
import sys
import time

ALLOWED_FUNCTIONS = (
    "initialize", "shutdown", "account_info", "symbol_select", "symbol_info_tick",
)


def _base() -> dict:
    return {
        "status": "upstream_error", "reason": "upstream_error",
        "initialized": False, "server_verified": False, "account_verified": False,
        "symbols_ready": False, "trade_allowed": False, "quotes": [], "source_ts": 0.0,
    }


def run(payload: dict, mt5=None, *, clock=time.time) -> dict:
    result = _base()
    if not isinstance(payload, dict):
        return result
    symbols = [s for s in payload.get("symbols", []) if isinstance(s, str) and len(s) == 6]
    if mt5 is None:
        try:
            import MetaTrader5 as mt5  # type: ignore  # Windows-only, optional
        except (ImportError, ModuleNotFoundError):
            result["status"] = "sdk_unavailable"
            result["reason"] = "sdk_unavailable"
            return result

    # Defensive: fail closed if a future SDK exposes an unexpected surface.
    if any(not hasattr(mt5, name) for name in ALLOWED_FUNCTIONS):
        result["status"] = "sdk_unavailable"
        result["reason"] = "sdk_unavailable"
        return result

    try:
        kwargs = {"path": payload["terminal_path"]} if payload.get("terminal_path") else {}
        initialized = bool(mt5.initialize(**kwargs))
    except Exception:
        result["status"] = "terminal_unavailable"
        result["reason"] = "terminal_disconnected"
        return result
    result["initialized"] = initialized
    if not initialized:
        result["status"] = "terminal_unavailable"
        result["reason"] = "terminal_disconnected"
        return result

    try:
        info = mt5.account_info()
        quotes = []
        newest = 0.0
        now = clock()
        for symbol in symbols:
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None or not getattr(tick, "bid", 0) or not getattr(tick, "ask", 0):
                continue
            ts = float(getattr(tick, "time", 0.0) or 0.0)
            if ts <= 0:
                ts = now - 1.0
            newest = max(newest, ts)
            quotes.append({"symbol": symbol, "bid": float(tick.bid), "ask": float(tick.ask),
                           "bid_size": float(getattr(tick, "bid_volume", 0.0) or 0.0),
                           "ask_size": float(getattr(tick, "ask_volume", 0.0) or 0.0),
                           "source_ts": ts, "received_ts": now, "sequence": 0})
        result["server_verified"] = bool(info is not None and
                                         str(getattr(info, "server", "")) == str(payload.get("expected_server", "")))
        login_ok = False
        if info is not None:
            try:
                login_ok = int(getattr(info, "login", -1)) == int(payload.get("expected_login", -2))
            except (TypeError, ValueError):
                login_ok = False
        result["account_verified"] = login_ok
        # Published but never required by the adapter's readiness gate.
        result["trade_allowed"] = bool(getattr(info, "trade_allowed", False)) if info else False
        result["symbols_ready"] = bool(quotes) and len(quotes) == len(symbols)
        result["quotes"] = quotes
        result["source_ts"] = newest
        result["status"] = "ok" if result["symbols_ready"] else "data_unavailable"
        result["reason"] = "ok" if result["symbols_ready"] else "upstream_unavailable"
    except Exception:
        result["status"] = "upstream_error"
        result["reason"] = "upstream_error"
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass
    return result


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        payload = {}
    print(json.dumps(run(payload), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
