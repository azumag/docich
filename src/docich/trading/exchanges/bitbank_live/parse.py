"""Raw bitbank JSON -> the L2 exchange types.  Pure functions, no I/O, no float.

Every value comes from the *string* the exchange sent.  Anything unexpected raises
``ExchangeDataError`` (with the field name only, never the value).  The response shapes follow the
bitbank REST API documentation (``data.pairs``, ``data.assets``, ``data.orders``, ``data.trades``);
they have not been checked against a live account yet (phase B does that), so every access is strict.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from ...live.exchange import (AssetBalance, BalanceSnapshot, DepositRecord, ExchangeDataError, FeeInfo,
                              FeeStatus, Fill, MarketRules, OrderBook, OrderSnapshot, OrderStatus, dec)

_ZERO = Decimal(0)
_JSON_SCALARS = (str, int, bool, type(None))


def _bad(name: str) -> ExchangeDataError:
    return ExchangeDataError(f"bad field: {name}")


def dec_str(value: object, name: str, *, positive: bool = False, nonneg: bool = False) -> Decimal:
    """A decimal from a JSON *string* (an int is accepted for fields documented as numbers)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _bad(name)
    try:
        result = dec(value)
    except ValueError:
        raise _bad(name) from None
    if (positive and result <= 0) or (nonneg and result < 0):
        raise _bad(name)
    return result


def ms_value(value: object, name: str) -> int:
    """A millisecond epoch: 13 digits.  Seconds (10 digits) or microseconds are refused, not guessed."""
    if isinstance(value, bool):
        raise _bad(name)
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not (10**12 <= value < 10**13):
        raise _bad(name)
    return value


def id_text(value: object, name: str) -> str:
    if isinstance(value, bool):
        raise _bad(name)
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.isdigit():
        return value
    raise _bad(name)


def text_value(value: object, name: str) -> str:
    """A non-empty string field (uuid, asset, status): never empty, never guessed."""
    if not isinstance(value, str) or not value:
        raise _bad(name)
    return value


def clean_raw(obj: object, name: str = "raw") -> object:
    """JSON-only (no float, no Decimal): what gets stored next to a fact."""
    if isinstance(obj, dict):
        return {str(k): clean_raw(v, name) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_raw(v, name) for v in obj]
    if isinstance(obj, _JSON_SCALARS):
        return obj
    raise _bad(name)


def _field(mapping: Mapping, key: str, name: str):
    if not isinstance(mapping, Mapping) or key not in mapping:
        raise _bad(name)
    return mapping[key]


def _digits(value: object, name: str) -> int:
    number = dec_str(value, name)
    if number != number.to_integral_value() or not (0 <= number <= 18):
        raise _bad(name)           # "3.9" or "-0.5" is refused, not truncated
    return int(number)


def _rate_or_none(value: object) -> Decimal | None:
    try:
        return dec_str(value, "fee_rate")
    except ExchangeDataError:
        return None


@dataclass(frozen=True)
class FeeRates:
    maker_base: Decimal | None
    taker_base: Decimal | None
    maker_quote: Decimal | None
    taker_quote: Decimal | None

    def max_abs(self, floor: Decimal = _ZERO) -> Decimal | None:
        values = (self.maker_base, self.taker_base, self.maker_quote, self.taker_quote)
        if any(v is None for v in values):
            return None
        return max([abs(v) for v in values] + [floor])


def pair_id(symbol: str) -> str:
    base, quote = symbol.split("/")
    return f"{base}_{quote}".lower()


def symbol_of(pair: object, symbols_by_pair: Mapping[str, str], name: str = "pair") -> str:
    if not isinstance(pair, str) or pair not in symbols_by_pair:
        raise _bad(name)
    return symbols_by_pair[pair]


def asset_precisions(assets_data: object) -> dict[str, int]:
    rows = _field(assets_data, "assets", "assets")
    if not isinstance(rows, list):
        raise _bad("assets")
    out = {}
    for row in rows:
        asset = _field(row, "asset", "asset")
        if not isinstance(asset, str):
            raise _bad("asset")
        out[asset.upper()] = _digits(_field(row, "amount_precision", "amount_precision"), "amount_precision")
    return out


