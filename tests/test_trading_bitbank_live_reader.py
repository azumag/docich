"""BitbankReader over a fake transport (Issue #3623, L3). Synthetic data, no network."""
from decimal import Decimal as D

import pytest

from bitbank_live_support import (SYMBOLS, T0, FakeTransport, assets_payload, default_script, order_row,
                                  pairs_payload, trade_row)
from docich.trading.exchanges.bitbank_live import errors as er
from docich.trading.exchanges.bitbank_live import reader as rd
from docich.trading.live.exchange import (ExchangeAmbiguous, ExchangeDataError, ExchangeRejected, FeeStatus,
                                          OrderNotFound, OrderStatus)


def make(script=None, **kw):
    transport = FakeTransport(script or default_script())
    reader = rd.BitbankReader(transport, symbols=SYMBOLS, clock=lambda: 50.0, **kw)
    return reader, transport


def calls(transport, endpoint):
    return [params for name, params in transport.calls if name == endpoint]


def test_building_reads_pairs_and_assets_and_nothing_else():
    reader, transport = make()
    assert [name for name, _ in transport.calls] == ["assets", "pairs"]
    assert reader.market_rules("OAS/JPY").amount_step == D(1) and reader.capabilities == rd.CAPABILITIES
    assert not reader.capabilities.client_order_id and reader.capabilities.fills_by_order


def test_market_rules_never_touch_the_network_and_unknown_symbols_are_data_errors():
    reader, transport = make()
    transport.calls.clear()
    reader.market_rules("XYM/JPY")
    assert transport.calls == []
    with pytest.raises(ExchangeDataError):
        reader.market_rules("BTC/JPY")


@pytest.mark.parametrize("script", [
    {"pairs": ExchangeAmbiguous("down")},
    {"assets": ExchangeAmbiguous("down")},
    {"pairs": pairs_payload(symbols=("OAS/JPY",))},
    {"pairs": pairs_payload(**{"OAS/JPY": {"is_enabled": False}})},
    {"assets": {"assets": []}},
])
def test_a_reader_that_cannot_load_the_rules_is_never_built(script):
    with pytest.raises(rd.ReaderUnavailable) as err:
        make(default_script(**script))
    assert "SENTINEL" not in str(err.value)


def test_client_id_lookup_is_not_supported():
    reader, _ = make()
    with pytest.raises(ExchangeDataError):
        reader.fetch_order_by_client_id("OAS/JPY", "d1")


def test_balance_is_read_in_full():
    reader, transport = make(default_script(assets=assets_payload(jpy=("500", "50"), coins={"OAS": ("2", "1"),
                                                                                            "XYM": ("0", "0")})))
    snap = reader.fetch_balance()
    assert snap.assets["JPY"].free == D(450) and snap.assets["OAS"].total == D(2) and snap.fetched_at == 50.0


def test_fetch_order_checks_that_it_got_what_it_asked_for():
    reader, transport = make(default_script(order=order_row(order_id=1000001)))
    assert reader.fetch_order("OAS/JPY", "1000001").exchange_order_id == "1000001"
    assert calls(transport, "order")[-1] == {"pair": "oas_jpy", "order_id": "1000001"}
    with pytest.raises(ExchangeDataError):
        reader.fetch_order("OAS/JPY", "999")                              # answers with another order
    transport.script["order"] = order_row(pair="xym_jpy")
    with pytest.raises(ExchangeDataError):
        reader.fetch_order("OAS/JPY", "1000001")                          # answers with another pair


def test_order_not_found_passes_through_unchanged():
    reader, _ = make(default_script(order=er.classify(endpoint="order", body='{"success":0,"data":{"code":50009}}')))
    with pytest.raises(OrderNotFound):
        reader.fetch_order("OAS/JPY", "1")


def test_open_orders_are_complete_only_below_the_page_size():
    rows = [order_row(order_id=1000001 + i) for i in range(3)]
    reader, transport = make(default_script(active_orders={"orders": rows}), open_orders_count=5)
    page = reader.fetch_open_orders("OAS/JPY")
    assert len(page.orders) == 3 and page.complete
    assert calls(transport, "active_orders")[-1] == {"pair": "oas_jpy", "count": 5} and "since" not in str(calls(
        transport, "active_orders"))
    reader, _ = make(default_script(active_orders={"orders": rows}), open_orders_count=3)
    assert not reader.fetch_open_orders("OAS/JPY").complete                # exactly a full page: maybe cut off
    reader, _ = make(default_script(active_orders={"orders": [order_row(pair="xym_jpy")]}))
    with pytest.raises(ExchangeDataError):
        reader.fetch_open_orders("OAS/JPY")


