"""Exchange types and the deterministic mock exchange (Issue #3623, L2). No network."""
from decimal import Decimal as D

import pytest

from docich.trading.live import exchange as ex
from docich.trading.live.exchange import (CancelKind, Capabilities, ExchangeAmbiguous, ExchangeDataError,
                                          ExchangeRejected, FeeInfo, FeeStatus, OrderNotFound, OrderRequest,
                                          OrderStatus)
from docich.trading.live.mock_exchange import MockFees
from test_trading_live_support import make_mock


def req(side="buy", otype="limit", amount="10", price="100", cid=None, symbol="OAS/JPY"):
    return OrderRequest(symbol, side, otype, D(amount), None if price is None else D(price), cid)


@pytest.fixture(autouse=True)
def _no_network():
    import socket
    real = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used"))
    yield
    socket.socket.connect = real


# ---- types -----------------------------------------------------------------------------
def test_dec_refuses_float_bool_none_and_non_finite():
    for bad in (1.5, True, None, "abc", "NaN", "Infinity", D("NaN")):
        with pytest.raises(ValueError):
            ex.dec(bad)
    assert ex.dec(" 10.50 ") == D("10.5") and ex.dec(3) == D(3)


def test_dec_text_is_one_text_per_value():
    assert {ex.dec_text(D(v)) for v in ("10", "10.0000", "1E+1", "0010")} == {"10"}
    assert ex.dec_text(D("0.0")) == "0" and ex.dec_text(D("-0")) == "0"
    assert ex.dec_text(D("0.250")) == "0.25" and ex.dec_text(D("1E-8")) == "0.00000001"
    # more significant digits than the context precision must not be rounded
    assert ex.dec_text(D("1.12345678901234567890123456789")) == "1.12345678901234567890123456789"


def test_ms_to_seconds_is_stable():
    assert ex.ms_to_seconds(1_700_000_000_123) == ex.ms_to_seconds(1_700_000_000_123) == 1700000000.123


def test_raw_json_text_refuses_floats_and_sorts_keys():
    assert ex.raw_json_text(None) is None
    assert ex.raw_json_text({"b": "1", "a": "2"}) == '{"a":"2","b":"1"}'
    with pytest.raises(ValueError):
        ex.raw_json_text({"price": 1.5})


def test_fee_info_shape_is_enforced():
    FeeInfo(FeeStatus.UNKNOWN)
    FeeInfo(FeeStatus.KNOWN, D("-0.01"), "JPY")
    with pytest.raises(ValueError):
        FeeInfo(FeeStatus.UNKNOWN, D(1), "JPY")
    with pytest.raises(ValueError):
        FeeInfo(FeeStatus.KNOWN, None, "JPY")


def test_client_order_id_is_deterministic_and_optional():
    caps = Capabilities(True, True, True, 20)
    a, b = ex.make_client_order_id("k1", caps), ex.make_client_order_id("k1", caps)
    assert a == b and a != ex.make_client_order_id("k2", caps) and len(a) <= 20 and a.isalnum()
    assert ex.make_client_order_id("k1", Capabilities(False, False, True)) is None


# ---- matching --------------------------------------------------------------------------
def test_limit_buy_takes_asks_at_their_price_and_rests_the_remainder():
    mock, clock = make_mock()
    mock.set_book("OAS/JPY", asks=[(D("99"), D("4")), (D("100"), D("3")), (D("101"), D("50"))])
    snap = mock.submit_order(req(amount="10", price="100"))
    assert snap.status is OrderStatus.PARTIALLY_FILLED
    assert snap.executed_amount == 7 and snap.remaining_amount == 3
    fills = mock.all_fills()
    assert [(f.amount, f.price, f.maker_taker) for f in fills] == [(D(4), D(99), "taker"), (D(3), D(100), "taker")]
    # 4*99 + 3*100 = 696 JPY spent, taker fee 0.1% (rounded to 0.0001): 0.696
    assert mock.totals()["JPY"] == D("10000") - D("696") - D("0.696")
    assert mock.totals()["OAS"] == D(7)
    balance = mock.fetch_balance().assets
    # the resting remainder locks its fee too: 3 * 100 * 1.001
    assert balance["JPY"].locked == D("300.3") and balance["JPY"].free == balance["JPY"].total - D("300.3")
    assert [o.exchange_order_id for o in mock.fetch_open_orders("OAS/JPY").orders] == [snap.exchange_order_id]


