"""Per-market freshness assessment for paper trading public data.

Display/data freshness is tracked per market, independent of the worker
heartbeat. A market is fresh only when its last bar is recent, finite, and
not from the future. Stale or missing markets never become fresh just
because a new worker cycle started.

Note: `now` is currently unused by the assessment itself (the fetched/data
pair carries the ordering evidence) and is kept for future clock-skew
checks and call-site readability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# A bar older than this many timeframe intervals is stale (matches the
# frame-level contract in market_data.frame_from_ohlcv).
STALE_AFTER_INTERVALS = 2
# Exchange timestamps may lead the local clock slightly; beyond this the
# bar is classified as future data (clock-skew bucket), never fresh.
FUTURE_TOLERANCE_S = 1.0

QUALITY_FRESH = "fresh"
QUALITY_STALE = "stale"
QUALITY_MISSING = "missing"
QUALITY_INVALID = "invalid"

REASON_OK = "ok"
REASON_FETCH_ERROR = "fetch_error"
REASON_STALE_DATA = "stale_data"
REASON_FUTURE_DATA = "future_data"
REASON_INVALID_TIMESTAMP = "invalid_timestamp"
REASON_NOT_ATTEMPTED_BUDGET = "not_attempted_budget"
REASON_NO_FRAME_DATA = "no_frame_data"


@dataclass(frozen=True)
class MarketFreshness:
    symbol: str
    fetched_at: float | None
    data_as_of: float | None
    timeframe_s: int | None
    bar_closed: bool | None
    quality: str
    reason_code: str

    def payload(self) -> dict[str, object]:
        # Normalize non-finite timestamps to None so json.dump never emits
        # non-standard NaN/Infinity literals (unreadable by JSON.parse).
        def _finite_or_none(value: float | int | None) -> float | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)) and math.isfinite(value):
                return float(value)
            return None

        return {
            "symbol": self.symbol,
            "fetched_at": _finite_or_none(self.fetched_at),
            "data_as_of": _finite_or_none(self.data_as_of),
            "timeframe_s": self.timeframe_s,
            "bar_closed": self.bar_closed,
            "quality": self.quality,
            "reason_code": self.reason_code,
        }


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if isinstance(number, bool) or not math.isfinite(number):
        return None
    return number


def assess_market_freshness(
    symbol: str,
    *,
    fetched_at: float | None,
    data_as_of: float | None,
    timeframe_s: int | None,
    last_bar_start: float | None,
    now: float,
    error: str | None = None,
    attempted: bool = True,
) -> MarketFreshness:
    """Assess one market. Never raises; unparseable input is invalid, not fresh."""
    if not attempted:
        return MarketFreshness(symbol, None, None, timeframe_s, None, QUALITY_MISSING, REASON_NOT_ATTEMPTED_BUDGET)
    if error is not None:
        return MarketFreshness(symbol, fetched_at, None, timeframe_s, None, QUALITY_MISSING, REASON_FETCH_ERROR)
    fetched = _finite_number(fetched_at)
    observed = _finite_number(data_as_of)
    width = _finite_number(timeframe_s)
    if fetched is None or observed is None or width is None or width <= 0:
        return MarketFreshness(symbol, fetched_at, data_as_of, timeframe_s, None, QUALITY_INVALID, REASON_INVALID_TIMESTAMP)
    bar_start = _finite_number(last_bar_start)
    bar_closed: bool | None = None
    if bar_start is not None:
        bar_closed = (bar_start + width) <= fetched
    if observed <= 0:
        return MarketFreshness(symbol, fetched, observed, int(width), bar_closed, QUALITY_INVALID, REASON_NO_FRAME_DATA)
    if observed > fetched + FUTURE_TOLERANCE_S:
        return MarketFreshness(symbol, fetched, observed, int(width), bar_closed, QUALITY_INVALID, REASON_FUTURE_DATA)
    if fetched - observed > width * STALE_AFTER_INTERVALS:
        return MarketFreshness(symbol, fetched, observed, int(width), bar_closed, QUALITY_STALE, REASON_STALE_DATA)
    return MarketFreshness(symbol, fetched, observed, int(width), bar_closed, QUALITY_FRESH, REASON_OK)
