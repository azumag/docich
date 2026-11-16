"""Strict parsing of raw bitbank responses into the L2 types (Issue #3623, L3). Synthetic data only."""
from decimal import Decimal as D

import pytest

from bitbank_live_support import (SYMBOLS, T0, assets_payload, depth_payload, order_row, pairs_payload, trade_row)
from docich.trading.exchanges.bitbank_live import parse as p
from docich.trading.live.exchange import ExchangeDataError, FeeStatus, OrderStatus, raw_json_text

BY_PAIR = {"oas_jpy": "OAS/JPY", "xym_jpy": "XYM/JPY"}
RATES = {"OAS/JPY": p.FeeRates(D("0"), D("0"), D("-0.0002"), D("0.0012"))}


def _bad(fn, *args, **kw):
    with pytest.raises(ExchangeDataError) as err:
        fn(*args, **kw)
    assert "SENTINEL" not in str(err.value)
    return str(err.value)


# ---- primitives ------------------------------------------------------------------------
def test_dec_str_takes_strings_and_ints_only():
    assert p.dec_str("1.50", "x") == D("1.5") and p.dec_str(3, "x") == 3
    for bad in (1.5, True, None, "abc", "", "NaN", "Infinity", [], {}):
        _bad(p.dec_str, bad, "x")
    _bad(p.dec_str, "0", "x", positive=True)
    _bad(p.dec_str, "-1", "x", nonneg=True)


