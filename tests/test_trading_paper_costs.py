from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.arbitrage import TopOfBook  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.models import AllocationDecision, MarketInfo  # noqa: E402
from docich.trading.paper import (  # noqa: E402
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_RATE,
    PaperBroker,
    resolve_fee_rate,
    resolve_slippage_bps,
)
from docich.trading.performance import realized_pnl_for_fill  # noqa: E402
from docich.trading.risk import CapitalPolicy  # noqa: E402
from docich.trading.status import build_public_status  # noqa: E402


D = Decimal


def market(
    symbol: str = "BTC/JPY",
    *,
    base_fee: str | None = "0.0005",
    quote_fee: str | None = "0.001",
) -> MarketInfo:
    return MarketInfo(
        symbol=symbol, base="BTC", quote="JPY", spot=True, active=True,
        taker_fee_rate_base=None if base_fee is None else D(base_fee),
        taker_fee_rate_quote=None if quote_fee is None else D(quote_fee),
    )


def decision(
    oid: str = "opp-1", *, side: str = "buy", price: str = "1000000",
    amount: str = "0.01",
) -> AllocationDecision:
    qty = D(amount)
    notional = qty * D(price)
    return AllocationDecision(
        opportunity_id=oid, strategy_id="momentum-v1", symbol="BTC/JPY",
        side=side, quote="JPY", amount=qty, price=D(price),
        quote_notional=notional, reference_notional=notional,
        reason_code="momentum_breakout",
    )


def deep_book() -> DepthBook:
    return DepthBook(
        "BTC/JPY",
        bids=(DepthLevel(D("1000000"), D("10")), DepthLevel(D("999000"), D("10"))),
        asks=(DepthLevel(D("1000000"), D("10")), DepthLevel(D("1001000"), D("10"))),
        as_of=1000.0,
    )


class TestPerMarketFees(unittest.TestCase):
    def test_buy_uses_base_fee(self):
        rate, source = resolve_fee_rate(market(), "buy")
        self.assertEqual((rate, source), (D("0.0005"), "market"))

    def test_sell_uses_quote_fee(self):
        rate, source = resolve_fee_rate(market(), "sell")
        self.assertEqual((rate, source), (D("0.001"), "market"))

    def test_missing_market_falls_back(self):
        rate, source = resolve_fee_rate(None, "buy")
        self.assertEqual((rate, source), (DEFAULT_TAKER_FEE_RATE, "fallback"))

    def test_missing_fee_metadata_falls_back(self):
        bare = MarketInfo(symbol="BTC/JPY", base="BTC", quote="JPY", spot=True, active=True)
        rate, source = resolve_fee_rate(bare, "sell")
        self.assertEqual((rate, source), (DEFAULT_TAKER_FEE_RATE, "fallback"))

    def test_broker_applies_market_fee_to_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperLedger(Path(tmp) / "paper.sqlite3")
            try:
                broker = PaperBroker(ledger, markets={"BTC/JPY": market()}, books={"BTC/JPY": deep_book()})
                fill = broker.fill(decision(), timestamp=1000.0)
                # Deep book: zero slippage, only the base taker fee widens the buy.
                self.assertEqual(fill.price, D("1000000") * D("1.0005"))
                assert fill.cost_context is not None
                self.assertEqual(fill.cost_context["fee_source"], "market")
                self.assertEqual(fill.cost_context["slippage_source"], "depth")
            finally:
                ledger.close()


