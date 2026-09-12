import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading import dashboard_server
from docich.trading.dashboard_live import BitbankLiveReader, LiveMarketSampler


class FakeLiveSampler:
    def __init__(self):
        self.calls = 0

    def snapshot(self, *, now=None):
        self.calls += 1
        return {
            "schema_version": 1,
            "available": True,
            "symbol": "BTC/JPY",
            "fetched_at": float(now or 0),
            "ticker": {"last": 123.0, "bid": 122.0, "ask": 124.0, "as_of": 100.0},
            "trades": [{"id": "1", "timestamp": 100.0, "side": "buy", "price": 123.0, "amount": 0.1}],
        }


def test_server_serves_page_snapshot_live_feed_and_is_read_only(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({"worker_state": "running", "capital_reference": "10000",
                    "eligible_symbols": ["BTC/JPY"]}),
        encoding="utf-8",
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"BTC/JPY": {"closes": [1, 2, 3], "fetched_at": 1.0}}}),
        encoding="utf-8",
    )
    live_sampler = FakeLiveSampler()
    handler = dashboard_server.make_handler(trading_dir, live_sampler=live_sampler)
    httpd = dashboard_server._ReusableServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
        assert "PAPER" in html and "dashboard.js" in html
        assert "dashboard_candles.js" in html
        assert "10秒足ローソク" in html
        assert "市場歩み値" in html
        # Chrome must not offer to translate the Japanese page (the tip would
        # render over the streamed viewport).
        assert 'name="google" content="notranslate"' in html

        snapshot = json.loads(urllib.request.urlopen(base + "/api/trading/dashboard", timeout=5).read())
        assert snapshot["schema_version"] == 1
        assert snapshot["portfolio"]["capital_jpy"] == "10000"
        assert snapshot["chart"]["closes"] == [1.0, 2.0, 3.0]

        live = json.loads(urllib.request.urlopen(base + "/api/trading/live", timeout=5).read())
        assert live["schema_version"] == 1
        assert live["available"] is True
        assert live["symbol"] == "BTC/JPY"
        assert live["ticker"]["last"] == 123.0
        assert live["trades"][0]["side"] == "buy"
        assert live_sampler.calls == 1

        js = urllib.request.urlopen(base + "/dashboard.js", timeout=5).read().decode("utf-8")
        assert "api/trading/dashboard" in js
        assert "api/trading/live" in js
        assert "pollLive" in js
        assert "LIVE" in js
        candles = urllib.request.urlopen(base + "/dashboard_candles.js", timeout=5).read().decode("utf-8")
        assert "BUCKET_SECONDS = 10" in candles
        assert "open" in candles and "high" in candles and "low" in candles and "close" in candles
        assert "売買判断は従来の5分足" in candles

        for method in ("POST", "PUT", "DELETE"):
            for endpoint in ("/api/trading/dashboard", "/api/trading/live"):
                req = urllib.request.Request(base + endpoint, data=b"x", method=method)
                try:
                    urllib.request.urlopen(req, timeout=5)
                    raise AssertionError(f"{method} must be refused")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 405

        try:
            urllib.request.urlopen(base + "/nope", timeout=5)
            raise AssertionError("unknown path must be 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_live_reader_normalizes_public_ticker_and_recent_trades():
    class Exchange:
        def fetch_ticker(self, symbol):
            assert symbol == "BTC/JPY"
            return {"last": "12000000", "bid": "11999000", "ask": "12001000", "timestamp": 1_800_000_000_000}

        def fetch_trades(self, symbol, limit=10):
            assert symbol == "BTC/JPY" and limit == 10
            return [
                {"id": "old", "timestamp": 1_799_999_999_000, "side": "sell", "price": "11999000", "amount": "0.01"},
                {"id": "new", "timestamp": 1_800_000_000_000, "side": "buy", "price": "12000000", "amount": "0.02"},
                {"id": "bad", "timestamp": None, "side": "buy", "price": "0", "amount": "1"},
            ]

    payload = BitbankLiveReader(exchange=Exchange()).fetch("BTC/JPY", now=1_800_000_001.0)
    assert payload["available"] is True
    assert payload["ticker"]["last"] == 12000000.0
    assert payload["ticker"]["as_of"] == 1_800_000_000.0
    assert [item["id"] for item in payload["trades"]] == ["new", "old"]
    assert payload["trades"][0]["side"] == "buy"


def test_live_sampler_caches_public_requests_for_two_seconds(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({"eligible_symbols": ["BTC/JPY"], "open_positions": {}}), encoding="utf-8"
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"BTC/JPY": {"closes": [100, 101], "fetched_at": 1.0}}}), encoding="utf-8"
    )

    class Reader:
        def __init__(self):
            self.calls = 0

        def fetch(self, symbol, *, limit, now):
            self.calls += 1
            return {
                "schema_version": 1, "available": True, "symbol": symbol, "fetched_at": now,
                "ticker": {"last": 100 + self.calls, "bid": 99, "ask": 102, "as_of": now},
                "trades": [],
            }

    reader = Reader()
    sampler = LiveMarketSampler(trading_dir, reader_factory=lambda: reader, ttl_s=2.0)
    first = sampler.snapshot(now=100.0)
    second = sampler.snapshot(now=101.0)
    third = sampler.snapshot(now=102.1)
    assert first["ticker"]["last"] == 101
    assert second["ticker"]["last"] == 101
    assert third["ticker"]["last"] == 102
    assert reader.calls == 2


def test_live_sampler_keeps_last_frame_when_public_refresh_temporarily_fails(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({"eligible_symbols": ["BTC/JPY"], "open_positions": {}}), encoding="utf-8"
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"BTC/JPY": {"closes": [100, 101], "fetched_at": 1.0}}}), encoding="utf-8"
    )

    class Reader:
        def __init__(self):
            self.calls = 0

        def fetch(self, symbol, *, limit, now):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("public endpoint unavailable")
            return {
                "schema_version": 1, "available": True, "symbol": symbol, "fetched_at": now,
                "ticker": {"last": 101, "bid": 100, "ask": 102, "as_of": now}, "trades": [],
            }

    reader = Reader()
    sampler = LiveMarketSampler(trading_dir, reader_factory=lambda: reader, ttl_s=0.5)
    assert sampler.snapshot(now=100.0)["available"] is True
    stale = sampler.snapshot(now=101.0)
    assert stale["available"] is True
    assert stale["stale"] is True
    assert stale["ticker"]["last"] == 101


def test_serve_rejects_non_loopback_bind(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        dashboard_server.serve(trading_dir=tmp_path, host="0.0.0.0", port=8799)
