import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.dashboard_live import LiveMarketSampler


def _payload(symbol, now, *, version=1):
    price = 100.0 + version
    return {
        "schema_version": 1,
        "available": True,
        "symbol": symbol,
        "fetched_at": now,
        "ticker": {"last": price, "bid": price - 1, "ask": price + 1, "as_of": now},
        "trades": [{
            "id": f"{symbol}:{version}",
            "timestamp": now if version > 1 else 100.0,
            "side": "buy",
            "price": price,
            "amount": 1.0,
        }],
    }


def test_idle_focus_falls_back_to_btc_and_returns_on_activity(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({
            "eligible_symbols": ["BTC/JPY", "ETH/JPY"],
            "open_positions": {"ETH/JPY": "1"},
        }),
        encoding="utf-8",
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {
            "ETH/JPY": {"closes": [100, 101], "fetched_at": 1.0},
            "BTC/JPY": {"closes": [1000, 1001], "fetched_at": 1.0},
        }}),
        encoding="utf-8",
    )

    class Reader:
        def __init__(self):
            self.eth_version = 1
            self.calls = []

        def fetch(self, symbol, *, limit, now):
            self.calls.append(symbol)
            if symbol == "ETH/JPY":
                return _payload(symbol, now, version=self.eth_version)
            return _payload(symbol, now, version=9)

    reader = Reader()
    sampler = LiveMarketSampler(
        trading_dir,
        reader_factory=lambda: reader,
        ttl_s=0.5,
        inactive_after_s=5.0,
    )

    first = sampler.snapshot(now=100.0)
    assert first["symbol"] == "ETH/JPY"
    assert first["display_fallback"] is False

    quiet = sampler.snapshot(now=106.0)
    assert quiet["symbol"] == "BTC/JPY"
    assert quiet["preferred_symbol"] == "ETH/JPY"
    assert quiet["display_fallback"] is True
    assert quiet["display_reason"] == "focus_inactive"
    assert quiet["focus_inactive_sec"] >= 5

    reader.eth_version = 2
    resumed = sampler.snapshot(now=107.0)
    assert resumed["symbol"] == "ETH/JPY"
    assert resumed["preferred_symbol"] == "ETH/JPY"
    assert resumed["display_fallback"] is False
    assert resumed["display_reason"] == "preferred_active"


def test_btc_focus_never_self_falls_back(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text(
        json.dumps({"eligible_symbols": ["BTC/JPY"], "open_positions": {}}),
        encoding="utf-8",
    )
    (trading_dir / "market_cache.json").write_text(
        json.dumps({"symbols": {"BTC/JPY": {"closes": [100, 101], "fetched_at": 1.0}}}),
        encoding="utf-8",
    )

    class Reader:
        def fetch(self, symbol, *, limit, now):
            return _payload(symbol, now, version=1)

    sampler = LiveMarketSampler(
        trading_dir,
        reader_factory=Reader,
        ttl_s=0.5,
        inactive_after_s=5.0,
    )
    assert sampler.snapshot(now=100.0)["display_fallback"] is False
    later = sampler.snapshot(now=120.0)
    assert later["symbol"] == "BTC/JPY"
    assert later["display_fallback"] is False
