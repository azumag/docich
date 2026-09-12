"""Short-lived read-only live market feed for the PAPER dashboard.

This module is presentation-only. It uses bitbank public ticker/trade APIs,
keeps a small in-memory cache to avoid request storms, and never mutates PAPER
state or exposes any private/order operation.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import importlib
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping

from .dashboard import _focus_symbol, load_snapshot
from .exchanges.bitbank_ccxt import CCXTUnavailableError

LIVE_SCHEMA_VERSION = 1
LIVE_CACHE_TTL_S = 2.0
LIVE_TRADE_LIMIT = 10
LIVE_INACTIVE_AFTER_S = 20.0
LIVE_FALLBACK_SYMBOL = "BTC/JPY"


class LiveMarketError(RuntimeError):
    """Raised when public live market data is unavailable or malformed."""


def _positive_float(value: object) -> float | None:
    try:
        number = float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _epoch_seconds(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    # CCXT timestamps are milliseconds. Accept seconds too for testability.
    return number / 1000.0 if number > 10_000_000_000 else number


def _safe_side(value: object) -> str:
    side = str(value or "").strip().lower()
    return side if side in {"buy", "sell"} else "unknown"


def _activity_signature(payload: Mapping[str, object]) -> tuple[object, ...] | None:
    """Stable signature for actual visible market activity.

    Ticker timestamps are deliberately excluded: some venues refresh the
    timestamp even when price/trades are unchanged, which would make an idle
    market look active forever.
    """
    if payload.get("available") is not True:
        return None
    ticker = payload.get("ticker") if isinstance(payload.get("ticker"), Mapping) else {}
    last = ticker.get("last")
    trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
    newest = trades[0] if trades and isinstance(trades[0], Mapping) else {}
    return (
        str(newest.get("id") or ""),
        newest.get("timestamp"),
        newest.get("price"),
        newest.get("amount"),
        last,
    )


class BitbankLiveReader:
    """Tiny public-only CCXT reader for dashboard ticker and market trades."""

    def __init__(self, *, exchange: Any | None = None, timeout_ms: int = 5000):
        if exchange is not None:
            self._exchange = exchange
            return
        try:
            ccxt = importlib.import_module("ccxt")
        except (ImportError, ModuleNotFoundError) as exc:
            raise CCXTUnavailableError(
                "CCXT is optional. Install requirements-trading.txt to enable live dashboard data."
            ) from exc
        self._exchange = ccxt.bitbank({"enableRateLimit": True, "timeout": int(timeout_ms)})

    def fetch(self, symbol: str, *, limit: int = LIVE_TRADE_LIMIT, now: float | None = None) -> dict[str, object]:
        selected = str(symbol or "").strip()
        if not selected:
            raise LiveMarketError("live symbol is empty")
        fetched_at = time.time() if now is None else float(now)
        ticker = self._exchange.fetch_ticker(selected)
        if not isinstance(ticker, Mapping):
            raise LiveMarketError("ticker response is malformed")
        last = _positive_float(ticker.get("last"))
        bid = _positive_float(ticker.get("bid"))
        ask = _positive_float(ticker.get("ask"))
        if last is None and bid is None and ask is None:
            raise LiveMarketError("ticker has no usable price")

        raw_trades = self._exchange.fetch_trades(selected, limit=max(1, int(limit)))
        if not isinstance(raw_trades, list):
            raw_trades = []
        trades: list[dict[str, object]] = []
        for raw in raw_trades:
            if not isinstance(raw, Mapping):
                continue
            price = _positive_float(raw.get("price"))
            amount = _positive_float(raw.get("amount"))
            timestamp = _epoch_seconds(raw.get("timestamp"))
            if price is None or amount is None or timestamp is None:
                continue
            trades.append(
                {
                    "id": str(raw.get("id") or "")[:80],
                    "timestamp": timestamp,
                    "side": _safe_side(raw.get("side")),
                    "price": price,
                    "amount": amount,
                }
            )
        trades.sort(key=lambda item: float(item["timestamp"]), reverse=True)
        trades = trades[: max(1, int(limit))]
        return {
            "schema_version": LIVE_SCHEMA_VERSION,
            "available": True,
            "symbol": selected,
            "fetched_at": fetched_at,
            "ticker": {
                "last": last,
                "bid": bid,
                "ask": ask,
                "as_of": _epoch_seconds(ticker.get("timestamp")),
            },
            "trades": trades,
        }


class LiveMarketSampler:
    """Resolve the strategy focus and fail over display-only idle charts.

    The preferred focus is always monitored. If neither its latest trade nor
    last price changes for a bounded period, the presentation temporarily
    switches to BTC/JPY, the consistently active market heartbeat. As soon as
    preferred-market activity resumes, the next poll returns to it. Trading
    decisions never read this state.
    """

    def __init__(
        self,
        trading_dir: Path,
        *,
        reader_factory: Callable[[], BitbankLiveReader] = BitbankLiveReader,
        ttl_s: float = LIVE_CACHE_TTL_S,
        inactive_after_s: float = LIVE_INACTIVE_AFTER_S,
        fallback_symbol: str = LIVE_FALLBACK_SYMBOL,
        now_fn: Callable[[], float] = time.time,
    ):
        self.trading_dir = Path(trading_dir)
        self.reader_factory = reader_factory
        self.ttl_s = max(0.5, min(10.0, float(ttl_s)))
        self.inactive_after_s = max(5.0, min(300.0, float(inactive_after_s)))
        self.fallback_symbol = str(fallback_symbol or LIVE_FALLBACK_SYMBOL).strip() or LIVE_FALLBACK_SYMBOL
        self.now_fn = now_fn
        self._reader: BitbankLiveReader | None = None
        self._cached: dict[str, dict[str, object]] = {}
        self._preferred_symbol: str | None = None
        self._preferred_signature: tuple[object, ...] | None = None
        self._preferred_activity_at: float | None = None
        self._lock = threading.Lock()

    def _symbol(self) -> str | None:
        snapshot, closes = load_snapshot(self.trading_dir)
        return _focus_symbol(snapshot, closes)

    def _fetch(self, symbol: str, moment: float) -> dict[str, object]:
        cached = self._cached.get(symbol)
        if (
            isinstance(cached, dict)
            and moment - float(cached.get("fetched_at") or 0) < self.ttl_s
        ):
            return dict(cached)
        try:
            if self._reader is None:
                self._reader = self.reader_factory()
            fresh = self._reader.fetch(symbol, limit=LIVE_TRADE_LIMIT, now=moment)
            self._cached[symbol] = dict(fresh)
            return dict(fresh)
        except Exception:
            if isinstance(cached, dict):
                stale = dict(cached)
                stale["stale"] = True
                stale["error"] = "live refresh unavailable"
                return stale
            return {
                "schema_version": LIVE_SCHEMA_VERSION,
                "available": False,
                "symbol": symbol,
                "fetched_at": moment,
                "error": "live refresh unavailable",
            }

    @staticmethod
    def _decorate(
        payload: Mapping[str, object],
        *,
        preferred_symbol: str,
        fallback: bool,
        inactive_sec: int,
    ) -> dict[str, object]:
        result = dict(payload)
        result["preferred_symbol"] = preferred_symbol
        result["display_fallback"] = bool(fallback)
        result["display_reason"] = "focus_inactive" if fallback else "preferred_active"
        result["focus_inactive_sec"] = max(0, int(inactive_sec))
        return result

    def snapshot(self, *, now: float | None = None) -> dict[str, object]:
        moment = float(self.now_fn() if now is None else now)
        preferred = self._symbol()
        if not preferred:
            return {
                "schema_version": LIVE_SCHEMA_VERSION,
                "available": False,
                "symbol": None,
                "preferred_symbol": None,
                "display_fallback": False,
                "fetched_at": moment,
                "error": "no focus market",
            }
        with self._lock:
            if preferred != self._preferred_symbol:
                self._preferred_symbol = preferred
                self._preferred_signature = None
                self._preferred_activity_at = moment

            primary = self._fetch(preferred, moment)
            signature = _activity_signature(primary)
            if signature is not None:
                if self._preferred_signature is None or signature != self._preferred_signature:
                    self._preferred_signature = signature
                    self._preferred_activity_at = moment
            if self._preferred_activity_at is None:
                self._preferred_activity_at = moment
            inactive = max(0.0, moment - self._preferred_activity_at)

            if preferred != self.fallback_symbol and inactive >= self.inactive_after_s:
                fallback = self._fetch(self.fallback_symbol, moment)
                if fallback.get("available") is True:
                    return self._decorate(
                        fallback,
                        preferred_symbol=preferred,
                        fallback=True,
                        inactive_sec=int(inactive),
                    )

            return self._decorate(
                primary,
                preferred_symbol=preferred,
                fallback=False,
                inactive_sec=int(inactive),
            )