def test_resting_order_is_filled_by_an_outside_taker_as_maker_in_price_time_order():
    mock, clock = make_mock(fees=MockFees(maker_rate=D("-0.0002"), taker_rate=D("0.001")))
    first = mock.submit_order(req(amount="5", price="100"))
    second = mock.submit_order(req(amount="5", price="101"))
    done = mock.trade_against("OAS/JPY", taker_side="sell", price="100", amount="6")
    assert done == 6
    assert mock.fetch_order("OAS/JPY", second.exchange_order_id).status is OrderStatus.FULLY_FILLED  # best price first
    after = mock.fetch_order("OAS/JPY", first.exchange_order_id)
    assert after.status is OrderStatus.PARTIALLY_FILLED and after.executed_amount == 1
    maker_fills = mock.fetch_fills("OAS/JPY", since_ms=0).fills
    assert all(f.maker_taker == "maker" for f in maker_fills)
    assert maker_fills[0].fee.amount < 0  # rebate


def test_sell_and_market_orders():
    mock, _ = make_mock()
    mock.set_book("OAS/JPY", asks=[(D("100"), D("10"))], bids=[(D("99"), D("6")), (D("98"), D("6"))])
    mock.submit_order(req("buy", "market", amount="10", price=None))
    assert mock.totals()["OAS"] == 10
    sell = mock.submit_order(req("sell", "market", amount="10", price=None))
    assert sell.status is OrderStatus.CANCELED_PARTIALLY_FILLED or sell.status is OrderStatus.FULLY_FILLED
    assert sell.executed_amount == 10 and sell.status is OrderStatus.FULLY_FILLED
    assert mock.totals()["OAS"] == 0


def test_market_order_with_thin_book_fills_what_it_can_and_with_no_book_is_rejected():
    mock, _ = make_mock()
    mock.set_book("OAS/JPY", asks=[(D("100"), D("4"))])
    snap = mock.submit_order(req("buy", "market", amount="10", price=None))
    assert snap.status is OrderStatus.CANCELED_PARTIALLY_FILLED and snap.executed_amount == 4
    with pytest.raises(ExchangeRejected) as err:
        mock.submit_order(req("buy", "market", amount="10", price=None))
    assert err.value.code == "no_liquidity"


def test_base_fee_on_a_buy_is_taken_from_the_coins_received():
    mock, _ = make_mock(fees=MockFees(maker_rate=D("0"), taker_rate=D("0.01"), currency="base"))
    mock.set_book("OAS/JPY", asks=[(D("100"), D("10"))])
    mock.submit_order(req(amount="10", price="100"))
    assert mock.totals()["OAS"] == D("10") - D("0.1") and mock.totals()["JPY"] == D("9000")
    fill = mock.all_fills()[0]
    assert fill.fee.currency == "OAS" and fill.fee.amount == D("0.1")


@pytest.mark.parametrize("kwargs,code", [
    (dict(amount="0.5"), "amount_below_min"), (dict(amount="1.5"), "amount_step"),
    (dict(price="100.0005"), "price_tick"), (dict(otype="limit", price=None), "limit_needs_price"),
    (dict(amount="1000"), "insufficient_funds"), (dict(symbol="XYM/JPY"), "unknown_market"),
    (dict(side="sell", amount="1"), "insufficient_funds")])
def test_rejections(kwargs, code):
    mock, _ = make_mock()
    with pytest.raises(ExchangeRejected) as err:
        mock.submit_order(req(**kwargs))
    assert err.value.code == code
    assert mock.all_orders() == [] and mock.totals() == {"JPY": D(10000), "OAS": D(0)}


def test_funds_are_reserved_by_resting_orders():
    mock, _ = make_mock(jpy="1000")
    mock.submit_order(req(amount="9", price="100"))
    with pytest.raises(ExchangeRejected):
        mock.submit_order(req(amount="2", price="100"))
    assert mock.fetch_balance().assets["JPY"].free == D("99.1")  # 900 * 1.001 (worst-case fee) is locked


