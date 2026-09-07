"""Optional, read-only CCXT adapter for bitbank public market discovery."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import importlib
from typing import Any, Mapping

from ..models import MarketInfo


class CCXTUnavailableError(RuntimeError):
    """Raised when the optional trading dependency is not installed."""


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
    value = _positive_decimal(raw)
    if value is None:
        return None
    if value < 1:
        return value
    if value == value.to_integral_value() and value <= 18:
        return Decimal("1").scaleb(-int(value))
    return None


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
    # can still be under an exchange-side order stop.  This first slice only
    # allocates buys, so a global or buy-side stop must fail closed.
    for key in ("stop_order", "stopOrder", "stop_order_and_cancel", "stopOrderAndCancel", "stop_buy_order", "stopBuyOrder"):
        if info.get(key) is True or info.get(key) == 1:
            return False
    return True


class BitbankPublicGateway:
    """Expose only public market discovery; no private/order methods exist."""

    def __init__(self, *, exchange: Any | None = None):
        if exchange is not None:
            self._exchange = exchange
            return
        try:
            ccxt = importlib.import_module("ccxt")
        except (ImportError, ModuleNotFoundError) as exc:
            raise CCXTUnavailableError(
                "CCXT is optional. Install requirements-trading.txt to enable bitbank discovery."
            ) from exc
        self._exchange = ccxt.bitbank({"enableRateLimit": True})

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
                min_amount=_positive_decimal(_nested(raw_market, "limits", "amount", "min")),
                min_cost=_positive_decimal(_nested(raw_market, "limits", "cost", "min")),
            )
            discovered[symbol] = market
        return discovered
