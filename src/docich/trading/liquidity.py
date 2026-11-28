"""Pure liquidity checks for new entries (no network, no files).

The PAPER and LIVE workers must not create a new buy in a market whose
executable spread is wider than ``MAX_ENTRY_SPREAD_BPS`` or whose recent bars
are mostly empty.  Sells are deliberately never gated: closing a position must
remain possible even when liquidity is poor.

Frame/book objects are duck-typed so the same checks work for the PAPER
``DepthBook``/``TopOfBook`` and the LIVE ``OrderBook``.
"""
from __future__ import annotations

from decimal import Decimal, DecimalException

from .models import as_decimal

D = Decimal
ZERO = D("0")
BPS = D("10000")

MAX_ENTRY_SPREAD_BPS = D("40")
MIN_ACTIVE_BAR_FRACTION = D("0.5")
ACTIVITY_BARS = 24

__all__ = [
    "MAX_ENTRY_SPREAD_BPS",
    "MIN_ACTIVE_BAR_FRACTION",
    "ACTIVITY_BARS",
    "spread_bps",
    "active_bar_fraction",
    "entry_block",
]


def _top_level(levels: object, *, best: str) -> Decimal | None:
    """Best price of one side, or None when the side is empty/malformed."""
    try:
        items = list(levels)  # type: ignore[arg-type]
    except TypeError:
        return None
    prices: list[Decimal] = []
    for item in items:
        try:
            raw_price = getattr(item, "price", None)
            if raw_price is None:
                raw_price = item[0]
            price = as_decimal(raw_price, "book price")
        except (DecimalException, TypeError, ValueError, IndexError):
            return None
        if price <= 0:
            return None
        prices.append(price)
    if not prices:
        return None
    return max(prices) if best == "bid" else min(prices)


def best_bid_ask(book: object) -> tuple[Decimal, Decimal] | None:
    """(best bid, best ask) of a book, or None when the book is unusable.

    Supports level containers (``bids``/``asks`` of objects with ``price`` or
    ``(price, amount)`` pairs) and top-of-book objects with ``bid``/``ask``.
    """
    if book is None:
        return None
    bids = getattr(book, "bids", None)
    asks = getattr(book, "asks", None)
    if bids is not None and asks is not None:
        bid = _top_level(bids, best="bid")
        ask = _top_level(asks, best="ask")
    else:
        raw_bid = getattr(book, "bid", None)
        raw_ask = getattr(book, "ask", None)
        if raw_bid is None or raw_ask is None:
            return None
        try:
            bid = as_decimal(raw_bid, "bid")
            ask = as_decimal(raw_ask, "ask")
        except (DecimalException, TypeError, ValueError):
            return None
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return bid, ask


def spread_bps(book: object) -> Decimal | None:
    """Relative bid/ask spread in bps, or None when the book is unusable."""
    top = best_bid_ask(book)
    if top is None:
        return None
    bid, ask = top
    mid = (bid + ask) / D("2")
    if mid <= 0:
        return None
    return (ask - bid) / mid * BPS


def active_bar_fraction(frame: object, bars: int = ACTIVITY_BARS) -> Decimal:
    """Share of the most recent ``bars`` bars that traded (volume > 0).

    A frame with fewer than ``bars`` observations is judged on the bars it
    actually has, so the newest live feeds (and small fixtures) are not
    treated as illiquid merely for being short.  A frame with no usable
    volumes has fraction 0.
    """
    if bars <= 0 or frame is None:
        return ZERO
    try:
        volumes = list(getattr(frame, "volumes", None) or ())
    except TypeError:
        return ZERO
    if not volumes:
        return ZERO
    window = volumes[-bars:]
    active = 0
    for value in window:
        try:
            if as_decimal(value, "volume") > 0:
                active += 1
        except (DecimalException, TypeError, ValueError):
            return ZERO
    return D(active) / D(len(window))


def entry_block(book: object, frame: object) -> str | None:
    """Reason code blocking a NEW BUY, or None when the entry may proceed."""
    spread = spread_bps(book)
    if spread is None:
        return "book_unavailable"
    if spread > MAX_ENTRY_SPREAD_BPS:
        return "spread_too_wide"
    if active_bar_fraction(frame) < MIN_ACTIVE_BAR_FRACTION:
        return "illiquid"
    return None
