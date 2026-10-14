from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.arbitrage import TopOfBook, find_triangle_symbols, scan_triangular_arbitrage
from docich.trading.models import MarketInfo

D=Decimal
NOW=1_800_000_000.0

def market(symbol,base,quote,fee="0.001"):
    return MarketInfo(symbol=symbol,base=base,quote=quote,spot=True,active=True,taker_fee_rate=D(fee))

def book(symbol,bid,ask,bid_amount="10",ask_amount="10"):
    return TopOfBook(symbol=symbol,bid=D(str(bid)),ask=D(str(ask)),as_of=NOW,bid_amount=D(str(bid_amount)),ask_amount=D(str(ask_amount)))

class TestTriangleDiscovery(unittest.TestCase):
    def test_jpy_star_has_no_triangle(self):
        markets={"BTC/JPY":market("BTC/JPY","BTC","JPY"),"ETH/JPY":market("ETH/JPY","ETH","JPY"),"SOL/JPY":market("SOL/JPY","SOL","JPY")}
        self.assertEqual(find_triangle_symbols(markets),())

    def test_market_order_disabled_market_is_not_a_triangle_leg(self):
        markets={
            "BTC/JPY": MarketInfo("BTC/JPY","BTC","JPY",True,True,taker_fee_rate=D("0.001"),market_order_enabled=False),
            "ETH/BTC": market("ETH/BTC","ETH","BTC"),
            "ETH/JPY": market("ETH/JPY","ETH","JPY"),
        }
        self.assertEqual(find_triangle_symbols(markets),())
        books={
            "BTC/JPY": book("BTC/JPY",99,100),
            "ETH/BTC": book("ETH/BTC",0.049,0.05),
            "ETH/JPY": book("ETH/JPY",5.2,5.3),
        }
        self.assertEqual(scan_triangular_arbitrage(markets,books,now=NOW),())

    def test_finds_real_three_market_cycle(self):
        markets={"BTC/JPY":market("BTC/JPY","BTC","JPY"),"ETH/BTC":market("ETH/BTC","ETH","BTC"),"ETH/JPY":market("ETH/JPY","ETH","JPY")}
        self.assertEqual(set(find_triangle_symbols(markets)),{"BTC/JPY","ETH/BTC","ETH/JPY"})

class TestTrianglePricing(unittest.TestCase):
    def triangle(self,fee="0.001"):
        return {"BTC/JPY":market("BTC/JPY","BTC","JPY",fee),"ETH/BTC":market("ETH/BTC","ETH","BTC",fee),"ETH/JPY":market("ETH/JPY","ETH","JPY",fee)}

    def test_emits_fee_aware_profitable_cycle(self):
        markets=self.triangle("0.001")
        books={"BTC/JPY":book("BTC/JPY",99,100),"ETH/BTC":book("ETH/BTC",0.049,0.05),"ETH/JPY":book("ETH/JPY",5.2,5.3)}
        routes=scan_triangular_arbitrage(markets,books,now=NOW,min_net_edge_bps=D("20"))
        self.assertTrue(routes)
        self.assertGreater(routes[0].net_edge_bps,D("20"))
        self.assertEqual(len(routes[0].legs),3)
        self.assertGreater(routes[0].max_start_amount,D("0"))
        self.assertLess(routes[0].max_start_amount,D("60"))

    def test_fees_can_erase_apparent_edge(self):
        markets=self.triangle("0.02")
        books={"BTC/JPY":book("BTC/JPY",99,100),"ETH/BTC":book("ETH/BTC",0.049,0.05),"ETH/JPY":book("ETH/JPY",5.2,5.3)}
        self.assertEqual(scan_triangular_arbitrage(markets,books,now=NOW,min_net_edge_bps=D("20")),())

    def test_missing_book_fails_closed(self):
        markets=self.triangle()
        books={"BTC/JPY":book("BTC/JPY",99,100),"ETH/BTC":book("ETH/BTC",0.049,0.05)}
        self.assertEqual(scan_triangular_arbitrage(markets,books,now=NOW),())

    def test_stale_book_fails_closed(self):
        markets=self.triangle()
        books={"BTC/JPY":TopOfBook("BTC/JPY",D("99"),D("100"),NOW-10),"ETH/BTC":book("ETH/BTC",0.049,0.05),"ETH/JPY":book("ETH/JPY",5.2,5.3)}
        self.assertEqual(scan_triangular_arbitrage(markets,books,now=NOW,max_book_age_seconds=2),())

if __name__ == "__main__": unittest.main()