def test_resting_buys_lock_their_fee_so_totals_never_go_negative():
    fees = MockFees(maker_rate=D("0.01"), taker_rate=D("0.01"))
    mock, _ = make_mock(jpy="201", fees=fees)
    mock.submit_order(req(amount="1", price="100"))
    with pytest.raises(ExchangeRejected):
        mock.submit_order(req(amount="1", price="100"))  # 101 is locked, only 100 is free
    mock.trade_against("OAS/JPY", taker_side="sell", price="100", amount="1")
    assert mock.totals()["JPY"] == D("100")
    # two orders that both fit (fees included) still cannot drive the balance negative
    mock2, _ = make_mock(jpy="202", fees=fees)
    first = mock2.submit_order(req(amount="1", price="100"))
    second = mock2.submit_order(req(amount="1", price="100"))
    assert mock2.fetch_balance().assets["JPY"].free == 0
    mock2.trade_against("OAS/JPY", taker_side="sell", price="100", amount="1")
    balance = mock2.fetch_balance().assets["JPY"]
    assert balance.total == D("101") and balance.free == 0  # the fill freed nothing usable
    mock2.trade_against("OAS/JPY", taker_side="sell", price="100", amount="1")
    assert mock2.totals()["JPY"] == 0 and mock2.fetch_balance().assets["JPY"].free == 0
    assert mock2.fetch_order("OAS/JPY", first.exchange_order_id).status is OrderStatus.FULLY_FILLED
    assert mock2.fetch_order("OAS/JPY", second.exchange_order_id).status is OrderStatus.FULLY_FILLED


def test_base_fee_buy_is_not_charged_a_quote_margin():
    fees = MockFees(maker_rate=D("0.01"), taker_rate=D("0.01"), currency="base")
    mock, _ = make_mock(jpy="100", fees=fees)
    mock.submit_order(req(amount="1", price="100"))  # rests; the fee comes out of the coins
    mock2, _ = make_mock(jpy="100", fees=fees)
    mock2.set_book("OAS/JPY", asks=[(D("100"), D("1"))])
    mock2.submit_order(req("buy", "market", amount="1", price=None))
    assert mock2.totals()["JPY"] == 0 and mock2.totals()["OAS"] == D("0.99")


def test_cancel_kinds_and_race():
    mock, _ = make_mock()
    resting = mock.submit_order(req(amount="5", price="100"))
    out = mock.cancel_order("OAS/JPY", resting.exchange_order_id)
    assert out.kind is CancelKind.CANCELED and out.snapshot.status is OrderStatus.CANCELED_UNFILLED
    assert mock.cancel_order("OAS/JPY", resting.exchange_order_id).kind is CancelKind.ALREADY_CLOSED
    with pytest.raises(OrderNotFound):
        mock.cancel_order("OAS/JPY", "nope")
    part = mock.submit_order(req(amount="5", price="100"))
    mock.trade_against("OAS/JPY", taker_side="sell", price="100", amount="2")
    assert mock.cancel_order("OAS/JPY", part.exchange_order_id).snapshot.status is OrderStatus.CANCELED_PARTIALLY_FILLED
    assert mock.fetch_balance().assets["JPY"].locked == 0
    raced = mock.submit_order(req(amount="5", price="100"))
    mock.faults.push("cancel", "CANCEL_RACE")
    out = mock.cancel_order("OAS/JPY", raced.exchange_order_id)
    assert out.kind is CancelKind.ALREADY_CLOSED and out.snapshot.status is OrderStatus.FULLY_FILLED


def test_duplicate_client_order_id_is_rejected_when_the_exchange_has_ids():
    mock, _ = make_mock()
    mock.submit_order(req(cid="dabc"))
    with pytest.raises(ExchangeRejected) as err:
        mock.submit_order(req(cid="dabc"))
    assert err.value.code == "duplicate_client_order_id"


def test_lookup_by_client_id_and_the_exchange_without_it():
    mock, _ = make_mock()
    snap = mock.submit_order(req(cid="dabc"))
    assert mock.fetch_order_by_client_id("OAS/JPY", "dabc").exchange_order_id == snap.exchange_order_id
    assert mock.fetch_order_by_client_id("OAS/JPY", "other") is None
    plain, _ = make_mock(caps=Capabilities(False, False, True))
    snap = plain.submit_order(req(cid="dabc"))
    assert snap.client_order_id is None and plain.submissions[0]["client_order_id"] == "dabc"
    with pytest.raises(ExchangeDataError):
        plain.fetch_order_by_client_id("OAS/JPY", "dabc")


