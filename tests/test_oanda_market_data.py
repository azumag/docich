import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from docich.trading.markets.core import Quote
from docich.trading.markets.moomoo_market_data import SOURCE as STOCK_SOURCE
from docich.trading.markets.oanda_market_data import (
    OandaMarketDataUnavailable,
    SOURCE,
    _validate_symbols,
    collect_once,
)


NY = ZoneInfo("America/New_York")


class OandaMarketDataTests(unittest.TestCase):
    def setUp(self):
        # Wednesday 2026-09-16 06:00 New York: normal FX week-open fixture.
        self.now = dt.datetime(2026, 9, 16, 6, 0, tzinfo=NY).timestamp()

    def quote(self, *, symbol="USD_JPY", age=1, source=SOURCE, tradeable=True):
        return Quote(
            symbol=symbol,
            ts=self.now - age,
            bid="150.000",
            ask="150.010",
            bid_size="1000000",
            ask_size="1000000",
            tradeable=tradeable,
            source=source,
            currency="JPY",
        )

    def test_collect_uses_oanda_pricing_adapter_and_publishes_file_contract(self):
        calls = []

        def reader(config, market, root, now):
            calls.append((config, market, root, now))
            return [self.quote(), self.quote(symbol="EUR_JPY")]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health = collect_once(root, now=self.now, reader=reader)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], {"feed": "oanda", "symbols": ["USD_JPY", "EUR_JPY"]})
            self.assertEqual(calls[0][1], "fx")
            payload = json.loads((root / "market-fx-quotes.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["market"], "fx")
            self.assertIs(payload["realtime"], True)
            self.assertEqual({q["symbol"] for q in payload["quotes"]}, {"USD_JPY", "EUR_JPY"})
            self.assertTrue(all(q["source"] == SOURCE for q in payload["quotes"]))
            self.assertTrue(all(q["currency"] == "JPY" for q in payload["quotes"]))
            self.assertEqual(health["provider"], "oanda-practice")
            self.assertEqual(health["quote_count"], 2)
            self.assertFalse(health["live_order_capability"])

    def test_stale_or_untradeable_quotes_do_not_refresh_existing_file(self):
        for row in (self.quote(age=30), self.quote(tradeable=False), self.quote(source="other")):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                quote_file = root / "market-fx-quotes.json"
                quote_file.write_text("old", encoding="utf-8")
                with self.assertRaises(OandaMarketDataUnavailable):
                    collect_once(root, now=self.now, reader=lambda *args: [row])
                self.assertEqual(quote_file.read_text(encoding="utf-8"), "old")

    def test_reader_failure_does_not_expose_provider_detail(self):
        def failed(*args):
            raise ValueError("SECRET-TOKEN https://example.invalid/private")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(OandaMarketDataUnavailable, "pricing unavailable") as ctx:
                collect_once(Path(tmp), now=self.now, reader=failed)
            self.assertNotIn("SECRET", str(ctx.exception))
            self.assertNotIn("example.invalid", str(ctx.exception))

    def test_only_jpy_crosses_are_accepted(self):
        self.assertEqual(_validate_symbols(["USD_JPY", "EUR_JPY"]), ["USD_JPY", "EUR_JPY"])
        with self.assertRaises(ValueError):
            _validate_symbols(["EUR_USD"])
        with self.assertRaises(ValueError):
            _validate_symbols(["USD_JPY", "bad"])

    def test_closed_week_fails_before_reader(self):
        saturday = dt.datetime(2026, 9, 19, 12, 0, tzinfo=NY).timestamp()
        calls = []
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OandaMarketDataUnavailable):
            collect_once(Path(tmp), now=saturday, reader=lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_collector_source_has_no_order_or_live_host_surface(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "docich" / "trading" /
                  "markets" / "oanda_market_data.py").read_text(encoding="utf-8")
        for forbidden in (
            "api-fxtrade.oanda.com", "/orders", "place_order", "create_order",
            "replace_order", "cancel_order", "close_trade", "PATCH", "POST", "PUT",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotEqual(SOURCE, STOCK_SOURCE)


if __name__ == "__main__":
    unittest.main()
