from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.exchanges.bitbank_ccxt import BitbankPublicGateway  # noqa: E402
from docich.trading.market_data import MarketFrameError  # noqa: E402


class FakeExchange:
    def __init__(self, rows_by_symbol):
        self.rows_by_symbol = rows_by_symbol
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe="5m", limit=24):
        self.calls.append((symbol, timeframe, limit))
        return self.rows_by_symbol[symbol]


class TestBitbankMarketFrames(unittest.TestCase):
    def test_fetches_normalized_public_history_without_credentials(self):
        rows = [
            [1_800_000_000_000 + i * 300_000, 100+i, 102+i, 99+i, 101+i, 10+i]
            for i in range(6)
        ]
        exchange = FakeExchange({"BTC/JPY": rows})
        frames = BitbankPublicGateway(exchange=exchange).fetch_market_frames(
            ["BTC/JPY"], timeframe="5m", limit=6, now=1_800_001_600.0
        )
        frame = frames["BTC/JPY"]
        self.assertEqual(frame.closes[-1], Decimal("106"))
        self.assertEqual(frame.volumes[0], Decimal("10"))
        self.assertEqual(frame.timeframe_seconds, 300)
        self.assertEqual(exchange.calls, [("BTC/JPY", "5m", 6)])

    def test_rejects_stale_history(self):
        rows = [[1_700_000_000_000 + i * 300_000, 1, 1, 1, 1, 1] for i in range(6)]
        exchange = FakeExchange({"BTC/JPY": rows})
        with self.assertRaisesRegex(MarketFrameError, "stale"):
            BitbankPublicGateway(exchange=exchange).fetch_market_frames(
                ["BTC/JPY"], timeframe="5m", limit=6, now=1_800_000_000.0
            )

    def test_rejects_malformed_or_non_monotonic_history(self):
        bad = [
            [1_800_000_000_000, 1, 1, 1, 1, 1],
            [1_800_000_000_000, 1, 1, 1, 1, 1],
        ]
        exchange = FakeExchange({"BTC/JPY": bad})
        with self.assertRaises(MarketFrameError):
            BitbankPublicGateway(exchange=exchange).fetch_market_frames(
                ["BTC/JPY"], timeframe="5m", limit=2, now=1_800_000_100.0
            )

    def test_rejects_history_from_the_future(self):
        rows = [[1_800_010_000_000 + i * 300_000, 1, 1, 1, 1, 1] for i in range(6)]
        exchange = FakeExchange({"BTC/JPY": rows})
        with self.assertRaisesRegex(MarketFrameError, "future"):
            BitbankPublicGateway(exchange=exchange).fetch_market_frames(
                ["BTC/JPY"], timeframe="5m", limit=6, now=1_800_000_000.0
            )

    def test_rejects_nonfinite_history_timestamp(self):
        rows = [
            [1_800_000_000_000 + i * 300_000, 1, 1, 1, 1, 1]
            for i in range(6)
        ]
        rows[-1][0] = "nan"
        exchange = FakeExchange({"BTC/JPY": rows})
        with self.assertRaisesRegex(MarketFrameError, "invalid timestamp"):
            BitbankPublicGateway(exchange=exchange).fetch_market_frames(
                ["BTC/JPY"], timeframe="5m", limit=6, now=1_800_001_600.0
            )

    def test_rejects_too_small_history_limit_with_domain_error(self):
        exchange = FakeExchange({"BTC/JPY": []})
        with self.assertRaises(MarketFrameError):
            BitbankPublicGateway(exchange=exchange).fetch_market_frames(
                ["BTC/JPY"], timeframe="5m", limit=1, now=1_800_000_000.0
            )


if __name__ == "__main__":
    unittest.main()
