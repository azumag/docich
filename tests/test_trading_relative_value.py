from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.market_data import MarketFrame
from docich.trading.models import MarketInfo
from docich.trading.relative_value import scan_relative_value_opportunities

D = Decimal
NOW = 1_800_000_000.0

def frame(symbol, closes):
    return MarketFrame(symbol, 300, tuple(NOW-(len(closes)-i)*300 for i in range(len(closes))), tuple(D(str(x)) for x in closes), tuple(D("10") for _ in closes))

def market(symbol, base, quote="JPY"):
    return MarketInfo(symbol=symbol, base=base, quote=quote, spot=True, active=True)

class TestRelativeValue(unittest.TestCase):
    def test_generates_lagging_same_quote_candidate(self):
        markets={"BTC/JPY":market("BTC/JPY","BTC"),"ETH/JPY":market("ETH/JPY","ETH"),"SOL/JPY":market("SOL/JPY","SOL")}
        frames={
            "BTC/JPY":frame("BTC/JPY",[100,102,104,106,108,110,112]),
            "ETH/JPY":frame("ETH/JPY",[100,102,104,106,108,110,112]),
            "SOL/JPY":frame("SOL/JPY",[100,99,98,97,96,96,97]),
        }
        ops=scan_relative_value_opportunities(frames, markets, now=NOW)
        self.assertEqual([op.symbol for op in ops],["SOL/JPY"])
        self.assertEqual(ops[0].strategy_id,"relative-value-v1")
        self.assertEqual(ops[0].reason_code,"relative_value_lag")

    def test_requires_three_comparable_markets(self):
        markets={"BTC/JPY":market("BTC/JPY","BTC"),"ETH/JPY":market("ETH/JPY","ETH")}
        frames={"BTC/JPY":frame("BTC/JPY",[100,101,102,103,104,105,106]),"ETH/JPY":frame("ETH/JPY",[100,99,98,97,96,95,96])}
        self.assertEqual(scan_relative_value_opportunities(frames, markets, now=NOW),())

    def test_does_not_mix_quote_assets(self):
        markets={"BTC/JPY":market("BTC/JPY","BTC"),"ETH/JPY":market("ETH/JPY","ETH"),"SOL/USDT":market("SOL/USDT","SOL","USDT")}
        frames={"BTC/JPY":frame("BTC/JPY",[100,102,104,106,108,110,112]),"ETH/JPY":frame("ETH/JPY",[100,102,104,106,108,110,112]),"SOL/USDT":frame("SOL/USDT",[100,90,85,80,78,77,78])}
        self.assertEqual(scan_relative_value_opportunities(frames, markets, now=NOW),())

    def test_requires_stabilization_not_falling_knife(self):
        markets={"A/JPY":market("A/JPY","A"),"B/JPY":market("B/JPY","B"),"C/JPY":market("C/JPY","C")}
        frames={"A/JPY":frame("A/JPY",[100,102,104,106,108,110,112]),"B/JPY":frame("B/JPY",[100,102,104,106,108,110,112]),"C/JPY":frame("C/JPY",[100,99,98,97,96,95,94])}
        self.assertEqual(scan_relative_value_opportunities(frames, markets, now=NOW),())

if __name__ == "__main__": unittest.main()