def test_ms_refuses_seconds_and_microseconds_and_bools():
    assert p.ms_value(T0, "t") == T0 and p.ms_value(str(T0), "t") == T0
    for bad in (T0 // 1000, T0 * 1000, True, 1.7e12, "x", None, -T0):
        _bad(p.ms_value, bad, "t")


def test_ids_and_clean_raw():
    assert p.id_text(12, "i") == "12" and p.id_text("34", "i") == "34"
    for bad in (True, -1, "a1", 1.0, None):
        _bad(p.id_text, bad, "i")
    assert p.clean_raw({"a": [1, "x", None, True]}) == {"a": [1, "x", None, True]}
    _bad(p.clean_raw, {"a": 1.5})
    _bad(p.clean_raw, {"a": D(1)})


# ---- markets and balances --------------------------------------------------------------
def test_market_rules_come_from_pairs_and_asset_precisions():
    precisions = p.asset_precisions(assets_payload())
    rules, rates = p.market_rules_from(pairs_payload(), precisions, SYMBOLS)
    r = rules["OAS/JPY"]
    assert (r.base, r.quote, r.amount_step, r.price_tick, r.min_amount) == ("OAS", "JPY", D("1"), D("0.001"), D("1"))
    assert r.base_unit == D("0.0001") and r.quote_unit == D("0.0001")
    assert r.max_fee_rate == D("0.0012") and rates["OAS/JPY"].maker_quote == D("0.0002")


def test_max_fee_rate_uses_absolute_values_and_the_floor_and_is_none_when_a_rate_is_missing():
    precisions = p.asset_precisions(assets_payload())
    rules, _ = p.market_rules_from(pairs_payload(**{"OAS/JPY": {"maker_fee_rate_quote": "-0.002"}}), precisions,
                                   SYMBOLS)
    assert rules["OAS/JPY"].max_fee_rate == D("0.002")
    rules, _ = p.market_rules_from(pairs_payload(), precisions, SYMBOLS, fee_rate_floor=D("0.005"))
    assert rules["OAS/JPY"].max_fee_rate == D("0.005")
    rules, _ = p.market_rules_from(pairs_payload(**{"OAS/JPY": {"taker_fee_rate_base": None}}), precisions, SYMBOLS)
    assert rules["OAS/JPY"].max_fee_rate is None


@pytest.mark.parametrize("change", [
    {"is_enabled": False}, {"unit_amount": "0"}, {"unit_amount": 1.0}, {"amount_digits": 30}, {"price_digits": "x"},
    {"base_asset": "xym"}])
def test_a_bad_or_disabled_pair_makes_the_rules_unavailable(change):
    precisions = p.asset_precisions(assets_payload())
    _bad(p.market_rules_from, pairs_payload(**{"OAS/JPY": change}), precisions, SYMBOLS)


def test_a_missing_symbol_or_precision_is_refused():
    precisions = p.asset_precisions(assets_payload())
    _bad(p.market_rules_from, pairs_payload(symbols=("OAS/JPY",)), precisions, SYMBOLS)
    _bad(p.market_rules_from, pairs_payload(), {"JPY": 4}, SYMBOLS)
    _bad(p.asset_precisions, {"assets": [{"asset": "jpy"}]})
    _bad(p.asset_precisions, {"nope": []})


def test_balance_parses_every_field_as_a_string():
    snap = p.balance_from(assets_payload(jpy=("10000.5", "100"), coins={"OAS": ("3", "1")}), 12.0)
    assert snap.assets["JPY"].total == D("10000.5") and snap.assets["JPY"].free == D("9900.5")
    assert snap.assets["OAS"].locked == D("1") and snap.fetched_at == 12.0 and snap.exchange_ts_ms is None
    bad = assets_payload()
    bad["assets"][0]["onhand_amount"] = 100.5
    _bad(p.balance_from, bad, 0.0)
    bad["assets"][0]["onhand_amount"] = "-1"
    _bad(p.balance_from, bad, 0.0)


def test_depth_is_ordered_and_cut():
    book = p.book_from(depth_payload(), "OAS/JPY", 1)
    assert book.asks == ((D("101"), D("5")),) and book.bids == ((D("100"), D("4")),) and book.exchange_ts_ms == T0
    bad = depth_payload()
    bad["asks"] = [["102", "1"], ["101", "1"]]
    _bad(p.book_from, bad, "OAS/JPY", 5)


# ---- orders ----------------------------------------------------------------------------
def test_an_order_row_becomes_a_snapshot_with_the_raw_status_preserved():
    snap = p.order_from(order_row(status="PARTIALLY_FILLED", executed_amount="4", remaining_amount="6",
                                  average_price="99.5"), BY_PAIR)
    assert snap.status is OrderStatus.PARTIALLY_FILLED and snap.exchange_order_id == "1000001"
    assert (snap.amount, snap.executed_amount, snap.remaining_amount, snap.average_price) == (
        D(10), D(4), D(6), D("99.5"))
    assert snap.symbol == "OAS/JPY" and snap.ordered_at_ms == T0 and snap.client_order_id is None
    assert raw_json_text(snap.raw)          # float-free JSON


@pytest.mark.parametrize("status", [s.value for s in OrderStatus])
def test_every_documented_status_is_accepted(status):
    assert p.order_from(order_row(status=status), BY_PAIR).status.value == status


def test_remaining_is_taken_as_sent_and_a_null_is_only_derived_for_market_orders():
    snap = p.order_from(order_row(status="CANCELED_PARTIALLY_FILLED", executed_amount="4", remaining_amount="0"),
                        BY_PAIR)
    assert snap.remaining_amount == 0          # no normalisation: phase B shows whether bitbank does this
    market = p.order_from(order_row(type="market", price=None, remaining_amount=None, executed_amount="4",
                                    status="CANCELED_PARTIALLY_FILLED"), BY_PAIR)
    assert market.remaining_amount == 6 and market.price is None
    _bad(p.order_from, order_row(remaining_amount=None), BY_PAIR)


@pytest.mark.parametrize("change", [
    {"status": "WEIRD"}, {"side": "hold"}, {"pair": "abc_jpy"}, {"start_amount": None}, {"start_amount": "0"},
    {"executed_amount": 1.5}, {"ordered_at": 1700000000}, {"order_id": "x"}, {"price": 100.0}, {"type": ""}])
def test_malformed_order_rows_are_data_errors(change):
    _bad(p.order_from, order_row(**change), BY_PAIR)


def test_a_float_anywhere_in_an_order_row_is_refused_even_in_an_unused_field():
    _bad(p.order_from, order_row(extra=1.5), BY_PAIR)


# ---- fills and fees --------------------------------------------------------------------
def _fee(**kw):
    return p.fee_from_trade(trade_row(**kw), RATES["OAS/JPY"])


def test_fee_in_quote_in_base_or_zero():
    assert _fee() == p.FeeInfo(FeeStatus.KNOWN, D("1.2"), "JPY")
    base = _fee(fee_amount_base="0.01", fee_amount_quote="0", fee_occurred_amount_quote="0")
    assert base == p.FeeInfo(FeeStatus.KNOWN, D("0.01"), "OAS")
    zero = _fee(fee_amount_base="0", fee_amount_quote="0", fee_occurred_amount_quote="0")
    assert zero == p.FeeInfo(FeeStatus.KNOWN, D(0), "JPY")


def test_a_rebate_keeps_its_sign_when_it_matches_the_rate_sign():
    rebate = _fee(maker_taker="maker", fee_amount_quote="-0.2", fee_occurred_amount_quote="-0.2")
    assert rebate == p.FeeInfo(FeeStatus.KNOWN, D("-0.2"), "JPY")


@pytest.mark.parametrize("change", [
    {"fee_amount_base": None}, {"fee_amount_quote": "x"}, {"fee_amount_quote": 1.2},
    {"fee_occurred_amount_quote": "9.9"},                                         # disagrees with fee_amount_quote
    {"fee_amount_base": "0.01"},                                                  # both currencies non-zero
    {"maker_taker": "maker", "fee_amount_quote": "0.2", "fee_occurred_amount_quote": "0.2"},   # positive fee, negative maker rate
])
def test_unclear_fees_are_unknown_never_guessed(change):
    assert _fee(**change).status is FeeStatus.UNKNOWN


def test_missing_fee_fields_entirely_are_unknown():
    row = trade_row()
    for key in ("fee_amount_base", "fee_amount_quote", "fee_occurred_amount_quote"):
        del row[key]
    assert p.fee_from_trade(row, None).status is FeeStatus.UNKNOWN


def test_a_fill_row_becomes_a_fill():
    fill = p.fill_from(trade_row(), BY_PAIR, RATES)
    assert (fill.trade_id, fill.exchange_order_id, fill.symbol, fill.side) == ("5001", "1000001", "OAS/JPY", "buy")
    assert fill.amount == 10 and fill.price == 100 and fill.quote_notional is None and fill.notional() == 1000
    assert fill.maker_taker == "taker" and fill.executed_at_ms == T0 + 1000
    assert p.fill_from(trade_row(maker_taker="what"), BY_PAIR, RATES).maker_taker == "unknown"
    assert raw_json_text(fill.raw)


@pytest.mark.parametrize("change", [
    {"trade_id": None}, {"order_id": None}, {"amount": "0"}, {"price": 100.0}, {"executed_at": 1700000001},
    {"side": "x"}, {"pair": "nope_jpy"}])
def test_a_fill_missing_a_required_field_is_a_data_error(change):
    _bad(p.fill_from, trade_row(**change), BY_PAIR, RATES)


@pytest.mark.parametrize("digits", ["3.9", "-0.5", 3.0, "1.5", "19", "-1", "x"])
def test_a_precision_field_must_be_a_whole_number_in_range(digits):
    precisions = p.asset_precisions(assets_payload())
    _bad(p.market_rules_from, pairs_payload(**{"OAS/JPY": {"amount_digits": digits}}), precisions, SYMBOLS)
    bad = assets_payload()
    bad["assets"][0]["amount_precision"] = digits
    _bad(p.asset_precisions, bad)


def test_whole_number_precisions_as_text_or_int_are_fine():
    precisions = p.asset_precisions(assets_payload())
    rules, _ = p.market_rules_from(pairs_payload(**{"OAS/JPY": {"amount_digits": "2", "price_digits": 1}}), precisions,
                                   SYMBOLS)
    assert rules["OAS/JPY"].amount_step == D("0.01") and rules["OAS/JPY"].price_tick == D("0.1")
