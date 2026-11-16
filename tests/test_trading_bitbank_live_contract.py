"""The bitbank reader and the mock exchange read the same world the same way, and the L2b executor
works on top of the bitbank reader (Issue #3623, L3).  The mock is rendered into bitbank's JSON shape;
that rendering is itself an assumption (see the module notes), so this proves consistency, not truth."""
import random
from decimal import Decimal as D

import pytest

from bitbank_live_support import render_bitbank
from docich.trading.exchanges.bitbank_live.reader import BitbankReader
from docich.trading.live import executor as live_executor
from docich.trading.live import reconcile, views
from docich.trading.live.exchange import Capabilities, ExchangeError, OrderRequest
from docich.trading.live.mock_exchange import MockFees
from test_trading_live_support import Harness, Workspace, block_network

NO_CLIENT_ID = Capabilities(client_order_id=False, fetch_by_client_id=False, fills_by_order=True)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


FEE_VARIANTS = {
    "quote_taker": MockFees(maker_rate=D("0"), taker_rate=D("0.001")),
    "base_fee": MockFees(maker_rate=D("0"), taker_rate=D("0.001"), currency="base"),
    "maker_rebate": MockFees(maker_rate=D("-0.0002"), taker_rate=D("0.0012")),
}


def _bitbank_harness(ws, **kw):
    h = Harness(ws, caps=NO_CLIENT_ID, **kw)
    reader = BitbankReader(render_bitbank(h.mock), symbols=h.symbols, clock=h.clock.seconds)
    h.reader = reader
    h.ex = live_executor.OrderExecutor(h.ledger, h.lease, reader=reader, sender=h.mock, enable_check=h._gate,
                                       clock=h.clock.seconds, symbols=h.symbols, config=h.config)
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
    return h


def _same_order(a, b):
    return (a.exchange_order_id, a.symbol, a.side, a.order_type, a.amount, a.price, a.status, a.executed_amount,
            a.remaining_amount, a.average_price, a.ordered_at_ms) == (
        b.exchange_order_id, b.symbol, b.side, b.order_type, b.amount, b.price, b.status, b.executed_amount,
        b.remaining_amount, b.average_price, b.ordered_at_ms)


def _random_world(h, rng):
    """Random submits, outside takers and cancels.  Returns what happened (to prove the run is not vacuous)."""
    seen = {"submits": 0, "cancels": 0, "takes": 0}
    for _ in range(24):
        kind = rng.random()
        try:
            if kind < 0.5:
                h.mock.submit_order(OrderRequest("OAS/JPY", rng.choice(["buy", "sell"]), "limit",
                                                 D(rng.randint(1, 6)), D(rng.choice(["90", "100", "101"])), None))
                seen["submits"] += 1
            elif kind < 0.75:
                h.mock.trade_against("OAS/JPY", taker_side=rng.choice(["sell", "buy"]),
                                     price=rng.choice(["90", "100", "101"]), amount=str(rng.randint(1, 8)))
                seen["takes"] += 1
            else:
                open_orders = h.mock.fetch_open_orders("OAS/JPY").orders
                if open_orders:
                    h.mock.cancel_order("OAS/JPY", rng.choice(open_orders).exchange_order_id)
                    seen["cancels"] += 1
        except ExchangeError:
            pass                      # a rejected submit (funds, step) is part of the world
        h.clock.advance(rng.choice([1, 5]))
    return seen


def _compare(h):
    mine, theirs = h.reader, h.mock
    a, b = mine.fetch_balance().assets, theirs.fetch_balance().assets
    assert {k: (v.total, v.free, v.locked) for k, v in a.items()} == {k: (v.total, v.free, v.locked)
                                                                     for k, v in b.items()}
    opens_a, opens_b = mine.fetch_open_orders("OAS/JPY"), theirs.fetch_open_orders("OAS/JPY")
    assert opens_a.complete and len(opens_a.orders) == len(opens_b.orders)
    for x, y in zip(opens_a.orders, opens_b.orders):
        assert _same_order(x, y)
    for order in theirs.all_orders():
        assert _same_order(mine.fetch_order("OAS/JPY", order.exchange_order_id), order)
    fa, fb = mine.fetch_fills("OAS/JPY", since_ms=0), theirs.fetch_fills("OAS/JPY", since_ms=0)
    # a zero fee has no meaningful currency (the mock labels it base, the parser quote)
    key = lambda f: (f.trade_id, f.exchange_order_id, f.side, f.amount, f.price, f.maker_taker, f.fee.status,
                     f.fee.amount, None if f.fee.amount == 0 else f.fee.currency, f.executed_at_ms)
    assert fa.complete and [key(f) for f in fa.fills] == [key(f) for f in fb.fills]
    ra, rb = mine.market_rules("OAS/JPY"), theirs.market_rules("OAS/JPY")
    assert (ra.amount_step, ra.price_tick, ra.min_amount, ra.base_unit, ra.quote_unit) == (
        rb.amount_step, rb.price_tick, rb.min_amount, rb.base_unit, rb.quote_unit)
    return len(fb.fills), [o.status.value for o in theirs.all_orders()]