def market_rules_from(pairs_data: object, precisions: Mapping[str, int], symbols,
                      fee_rate_floor: Decimal = _ZERO) -> tuple[dict[str, MarketRules], dict[str, FeeRates]]:
    rows = _field(pairs_data, "pairs", "pairs")
    if not isinstance(rows, list):
        raise _bad("pairs")
    by_name = {r.get("name"): r for r in rows if isinstance(r, Mapping)}
    rules: dict[str, MarketRules] = {}
    rates: dict[str, FeeRates] = {}
    for symbol in symbols:
        base, quote = symbol.split("/")
        row = by_name.get(pair_id(symbol))
        if row is None:
            raise _bad(f"pairs[{symbol}]")
        if (str(row.get("base_asset", "")).upper() != base or str(row.get("quote_asset", "")).upper() != quote
                or row.get("is_enabled") is not True):
            raise _bad(f"pairs[{symbol}].identity")
        if base not in precisions or quote not in precisions:
            raise _bad(f"assets[{symbol}]")
        fee = FeeRates(_rate_or_none(row.get("maker_fee_rate_base")), _rate_or_none(row.get("taker_fee_rate_base")),
                       _rate_or_none(row.get("maker_fee_rate_quote")), _rate_or_none(row.get("taker_fee_rate_quote")))
        rates[symbol] = fee
        rules[symbol] = MarketRules(
            symbol=symbol, base=base, quote=quote,
            amount_step=Decimal(1).scaleb(-_digits(row.get("amount_digits"), "amount_digits")),
            min_amount=dec_str(row.get("unit_amount"), "unit_amount", positive=True),
            price_tick=Decimal(1).scaleb(-_digits(row.get("price_digits"), "price_digits")),
            base_unit=Decimal(1).scaleb(-precisions[base]), quote_unit=Decimal(1).scaleb(-precisions[quote]),
            max_fee_rate=fee.max_abs(fee_rate_floor))
    return rules, rates


def balance_from(assets_data: object, fetched_at: float) -> BalanceSnapshot:
    rows = _field(assets_data, "assets", "assets")
    if not isinstance(rows, list):
        raise _bad("assets")
    out = {}
    for row in rows:
        asset = _field(row, "asset", "asset")
        if not isinstance(asset, str) or not asset:
            raise _bad("asset")
        out[asset.upper()] = AssetBalance(
            total=dec_str(_field(row, "onhand_amount", "onhand_amount"), "onhand_amount", nonneg=True),
            free=dec_str(_field(row, "free_amount", "free_amount"), "free_amount", nonneg=True),
            locked=dec_str(_field(row, "locked_amount", "locked_amount"), "locked_amount", nonneg=True))
    return BalanceSnapshot(assets=out, exchange_ts_ms=None, fetched_at=fetched_at)


def book_from(depth_data: object, symbol: str, depth: int) -> OrderBook:
    def side(key: str, descending: bool):
        rows = _field(depth_data, key, key)
        if not isinstance(rows, list):
            raise _bad(key)
        levels = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                raise _bad(key)
            levels.append((dec_str(row[0], key, positive=True), dec_str(row[1], key, nonneg=True)))
        prices = [p for p, _ in levels]
        if prices != sorted(prices, reverse=descending):
            raise _bad(f"{key} order")
        return tuple(levels[:depth])

    return OrderBook(symbol=symbol, bids=side("bids", True), asks=side("asks", False),
                     exchange_ts_ms=ms_value(_field(depth_data, "timestamp", "timestamp"), "timestamp"))


def order_from(row: object, symbols_by_pair: Mapping[str, str]) -> OrderSnapshot:
    symbol = symbol_of(_field(row, "pair", "pair"), symbols_by_pair)
    side = _field(row, "side", "side")
    if side not in ("buy", "sell"):
        raise _bad("side")
    order_type = _field(row, "type", "type")
    if not isinstance(order_type, str) or not order_type:
        raise _bad("type")
    amount = dec_str(row.get("start_amount"), "start_amount", positive=True)
    executed = dec_str(_field(row, "executed_amount", "executed_amount"), "executed_amount", nonneg=True)
    remaining_raw = row.get("remaining_amount")
    if remaining_raw is None:
        if order_type != "market":
            raise _bad("remaining_amount")
        remaining = amount - executed
        if remaining < 0:
            raise _bad("remaining_amount")
    else:
        remaining = dec_str(remaining_raw, "remaining_amount", nonneg=True)
    try:
        status = OrderStatus(_field(row, "status", "status"))
    except ValueError:
        raise _bad("status") from None
    price_raw = row.get("price")
    price = None if order_type == "market" or price_raw is None else dec_str(price_raw, "price", positive=True)
    average_raw = row.get("average_price")
    average = None if executed == 0 or average_raw is None else dec_str(average_raw, "average_price", nonneg=True)
    if average == 0:
        average = None   # "0" means "no average yet"
    return OrderSnapshot(
        exchange_order_id=id_text(_field(row, "order_id", "order_id"), "order_id"), symbol=symbol, side=side,
        order_type=order_type.lower(), amount=amount, price=price, status=status, executed_amount=executed,
        remaining_amount=remaining, average_price=average,
        ordered_at_ms=ms_value(_field(row, "ordered_at", "ordered_at"), "ordered_at"), client_order_id=None,
        raw=clean_raw(row))