def _trades(n, start=T0):
    return {"trades": [trade_row(trade_id=9000 + i, executed_at=start + i * 1000) for i in range(n)]}


def test_fills_are_cut_locally_by_since_and_returned_oldest_first():
    reader, transport = make(default_script(trade_history=_trades(5)))
    page = reader.fetch_fills("OAS/JPY", since_ms=T0 + 2000)
    assert [f.executed_at_ms for f in page.fills] == [T0 + 2000, T0 + 3000, T0 + 4000] and page.complete
    sent = calls(transport, "trade_history")[-1]
    assert sent == {"pair": "oas_jpy", "count": 1000, "order": "desc"}      # no since/end is ever sent


def test_a_full_page_is_complete_only_if_it_already_reaches_back_past_since():
    reader, _ = make(default_script(trade_history=_trades(4)), fills_count=4)
    assert not reader.fetch_fills("OAS/JPY", since_ms=T0 - 1).complete      # the page is full and starts after since
    assert reader.fetch_fills("OAS/JPY", since_ms=T0 + 2500).complete       # oldest in the page precedes since
    reader, _ = make(default_script(trade_history=_trades(3)), fills_count=4)
    assert reader.fetch_fills("OAS/JPY", since_ms=0).complete               # not full


def test_fills_for_one_order_use_order_id_and_must_all_belong_to_it():
    reader, transport = make(default_script(trade_history={"trades": [trade_row(order_id=1000001)]}))
    reader.fetch_fills("OAS/JPY", since_ms=0, exchange_order_id="1000001")
    assert calls(transport, "trade_history")[-1]["order_id"] == "1000001"
    reader, _ = make(default_script(trade_history={"trades": [trade_row(order_id=7)]}))
    with pytest.raises(ExchangeDataError):
        reader.fetch_fills("OAS/JPY", since_ms=0, exchange_order_id="1000001")
    reader, _ = make(default_script(trade_history={"trades": [trade_row(pair="xym_jpy")]}))
    with pytest.raises(ExchangeDataError):
        reader.fetch_fills("OAS/JPY", since_ms=0)


def test_one_broken_trade_fails_the_whole_page_rather_than_being_dropped():
    rows = [trade_row(trade_id=1), trade_row(trade_id=2, amount="oops")]
    reader, _ = make(default_script(trade_history={"trades": rows}))
    with pytest.raises(ExchangeDataError):
        reader.fetch_fills("OAS/JPY", since_ms=0)


def test_a_trade_with_an_unclear_fee_is_still_a_fill_with_unknown_fee():
    row = trade_row(fee_amount_quote=None)
    reader, _ = make(default_script(trade_history={"trades": [row]}))
    assert reader.fetch_fills("OAS/JPY", since_ms=0).fills[0].fee.status is FeeStatus.UNKNOWN


def test_the_book_is_read_with_the_documented_endpoint():
    reader, transport = make()
    book = reader.fetch_order_book("OAS/JPY", depth=1)
    assert book.asks[0][0] == D(101) and calls(transport, "depth")[-1] == {"pair": "oas_jpy"}


def test_health_tracks_failures_and_recovers():
    script = default_script(assets=[ExchangeAmbiguous("x"), assets_payload()])
    script["assets"][0].health_kind = "auth"
    transport = FakeTransport(default_script())
    reader = rd.BitbankReader(transport, symbols=SYMBOLS, clock=lambda: 7.0)
    assert reader.health() == rd.ReaderHealth(None, 0, 7.0)
    bad = ExchangeAmbiguous("x")
    bad.health_kind = "auth"
    transport.script["assets"] = bad
    for n in (1, 2):
        with pytest.raises(ExchangeAmbiguous):
            reader.fetch_balance()
        assert reader.health().last_error_kind == "auth" and reader.health().consecutive_failures == n
    transport.script["assets"] = assets_payload()
    reader.fetch_balance()
    assert reader.health() == rd.ReaderHealth(None, 0, 7.0)
    transport.script["assets"] = {"assets": [{"asset": "jpy"}]}
    with pytest.raises(ExchangeDataError):
        reader.fetch_balance()
    assert reader.health().last_error_kind == "data"


def test_the_reader_is_not_a_sender():
    reader, _ = make()
    for name in ("submit_order", "cancel_order", "kind", "create_order", "withdraw"):
        assert not hasattr(reader, name)


