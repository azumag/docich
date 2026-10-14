"""Fail-closed regressions for external market-data timestamps."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docich.trading.markets.__main__ import Runtime
from docich.trading.markets.feeds import FeedUnavailable, read_quotes


def moment(text="2026-09-16T09:30:00+09:00"):
    return dt.datetime.fromisoformat(text).timestamp()


class FeedTimestampFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = moment()

    def tearDown(self):
        self.tmp.cleanup()

    def test_oanda_null_timestamp_is_feed_unavailable(self):
        payload = {
            "prices": [{
                "instrument": "USD_JPY",
                "time": None,
                "bids": [{"price": "150.00", "liquidity": 1000}],
                "asks": [{"price": "150.01", "liquidity": 1000}],
                "tradeable": True,
                "status": "tradeable",
            }]
        }
        env = {"DOCICH_OANDA_TOKEN": "fixture", "DOCICH_OANDA_ACCOUNT_ID": "101-000-1234567-001"}
        with patch.dict("os.environ", env), patch(
            "docich.trading.markets.feeds._read", return_value=json.dumps(payload).encode()
        ):
            with self.assertRaises(FeedUnavailable):
                read_quotes({"feed": "oanda", "symbols": ["USD_JPY"]}, "fx", self.root, self.now)

    def test_kabu_null_timestamp_is_feed_unavailable(self):
        calendar = self.root / "cal.json"
        calendar.write_text(json.dumps({
            "valid_from": "2026-09-01",
            "valid_through": "2026-09-30",
            "sessions": ["2026-09-16"],
        }))
        payload = {
            "BidPrice": 101,
            "AskPrice": 100,
            "BidQty": 200,
            "AskQty": 300,
            "BidTime": None,
            "AskTime": "2026-09-16T09:30:00+09:00",
            "CurrentPriceTime": "2026-09-16T09:30:00+09:00",
            "AskSign": "0101",
            "BidSign": "0101",
        }
        with patch.dict("os.environ", {"DOCICH_KABU_TOKEN": "fixture"}), patch(
            "docich.trading.markets.feeds._read", return_value=json.dumps(payload).encode()
        ):
            with self.assertRaises(FeedUnavailable):
                read_quotes(
                    {"feed": "kabu", "symbols": ["7203"], "calendar_file": "cal.json"},
                    "stocks",
                    self.root,
                    self.now,
                )

    def test_runtime_turns_bad_oanda_timestamp_into_feed_status(self):
        state_dir = self.root / "state"
        profile = self.root / "docich.toml"
        profile.write_text("[paper_corner]\nimprove_agents = ''\n")
        settings = self.root / "market.toml"
        settings.write_text(
            "[fx]\n"
            "enabled = true\n"
            "mode = 'paper'\n"
            "feed = 'oanda'\n"
            "symbols = ['USD_JPY']\n"
            "[fx.limits]\n"
            "lot = 1000\n"
        )
        runtime = Runtime(SimpleNamespace(state_dir=state_dir, config_path=profile), "fx", settings)
        payload = {
            "prices": [{
                "instrument": "USD_JPY",
                "time": None,
                "bids": [{"price": "150.00", "liquidity": 1000}],
                "asks": [{"price": "150.01", "liquidity": 1000}],
                "tradeable": True,
                "status": "tradeable",
            }]
        }
        env = {"DOCICH_OANDA_TOKEN": "fixture", "DOCICH_OANDA_ACCOUNT_ID": "101-000-1234567-001"}
        try:
            with patch.dict("os.environ", env), patch(
                "docich.trading.markets.feeds._read", return_value=json.dumps(payload).encode()
            ):
                result = runtime.tick(clock=lambda: self.now)
            self.assertEqual(result["status"], "price_feed_unavailable")
            self.assertEqual(result["accepted_quotes"], 0)
        finally:
            runtime.book.close()


if __name__ == "__main__":
    unittest.main()