# ---- faults ----------------------------------------------------------------------------
def test_lost_request_and_lost_response_differ_in_whether_the_order_exists():
    mock, _ = make_mock()
    mock.faults.push("submit", "LOST_REQUEST")
    with pytest.raises(ExchangeAmbiguous):
        mock.submit_order(req())
    assert mock.all_orders() == [] and mock.submissions == []
    mock.faults.push("submit", "LOST_RESPONSE")
    with pytest.raises(ExchangeAmbiguous):
        mock.submit_order(req())
    assert len(mock.all_orders()) == 1 and len(mock.submissions) == 1


def test_reject_fault_and_nth_counting():
    mock, _ = make_mock()
    mock.faults.push("submit", "REJECT", nth=2, code="system_busy")
    mock.submit_order(req(amount="1"))
    with pytest.raises(ExchangeRejected) as err:
        mock.submit_order(req(amount="2"))
    assert err.value.code == "system_busy" and len(mock.all_orders()) == 1
    assert len(mock.faults) == 0


def test_read_faults():
    mock, _ = make_mock()
    snap = mock.submit_order(req())
    mock.faults.push("fetch_order", "READ_TIMEOUT")
    with pytest.raises(ExchangeAmbiguous):
        mock.fetch_order("OAS/JPY", snap.exchange_order_id)
    mock.faults.push("fetch_balance", "MALFORMED")
    with pytest.raises(ExchangeDataError):
        mock.fetch_balance()
    assert mock.fetch_order("OAS/JPY", snap.exchange_order_id).exchange_order_id == snap.exchange_order_id


def test_visibility_lag_hides_a_new_order_for_n_lookups():
    mock, _ = make_mock()
    mock.faults.push("submit", "VISIBILITY_LAG", n=2)
    snap = mock.submit_order(req(cid="dabc"))
    assert mock.fetch_open_orders("OAS/JPY").orders == ()
    with pytest.raises(OrderNotFound):
        mock.fetch_order("OAS/JPY", snap.exchange_order_id)
    assert mock.fetch_order_by_client_id("OAS/JPY", "dabc").exchange_order_id == snap.exchange_order_id
    assert len(mock.fetch_open_orders("OAS/JPY").orders) == 1


def test_fee_missing_hides_the_fee_but_the_balance_really_moves_and_backfill_reveals_it():
    mock, _ = make_mock()
    mock.set_book("OAS/JPY", asks=[(D("100"), D("10"))])
    mock.faults.push("fill", "FEE_MISSING", backfill_after=1)
    mock.submit_order(req(amount="10", price="100"))
    assert mock.totals()["JPY"] == D("10000") - D("1000") - D("1.0")
    first = mock.fetch_fills("OAS/JPY", since_ms=0).fills[0]
    assert first.fee.status is FeeStatus.UNKNOWN
    # backfill_after=1: the first poll counts it down (still hidden), the second poll shows the fee
    second = mock.fetch_fills("OAS/JPY", since_ms=0).fills[0]
    assert second.fee.status is FeeStatus.KNOWN and second.fee.amount == D("1.0")
    assert mock.fetch_fills("OAS/JPY", since_ms=0).fills[0].fee.amount == D("1.0")
    mock2, _ = make_mock()
    mock2.set_book("OAS/JPY", asks=[(D("100"), D("10"))])
    mock2.faults.push("fill", "FEE_MISSING")
    mock2.submit_order(req(amount="10", price="100"))
    for _ in range(5):
        assert mock2.fetch_fills("OAS/JPY", since_ms=0).fills[0].fee.status is FeeStatus.UNKNOWN