def fee_from_trade(trade: Mapping, rates: FeeRates | None) -> FeeInfo:
    """From the exchange's own strings only (never ccxt's rounded fee).  Unclear -> UNKNOWN, never a guess."""
    base = _rate_or_none(trade.get("fee_amount_base"))
    quote = _rate_or_none(trade.get("fee_amount_quote"))
    if base is None or quote is None:
        return FeeInfo(FeeStatus.UNKNOWN)
    occurred = trade.get("fee_occurred_amount_quote")
    if occurred is not None:
        occurred_value = _rate_or_none(occurred)
        if occurred_value is None or occurred_value != quote:
            return FeeInfo(FeeStatus.UNKNOWN)
    pair = str(trade.get("pair", ""))
    base_asset, _, quote_asset = pair.partition("_")
    if base != 0 and quote != 0:
        return FeeInfo(FeeStatus.UNKNOWN)
    if base == 0 and quote == 0:
        return FeeInfo(FeeStatus.KNOWN, _ZERO, quote_asset.upper())
    amount, currency, side = (base, base_asset.upper(), "base") if base != 0 else (quote, quote_asset.upper(), "quote")
    role = trade.get("maker_taker")
    if rates is not None and role in ("maker", "taker"):
        rate = getattr(rates, f"{role}_{side}")
        if rate is not None and ((rate < 0 < amount) or (amount < 0 < rate)):
            return FeeInfo(FeeStatus.UNKNOWN)   # the sign convention is not what we assumed: do not trust it
    return FeeInfo(FeeStatus.KNOWN, amount, currency)


def fill_from(trade: object, symbols_by_pair: Mapping[str, str], rates_by_symbol: Mapping[str, FeeRates]) -> Fill:
    symbol = symbol_of(_field(trade, "pair", "pair"), symbols_by_pair)
    side = _field(trade, "side", "side")
    if side not in ("buy", "sell"):
        raise _bad("side")
    role = trade.get("maker_taker")
    return Fill(
        trade_id=id_text(_field(trade, "trade_id", "trade_id"), "trade_id"),
        exchange_order_id=id_text(_field(trade, "order_id", "order_id"), "order_id"), symbol=symbol, side=side,
        amount=dec_str(_field(trade, "amount", "amount"), "amount", positive=True),
        price=dec_str(_field(trade, "price", "price"), "price", positive=True), quote_notional=None,
        fee=fee_from_trade(trade, rates_by_symbol.get(symbol)),
        maker_taker=role if role in ("maker", "taker") else "unknown",
        executed_at_ms=ms_value(_field(trade, "executed_at", "executed_at"), "executed_at"), raw=clean_raw(trade))


def deposit_from(row: object) -> DepositRecord:
    """One ``deposit_history`` row (Issue #4587).

    ``uuid`` is the exchange's own stable id and becomes the deterministic record id, so a re-read
    after a restart is a no-op.  ``found_at`` is when the exchange first saw the deposit; only a
    ``DONE`` status means the money is in the balance (``FOUND``/``CONFIRMED`` are still in flight
    and are reported but never credited).
    """
    asset = text_value(_field(row, "asset", "asset"), "asset")
    status = text_value(_field(row, "status", "status"), "status")
    uid = text_value(_field(row, "uuid", "uuid"), "uuid")
    return DepositRecord(
        record_id=f"bitbank:deposit:{uid}", currency=asset.upper(),
        amount=dec_str(_field(row, "amount", "amount"), "amount", positive=True),
        status=status, credited=status == "DONE",
        occurred_at_ms=ms_value(_field(row, "found_at", "found_at"), "found_at"), raw=clean_raw(row))
