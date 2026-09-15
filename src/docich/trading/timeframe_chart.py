"""Presentation-only multi-timeframe OHLCV views for the PAPER corner (Issue #348).

The PAPER corner already shows a presentation-only 10-second candle chart and
narrates the reviewed 5-minute strategy. Issue #348 asks the corner to explain
the same market on the daily / 1-hour / 15-minute / 1-minute timeframes, with
chart context for the bot's recent mock trades.

This module fetches public bitbank candlesticks through the optional CCXT
dependency and turns them into allowlisted, bounded views. It is deliberately:

* read-only (public endpoints only, no credentials, no order methods);
* presentation-only (never read by a trading decision);
* honest (a failed or malformed timeframe is ``available: False``; nothing is
  invented) and bounded (fixed timeframes, bar caps, per-timeframe TTL).

bitbank's candlestick endpoint serves a single UTC date per request, so the
reader merges a bounded number of days. Higher timeframes therefore use more
requests; the sampler caches each timeframe independently so a dashboard poll
usually refreshes only the fast ones.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import importlib
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .dashboard import _focus_symbol, load_snapshot
from .exchanges.bitbank_ccxt import CCXTUnavailableError
from .strategy_store import load_strategy_policy

SCHEMA_VERSION = 1

TIMEFRAMES: tuple[str, ...] = ("1d", "1h", "15m", "1m")
TIMEFRAME_LABELS = {"1d": "日足", "1h": "1時間足", "15m": "15分足", "1m": "1分足"}
# One request per UTC day on bitbank. Keep the lookback bounded: enough bars for
# a 20-period Bollinger band on the daily chart without unbounded history.
TIMEFRAME_DAYS = {"1d": 22, "1h": 3, "15m": 2, "1m": 2}
# Bars kept per timeframe (display + narration).
TIMEFRAME_BARS = {"1d": 30, "1h": 48, "15m": 60, "1m": 60}
# Per-timeframe refresh TTL. Daily history barely moves; intraday is livelier.
TIMEFRAME_TTL_S = {"1d": 600.0, "1h": 120.0, "15m": 30.0, "1m": 20.0}
MAX_BARS = 90
BB_PERIOD = 20
BB_K = 2.0
MOMENTUM_LOOKBACK = 5

# bitbank's public candlestick endpoint currently serves intraday types only
# (1min/5min/15min/30min/1hour). The daily chart is therefore aggregated from
# hourly bars; see ``aggregate_ohlcv``.
_FETCH_TIMEFRAME = {"1d": "1h", "1h": "1h", "15m": "15m", "1m": "1m"}
_TIMEFRAME_SECONDS = {"1d": 86400, "1h": 3600, "15m": 900, "1m": 60}

# Fill-symbol spotlight: give the last mock trades chart context without
# fetching a full daily history for every position.
FILL_TIMEFRAMES: tuple[str, ...] = ("1h", "15m")
MAX_FILL_SYMBOLS = 2

# The strategy's own timeframe. Rendered from the reviewed 5-minute closes in
# the market cache so the screen shows the bars and thresholds the BOT actually
# trades on (no OHLC is invented from close-only data).
STRATEGY_TIMEFRAME = "5m"
STRATEGY_LABEL = "5分足(戦略)"
STRATEGY_BARS = 24

# Fetch the short, time-sensitive frames before the long daily history. The
# daily chart costs one request per UTC day; if a transient public-API failure
# lands at the end of that batch it must not drop the 1-minute chart from the
# one-shot corner narration.
_FETCH_PRIORITY = {"1m": 0, "15m": 1, "1h": 2, "1d": 3}
_FETCH_RETRY_DELAY_S = 0.5


class TimeframeDataError(RuntimeError):
    """Raised when public multi-timeframe data is unavailable or malformed."""


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: object) -> float | None:
    number = _finite(value)
    if number is None or number <= 0:
        return None
    return number


def _nonnegative(value: object) -> float | None:
    number = _finite(value)
    if number is None or number < 0:
        return None
    return number


def _round(value: float | None, digits: int = 8) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def simple_moving_average(values: Sequence[float], period: int) -> float | None:
    """Mean of the last ``period`` values, or None when there are too few."""
    if period < 1 or len(values) < period:
        return None
    window = [float(value) for value in values[-period:]]
    if not all(math.isfinite(value) for value in window):
        return None
    return sum(window) / period


def standard_deviation(values: Sequence[float], period: int) -> float | None:
    if period < 2 or len(values) < period:
        return None
    window = [float(value) for value in values[-period:]]
    if not all(math.isfinite(value) for value in window):
        return None
    mean = sum(window) / period
    variance = sum((value - mean) ** 2 for value in window) / period
    return math.sqrt(variance)


def bollinger_bands(
    values: Sequence[float],
    period: int = BB_PERIOD,
    k: float = BB_K,
) -> tuple[float | None, float | None, float | None]:
    """Return (middle, upper, lower) bands, or (None, None, None) when short."""
    middle = simple_moving_average(values, period)
    deviation = standard_deviation(values, period)
    if middle is None or deviation is None:
        return None, None, None
    width = deviation * float(k)
    return middle, middle + width, middle - width


def momentum_pct(values: Sequence[float], lookback: int = MOMENTUM_LOOKBACK) -> float | None:
    """Percent change over ``lookback`` bars (positive = rising)."""
    if lookback < 1 or len(values) < lookback + 1:
        return None
    start = float(values[-(lookback + 1)])
    end = float(values[-1])
    if not math.isfinite(start) or not math.isfinite(end) or start == 0:
        return None
    return (end / start - 1.0) * 100.0


def percent_change(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    start = float(values[0])
    end = float(values[-1])
    if not math.isfinite(start) or not math.isfinite(end) or start == 0:
        return None
    return (end / start - 1.0) * 100.0


def band_position(last: float | None, lower: float | None, upper: float | None) -> float | None:
    """Where the last price sits inside the bands: <0 below, >1 above."""
    if last is None or lower is None or upper is None:
        return None
    width = float(upper) - float(lower)
    if width <= 0:
        return None
    return (float(last) - float(lower)) / width


def classify_trend(last: float | None, middle: float | None, momentum: float | None) -> str:
    if last is None:
        return "不明"
    if momentum is None or middle is None:
        return "横ばい"
    above = last >= middle
    if momentum > 0.05 and above:
        return "上昇"
    if momentum < -0.05 and not above:
        return "下降"
    return "横ばい"


def _trend_phrase(position: float | None) -> str:
    if position is None:
        return "バンド位置は算出待ち"
    if position >= 1.0:
        return "終値がバンド上限を上抜け"
    if position >= 0.8:
        return "バンド上限寄り"
    if position <= 0.0:
        return "終値がバンド下限を下抜け"
    if position <= 0.2:
        return "バンド下限寄り"
    return "バンド中央付近"


def _clean_rows(rows: object) -> list[tuple[float, float, float, float, float, float]]:
    if not isinstance(rows, Sequence):
        return []
    cleaned: dict[float, tuple[float, float, float, float, float, float]] = {}
    for row in rows:
        if not isinstance(row, Sequence) or len(row) < 6:
            continue
        timestamp = _finite(row[0])
        if timestamp is None:
            continue
        timestamp = timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp
        if timestamp <= 0:
            continue
        open_ = _positive(row[1])
        high = _positive(row[2])
        low = _positive(row[3])
        close = _positive(row[4])
        volume = _nonnegative(row[5])
        if None in (open_, high, low, close, volume):
            continue
        if high < low:
            continue
        cleaned[timestamp] = (timestamp, open_, high, low, close, volume)
    return [cleaned[key] for key in sorted(cleaned)]


def aggregate_ohlcv(rows: object, bucket_seconds: int) -> list:
    """Aggregate raw OHLCV rows into fixed ``bucket_seconds`` bars.

    Used to build the daily chart from bitbank's hourly candles. Open is the
    first open, close the last close, high/low the extremes and volume the sum
    of the bucket. Non-finite or out-of-order rows are dropped.
    """
    seconds = max(1, int(bucket_seconds))
    cleaned = _clean_rows(rows)
    buckets: dict[float, list] = {}
    for timestamp, open_, high, low, close, volume in cleaned:
        bucket = math.floor(timestamp / seconds) * seconds
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = [bucket, open_, high, low, close, volume]
        else:
            current[2] = max(current[2], high)
            current[3] = min(current[3], low)
            current[4] = close
            current[5] = current[5] + volume
    return [buckets[key] for key in sorted(buckets)]


def build_timeframe_view(
    symbol: str,
    timeframe: str,
    rows: object,
    *,
    now: float,
    limit: int | None = None,
    stale_after_intervals: int = 2,
) -> dict[str, object]:
    """Allowlisted, bounded view of one timeframe. Never raises on bad input."""
    label = TIMEFRAME_LABELS.get(str(timeframe))
    if label is None:
        raise TimeframeDataError(f"unsupported timeframe: {timeframe}")
    keep = int(limit) if limit is not None else int(TIMEFRAME_BARS.get(timeframe, 60))
    keep = max(2, min(MAX_BARS, keep))
    cleaned = _clean_rows(rows)
    if cleaned:
        cleaned = cleaned[-keep:]
    base: dict[str, object] = {
        "timeframe": str(timeframe),
        "label": label,
        "symbol": str(symbol),
    }
    if len(cleaned) < 2:
        return {**base, "available": False, "bars": [], "reason": "history-too-short"}

    timestamps = [row[0] for row in cleaned]
    opens = [row[1] for row in cleaned]
    highs = [row[2] for row in cleaned]
    lows = [row[3] for row in cleaned]
    closes = [row[4] for row in cleaned]
    volumes = [row[5] for row in cleaned]
    if timestamps[-1] > float(now) + 1:
        return {**base, "available": False, "bars": [], "reason": "history-from-future"}

    middle, upper, lower = bollinger_bands(closes)
    position = band_position(closes[-1], lower, upper)
    momentum = momentum_pct(closes)
    tf_seconds = _TIMEFRAME_SECONDS.get(timeframe, 0)
    age_sec = max(0.0, float(now) - timestamps[-1])
    stale = bool(tf_seconds) and age_sec > tf_seconds * max(1, int(stale_after_intervals))
    view: dict[str, object] = {
        **base,
        "available": True,
        "bars": [
            {
                "t": _round(timestamp, 3),
                "o": _round(open_, 8),
                "h": _round(high, 8),
                "l": _round(low, 8),
                "c": _round(close, 8),
                "v": _round(volume, 8),
            }
            for timestamp, open_, high, low, close, volume in cleaned
        ],
        "bar_count": len(cleaned),
        "as_of": _round(timestamps[-1], 3),
        "last_close": _round(closes[-1], 8),
        "open": _round(opens[0], 8),
        "high": _round(max(highs), 8),
        "low": _round(min(lows), 8),
        "range_change_pct": _round(percent_change(closes), 4),
        "momentum_pct": _round(momentum, 4),
        "sma": _round(simple_moving_average(closes, BB_PERIOD), 8),
        "bb_mid": _round(middle, 8),
        "bb_upper": _round(upper, 8),
        "bb_lower": _round(lower, 8),
        "bb_position": _round(position, 4),
        "bb_phrase": _trend_phrase(position),
        "trend": classify_trend(closes[-1], middle, momentum),
        "age_sec": _round(age_sec, 3),
        "stale": stale,
    }
    return view


class TimeframeChartReader:
    """Optional public-only CCXT reader for multi-timeframe bitbank candles."""

    def __init__(self, *, exchange: Any | None = None, timeout_ms: int = 6000):
        if exchange is not None:
            self._exchange = exchange
            return
        try:
            ccxt = importlib.import_module("ccxt")
        except (ImportError, ModuleNotFoundError) as exc:
            raise CCXTUnavailableError(
                "CCXT is optional. Install requirements-trading.txt to enable multi-timeframe charts."
            ) from exc
        self._exchange = ccxt.bitbank({"enableRateLimit": True, "timeout": int(timeout_ms)})

    def _fetch_day(self, symbol: str, fetch_timeframe: str, day_ms: int) -> list:
        rows = self._exchange.fetch_ohlcv(symbol, timeframe=fetch_timeframe, since=day_ms, limit=1000)
        return rows if isinstance(rows, list) else []

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        *,
        now: float,
        days: int | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        selected = str(symbol or "").strip()
        if not selected:
            raise TimeframeDataError("timeframe symbol is empty")
        if timeframe not in TIMEFRAME_LABELS:
            raise TimeframeDataError(f"unsupported timeframe: {timeframe}")
        span = int(days) if days is not None else int(TIMEFRAME_DAYS.get(timeframe, 2))
        span = max(1, min(31, span))
        fetch_timeframe = _FETCH_TIMEFRAME.get(timeframe, timeframe)
        today_ms = (int(float(now)) // 86400) * 86400 * 1000
        rows: list = []
        for offset in range(span - 1, -1, -1):
            day_ms = today_ms - offset * 86400 * 1000
            rows.extend(self._fetch_day(selected, fetch_timeframe, day_ms))
        if fetch_timeframe != timeframe:
            rows = aggregate_ohlcv(rows, _TIMEFRAME_SECONDS[timeframe])
        return build_timeframe_view(selected, timeframe, rows, now=now, limit=limit)


class TimeframeChartSampler:
    """Resolve the dashboard focus symbol and cache its timeframe views.

    Per-timeframe TTLs keep dashboard polling cheap: the 1-minute chart
    refreshes every ~20s while the daily history is cached for minutes. A
    failed refresh reuses the last good frame for that timeframe instead of
    blanking the corner.
    """

    def __init__(
        self,
        trading_dir: Path,
        *,
        reader_factory: Callable[[], TimeframeChartReader] = TimeframeChartReader,
        now_fn: Callable[[], float] = time.time,
        ttl_overrides: Mapping[str, float] | None = None,
    ):
        self.trading_dir = Path(trading_dir)
        self.reader_factory = reader_factory
        self.now_fn = now_fn
        self.ttl = dict(TIMEFRAME_TTL_S)
        for key, value in (ttl_overrides or {}).items():
            if key in self.ttl:
                self.ttl[key] = max(1.0, min(3600.0, float(value)))
        self._reader: TimeframeChartReader | None = None
        self._cache: dict[tuple[str, str], dict[str, object]] = {}
        self._lock = threading.Lock()

    def _symbol(self) -> str | None:
        snapshot, closes = load_snapshot(self.trading_dir)
        return _focus_symbol(snapshot, closes)

    def _view(self, symbol: str, timeframe: str, moment: float) -> dict[str, object]:
        key = (symbol, timeframe)
        cached = self._cache.get(key)
        ttl = self.ttl.get(timeframe, 30.0)
        if isinstance(cached, dict) and moment - float(cached.get("_fetched_at") or 0) < ttl:
            return self._public(cached)
        fresh: dict[str, object] | None = None
        for attempt in range(2):
            try:
                if self._reader is None:
                    self._reader = self.reader_factory()
                fresh = self._reader.fetch(symbol, timeframe, now=moment)
                break
            except Exception:
                fresh = None
                if attempt == 0:
                    # One bounded retry for transient public-API failures.
                    time.sleep(_FETCH_RETRY_DELAY_S)
        if fresh is not None:
            record = dict(fresh)
            record["_fetched_at"] = moment
            self._cache[key] = record
            return self._public(record)
        if isinstance(cached, dict):
            stale = dict(cached)
            stale["stale"] = True
            stale["reason"] = "refresh-unavailable"
            return self._public(stale)
        return {
            "timeframe": timeframe,
            "label": TIMEFRAME_LABELS.get(timeframe, timeframe),
            "symbol": symbol,
            "available": False,
            "bars": [],
            "reason": "fetch-unavailable",
        }

    @staticmethod
    def _public(record: Mapping[str, object]) -> dict[str, object]:
        return {key: value for key, value in record.items() if not str(key).startswith("_")}

    def views(
        self,
        symbol: str,
        *,
        now: float | None = None,
        timeframes: Sequence[str] = TIMEFRAMES,
    ) -> list[dict[str, object]]:
        moment = float(self.now_fn() if now is None else now)
        requested = [str(timeframe) for timeframe in timeframes]
        ordered = sorted(requested, key=lambda timeframe: _FETCH_PRIORITY.get(timeframe, 99))
        with self._lock:
            fetched = {timeframe: self._view(str(symbol), timeframe, moment) for timeframe in ordered}
        return [fetched[timeframe] for timeframe in requested]

    def snapshot(self, *, now: float | None = None) -> dict[str, object]:
        moment = float(self.now_fn() if now is None else now)
        symbol = self._symbol()
        if not symbol:
            return {
                "schema_version": SCHEMA_VERSION,
                "available": False,
                "symbol": None,
                "fetched_at": moment,
                "timeframes": [],
                "error": "no focus market",
            }
        timeframes = self.views(symbol, now=moment)
        available = any(view.get("available") is True for view in timeframes)
        strategy = build_strategy_view(self.trading_dir, now=moment)
        return {
            "schema_version": SCHEMA_VERSION,
            "available": available,
            "symbol": symbol,
            "fetched_at": moment,
            "timeframes": timeframes,
            "strategy": strategy,
        }


def build_strategy_view(
    trading_dir: Path,
    *,
    now: float,
    policy: object | None = None,
) -> dict[str, object]:
    """The strategy's own 5-minute close series + its thresholds.

    Read-only and close-only: it never invents OHLC for the strategy frame. The
    screen uses it to show the bars and thresholds the BOT actually trades, next
    to the multi-timeframe context.
    """
    snapshot, closes = load_snapshot(Path(trading_dir))
    focus = _focus_symbol(snapshot, closes)
    series: list[float] = []
    if focus:
        series = [
            float(value)
            for value in (closes.get(focus) or [])[-STRATEGY_BARS:]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        ]
    base: dict[str, object] = {
        "timeframe": STRATEGY_TIMEFRAME,
        "label": STRATEGY_LABEL,
        "symbol": focus,
        "available": False,
    }
    if not focus or len(series) < 2:
        return base
    if policy is None:
        try:
            policy = load_strategy_policy(trading_dir)
        except Exception:
            policy = None
    middle, upper, lower = bollinger_bands(series)
    position = band_position(series[-1], lower, upper)
    momentum = momentum_pct(series)
    thresholds: dict[str, object] = {}
    if policy is not None:
        thresholds = {
            "momentum_lookback": getattr(policy, "momentum_lookback", None),
            "momentum_threshold_bps": str(getattr(policy, "momentum_threshold_bps", "")),
            "mean_reversion_lookback": getattr(policy, "mean_reversion_lookback", None),
            "mean_reversion_z": str(getattr(policy, "mean_reversion_z", "")),
        }
    return {
        **base,
        "available": True,
        "bars": [{"c": _round(value, 8)} for value in series],
        "bar_count": len(series),
        "last_close": _round(series[-1], 8),
        "sma": _round(middle, 8),
        "bb_mid": _round(middle, 8),
        "bb_upper": _round(upper, 8),
        "bb_lower": _round(lower, 8),
        "bb_position": _round(position, 4),
        "bb_phrase": _trend_phrase(position),
        "momentum_pct": _round(momentum, 4),
        "trend": classify_trend(series[-1], middle, momentum),
        "thresholds": thresholds,
    }


def _compact_timeframe(view: Mapping[str, object]) -> dict[str, object]:
    return {
        "timeframe": str(view.get("timeframe", "")),
        "label": str(view.get("label", "")),
        "bars": view.get("bar_count"),
        "trend": str(view.get("trend", "不明")),
        "last_close": view.get("last_close"),
        "range_change_pct": view.get("range_change_pct"),
        "momentum_pct": view.get("momentum_pct"),
        "sma": view.get("sma"),
        "bb_upper": view.get("bb_upper"),
        "bb_lower": view.get("bb_lower"),
        "bb_position": view.get("bb_position"),
        "bb_phrase": str(view.get("bb_phrase", "")),
        "high": view.get("high"),
        "low": view.get("low"),
        "as_of": view.get("as_of"),
        "stale": bool(view.get("stale")),
    }


def build_narration_facts(
    trading_dir: Path,
    *,
    sampler: TimeframeChartSampler | None = None,
    now: float | None = None,
    reader_factory: Callable[[], TimeframeChartReader] = TimeframeChartReader,
) -> dict[str, object]:
    """Compact multi-timeframe facts for the corner narration.

    Focus-market timeframes are always included. Up to ``MAX_FILL_SYMBOLS``
    distinct recent-fill markets also get the intraday frames so each mock
    trade can be explained against its own chart. Any failure returns an empty
    dict; narration then simply has no chart section.
    """
    moment = time.time() if now is None else float(now)
    active = sampler if sampler is not None else TimeframeChartSampler(
        Path(trading_dir), reader_factory=reader_factory, now_fn=lambda: moment
    )
    try:
        snapshot = active.snapshot(now=moment)
    except Exception:
        return {}
    if not isinstance(snapshot, Mapping):
        return {}
    focus = snapshot.get("symbol")
    raw_timeframes = snapshot.get("timeframes")
    if not focus or not isinstance(raw_timeframes, list):
        return {}
    timeframes = [
        _compact_timeframe(view)
        for view in raw_timeframes
        if isinstance(view, Mapping) and view.get("available") is True
    ]
    if not timeframes:
        return {}

    fills = _recent_fill_symbols(Path(trading_dir))
    fill_context: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in fills:
        symbol = str(item.get("symbol") or "")
        if not symbol or symbol == focus or symbol in seen:
            continue
        if len(seen) >= MAX_FILL_SYMBOLS:
            break
        seen.add(symbol)
        views = active.views(symbol, now=moment, timeframes=FILL_TIMEFRAMES)
        compact = [
            _compact_timeframe(view)
            for view in views
            if isinstance(view, Mapping) and view.get("available") is True
        ]
        if compact:
            fill_context.append(
                {
                    "symbol": symbol,
                    "side": str(item.get("side") or ""),
                    "price": str(item.get("price") or ""),
                    "filled_at": item.get("filled_at"),
                    "timeframes": compact,
                }
            )
    return {
        "symbol": str(focus),
        "generated_at": moment,
        "timeframes": timeframes,
        "fill_timeframes": fill_context,
    }


def _recent_fill_symbols(trading_dir: Path) -> list[dict[str, object]]:
    import json

    try:
        status = json.loads((Path(trading_dir) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = status.get("recent_fills") if isinstance(status, Mapping) else None
    if not isinstance(raw, list):
        return []
    result: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "side": str(item.get("side") or ""),
                "price": str(item.get("price") or ""),
                "filled_at": _finite(item.get("filled_at")),
            }
        )
    return result
