"""Optional, read-only CCXT adapter for bitbank public market discovery."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import importlib
import time
from typing import Any, Mapping

from ..arbitrage import ArbitrageDataError, TopOfBook
from ..depth import DepthBook, DepthLevel
from ..market_data import MarketFrame, MarketFrameError, frame_from_ohlcv
from ..models import MarketInfo, TradingValidationError
from ..settlement import CircuitBreakStatus


class CCXTUnavailableError(RuntimeError):
    """Raised when the optional trading dependency is not installed."""


def _nonnegative_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def _positive_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return result


def _amount_step(raw: Any) -> Decimal | None:
    # CCXT bitbank uses TICK_SIZE precision mode: precision.amount is the
    # actual quantity increment (1 means whole units, not one decimal place).
    return _positive_decimal(raw)


def _nested(mapping: Any, *keys: str) -> Any:
    current = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _flag_is_false(value: Any) -> bool:
    if value is False or value == 0:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "disabled", "off", "stop", "stopped"}
    return False


def _bitbank_info_allows_orders(info: Any) -> bool:
    if not isinstance(info, Mapping):
        return True
    for key in ("is_enabled", "isEnabled", "enable_order", "enableOrder", "order_enabled", "orderEnabled"):
        if key in info and _flag_is_false(info.get(key)):
            return False
    # CCXT currently maps bitbank's is_enabled to active, but an enabled pair
    # can still be under an exchange-side order stop.  Trading diagnostics and
    # eventual exits need both directions, so either side being stopped fails closed.
    for key in (
        "stop_order", "stopOrder", "stop_order_and_cancel", "stopOrderAndCancel",
        "stop_buy_order", "stopBuyOrder", "stop_sell_order", "stopSellOrder",
    ):
        if info.get(key) is True or info.get(key) == 1:
            return False
    return True


class BitbankPublicGateway:
    """Expose only public market discovery; no private/order methods exist."""

    def __init__(self, *, exchange: Any | None = None, timeout_ms: int = 10000):
        if exchange is not None:
            self._exchange = exchange
            return
        try:
            ccxt = importlib.import_module("ccxt")
        except (ImportError, ModuleNotFoundError) as exc:
            raise CCXTUnavailableError(
                "CCXT is optional. Install requirements-trading.txt to enable bitbank discovery."
            ) from exc
        # Per-request transport timeout (Issue #198 P0-2: 10s initial value).
        # The worker additionally bounds the whole cycle with CYCLE_BUDGET_S.
        self._exchange = ccxt.bitbank({"enableRateLimit": True, "timeout": int(timeout_ms)})

    def discover_markets(self) -> dict[str, MarketInfo]:
        raw_markets = self._exchange.load_markets()
        if not isinstance(raw_markets, Mapping):
            return {}
        discovered: dict[str, MarketInfo] = {}
        for raw_market in raw_markets.values():
            if not isinstance(raw_market, Mapping):
                continue
            symbol = str(raw_market.get("symbol") or "").strip()
            base = str(raw_market.get("base") or "").strip()
            quote = str(raw_market.get("quote") or "").strip()
            if not symbol or not base or not quote:
                continue
            if raw_market.get("spot") is not True:
                continue
            if raw_market.get("active") is False:
                continue
            if not _bitbank_info_allows_orders(raw_market.get("info")):
                continue
            market = MarketInfo(
                symbol=symbol,
                base=base,
                quote=quote,
                spot=True,
                active=True,
                amount_step=_amount_step(_nested(raw_market, "precision", "amount")),
                min_amount=(
                    _positive_decimal(_nested(raw_market, "info", "unit_amount"))
                    or _positive_decimal(_nested(raw_market, "limits", "amount", "min"))
                ),
                min_cost=_positive_decimal(_nested(raw_market, "limits", "cost", "min")),
                taker_fee_rate=(
                    _nonnegative_decimal(_nested(raw_market, "info", "taker_fee_rate_quote"))
                    if _nonnegative_decimal(_nested(raw_market, "info", "taker_fee_rate_quote")) is not None
                    else _nonnegative_decimal(raw_market.get("taker"))
                ),
                taker_fee_rate_base=_nonnegative_decimal(_nested(raw_market, "info", "taker_fee_rate_base")),
                taker_fee_rate_quote=(
                    _nonnegative_decimal(_nested(raw_market, "info", "taker_fee_rate_quote"))
                    if _nonnegative_decimal(_nested(raw_market, "info", "taker_fee_rate_quote")) is not None
                    else _nonnegative_decimal(raw_market.get("taker"))
                ),
                market_order_enabled=not (
                    _nested(raw_market, "info", "stop_market_order") is True
                    or _nested(raw_market, "info", "stop_market_order") == 1
                ),
            )
            discovered[symbol] = market
        return discovered

    def fetch_circuit_break_statuses(self, symbols, *, fetched_at: float | None = None) -> dict[str, CircuitBreakStatus]:
        endpoint = getattr(self._exchange, "publicGetPairCircuitBreakInfo", None)
        if not callable(endpoint):
            endpoint = getattr(self._exchange, "public_get_pair_circuit_break_info", None)
        if not callable(endpoint):
            raise ArbitrageDataError("bitbank circuit-break public endpoint is unavailable")
        statuses: dict[str, CircuitBreakStatus] = {}
        for symbol in symbols:
            pair_id = str(symbol).lower().replace("/", "_")
            market_fn = getattr(self._exchange, "market", None)
            if callable(market_fn):
                try:
                    market = market_fn(str(symbol))
                    if isinstance(market, Mapping) and market.get("id"):
                        pair_id = str(market["id"])
                except Exception:
                    pass
            raw = endpoint({"pair": pair_id})
            if not isinstance(raw, Mapping) or raw.get("success") != 1 or not isinstance(raw.get("data"), Mapping):
                raise ArbitrageDataError(f"{symbol} circuit-break response is malformed")
            data = raw["data"]
            timestamp = data.get("timestamp")
            mode = data.get("mode")
            if timestamp is None or mode is None:
                raise ArbitrageDataError(f"{symbol} circuit-break response is incomplete")
            try:
                observed_at = time.time() if fetched_at is None else float(fetched_at)
                statuses[str(symbol)] = CircuitBreakStatus(
                    str(symbol), str(mode), None if data.get("fee_type") is None else str(data.get("fee_type")),
                    float(timestamp) / 1000.0, fetched_at=observed_at,
                )
            except (TypeError, ValueError, TradingValidationError) as exc:
                raise ArbitrageDataError(f"{symbol} circuit-break response is invalid") from exc
        return statuses

    def fetch_market_frames(
        self,
        symbols,
        *,
        timeframe: str = "5m",
        limit: int = 24,
        now: float,
    ) -> dict[str, MarketFrame]:
        if limit < 2:
            raise MarketFrameError("limit must be at least 2")
        frames: dict[str, MarketFrame] = {}
        for symbol in symbols:
            rows = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            frames[str(symbol)] = frame_from_ohlcv(
                str(symbol), rows, timeframe=timeframe, now=now, min_bars=limit
            )
        return frames

    def fetch_depth_books(
        self,
        symbols,
        *,
        now: float,
        limit: int = 20,
    ) -> dict[str, DepthBook]:
        if limit < 1:
            raise ArbitrageDataError("order-book limit must be positive")
        books: dict[str, DepthBook] = {}
        for symbol in symbols:
            raw = self._exchange.fetch_order_book(str(symbol), limit=limit)
            if not isinstance(raw, Mapping):
                raise ArbitrageDataError(f"{symbol} order book is malformed")
            timestamp = raw.get("timestamp")
            bids = raw.get("bids")
            asks = raw.get("asks")
            if timestamp is None:
                raise ArbitrageDataError(f"{symbol} order book has no exchange timestamp")
            if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
                raise ArbitrageDataError(f"{symbol} order book has no depth")
            try:
                bid_levels = tuple(DepthLevel(level[0], level[1]) for level in bids[:limit])
                ask_levels = tuple(DepthLevel(level[0], level[1]) for level in asks[:limit])
                as_of = float(timestamp) / 1000.0
                books[str(symbol)] = DepthBook(str(symbol), bid_levels, ask_levels, as_of)
            except (TypeError, ValueError, IndexError, TradingValidationError) as exc:
                raise ArbitrageDataError(f"{symbol} order book depth is malformed") from exc
        return books

    def fetch_top_books(
        self,
        symbols,
        *,
        now: float,
        limit: int = 5,
    ) -> dict[str, TopOfBook]:
        if limit < 1:
            raise ArbitrageDataError("order-book limit must be positive")
        books: dict[str, TopOfBook] = {}
        for symbol in symbols:
            raw = self._exchange.fetch_order_book(str(symbol), limit=limit)
            if not isinstance(raw, Mapping):
                raise ArbitrageDataError(f"{symbol} order book is malformed")
            timestamp = raw.get("timestamp")
            if timestamp is None:
                raise ArbitrageDataError(f"{symbol} order book has no exchange timestamp")
            bids = raw.get("bids")
            asks = raw.get("asks")
            if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
                raise ArbitrageDataError(f"{symbol} order book has no best bid/ask")
            try:
                bid = bids[0][0]
                bid_amount = bids[0][1]
                ask = asks[0][0]
                ask_amount = asks[0][1]
                as_of = float(timestamp) / 1000.0
                books[str(symbol)] = TopOfBook(
                    str(symbol), bid, ask, as_of, bid_amount=bid_amount, ask_amount=ask_amount
                )
            except (TypeError, ValueError, IndexError, TradingValidationError) as exc:
                raise ArbitrageDataError(f"{symbol} order book best prices are malformed") from exc
        return books
