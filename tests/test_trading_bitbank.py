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

    def test_ccxt_is_optional_and_missing_dependency_has_stable_error(self):
        with patch("docich.trading.exchanges.bitbank_ccxt.importlib.import_module", side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(CCXTUnavailableError, "requirements-trading"):
                BitbankPublicGateway()

    def test_gateway_exposes_no_order_or_private_methods(self):
        gateway = BitbankPublicGateway(exchange=FakeExchange({}))
        for method in ("create_order", "cancel_order", "fetch_balance", "fetch_orders"):
            self.assertFalse(hasattr(gateway, method), method)


if __name__ == "__main__":
    unittest.main()
