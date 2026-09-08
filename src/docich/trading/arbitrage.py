"""Fee-aware public top-of-book triangular-arbitrage diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Mapping

from .models import MarketInfo, TradingValidationError, as_decimal

D = Decimal

class ArbitrageDataError(ValueError):
    """Raised when public order-book data is incomplete or unsafe."""

@dataclass(frozen=True)
class TopOfBook:
    symbol: str
    bid: Decimal
    ask: Decimal
    as_of: float
    bid_amount: Decimal = D("1")
    ask_amount: Decimal = D("1")

    def __post_init__(self) -> None:
        bid = as_decimal(self.bid, "bid")
        ask = as_decimal(self.ask, "ask")
        if bid <= 0 or ask <= 0 or ask < bid:
            raise TradingValidationError("top-of-book prices are invalid")
        if not math.isfinite(float(self.as_of)):
            raise TradingValidationError("top-of-book timestamp must be finite")
        bid_amount = as_decimal(self.bid_amount, "bid_amount")
        ask_amount = as_decimal(self.ask_amount, "ask_amount")
        if bid_amount <= 0 or ask_amount <= 0:
            raise TradingValidationError("top-of-book amounts must be positive")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)
        object.__setattr__(self, "bid_amount", bid_amount)
        object.__setattr__(self, "ask_amount", ask_amount)

@dataclass(frozen=True)
class ArbitrageLeg:
    symbol: str
    from_asset: str
    to_asset: str
    side: str
    price: Decimal
    fee_rate: Decimal
    multiplier: Decimal
    max_input: Decimal
    fee_rate_base: Decimal = D("0")

    @property
    def fee_rate_quote(self) -> Decimal:
        return self.fee_rate

@dataclass(frozen=True)
class ArbitrageRoute:
    route_id: str
    start_asset: str
    legs: tuple[ArbitrageLeg, ...]
    net_edge_bps: Decimal
    max_start_amount: Decimal

def _pair_key(a: str, b: str) -> frozenset[str]:
    return frozenset((a, b))

def _triangle_sets(markets: Mapping[str, MarketInfo]) -> list[tuple[str, str, str]]:
    pair_to_symbol: dict[frozenset[str], str] = {}
    assets: set[str] = set()
    for symbol, market in markets.items():
        if not market.active or not market.spot or market.base == market.quote:
            continue
        pair_to_symbol[_pair_key(market.base, market.quote)] = symbol
        assets.update((market.base, market.quote))
    ordered = sorted(assets)
    result: list[tuple[str, str, str]] = []
    for i, a in enumerate(ordered):
        for j in range(i + 1, len(ordered)):
            b = ordered[j]
            for k in range(j + 1, len(ordered)):
                c = ordered[k]
                keys = (_pair_key(a,b), _pair_key(b,c), _pair_key(c,a))
                if all(key in pair_to_symbol for key in keys):
                    result.append(tuple(pair_to_symbol[key] for key in keys))
    return result

def find_triangle_symbols(markets: Mapping[str, MarketInfo]) -> tuple[str, ...]:
    symbols = {symbol for triangle in _triangle_sets(markets) for symbol in triangle}
    return tuple(sorted(symbols))

def _leg(market: MarketInfo, book: TopOfBook, from_asset: str) -> ArbitrageLeg | None:
    quote_fee = market.taker_fee_rate_quote
    base_fee = market.taker_fee_rate_base
    if quote_fee is None or base_fee is None:
        return None
    quote_fee = as_decimal(quote_fee, "taker_fee_rate_quote")
    base_fee = as_decimal(base_fee, "taker_fee_rate_base")
    if from_asset == market.base:
        input_factor = D("1") + base_fee
        output_factor = D("1") - quote_fee
        multiplier = book.bid * output_factor / input_factor
        max_input = book.bid_amount * input_factor
        return ArbitrageLeg(
            market.symbol, market.base, market.quote, "sell", book.bid, quote_fee,
            multiplier, max_input, fee_rate_base=base_fee,
        )
    if from_asset == market.quote:
        input_factor = D("1") + quote_fee
        output_factor = D("1") - base_fee
        multiplier = output_factor / (book.ask * input_factor)
        max_input = book.ask_amount * book.ask * input_factor
        return ArbitrageLeg(
            market.symbol, market.quote, market.base, "buy", book.ask, quote_fee,
            multiplier, max_input, fee_rate_base=base_fee,
        )
    return None

def _canonical_cycle(legs: tuple[ArbitrageLeg, ...]) -> str:
    parts = [f"{leg.from_asset}>{leg.to_asset}@{leg.symbol}" for leg in legs]
    rotations = [parts[i:] + parts[:i] for i in range(len(parts))]
    return "|".join(min(rotations))

def scan_triangular_arbitrage(
    markets: Mapping[str, MarketInfo],
    books: Mapping[str, TopOfBook],
    *,
    now: float,
    min_net_edge_bps: Decimal = D("10"),
    max_book_age_seconds: float = 2.0,
) -> tuple[ArbitrageRoute, ...]:
    threshold = as_decimal(min_net_edge_bps, "min_net_edge_bps")
    usable: dict[str, tuple[MarketInfo, TopOfBook]] = {}
    for symbol, market in markets.items():
        book = books.get(symbol)
        if book is None or market.taker_fee_rate_quote is None or market.taker_fee_rate_base is None:
            continue
        age = float(now) - float(book.as_of)
        if age < -1 or age > max_book_age_seconds:
            continue
        usable[symbol] = (market, book)

    outgoing: dict[str, list[ArbitrageLeg]] = {}
    for market, book in usable.values():
        for asset in (market.base, market.quote):
            leg = _leg(market, book, asset)
            if leg is not None:
                outgoing.setdefault(asset, []).append(leg)

    found: dict[str, ArbitrageRoute] = {}
    for start in sorted(outgoing):
        for first in outgoing.get(start, []):
            for second in outgoing.get(first.to_asset, []):
                if second.symbol == first.symbol:
                    continue
                for third in outgoing.get(second.to_asset, []):
                    if third.symbol in {first.symbol, second.symbol} or third.to_asset != start:
                        continue
                    if len({start, first.to_asset, second.to_asset}) != 3:
                        continue
                    legs = (first, second, third)
                    multiplier = first.multiplier * second.multiplier * third.multiplier
                    edge = (multiplier - D("1")) * D("10000")
                    if edge < threshold:
                        continue
                    key = _canonical_cycle(legs)
                    first_prefix = first.multiplier
                    second_prefix = first.multiplier * second.multiplier
                    max_start = min(
                        first.max_input,
                        second.max_input / first_prefix,
                        third.max_input / second_prefix,
                    )
                    if max_start <= 0:
                        continue
                    route = ArbitrageRoute(key, start, legs, edge, max_start)
                    previous = found.get(key)
                    if previous is None or route.net_edge_bps > previous.net_edge_bps:
                        found[key] = route
    return tuple(sorted(found.values(), key=lambda route: (-route.net_edge_bps, route.route_id)))
