import datetime as dt
import inspect
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from docich.trading.markets import provider_probe
from docich.trading.markets.provider_probe import probe_moomoo


JST = dt.timezone(dt.timedelta(hours=9))


def ts(text="2026-09-16T09:30:00+09:00"):
    return dt.datetime.fromisoformat(text).timestamp()


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        if orient != "records":
            raise AssertionError(orient)
        return list(self.rows)


class FakeContext:
    def __init__(self, now, *, state_error=False, subscribe_ok=True, quote_ok=True):
        self.now = now
        self.state_error = state_error
        self.subscribe_ok = subscribe_ok
        self.quote_ok = quote_ok
        self.closed = False

    def get_market_state(self, symbols):
        if self.state_error:
            raise RuntimeError("secret account/provider details")
        return 0, Frame([{"code": symbol, "stock_name": "hidden", "market_state": "AFTERNOON"}
                         for symbol in symbols])

    def get_market_snapshot(self, symbols):
        text = dt.datetime.fromtimestamp(self.now, JST).strftime("%Y-%m-%d %H:%M:%S")
        return 0, Frame([{"code": symbol, "name": "hidden", "update_time": text,
                          "last_price": 1234.5} for symbol in symbols])

    def subscribe(self, symbols, subtypes, subscribe_push=False):
        self.subscribe_push = subscribe_push
        return (0 if self.subscribe_ok else -1), "TOKEN=must-not-leak"

    def get_stock_quote(self, symbols):
        if not self.quote_ok:
            return -1, "PASSWORD=must-not-leak"
        date = dt.datetime.fromtimestamp(self.now, JST).strftime("%Y-%m-%d")
        clock = dt.datetime.fromtimestamp(self.now - 1, JST).strftime("%H:%M:%S")
        return 0, Frame([{"code": symbol, "name": "hidden", "data_date": date,
                          "data_time": clock, "last_price": 999999.0} for symbol in symbols])

    def close(self):
        self.closed = True


class FakeSDK:
    RET_OK = 0
    SubType = SimpleNamespace(QUOTE="QUOTE")

    def __init__(self, ctx):
        self.ctx = ctx

    def OpenQuoteContext(self, **kwargs):
        self.host = kwargs["host"]
        self.port = kwargs["port"]
        return self.ctx


class MoomooProbeTests(unittest.TestCase):
    def setUp(self):
        self.now = ts()

    def test_success_is_sanitized_and_realtime_ready(self):
        ctx = FakeContext(self.now)
        sdk = FakeSDK(ctx)
        clock = iter([0.0, .05, .10, .15, .20, .25, .30, .35])
        result = probe_moomoo(["JP.7203", "JP.7974"], now=self.now, sdk=sdk,
                              monotonic=lambda: next(clock))
        self.assertTrue(result["sdk_available"])
        self.assertTrue(result["opend_reachable"])
        self.assertTrue(result["market_state_known"])
        self.assertTrue(result["market_open"])
        self.assertEqual(result["snapshot_symbol_count"], 2)
        self.assertTrue(result["jp_quote_entitled"])
        self.assertEqual(result["fresh_quote_count"], 2)
        self.assertEqual(result["status"], "realtime_ready")
        self.assertTrue(result["realtime_ready"])
        self.assertTrue(ctx.closed)
        self.assertFalse(ctx.subscribe_push)
        serialized = json.dumps(result)
        self.assertNotIn("999999", serialized)
        self.assertNotIn("hidden", serialized)
        self.assertNotIn("TOKEN", serialized)
        self.assertNotIn("PASSWORD", serialized)

    def test_market_state_failure_does_not_block_quote_entitlement_probe(self):
        ctx = FakeContext(self.now, state_error=True)
        result = probe_moomoo(["JP.7203"], now=self.now, sdk=FakeSDK(ctx))
        self.assertTrue(result["opend_reachable"])
        self.assertFalse(result["market_state_ok"])
        self.assertTrue(result["jp_quote_entitled"])
        self.assertFalse(result["realtime_ready"])
        self.assertEqual(result["status"], "entitled_not_realtime_ready")
        self.assertTrue(ctx.closed)

    def test_subscription_failure_is_fixed_status_without_raw_error(self):
        ctx = FakeContext(self.now, subscribe_ok=False)
        result = probe_moomoo(["JP.7203"], now=self.now, sdk=FakeSDK(ctx))
        self.assertTrue(result["snapshot_ok"])
        self.assertFalse(result["jp_quote_entitled"])
        self.assertEqual(result["status"], "jp_quote_unavailable")
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_sdk_missing_fails_closed(self):
        with patch.object(provider_probe, "_load_sdk", side_effect=ModuleNotFoundError("moomoo")):
            result = probe_moomoo(["JP.7203"], now=self.now)
        self.assertEqual(result["status"], "sdk_unavailable")
        self.assertFalse(result["sdk_available"])
        self.assertFalse(result["jp_quote_entitled"])

    def test_remote_opend_and_non_jp_symbols_are_rejected(self):
        with self.assertRaises(ValueError):
            probe_moomoo(["JP.7203"], host="10.0.0.1", now=self.now, sdk=FakeSDK(FakeContext(self.now)))
        with self.assertRaises(ValueError):
            probe_moomoo(["US.AAPL"], now=self.now, sdk=FakeSDK(FakeContext(self.now)))
        with self.assertRaises(ValueError):
            probe_moomoo(["JP.7203", "JP.7203"], now=self.now, sdk=FakeSDK(FakeContext(self.now)))

    def test_probe_source_contains_no_trade_context_or_order_calls(self):
        source = inspect.getsource(provider_probe)
        for forbidden in ("OpenSecTradeContext", "place_order", "unlock_trade", "get_acc_list"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
