"""open_bitbank_reader (clock check, fail-closed) and the owner's check script (Issue #3623, L3b)."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from bitbank_live_support import (SENTINEL_KEY, SENTINEL_SECRET, SYMBOLS, T0, FakeTransport, assets_payload,
                                  default_script, order_row, trade_row)
from docich.trading.exchanges.bitbank_live import check as ck
from docich.trading.exchanges.bitbank_live import factory as fc
from docich.trading.exchanges.bitbank_live.credentials import KEY_FILENAME
from docich.trading.exchanges.bitbank_live.reader import ReaderUnavailable
from docich.trading.live.exchange import ExchangeAmbiguous, OrderNotFound
from docich.trading.live.paths import live_layout


@pytest.fixture
def state():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _install_key(state):
    layout = live_layout(state)
    layout.root.mkdir(mode=0o700)
    layout.owner_dir.mkdir(mode=0o700)
    path = layout.owner_dir / KEY_FILENAME
    path.write_text(json.dumps({"schema_version": 1, "kind": "docich-bitbank-readonly-key",
                                "api_key": SENTINEL_KEY, "api_secret": SENTINEL_SECRET}))
    os.chmod(path, 0o600)


def _depth_at(server_ms):
    return {"asks": [["101", "5"]], "bids": [["100", "4"]], "timestamp": server_ms}


def _open(state, server_ms, *, wall=None, transport=None, **kw):
    script = default_script(depth=_depth_at(server_ms))
    made = transport or FakeTransport(script)
    return fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0,
                                  wall_clock=wall or (lambda: T0 / 1000), transport_factory=lambda creds: made, **kw)


# ---- factory -------------------------------------------------------------------------------
def test_a_clean_start_builds_a_reader(state):
    _install_key(state)
    reader = _open(state, T0)
    assert reader.market_rules("OAS/JPY").min_amount == 1


@pytest.mark.parametrize("server_offset_ms,ok", [(0, True), (700, True), (-700, True), (900, False), (-900, False),
                                                 (5000, False)])
def test_the_local_clock_must_be_close_to_bitbanks(state, server_offset_ms, ok):
    _install_key(state)
    if ok:
        _open(state, T0 + server_offset_ms)
    else:
        with pytest.raises(ReaderUnavailable) as err:
            _open(state, T0 + server_offset_ms)
        assert "clock" in str(err.value)


def test_no_key_means_no_reader_and_no_transport_is_even_built(state):
    built = []
    with pytest.raises(ReaderUnavailable) as err:
        fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0,
                               transport_factory=lambda creds: built.append(creds))
    assert "file_missing" in str(err.value) and built == []
    assert SENTINEL_KEY not in str(err.value)


def test_a_transport_that_cannot_be_built_is_reported_by_class_name_only(state):
    _install_key(state)

    def broken(creds):
        raise RuntimeError(f"cannot import ccxt {SENTINEL_KEY}")

    with pytest.raises(ReaderUnavailable) as err:
        fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0, transport_factory=broken)
    assert "RuntimeError" in str(err.value) and SENTINEL_KEY not in str(err.value)
    assert err.value.__cause__ is None


@pytest.mark.parametrize("script", [
    {"pairs": ExchangeAmbiguous("down")}, {"assets": ExchangeAmbiguous("down")},
    {"depth": ExchangeAmbiguous("down")}, {"depth": {"asks": [], "bids": []}}])
def test_any_failure_while_starting_means_no_reader(state, script):
    _install_key(state)
    with pytest.raises(ReaderUnavailable):
        _open(state, T0, transport=FakeTransport(default_script(**script)))


# ---- the check script ------------------------------------------------------------------------
def _per_pair(pair, rows_key, rows):
    return lambda params: {rows_key: rows if params.get("pair") == pair else []}


def _check_script():
    from bitbank_live_support import pairs_payload
    return default_script(
        pairs=pairs_payload(**{"OAS/JPY": {"maker_fee_rate_quote": "-0.0002"}}),
        depth=_depth_at(T0),
        active_orders=_per_pair("oas_jpy", "orders", [order_row(order_id=1000001, status="UNFILLED")]),
        trade_history=_per_pair("oas_jpy", "trades", [
            trade_row(trade_id=7, order_id=1000001, executed_at=T0 + 100),
            trade_row(trade_id=8, order_id=1000002, maker_taker="maker", fee_amount_quote="-0.2",
                      fee_occurred_amount_quote="-0.2", executed_at=T0 + 200)]),
        deposit_history={"deposits": []}, spot_status={"statuses": []},
        order=lambda params: (_ for _ in ()).throw(OrderNotFound("1")) if params["order_id"] == "1" else
        order_row(order_id=int(params["order_id"]), status="FULLY_FILLED", executed_amount="10", remaining_amount="0"))


def test_the_check_report_has_counts_and_shapes_and_no_secrets():
    transport = FakeTransport(_check_script())
    lines, code = ck.run_check(transport, SYMBOLS, wall_clock=lambda: T0 / 1000, secrets=(SENTINEL_KEY, SENTINEL_SECRET))
    text = "\n".join(lines)
    assert code == 0, text
    for expected in ("clock: skew_ms=0", "reader: built for 2 symbols", "endpoint assets: ok", "endpoint deposit_history: ok",
                     "endpoint spot_status: ok", "OrderNotFound (code 50009", "balances: currencies=3",
                     "open orders OAS/JPY: count=1", "fills OAS/JPY: count=2", "negative_fees=1",
                     "maker_taker=", "executed_at_digits={13: 2}", "since unit experiment", "reader smoke:"):
        assert expected in text, (expected, text)
    # the report never carries an amount, price or id of a real order
    for forbidden in ("10000", "1000001", "100.0", SENTINEL_KEY, SENTINEL_SECRET):
        assert forbidden not in text.replace("amount_step=1", ""), forbidden


def test_the_remaining_amount_relation_is_reported_from_lookups_of_orders_seen_in_history():
    lines, _ = ck.run_check(FakeTransport(_check_script()), SYMBOLS, wall_clock=lambda: T0 / 1000)
    text = "\n".join(lines)
    assert "orders from history" in text and "FULLY_FILLED" in text


def test_a_failing_endpoint_is_reported_and_the_exit_code_says_so():
    script = _check_script()
    script["deposit_history"] = ExchangeAmbiguous("no permission")
    lines, code = ck.run_check(FakeTransport(script), SYMBOLS, wall_clock=lambda: T0 / 1000)
    assert code == 1 and any(line.startswith("endpoint deposit_history: FAILED") for line in lines)


def test_a_reader_that_cannot_be_built_stops_the_check_with_a_failure():
    script = _check_script()
    script["pairs"] = ExchangeAmbiguous("down")
    lines, code = ck.run_check(FakeTransport(script), SYMBOLS, wall_clock=lambda: T0 / 1000)
    assert code == 1 and any("reader: NOT BUILT" in line for line in lines)


def test_the_check_refuses_to_print_anything_that_contains_the_key():
    assert ck._finish(["fine", f"balance {SENTINEL_SECRET}"], (SENTINEL_KEY, SENTINEL_SECRET), 0) == (
        ["refused to print: a line contained the key or secret"], 2)
    assert ck._finish(["fine"], (SENTINEL_KEY, SENTINEL_SECRET), 1) == (["fine"], 1)


def test_an_exception_message_that_contains_the_secret_never_reaches_the_report():
    class Leaky(FakeTransport):
        def get(self, endpoint, params):
            data = super().get(endpoint, params)
            if endpoint == "trade_history":
                raise ExchangeAmbiguous(f"oops {SENTINEL_SECRET}")
            return data

    lines, code = ck.run_check(Leaky(_check_script()), SYMBOLS, wall_clock=lambda: T0 / 1000,
                               secrets=(SENTINEL_KEY, SENTINEL_SECRET))
    assert code == 1 and SENTINEL_SECRET not in "\n".join(lines)    # only the class name is ever printed


def test_main_without_a_key_says_so_and_touches_nothing(state, capsys):
    assert ck.main(["--state-dir", str(state)]) == 1
    out = capsys.readouterr().out
    assert "NOT AVAILABLE (file_missing)" in out and list(state.iterdir()) == []