class TestDepthSlippage(unittest.TestCase):
    def test_deep_book_has_no_slippage(self):
        bps, source = resolve_slippage_bps(amount=D("0.01"), side="buy", book=deep_book())
        self.assertEqual(source, "depth")
        self.assertEqual(bps, D("0"))

    def test_thin_book_widens_with_size(self):
        book = DepthBook(
            "BTC/JPY",
            bids=(DepthLevel(D("100"), D("10")),),
            asks=(DepthLevel(D("100"), D("1")), DepthLevel(D("102"), D("10"))),
            as_of=1000.0,
        )
        bps, source = resolve_slippage_bps(amount=D("2"), side="buy", book=book)
        self.assertEqual(source, "depth")
        # VWAP = (100*1 + 102*1)/2 = 101 -> 100 bps vs top 100.
        self.assertEqual(bps, D("100"))

    def test_insufficient_depth_falls_back(self):
        bps, source = resolve_slippage_bps(amount=D("100"), side="buy", book=deep_book())
        self.assertEqual((bps, source), (DEFAULT_SLIPPAGE_BPS, "fallback"))

    def test_missing_book_falls_back(self):
        bps, source = resolve_slippage_bps(amount=D("1"), side="sell", book=None)
        self.assertEqual((bps, source), (DEFAULT_SLIPPAGE_BPS, "fallback"))

    def test_top_of_book_quote_supported(self):
        top = TopOfBook("BTC/JPY", D("999"), D("1000"), 1000.0, bid_amount=D("5"), ask_amount=D("5"))
        bps, source = resolve_slippage_bps(amount=D("1"), side="sell", book=top)
        self.assertEqual((bps, source), (D("0"), "depth"))
        bps, source = resolve_slippage_bps(amount=D("50"), side="sell", book=top)
        self.assertEqual((bps, source), (DEFAULT_SLIPPAGE_BPS, "fallback"))

    def test_resolution_is_deterministic(self):
        first = resolve_slippage_bps(amount=D("2"), side="buy", book=deep_book())
        second = resolve_slippage_bps(amount=D("2"), side="buy", book=deep_book())
        self.assertEqual(first, second)


class TestCostAccounting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "paper.sqlite3"
        self.ledger = PaperLedger(self.db)
        self.broker = PaperBroker(
            self.ledger, markets={"BTC/JPY": market()}, books={"BTC/JPY": deep_book()},
        )

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_cost_breakdown_is_private_but_auditable(self):
        fill = self.broker.fill(decision(), timestamp=1000.0)
        stored = self.ledger.get_fill_for_opportunity("opp-1")
        assert stored is not None and stored.cost_context is not None
        self.assertEqual(stored.cost_context["fee_rate"], "0.0005")
        payload = build_public_status(
            worker_state="idle", last_cycle_at=1000.0, eligible_symbols=["BTC/JPY"],
            capital_reference=D("100000"), deployed_reference=D("10000"),
            open_positions={"BTC/JPY": D("0.01")}, recent_fills=[fill],
            skipped_reason_codes=[],
        )
        blob = json.dumps(payload, sort_keys=True)
        self.assertNotIn("cost_context", blob)
        self.assertNotIn("fee_rate", blob)
        self.assertNotIn("slippage", blob)

    def test_pnl_notification_dashboard_share_net_cost(self):
        buy = self.broker.fill(decision("buy-1"), timestamp=1000.0)
        sell_decision = decision("sell-1", side="sell")
        sell = self.broker.fill(
            sell_decision, timestamp=1001.0,
        )
        # Sell uses the quote fee on the bid side: 1000000 * (1 - 0.001).
        self.assertEqual(sell.price, D("1000000") * D("0.999"))
        pnl = realized_pnl_for_fill(self.db, sell.fill_id)
        self.assertIsNotNone(pnl)
        # Same net-cost inputs the dashboard/performance path reads.
        self.assertEqual(pnl, sell.amount * (sell.price - buy.price))
        assert pnl is not None and pnl < D("0")  # round trip cannot profit after costs

    def test_reference_notional_stays_gross(self):
        fill = self.broker.fill(decision(), timestamp=1000.0)
        self.assertEqual(fill.reference_notional, D("0.01") * D("1000000"))

    def test_capital_ceiling_unchanged(self):
        policy = CapitalPolicy()
        self.assertEqual(policy.max_opportunity_fraction, D("0.30"))
        self.assertEqual(policy.max_total_deployed_fraction, D("1"))

    def test_execution_uses_public_data_only(self):
        source = Path(__file__).resolve().parents[1] / "src" / "docich" / "trading" / "paper.py"
        text = source.read_text(encoding="utf-8").lower()
        for banned in ("ccxt", "private_get", "private_post", "api_key", "api secret"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