def test_fill_delay_duplicate_conflict_and_truncation():
    mock, _ = make_mock()
    mock.set_book("OAS/JPY", asks=[(D("100"), D("10"))])
    mock.faults.push("fill", "FILL_DELAY", n=2)
    mock.submit_order(req(amount="10", price="100"))
    assert mock.fetch_fills("OAS/JPY", since_ms=0).fills == ()
    assert mock.fetch_fills("OAS/JPY", since_ms=0).fills == ()
    assert len(mock.fetch_fills("OAS/JPY", since_ms=0).fills) == 1
    mock.faults.push("fetch_fills", "DUPLICATE_FILL")
    dup = mock.fetch_fills("OAS/JPY", since_ms=0).fills
    assert len(dup) == 2 and dup[0] == dup[1]
    mock.faults.push("fetch_fills", "CONFLICTING_FILL")
    conflict = mock.fetch_fills("OAS/JPY", since_ms=0).fills
    assert conflict[0].trade_id == conflict[1].trade_id and conflict[0].price != conflict[1].price
    mock.faults.push("fetch_fills", "TRUNCATED_PAGE")
    page = mock.fetch_fills("OAS/JPY", since_ms=0)
    assert not page.complete
    mock.submit_order(req(amount="2", price="50"))
    mock.faults.push("fetch_open_orders", "TRUNCATED_PAGE")
    assert not mock.fetch_open_orders("OAS/JPY").complete


def test_stale_balance_clock_skew_and_external_changes():
    mock, clock = make_mock()
    mock.lag_balance(2)
    mock.adjust_balance("JPY", "500")
    assert mock.fetch_balance().assets["JPY"].total == D(10000)
    assert mock.fetch_balance().assets["JPY"].total == D(10000)
    assert mock.fetch_balance().assets["JPY"].total == D(10500)
    mock.set_clock_skew(60_000)
    assert mock.fetch_balance().exchange_ts_ms == clock.ms + 60_000
    eid = mock.place_external_order(req(amount="3", price="90"))
    assert mock.submissions == [] and mock.fetch_open_orders("OAS/JPY").orders[0].exchange_order_id == eid


def test_fills_filter_by_order_only_when_the_exchange_supports_it():
    mock, _ = make_mock()
    mock.set_book("OAS/JPY", asks=[(D("100"), D("20"))])
    a = mock.submit_order(req(amount="4", price="100"))
    mock.submit_order(req(amount="6", price="100"))
    assert len(mock.fetch_fills("OAS/JPY", since_ms=0, exchange_order_id=a.exchange_order_id).fills) == 1
    plain, _ = make_mock(caps=Capabilities(False, False, False))
    plain.set_book("OAS/JPY", asks=[(D("100"), D("20"))])
    a = plain.submit_order(req(amount="4", price="100"))
    plain.submit_order(req(amount="6", price="100"))
    assert len(plain.fetch_fills("OAS/JPY", since_ms=0, exchange_order_id=a.exchange_order_id).fills) == 2


# ---- determinism and persistence -------------------------------------------------------
def _scenario(mock):
    mock.set_book("OAS/JPY", asks=[(D("99"), D("4")), (D("100"), D("30"))], bids=[(D("98"), D("5"))])
    mock.submit_order(req(amount="10", price="100", cid="d1"))
    mock.submit_order(req(amount="5", price="90", cid="d2"))
    mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="3")
    return (mock.totals(), mock.all_orders(), mock.all_fills(), mock.submissions)


def test_same_inputs_give_the_same_result():
    assert _scenario(make_mock()[0]) == _scenario(make_mock()[0])


def test_state_survives_a_restart_through_the_state_file(tmp_path):
    path = tmp_path / "mock.json"
    first, clock = make_mock(state_path=path)
    expected = _scenario(first)
    again, _ = make_mock(clock, state_path=path)
    assert (again.totals(), again.all_orders(), again.all_fills(), again.submissions) == expected
    snap = again.submit_order(req(amount="1", price="50"))
    assert int(snap.exchange_order_id) > max(int(o.exchange_order_id) for o in expected[1])
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_state_is_written_before_a_lost_response_is_raised(tmp_path):
    path = tmp_path / "mock.json"
    mock, clock = make_mock(state_path=path)
    mock.faults.push("submit", "LOST_RESPONSE")
    with pytest.raises(ExchangeAmbiguous):
        mock.submit_order(req())
    reread, _ = make_mock(clock, state_path=path)
    assert len(reread.all_orders()) == 1


