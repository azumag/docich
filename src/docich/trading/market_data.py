"""Normalized public market-history frames for paper trading strategies."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from .models import TradingValidationError, as_decimal


class MarketFrameError(ValueError):
    """Raised when public market history is malformed or unsafe to use."""


_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_seconds(timeframe: str) -> int:
    try:
        return _TIMEFRAME_SECONDS[str(timeframe)]
    except KeyError as exc:
        raise MarketFrameError(f"unsupported timeframe: {timeframe}") from exc


@dataclass(frozen=True)
class MarketFrame:
    symbol: str
    timeframe_seconds: int
    timestamps: tuple[float, ...]
    closes: tuple[Decimal, ...]
    volumes: tuple[Decimal, ...]

    @property
    def as_of(self) -> float:
        return self.timestamps[-1]

    @property
    def last_price(self) -> Decimal:
        return self.closes[-1]


def frame_from_ohlcv(
    symbol: str,
    rows: Sequence[Sequence[Any]],
    *,
    timeframe: str,
    now: float,
    min_bars: int,
    stale_after_intervals: int = 2,
) -> MarketFrame:
    if not isinstance(rows, Sequence) or len(rows) < min_bars:
        raise MarketFrameError(f"{symbol} history has too few bars")
    tf_seconds = timeframe_seconds(timeframe)
    timestamps: list[float] = []
    closes: list[Decimal] = []
    volumes: list[Decimal] = []
    try:
        for row in rows:
            if not isinstance(row, Sequence) or len(row) < 6:
                raise MarketFrameError(f"{symbol} history row is malformed")
            timestamp = float(row[0]) / 1000.0
            close = as_decimal(row[4], f"{symbol}.close")
            volume = as_decimal(row[5], f"{symbol}.volume")
            if close <= 0 or volume < 0:
                raise MarketFrameError(f"{symbol} history contains invalid price/volume")
            timestamps.append(timestamp)
            closes.append(close)
            volumes.append(volume)
    except (TypeError, ValueError, TradingValidationError) as exc:
        if isinstance(exc, MarketFrameError):
            raise
        raise MarketFrameError(f"{symbol} history contains invalid values") from exc

    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise MarketFrameError(f"{symbol} history timestamps are non-monotonic")
    if timestamps[-1] > float(now) + 1:
        raise MarketFrameError(f"{symbol} history is from the future")
    if float(now) - timestamps[-1] > tf_seconds * stale_after_intervals:
        raise MarketFrameError(f"{symbol} history is stale")
    return MarketFrame(
        symbol=str(symbol),
        timeframe_seconds=tf_seconds,
        timestamps=tuple(timestamps),
        closes=tuple(closes),
        volumes=tuple(volumes),
    )
