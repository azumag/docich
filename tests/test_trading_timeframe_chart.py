from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from docich.trading.timeframe_chart import (  # noqa: E402
    BB_PERIOD,
    TIMEFRAME_BARS,
    TIMEFRAMES,
    TimeframeChartReader,
    TimeframeChartSampler,
    TimeframeDataError,
    aggregate_ohlcv,
    band_position,
    bollinger_bands,
    build_narration_facts,
    build_strategy_view,
    build_timeframe_view,
    classify_trend,
    momentum_pct,
    simple_moving_average,
)

NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC (mid-day, no date-boundary edge)
STEP_MS = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000, "1d": 86_400_000}


def _rows(timeframe: str, *, count: int, start_ms: int, base: float = 100.0):
    step = STEP_MS[timeframe]
    rows = []
    for index in range(count):
        price = base + index
        rows.append([start_ms + index * step, price, price + 1, price - 1, price + 0.5, 2.0])
    return rows


def test_pure_indicators_are_finite_and_bounded():
    values = [float(100 + index) for index in range(25)]
    assert simple_moving_average(values, 20) == pytest.approx(114.5)
    assert simple_moving_average(values, 26) is None
    middle, upper, lower = bollinger_bands(values, period=20, k=2.0)
    assert middle == pytest.approx(114.5)
    assert upper is not None and lower is not None and upper > middle > lower
    assert 0.0 <= band_position(values[-1], lower, upper) <= 1.0
    assert momentum_pct(values, 5) == pytest.approx((124 / 119 - 1) * 100)
    assert momentum_pct(values[:1], 5) is None
    assert band_position(None, lower, upper) is None
    assert band_position(values[-1], None, upper) is None
    assert classify_trend(values[-1], middle, momentum_pct(values, 5)) == "上昇"
    assert classify_trend(values[-1], middle, None) == "横ばい"
    assert classify_trend(None, middle, 1.0) == "不明"


