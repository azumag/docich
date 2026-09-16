import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from docich.trading.markets.moomoo_market_data import (
    MoomooMarketDataUnavailable,
    _collection_window,
    collect_once,
)


JST = ZoneInfo("Asia/Tokyo")


class FakeFilter:
    def __init__(self):
        self.stock_field = None
        self.is_no_filter = None
        self.sort = None


class FakeItem:
    def __init__(self, code, values):
        self.stock_code = code
        self.values = values

    def __getitem__(self, item):
        return self.values[item.stock_field]


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        if orient != "records":
            raise ValueError("records only")
        return list(self.rows)


class FakeSDK:
    RET_OK = 0

    class Market:
        JP = "JP"

    class SortDir:
        NONE = 0
        ASCEND = 1
        DESCEND = 2

    class StockField:
        CHANGE_RATE_5MIN = "change5"
        VOLUME_RATIO = "volume_ratio"
        TURNOVER = "turnover"
        AMPLITUDE = "amplitude"

    SimpleFilter = FakeFilter


class FakeContext:
    def __init__(self, now, *, stale=False, suspended=False, closed=False):
        self.now = now
        self.stale = stale
        self.suspended = suspended
        self.closed = closed
        self.filter_calls = 0
        self.closed_context = False
        self.items = [
            FakeItem("7203", {
                "change5": 1.25, "volume_ratio": 3.5,
                "turnover": 5_000_000_000, "amplitude": 2.0,
            }),
            FakeItem("JP.7974", {
                "change5": -1.5, "volume_ratio": 2.2,
                "turnover": 4_000_000_000, "amplitude": 2.5,
            }),
        ]

    def get_stock_filter(self, *, market, filter_list, begin, num):
        self.filter_calls += 1
        assert market == FakeSDK.Market.JP
        assert begin == 0
        assert 1 <= num <= 30
        assert len(filter_list) == 4
        assert sum(item.sort != FakeSDK.SortDir.NONE for item in filter_list) == 1
        return FakeSDK.RET_OK, (True, len(self.items), self.items[:num])

    def get_market_snapshot(self, codes):
        ts = self.now - (30 if self.stale else 1)
        rows = []
        for code in codes:
            base = 1000 if code.endswith("7203") else 8000
            rows.append({
                "code": code,
                "update_timestamp": ts,
                "last_price": base + 0.5,
                "bid_price": base,
                "ask_price": base + 1,
                "bid_vol": 1000,
                "ask_vol": 1200,
                "turnover": 250_000_000,
                "suspension": self.suspended,
            })
        return FakeSDK.RET_OK, FakeFrame(rows)

    def get_market_state(self, codes):
        state = "CLOSED" if self.closed else "MORNING"
        return FakeSDK.RET_OK, FakeFrame([
            {"code": code, "market_state": state} for code in codes
        ])

    def close(self):
        self.closed_context = True


class MoomooMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 16, 9, 15, tzinfo=JST).timestamp()

    def test_collect_publishes_existing_file_contracts_with_three_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = FakeContext(self.now)
            health = collect_once(
                root, now=self.now, sdk=FakeSDK,
                context_factory=lambda **kwargs: ctx,
            )
            self.assertEqual(ctx.filter_calls, 3)
            self.assertTrue(ctx.closed_context)
            self.assertEqual(health["status"], "ok")
            self.assertFalse(health["live_order_capability"])
            self.assertEqual(health["candidate_count"], 2)

            candidates = json.loads((root / "market-stocks-candidates.json").read_text())
            quotes = json.loads((root / "market-stocks-quotes.json").read_text())
            provider = json.loads((root / "market-stocks-provider-health.json").read_text())
            self.assertEqual(candidates["market"], "stocks")
            self.assertIs(candidates["realtime"], True)
            self.assertEqual({row["symbol"] for row in candidates["candidates"]}, {"7203", "7974"})
            self.assertEqual({row["symbol"] for row in quotes["quotes"]}, {"7203", "7974"})
            self.assertTrue(all(row["source"] == "moomoo-opend-readonly" for row in quotes["quotes"]))
            self.assertTrue(all(row["currency"] == "JPY" for row in quotes["quotes"]))
            self.assertNotIn("symbols", provider)
            self.assertNotIn("prices", provider)

    def test_stale_snapshot_does_not_refresh_existing_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "market-stocks-candidates.json"
            quotes = root / "market-stocks-quotes.json"
            candidates.write_text("old-candidates", encoding="utf-8")
            quotes.write_text("old-quotes", encoding="utf-8")
            ctx = FakeContext(self.now, stale=True)
            with self.assertRaises(MoomooMarketDataUnavailable):
                collect_once(root, now=self.now, sdk=FakeSDK, context_factory=lambda **kwargs: ctx)
            self.assertEqual(candidates.read_text(encoding="utf-8"), "old-candidates")
            self.assertEqual(quotes.read_text(encoding="utf-8"), "old-quotes")

    def test_closed_or_suspended_stock_fails_closed(self):
        for kwargs in ({"closed": True}, {"suspended": True}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                ctx = FakeContext(self.now, **kwargs)
                with self.assertRaises(MoomooMarketDataUnavailable):
                    collect_once(Path(tmp), now=self.now, sdk=FakeSDK,
                                 context_factory=lambda **ignored: ctx)

    def test_only_loopback_opend_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                collect_once(Path(tmp), now=self.now, host="192.0.2.1", sdk=FakeSDK)

    def test_collection_window_warms_one_minute_before_show(self):
        def at(hour, minute):
            return dt.datetime(2026, 9, 16, hour, minute, tzinfo=JST).timestamp()

        self.assertFalse(_collection_window(at(8, 58)))
        self.assertTrue(_collection_window(at(8, 59)))
        self.assertTrue(_collection_window(at(9, 59)))
        self.assertFalse(_collection_window(at(10, 0)))

    def test_collector_source_has_no_trade_api_surface(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "docich" / "trading" /
                  "markets" / "moomoo_market_data.py").read_text(encoding="utf-8")
        forbidden = (
            "OpenSecTradeContext", "OpenHKTradeContext", "OpenUSTradeContext",
            "unlock_trade", "place_order", "modify_order", "cancel_order",
        )
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
