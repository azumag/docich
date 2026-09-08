from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.exchanges.bitbank_ccxt import (  # noqa: E402
    BitbankPublicGateway,
    CCXTUnavailableError,
)
from docich.trading.arbitrage import ArbitrageDataError  # noqa: E402
from docich.trading.depth import DepthBook  # noqa: E402


class FakeExchange:
    def __init__(self, markets):
        self.markets = markets
        self.calls = 0

    def load_markets(self):
        self.calls += 1
        return self.markets


class TestBitbankPublicGateway(unittest.TestCase):
    def test_discovers_active_spot_markets_without_jpy_hardcoding(self):
        exchange = FakeExchange({
            "BTC/JPY": {
                "symbol": "BTC/JPY", "base": "BTC", "quote": "JPY",
                "spot": True, "active": True, "precision": {"amount": 0.0001},
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": 1}}, "info": {},
            },
            "ETH/USDT": {
                "symbol": "ETH/USDT", "base": "ETH", "quote": "USDT",
                "spot": True, "active": True, "precision": {"amount": 0.001},
                "limits": {"amount": {"min": 0.001}, "cost": {"min": 1}}, "info": {},
            },
        })
        markets = BitbankPublicGateway(exchange=exchange).discover_markets()
        self.assertEqual(set(markets), {"BTC/JPY", "ETH/USDT"})
        self.assertEqual(markets["BTC/JPY"].amount_step, Decimal("0.0001"))
        self.assertEqual(exchange.calls, 1)

    def test_integer_amount_precision_is_a_tick_size_not_decimal_digits(self):
        exchange = FakeExchange({
            "WHOLE/JPY": {
                "symbol": "WHOLE/JPY", "base": "WHOLE", "quote": "JPY",
                "spot": True, "active": True, "precision": {"amount": 1},
                "limits": {"amount": {"min": 1}, "cost": {"min": None}},
                "info": {"is_enabled": True, "stop_order": False, "stop_buy_order": False},
            }
        })
        markets = BitbankPublicGateway(exchange=exchange).discover_markets()
        self.assertEqual(markets["WHOLE/JPY"].amount_step, Decimal("1"))

    def test_rejects_inactive_nonspot_and_malformed_markets(self):
        exchange = FakeExchange({
            "OFF/JPY": {"symbol": "OFF/JPY", "base": "OFF", "quote": "JPY", "spot": True, "active": False},
            "SWAP/JPY": {"symbol": "SWAP/JPY", "base": "SWAP", "quote": "JPY", "spot": False, "active": True},
            "BAD": {"symbol": "BAD", "base": "BAD", "quote": "", "spot": True, "active": True},
        })
        self.assertEqual(BitbankPublicGateway(exchange=exchange).discover_markets(), {})

    def test_rejects_bitbank_specific_disabled_or_order_stopped_markets(self):
        exchange = FakeExchange({
            "A/JPY": {
                "symbol": "A/JPY", "base": "A", "quote": "JPY", "spot": True, "active": True,
                "info": {"is_enabled": False},
            },
            "B/JPY": {
                "symbol": "B/JPY", "base": "B", "quote": "JPY", "spot": True, "active": True,
                "info": {"enable_order": False},
            },
            "C/JPY": {
                "symbol": "C/JPY", "base": "C", "quote": "JPY", "spot": True, "active": True,
                "info": {"is_enabled": True, "enable_order": True},
            },
        })
        markets = BitbankPublicGateway(exchange=exchange).discover_markets()
        self.assertEqual(set(markets), {"C/JPY"})

    def test_rejects_bitbank_markets_with_global_or_buy_order_stop(self):
        exchange = FakeExchange({
            "STOP/JPY": {
                "symbol": "STOP/JPY", "base": "STOP", "quote": "JPY",
                "spot": True, "active": True,
                "info": {"is_enabled": True, "stop_order": True, "stop_buy_order": True},
            },
            "BUYOFF/JPY": {
                "symbol": "BUYOFF/JPY", "base": "BUYOFF", "quote": "JPY",
                "spot": True, "active": True,
                "info": {"is_enabled": True, "stop_order": False, "stop_buy_order": True},
            },
            "OK/JPY": {
                "symbol": "OK/JPY", "base": "OK", "quote": "JPY",
                "spot": True, "active": True,
                "info": {"is_enabled": True, "stop_order": False, "stop_buy_order": False},
            },
        })
        markets = BitbankPublicGateway(exchange=exchange).discover_markets()
        self.assertEqual(set(markets), {"OK/JPY"})


    def test_discovers_taker_fee_rate_for_fee_aware_arbitrage(self):
        exchange = FakeExchange({
            "BTC/JPY": {
                "symbol": "BTC/JPY", "base": "BTC", "quote": "JPY",
                "spot": True, "active": True, "taker": 0.009,
                "precision": {"amount": 0.0001},
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": 1}},
                "info": {
                    "is_enabled": True, "stop_order": False, "stop_buy_order": False,
                    "taker_fee_rate_base": "0.002", "taker_fee_rate_quote": "0.001",
                },
            }
        })
        markets = BitbankPublicGateway(exchange=exchange).discover_markets()
        self.assertEqual(markets["BTC/JPY"].taker_fee_rate_base, Decimal("0.002"))
        self.assertEqual(markets["BTC/JPY"].taker_fee_rate_quote, Decimal("0.001"))
        self.assertEqual(markets["BTC/JPY"].taker_fee_rate, Decimal("0.001"))


    def test_fetch_depth_books_preserves_multiple_public_levels(self):
        class BookExchange(FakeExchange):
            def fetch_order_book(self, symbol, limit=20):
                self.last_book_call = (symbol, limit)
                return {
                    "symbol": symbol, "timestamp": 1_800_000_000_000,
                    "bids": [[99, 2], [98, 3]],
                    "asks": [[100, 4], [101, 5]],
                }
        exchange = BookExchange({})
        books = BitbankPublicGateway(exchange=exchange).fetch_depth_books(["BTC/JPY"], now=1_800_000_001.0, limit=20)
        book = books["BTC/JPY"]
        self.assertIsInstance(book, DepthBook)
        self.assertEqual([level.price for level in book.bids], [Decimal("99"), Decimal("98")])
        self.assertEqual([level.amount for level in book.asks], [Decimal("4"), Decimal("5")])
        self.assertEqual(exchange.last_book_call, ("BTC/JPY", 20))


    def test_fetch_depth_books_enforces_local_level_limit_when_exchange_ignores_it(self):
        class BookExchange(FakeExchange):
            def fetch_order_book(self, symbol, limit=2):
                return {
                    "symbol": symbol, "timestamp": 1_800_000_000_000,
                    "bids": [[99, 1], [98, 1], [97, 1]],
                    "asks": [[100, 1], [101, 1], [102, 1]],
                }
        book = BitbankPublicGateway(exchange=BookExchange({})).fetch_depth_books(
            ["BTC/JPY"], now=1_800_000_001.0, limit=2
        )["BTC/JPY"]
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(len(book.asks), 2)

    def test_fetch_depth_books_rejects_unsorted_or_zero_levels(self):
        class BookExchange(FakeExchange):
            def fetch_order_book(self, symbol, limit=20):
                return {
                    "symbol": symbol, "timestamp": 1_800_000_000_000,
                    "bids": [[98, 2], [99, 3]],
                    "asks": [[100, 0], [101, 5]],
                }
        with self.assertRaises(ArbitrageDataError):
            BitbankPublicGateway(exchange=BookExchange({})).fetch_depth_books(["BTC/JPY"], now=1_800_000_001.0)

    def test_fetch_top_books_normalizes_public_best_prices(self):
        class BookExchange(FakeExchange):
            def fetch_order_book(self, symbol, limit=5):
                self.last_book_call = (symbol, limit)
                return {"symbol": symbol, "timestamp": 1_800_000_000_000, "bids": [[99, 2]], "asks": [[100, 3]]}
        exchange = BookExchange({})
        books = BitbankPublicGateway(exchange=exchange).fetch_top_books(["BTC/JPY"], now=1_800_000_001.0, limit=5)
        self.assertEqual(books["BTC/JPY"].bid, Decimal("99"))
        self.assertEqual(books["BTC/JPY"].ask, Decimal("100"))
        self.assertEqual(books["BTC/JPY"].bid_amount, Decimal("2"))
        self.assertEqual(books["BTC/JPY"].ask_amount, Decimal("3"))
        self.assertEqual(exchange.last_book_call, ("BTC/JPY", 5))


    def test_fetch_top_books_rejects_zero_best_level_amount(self):
        class BookExchange(FakeExchange):
            def fetch_order_book(self, symbol, limit=5):
                return {"symbol": symbol, "timestamp": 1_800_000_000_000, "bids": [[99, 0]], "asks": [[100, 1]]}
        with self.assertRaises(ArbitrageDataError):
            BitbankPublicGateway(exchange=BookExchange({})).fetch_top_books(["BTC/JPY"], now=1_800_000_001.0)

    def test_fetch_top_books_rejects_missing_exchange_timestamp(self):
        class BookExchange(FakeExchange):
            def fetch_order_book(self, symbol, limit=5):
                return {"symbol": symbol, "timestamp": None, "bids": [[99, 1]], "asks": [[100, 1]]}
        with self.assertRaises(ArbitrageDataError):
            BitbankPublicGateway(exchange=BookExchange({})).fetch_top_books(["BTC/JPY"], now=1_800_000_001.0)


    def test_rejects_bitbank_market_when_sell_orders_are_stopped(self):
        exchange = FakeExchange({
            "SELLSTOP/JPY": {
                "symbol": "SELLSTOP/JPY", "base": "SELLSTOP", "quote": "JPY",
                "spot": True, "active": True,
                "info": {
                    "is_enabled": True, "stop_order": False,
                    "stop_buy_order": False, "stop_sell_order": True,
                },
            }
        })
        self.assertEqual(BitbankPublicGateway(exchange=exchange).discover_markets(), {})

    def test_ccxt_is_optional_and_missing_dependency_has_stable_error(self):
        with patch("docich.trading.exchanges.bitbank_ccxt.importlib.import_module", side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(CCXTUnavailableError, "requirements-trading"):
                BitbankPublicGateway()

    def test_gateway_exposes_no_order_or_private_methods(self):
        gateway = BitbankPublicGateway(exchange=FakeExchange({}))
        for method in ("create_order", "cancel_order", "fetch_balance", "fetch_orders"):
            self.assertFalse(hasattr(gateway, method), method)


class TestBitbankSettlementMetadata(unittest.TestCase):
    def test_unit_amount_is_authoritative_and_market_order_stop_is_preserved(self):
        exchange = FakeExchange({
            "BTC/JPY": {
                "symbol": "BTC/JPY", "base": "BTC", "quote": "JPY",
                "spot": True, "active": True, "precision": {"amount": 0.0001},
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": None}},
                "info": {
                    "is_enabled": True, "stop_order": False, "stop_buy_order": False, "stop_sell_order": False,
                    "stop_market_order": True, "unit_amount": "0.001",
                    "taker_fee_rate_base": "0", "taker_fee_rate_quote": "0.001",
                },
            }
        })
        market = BitbankPublicGateway(exchange=exchange).discover_markets()["BTC/JPY"]
        self.assertEqual(market.min_amount, Decimal("0.001"))
        self.assertEqual(market.amount_step, Decimal("0.0001"))
        self.assertFalse(market.market_order_enabled)

    def test_fetches_public_circuit_break_status(self):
        class CircuitExchange(FakeExchange):
            def publicGetPairCircuitBreakInfo(self, params):
                self.last_circuit_params = params
                return {"success": 1, "data": {
                    "mode": "NONE", "fee_type": "NORMAL", "timestamp": 1_800_000_000_000,
                }}
        exchange = CircuitExchange({})
        statuses = BitbankPublicGateway(exchange=exchange).fetch_circuit_break_statuses(["BTC/JPY"])
        self.assertEqual(statuses["BTC/JPY"].mode, "NONE")
        self.assertEqual(statuses["BTC/JPY"].fee_type, "NORMAL")
        self.assertEqual(statuses["BTC/JPY"].as_of, 1_800_000_000.0)
        self.assertEqual(exchange.last_circuit_params, {"pair": "btc_jpy"})


if __name__ == "__main__":
    unittest.main()