def test_build_view_is_allowlisted_and_capped():
    day_ms = int(NOW // 86400) * 86400 * 1000
    rows = _rows("1m", count=200, start_ms=day_ms)
    view = build_timeframe_view("BTC/JPY", "1m", rows, now=NOW)
    assert view["available"] is True
    assert view["timeframe"] == "1m" and view["label"] == "1分足"
    assert view["bar_count"] == TIMEFRAME_BARS["1m"]
    assert len(view["bars"]) == TIMEFRAME_BARS["1m"]
    bar = view["bars"][-1]
    assert set(bar) == {"t", "o", "h", "l", "c", "v"}
    assert view["bb_upper"] > view["bb_mid"] > view["bb_lower"]
    assert view["bb_phrase"]
    assert view["trend"] in {"上昇", "下降", "横ばい"}


def test_build_view_rejects_short_future_and_bad_rows():
    day_ms = int(NOW // 86400) * 86400 * 1000
    short = build_timeframe_view("BTC/JPY", "15m", _rows("15m", count=1, start_ms=day_ms), now=NOW)
    assert short["available"] is False and short["bars"] == []
    future = _rows("15m", count=5, start_ms=day_ms + 86_400_000)
    assert build_timeframe_view("BTC/JPY", "15m", future, now=NOW)["available"] is False
    mixed = [[day_ms, 1, 2, 0.5, 1.5, 1], [day_ms + 900_000, "x", 2, 1, 1.5, 1]]
    view = build_timeframe_view("BTC/JPY", "15m", mixed, now=NOW)
    # Only the malformed row is dropped; one valid bar is still too short.
    assert view["available"] is False
    with pytest.raises(TimeframeDataError):
        build_timeframe_view("BTC/JPY", "2m", mixed, now=NOW)


def test_build_view_flags_stale_history():
    day_ms = int(NOW // 86400) * 86400 * 1000
    rows = _rows("1h", count=24, start_ms=day_ms - 86_400_000)
    view = build_timeframe_view("BTC/JPY", "1h", rows, now=NOW)
    assert view["available"] is True
    assert view["stale"] is True
    assert view["age_sec"] > 7200


class FakeExchange:
    def __init__(self, *, fail_timeframes=(), now: float = NOW):
        self.calls: list[tuple] = []
        self.fail_timeframes = set(fail_timeframes)
        self.now = now

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None):
        if timeframe in self.fail_timeframes:
            raise RuntimeError("public endpoint unavailable")
        self.calls.append((symbol, timeframe, since, limit))
        step = STEP_MS[timeframe]
        day_ms = int(since // 86_400_000) * 86_400_000
        count = max(1, 86_400_000 // step)
        rows = _rows(timeframe, count=count, start_ms=day_ms)
        # Like bitbank, a day's response never includes candles after "now".
        ceiling = self.now * 1000
        return [row for row in rows if row[0] <= ceiling]


def test_reader_aggregates_daily_from_hourly():
    exchange = FakeExchange()
    reader = TimeframeChartReader(exchange=exchange)
    view = reader.fetch("BTC/JPY", "1d", now=NOW, days=22)
    assert view["available"] is True
    assert view["timeframe"] == "1d" and view["label"] == "日足"
    assert view["bar_count"] == 22
    # Daily is aggregated from hourly requests (bitbank serves no 1day type).
    assert len(exchange.calls) == 22
    assert all(call[1] == "1h" for call in exchange.calls)
    timestamps = [bar["t"] for bar in view["bars"]]
    assert timestamps == sorted(timestamps)
    # Each daily bucket spans one UTC day.
    assert view["bars"][1]["t"] - view["bars"][0]["t"] == 86400.0
    assert view["bars"][0]["h"] >= view["bars"][0]["l"]


def test_aggregate_ohlcv_builds_buckets():
    day_ms = 1_800_000_000_000
    rows = [
        [day_ms, 10, 12, 9, 11, 1.0],
        [day_ms + 3_600_000, 11, 15, 10, 14, 2.0],
        [day_ms + 7_200_000, 14, 14, 8, 9, 3.0],
    ]
    daily = aggregate_ohlcv(rows, 86_400)
    assert len(daily) == 1
    assert daily[0][1] == 10 and daily[0][2] == 15 and daily[0][3] == 8
    assert daily[0][4] == 9 and daily[0][5] == 6.0


def test_reader_merges_multiple_utc_days():
    exchange = FakeExchange()
    reader = TimeframeChartReader(exchange=exchange)
    view = reader.fetch("BTC/JPY", "1h", now=NOW, days=3)
    assert view["available"] is True
    assert view["bar_count"] == TIMEFRAME_BARS["1h"]
    # One public request per UTC day.
    assert len(exchange.calls) == 3
    assert all(call[1] == "1h" for call in exchange.calls)
    timestamps = [bar["t"] for bar in view["bars"]]
    assert timestamps == sorted(timestamps)


def _trading_dir(tmp_path: Path) -> Path:
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({
            "eligible_symbols": ["BTC/JPY"],
            "open_positions": {"BTC/JPY": "1"},
            "recent_fills": [
                {"symbol": "BTC/JPY", "side": "buy", "price": "100", "filled_at": NOW - 60},
                {"symbol": "ETH/JPY", "side": "sell", "price": "200", "filled_at": NOW - 30},
            ],
        }),
        encoding="utf-8",
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"BTC/JPY": {"closes": [100, 101], "fetched_at": NOW - 10}}}),
        encoding="utf-8",
    )
    return trading_dir


class CountingReader:
    def __init__(self, *, fail_timeframes=()):
        self.exchange = FakeExchange(fail_timeframes=fail_timeframes)
        self.reader = TimeframeChartReader(exchange=self.exchange)

    def fetch(self, symbol, timeframe, *, now, days=None, limit=None):
        self.exchange.now = float(now)
        return self.reader.fetch(symbol, timeframe, now=now, days=days, limit=limit)