@pytest.mark.parametrize("raised", [ExchangeRejected("code"), RuntimeError("boom"), KeyError("k"),
                                    ExchangeAmbiguous("x"), OrderNotFound("1")])
@pytest.mark.parametrize("method", ["fetch_balance", "fetch_open_orders", "fetch_fills", "fetch_order_book"])
def test_a_read_never_raises_a_rejection_or_a_stray_exception(method, raised):
    reader, transport = make()
    endpoint = {"fetch_balance": "assets", "fetch_open_orders": "active_orders", "fetch_fills": "trade_history",
                "fetch_order_book": "depth"}[method]
    transport.script[endpoint] = raised
    args = {"fetch_balance": (), "fetch_open_orders": ("OAS/JPY",), "fetch_fills": ("OAS/JPY",),
            "fetch_order_book": ("OAS/JPY",)}[method]
    kwargs = {"since_ms": 0} if method == "fetch_fills" else {}
    with pytest.raises(ExchangeAmbiguous) as err:
        getattr(reader, method)(*args, **kwargs)
    assert not isinstance(err.value, (ExchangeRejected, OrderNotFound))


def test_order_not_found_is_only_allowed_through_for_an_order_lookup():
    reader, transport = make()
    transport.script["order"] = OrderNotFound("1")
    with pytest.raises(OrderNotFound):
        reader.fetch_order("OAS/JPY", "1")
    transport.script["order"] = ExchangeRejected("c")
    with pytest.raises(ExchangeAmbiguous):
        reader.fetch_order("OAS/JPY", "1")
    transport.script["order"] = RuntimeError("x")
    with pytest.raises(ExchangeAmbiguous):
        reader.fetch_order("OAS/JPY", "1")


def test_a_bad_row_in_old_history_is_tolerated_but_a_bad_row_inside_the_window_fails():
    old_bad = trade_row(trade_id=1, executed_at=T0, amount="oops")           # before since
    good = trade_row(trade_id=2, executed_at=T0 + 5000)
    reader, _ = make(default_script(trade_history={"trades": [good, old_bad]}))
    page = reader.fetch_fills("OAS/JPY", since_ms=T0 + 1000)
    assert [f.trade_id for f in page.fills] == ["2"] and page.complete
    reader, _ = make(default_script(trade_history={"trades": [trade_row(trade_id=3, executed_at=T0 + 5000,
                                                                        amount="oops"), good]}))
    with pytest.raises(ExchangeDataError):
        reader.fetch_fills("OAS/JPY", since_ms=T0 + 1000)
    reader, _ = make(default_script(trade_history={"trades": [dict(trade_row(), executed_at="soon")]}))
    with pytest.raises(ExchangeDataError):                                    # an undatable row is never skipped
        reader.fetch_fills("OAS/JPY", since_ms=0)


def test_health_success_is_recorded_only_when_a_whole_read_succeeded():
    now = [10.0]
    transport = FakeTransport(default_script())
    reader = rd.BitbankReader(transport, symbols=SYMBOLS, clock=lambda: now[0])
    assert reader.health() == rd.ReaderHealth(None, 0, 10.0)
    bad = {"assets": [{"asset": "jpy"}]}               # arrives fine, cannot be parsed
    transport.script["assets"] = bad
    for t, expected in ((20.0, 1), (30.0, 2), (40.0, 3)):
        now[0] = t
        with pytest.raises(ExchangeDataError):
            reader.fetch_balance()
        assert reader.health() == rd.ReaderHealth("data", expected, 10.0)      # the last success stays at 10
    transport.script["assets"] = assets_payload()
    now[0] = 50.0
    reader.fetch_balance()
    assert reader.health() == rd.ReaderHealth(None, 0, 50.0)


@pytest.mark.parametrize("method,endpoint,breaker", [
    ("fetch_open_orders", "active_orders", {"orders": [order_row(pair="xym_jpy")]}),
    ("fetch_fills", "trade_history", {"trades": [trade_row(pair="xym_jpy")]}),
    ("fetch_order", "order", order_row(order_id=5)),
])
def test_a_mismatched_response_is_a_failed_read_not_a_success(method, endpoint, breaker):
    reader, transport = make()
    transport.script[endpoint] = breaker
    args = ("OAS/JPY", "1000001") if method == "fetch_order" else ("OAS/JPY",)
    kwargs = {"since_ms": 0} if method == "fetch_fills" else {}
    with pytest.raises(ExchangeDataError):
        getattr(reader, method)(*args, **kwargs)
    assert reader.health().consecutive_failures == 1 and reader.health().last_error_kind == "data"