@pytest.mark.parametrize("variant", sorted(FEE_VARIANTS))
@pytest.mark.parametrize("seed", range(20))
def test_reader_and_mock_agree_after_random_operations(ws, seed, variant):
    h = _bitbank_harness(ws, fees=FEE_VARIANTS[variant])
    try:
        _random_world(h, random.Random(seed))
        _compare(h)
    finally:
        h.close()


def test_the_random_worlds_really_contain_fills_partial_fills_cancels_and_both_fee_currencies(ws):
    totals = {"fills": 0, "partial": 0, "canceled": 0, "base_fee": 0, "rebate": 0}
    for variant in sorted(FEE_VARIANTS):
        for seed in range(20):
            workspace = Workspace()
            h = _bitbank_harness(workspace, fees=FEE_VARIANTS[variant])
            try:
                _random_world(h, random.Random(seed))
                fills, statuses = _compare(h)
                totals["fills"] += fills
                totals["partial"] += statuses.count("PARTIALLY_FILLED") + statuses.count("CANCELED_PARTIALLY_FILLED")
                totals["canceled"] += sum(s.startswith("CANCELED") for s in statuses)
                for fill in h.mock.all_fills():
                    totals["base_fee"] += fill.fee.currency == "OAS"
                    totals["rebate"] += fill.fee.amount < 0
            finally:
                h.close()
                workspace.cleanup()
    assert all(v > 0 for v in totals.values()), totals


# ---- the L2b executor on top of the bitbank reader (list matching is the only way, no client ids) ----
def test_a_filled_buy_and_sell_through_the_bitbank_reader(ws):
    h = _bitbank_harness(ws)
    try:
        buy, _ = h.send(side="buy", key="b1", amount="10", price="100")
        assert h.state(buy.intent_id) == "FILLED"
        sell, _ = h.send(side="sell", key="s1", amount="10", price="99")
        assert h.state(sell.intent_id) == "FILLED"
        assert h.ex.step().balance_checked and h.trips() == []
    finally:
        h.close()


def test_a_lost_response_is_found_by_matching_through_the_bitbank_reader(ws):
    h = _bitbank_harness(ws)
    try:
        h.mock.faults.push("submit", "LOST_RESPONSE")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="100")
        h.ex.step()
        assert h.state(intent.intent_id) == "FILLED" and len(h.mock.submissions) == 1 and h.trips() == []
    finally:
        h.close()


def test_a_lost_request_becomes_not_created_through_the_bitbank_reader(ws):
    h = _bitbank_harness(ws)
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="100")
        for _ in range(3):
            h.advance(121)
            h.ex.step()
        assert h.state(intent.intent_id) == "NOT_CREATED" and h.mock.submissions == [] and h.trips() == []
    finally:
        h.close()


def test_partial_fill_then_cancel_and_a_restart_through_the_bitbank_reader(ws):
    h = _bitbank_harness(ws)
    try:
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
        h.crash()
        h.reader = BitbankReader(render_bitbank(h.mock), symbols=h.symbols, clock=h.clock.seconds)
        h.ex = live_executor.OrderExecutor(h.ledger, h.lease, reader=h.reader, sender=h.mock, enable_check=h._gate,
                                           clock=h.clock.seconds, symbols=h.symbols, config=h.config)
        reconcile.on_startup(h.ex)
        assert h.state(intent.intent_id) == "PARTIALLY_FILLED"
        h.ex.cancel(intent.intent_id)
        assert h.state(intent.intent_id) == "CANCELED" and views.position_net_base(h.ledger, intent.position_id) == 4
        assert h.trips() == []
    finally:
        h.close()


def test_a_hidden_fee_through_the_bitbank_reader_is_unknown_not_a_crash(ws):
    h = _bitbank_harness(ws)
    try:
        h.mock.faults.push("fill", "FEE_MISSING")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="100")
        for _ in range(5):
            h.ex.step()
        assert h.state(intent.intent_id) == "FILLED" and h.trips() == ["fee_missing"]
    finally:
        h.close()