def test_sampler_caches_per_timeframe_ttl(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    reader = CountingReader()
    sampler = TimeframeChartSampler(
        trading_dir,
        reader_factory=lambda: reader,
        ttl_overrides={"1m": 5.0, "15m": 5.0, "1h": 5.0, "1d": 5.0},
    )
    first = sampler.snapshot(now=NOW)
    assert first["available"] is True and first["symbol"] == "BTC/JPY"
    assert [view["timeframe"] for view in first["timeframes"]] == list(TIMEFRAMES)
    calls_after_first = len(reader.exchange.calls)
    second = sampler.snapshot(now=NOW + 1)
    assert len(reader.exchange.calls) == calls_after_first
    assert second["timeframes"][0]["stale"] is False
    sampler.snapshot(now=NOW + 6)
    assert len(reader.exchange.calls) > calls_after_first


def test_sampler_reports_unavailable_without_inventing(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    reader = CountingReader(fail_timeframes=TIMEFRAMES)
    sampler = TimeframeChartSampler(trading_dir, reader_factory=lambda: reader)
    snapshot = sampler.snapshot(now=NOW)
    assert snapshot["available"] is False
    assert all(view["available"] is False for view in snapshot["timeframes"])
    assert all(view["bars"] == [] for view in snapshot["timeframes"])


def test_sampler_without_focus_market_is_unavailable(tmp_path):
    trading_dir = tmp_path / "empty"
    trading_dir.mkdir(parents=True)
    sampler = TimeframeChartSampler(trading_dir, reader_factory=CountingReader)
    snapshot = sampler.snapshot(now=NOW)
    assert snapshot["available"] is False and snapshot["symbol"] is None


def test_sampler_fetches_intraday_before_daily(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    reader = CountingReader()
    sampler = TimeframeChartSampler(trading_dir, reader_factory=lambda: reader)
    sampler.snapshot(now=NOW)
    order = [call[1] for call in reader.exchange.calls]
    # 1m (2 days) and 15m (2 days) are fetched first; the daily chart is
    # requested last (as hourly bars across 22 days).
    assert order[:2] == ["1m", "1m"]
    assert order[2:4] == ["15m", "15m"]
    assert order[4:7] == ["1h", "1h", "1h"]
    # The oldest (daily-history) request only appears after the intraday
    # batches, so an intraday failure cannot be caused by the long daily batch.
    since_values = [call[2] for call in reader.exchange.calls]
    assert since_values.index(min(since_values)) >= 7
    # The caller still receives the display order (daily first).
    snapshot = sampler.snapshot(now=NOW)
    assert [view["timeframe"] for view in snapshot["timeframes"]] == list(TIMEFRAMES)


def test_sampler_retries_once_on_transient_failure(tmp_path, monkeypatch):
    from docich.trading import timeframe_chart as tc

    monkeypatch.setattr(tc, "_FETCH_RETRY_DELAY_S", 0.0)
    trading_dir = _trading_dir(tmp_path)

    class FlakyReader:
        def __init__(self):
            self.inner = CountingReader()
            self.failures: set[tuple[str, str]] = set()

        def fetch(self, symbol, timeframe, *, now, days=None, limit=None):
            key = (symbol, timeframe)
            if key not in self.failures:
                self.failures.add(key)
                raise RuntimeError("transient public-API failure")
            return self.inner.fetch(symbol, timeframe, now=now, days=days, limit=limit)

    flaky = FlakyReader()
    sampler = TimeframeChartSampler(trading_dir, reader_factory=lambda: flaky)
    snapshot = sampler.snapshot(now=NOW)
    assert snapshot["available"] is True
    assert all(view["available"] for view in snapshot["timeframes"])


def test_narration_facts_include_focus_and_fill_context(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    facts = build_narration_facts(
        trading_dir, reader_factory=CountingReader, now=NOW
    )
    assert facts["symbol"] == "BTC/JPY"
    assert [view["timeframe"] for view in facts["timeframes"]] == list(TIMEFRAMES)
    assert facts["timeframes"][0]["trend"] in {"上昇", "下降", "横ばい"}
    assert facts["fill_timeframes"] and facts["fill_timeframes"][0]["symbol"] == "ETH/JPY"
    # Fill symbols only fetch the intraday frames.
    assert [view["timeframe"] for view in facts["fill_timeframes"][0]["timeframes"]] == ["1h", "15m"]


def test_narration_facts_empty_when_public_data_fails(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    reader = CountingReader(fail_timeframes=TIMEFRAMES)
    facts = build_narration_facts(trading_dir, reader_factory=lambda: reader, now=NOW)
    assert facts == {}


def test_strategy_view_uses_cached_five_minute_closes(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    view = build_strategy_view(trading_dir, now=NOW)
    assert view["timeframe"] == "5m"
    assert view["label"] == "5分足(戦略)"
    assert view["available"] is True
    assert view["bar_count"] == 2
    assert view["symbol"] == "BTC/JPY"
    assert "thresholds" in view


def test_snapshot_includes_strategy_view(tmp_path):
    trading_dir = _trading_dir(tmp_path)
    sampler = TimeframeChartSampler(trading_dir, reader_factory=CountingReader)
    snapshot = sampler.snapshot(now=NOW)
    assert snapshot["strategy"]["label"] == "5分足(戦略)"