def test_read_progress_survives_a_restart(tmp_path):
    path = tmp_path / "mock.json"
    mock, clock = make_mock(state_path=path)
    mock.set_book("OAS/JPY", asks=[(D("100"), D("10"))])
    mock.faults.push("fill", "FEE_MISSING", backfill_after=1)
    mock.submit_order(req(amount="1", price="100"))
    assert mock.fetch_fills("OAS/JPY", since_ms=0).fills[0].fee.status is FeeStatus.UNKNOWN
    again, _ = make_mock(clock, state_path=path)
    assert again.fetch_fills("OAS/JPY", since_ms=0).fills[0].fee.status is FeeStatus.KNOWN


def test_delayed_fills_and_hidden_orders_keep_their_progress_over_a_restart(tmp_path):
    path = tmp_path / "mock.json"
    mock, clock = make_mock(state_path=path)
    mock.set_book("OAS/JPY", asks=[(D("100"), D("10"))])
    mock.faults.push("fill", "FILL_DELAY", n=2)
    mock.submit_order(req(amount="1", price="100"))
    assert mock.fetch_fills("OAS/JPY", since_ms=0).fills == ()
    again, _ = make_mock(clock, state_path=path)
    assert again.fetch_fills("OAS/JPY", since_ms=0).fills == ()  # one delay tick was already spent
    assert len(again.fetch_fills("OAS/JPY", since_ms=0).fills) == 1

    path2 = tmp_path / "mock2.json"
    mock2, clock2 = make_mock(state_path=path2)
    mock2.faults.push("submit", "VISIBILITY_LAG", n=2)
    mock2.submit_order(req(amount="1", price="100"))
    assert mock2.fetch_open_orders("OAS/JPY").orders == ()
    again2, _ = make_mock(clock2, state_path=path2)
    assert again2.fetch_open_orders("OAS/JPY").orders == ()  # one poll was already spent
    assert len(again2.fetch_open_orders("OAS/JPY").orders) == 1


def test_free_never_goes_negative_and_total_equals_free_plus_locked():
    mock, _ = make_mock(jpy="1000", fees=MockFees(maker_rate=D("0.0005"), taker_rate=D("0.002")))
    mock.set_book("OAS/JPY", asks=[(D("99"), D("3")), (D("100"), D("2"))])
    mock.submit_order(req(amount="9", price="100"))
    mock.trade_against("OAS/JPY", taker_side="sell", price="100", amount="2")
    for asset in mock.fetch_balance().assets.values():
        assert asset.free >= 0 and asset.total == asset.free + asset.locked


# ---- the only things that can "send" are the mock and the isolated pre-E adapter ----------
def test_only_the_mock_and_the_isolated_spot_adapter_define_send_methods_and_live_does_not_import_them():
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "docich"
    definers = []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                if names & {"submit_order", "cancel_order"}:
                    definers.append((str(path.relative_to(src)), node.name))
    # exchange.py only declares the Protocol; the bitbank adapter is isolated and not allow-listed.
    assert sorted(definers) == [("trading/exchanges/bitbank_spot_sender.py", "BitbankSpotSender"),
                                ("trading/live/exchange.py", "OrderSender"),
                                ("trading/live/mock_exchange.py", "MockExchange")]
    for path in (src / "trading" / "live").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "trading.exchanges" not in text and "bitbank_ccxt" not in text.replace("bitbank_ccxt.py", ""), path


def test_truncated_page_with_nothing_visible_is_still_incomplete():
    mock, _ = make_mock()
    mock.faults.push("fetch_fills", "TRUNCATED_PAGE")
    page = mock.fetch_fills("OAS/JPY", since_ms=0)
    assert page.fills == () and not page.complete
    assert mock.fetch_fills("OAS/JPY", since_ms=0).complete


def test_every_queued_fault_on_an_op_counts_each_call():
    mock, _ = make_mock()
    mock.faults.push("submit", "REJECT", nth=1, code="a")
    mock.faults.push("submit", "REJECT", nth=2, code="b")
    codes = []
    for amount in ("1", "2", "3"):
        try:
            mock.submit_order(req(amount=amount))
            codes.append("ok")
        except ExchangeRejected as exc:
            codes.append(exc.code)
    assert codes == ["a", "b", "ok"]
