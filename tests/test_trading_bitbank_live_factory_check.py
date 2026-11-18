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
from docich.trading.live.exchange import ExchangeAmbiguous, ExchangeDataError, OrderNotFound
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


def _open(state, *, skew_s=0.0, transport=None, **kw):
    made = transport or FakeTransport(default_script(), skew_s=skew_s)
    return fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0, transport_factory=lambda creds: made, **kw)


# ---- factory -------------------------------------------------------------------------------
def test_a_clean_start_builds_a_reader(state):
    _install_key(state)
    assert _open(state).market_rules("OAS/JPY").min_amount == 1


@pytest.mark.parametrize("skew_s,ok", [(0.0, True), (0.5, True), (0.8, True), (0.9, False), (3.0, False),
                                       (-0.9, True), (-6.9, True), (-7.1, False), (-30.0, False)])
def test_the_local_clock_must_be_close_to_bitbanks_response_date(state, skew_s, ok):
    _install_key(state)
    if ok:
        _open(state, skew_s=skew_s)
    else:
        with pytest.raises(ReaderUnavailable) as err:
            _open(state, skew_s=skew_s)
        assert "clock" in str(err.value)


def _intervals(pairs):
    class Intervals(FakeTransport):
        it = iter(pairs)

        def get(self, endpoint, params):
            data = super().get(endpoint, params)
            self._samples[-1] = next(self.it, pairs[-1])
            return data
    return Intervals


def test_each_call_gives_an_interval_and_intervals_intersect_into_a_tight_one(state):
    _install_key(state)
    # true offset ~0.3 s; a floored Date makes each single interval a full second wide, they cross at [0.2, 0.4]
    spread = [(0.9 - 1.0, 0.9), (1.1 - 1.0, 1.1), (0.2, 1.2), (-0.8, 0.4), (-0.6, 0.6), (0.0, 1.0), (0.1, 1.1)]
    assert fc.judge_clock(max(l for l, _ in spread), min(h for _, h in spread)) is None
    fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0,
                           transport_factory=lambda c: _intervals(spread)(default_script()))


def test_ahead_is_judged_by_the_highest_possible_offset_and_behind_by_the_lowest():
    assert fc.judge_clock(0.5, 0.9) is not None and "ahead" in fc.judge_clock(0.5, 0.9)      # could be 0.9 ahead
    assert fc.judge_clock(0.1, 0.8) is None
    assert fc.judge_clock(-7.4, -6.5) is not None and "behind" in fc.judge_clock(-7.4, -6.5)  # could be 7.4 behind
    assert fc.judge_clock(-6.9, -6.0) is None
    assert "inconsistent" in fc.judge_clock(2.0, 0.5)


def test_a_slow_round_trip_widens_the_interval_instead_of_biasing_the_estimate():
    import docich.trading.exchanges.bitbank_live.transport as tr
    # started 100.0, answer received 101.5 (a 1.5 s round trip), the server said 99:  d in [100-99-1, 101.5-99]
    assert tr.intersect_bounds([(100.0 - 99 - 1.0, 101.5 - 99)]) == (0.0, 2.5)
    # the same offset seen on a fast round trip pins it down
    assert tr.intersect_bounds([(0.0, 2.5), (0.4, 1.3)]) == (0.4, 1.3)


def test_no_date_header_at_all_means_the_clock_cannot_be_trusted(state):
    _install_key(state)
    with pytest.raises(ReaderUnavailable) as err:
        _open(state, skew_s=None)
    assert "clock check failed" in str(err.value)


def test_depth_timestamps_play_no_part_in_the_clock_check(state):
    _install_key(state)
    transport = FakeTransport(default_script(depth=_depth_at(T0 - 5000)), skew_s=0.0)   # a book published 5 s ago
    assert _open(state, transport=transport) is not None


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
    {"depth": ExchangeAmbiguous("down")}])
def test_any_failure_while_starting_means_no_reader(state, script):
    _install_key(state)
    with pytest.raises(ReaderUnavailable):
        _open(state, transport=FakeTransport(default_script(**script), skew_s=0.0))


# ---- the check script ------------------------------------------------------------------------
def _per_pair(pair, rows_key, rows):
    return lambda params: {rows_key: rows if params.get("pair") == pair else []}


def _trades(n=6, start=T0):
    return [trade_row(trade_id=100 + i, order_id=1000000 + i, executed_at=start + i * 1000) for i in range(n)]


def _since_aware(rows, unit):
    """A trade_history that honours `since` in the given unit ("ms" or "sec") for the pair of `rows`."""
    def answer(params):
        if params.get("pair") != "oas_jpy":
            return {"trades": []}
        since = params.get("since")
        if since is None:
            return {"trades": rows}
        threshold = int(since) if unit == "ms" else int(since) * 1000
        return {"trades": [r for r in rows if r["executed_at"] >= threshold]}
    return answer


def _check_script(unit="ms"):
    from bitbank_live_support import pairs_payload
    rows = _trades()
    rows[1] = trade_row(trade_id=101, order_id=1000001, maker_taker="maker", fee_amount_quote="-0.2",
                        fee_occurred_amount_quote="-0.2", executed_at=T0 + 1000)
    rows[2] = trade_row(trade_id=102, order_id=1000002, maker_taker="maker", fee_amount_quote="0.2",
                        fee_occurred_amount_quote="0.2", executed_at=T0 + 2000)       # positive on a negative-rate pair
    return default_script(
        pairs=pairs_payload(**{"OAS/JPY": {"maker_fee_rate_quote": "-0.0002"}}),
        depth=_depth_at(T0),
        active_orders=_per_pair("oas_jpy", "orders", [order_row(order_id=1000001, status="UNFILLED")]),
        trade_history=_since_aware(rows, unit),
        deposit_history={"deposits": []}, spot_status={"statuses": []},
        order=lambda params: (_ for _ in ()).throw(OrderNotFound("1")) if params["order_id"] == "1" else
        order_row(order_id=int(params["order_id"]), status="CANCELED_PARTIALLY_FILLED", executed_amount="4",
                  remaining_amount=None if params["order_id"].endswith("3") else "0"))


def _run(script=None, **kw):
    transport = FakeTransport(script or _check_script())
    return ck.run_check(transport, SYMBOLS, wall_clock=lambda: T0 / 1000, **kw), transport


def test_the_default_symbols_are_the_policys_symbols():
    from docich.trading.live.policy import INITIAL_SYMBOLS
    assert ck.DEFAULT_SYMBOLS == tuple(INITIAL_SYMBOLS)


def test_the_report_shows_raw_shapes_counts_and_no_secrets():
    (lines, code), _ = _run(secrets=(SENTINEL_KEY, SENTINEL_SECRET))
    text = "\n".join(lines)
    assert code == 0, text
    for expected in ("clock: local minus bitbank is within [0.00, 0.00] s", "-> ok", "book snapshot age ms per symbol",
                     "NOT the current time", "reader: built for 2/2 symbols",
                     "endpoint assets: ok", "endpoint deposit_history: ok", "endpoint spot_status: ok",
                     "OrderNotFound (code 50009", "balances: currencies=3", "amount_precision(watched)=",
                     "open orders OAS/JPY: count=1", "fills OAS/JPY: count=6", "executed_at digits: {13: 6}",
                     "reader smoke (strict parsing):"):
        assert expected in text, (expected, text)
    for forbidden in ("1000001", "10000.", SENTINEL_KEY, SENTINEL_SECRET):
        assert forbidden not in text, forbidden


def test_fee_shapes_tell_a_wrong_sign_from_a_missing_field():
    (lines, _), _ = _run()
    text = "\n".join(lines)
    assert "quote-negative/maker/occurred-agrees" in text and "quote-positive/maker/occurred-agrees" in text
    assert "quote-positive/taker/occurred-agrees" in text
    assert ck.fee_shape(trade_row(fee_amount_quote=None)) == "field-missing-or-unreadable"
    assert ck.fee_shape(trade_row(fee_occurred_amount_quote="9")) .endswith("occurred-DISAGREES")
    assert ck.fee_shape(trade_row(fee_amount_base="0.1")).startswith("both-nonzero")
    assert ck.fee_shape(trade_row(fee_amount_base="0.1", fee_amount_quote="0")).startswith("base-positive")
    assert ck.fee_shape(trade_row(fee_amount_base="0", fee_amount_quote="0")).startswith("both-zero")


def test_remaining_amount_answers_are_counted_even_when_the_strict_reader_would_refuse_them():
    (lines, _), _ = _run()
    text = "\n".join(lines)
    assert "remaining-null" in text and "remaining==0" in text      # order ids ending in 3 return a null remaining
    assert "CANCELED_PARTIALLY_FILLED" in text


@pytest.mark.parametrize("unit,verdict", [("ms", "since is in MILLISECONDS"), ("sec", "since is in SECONDS")])
def test_the_since_experiment_tells_milliseconds_from_seconds(unit, verdict):
    (lines, _), _ = _run(_check_script(unit))
    assert verdict in "\n".join(lines)


def test_the_since_experiment_says_undetermined_with_too_few_trades():
    script = _check_script()
    script["trade_history"] = _since_aware(_trades(2), "ms")
    (lines, _), _ = _run(script)
    assert "since unit experiment: undetermined" in "\n".join(lines)
    script["trade_history"] = {"trades": []}
    (lines, _), _ = _run(script)
    assert "since unit experiment: undetermined" in "\n".join(lines)


def test_a_bad_pair_is_named_and_the_rest_of_the_report_goes_on():
    from bitbank_live_support import pairs_payload
    script = _check_script()
    script["pairs"] = pairs_payload(**{"XYM/JPY": {"unit_amount": "zero"}})
    (lines, code), _ = _run(script)
    text = "\n".join(lines)
    assert code == 1 and "market XYM/JPY: BAD (bad field: unit_amount)" in text
    assert "reader: built for 1/2 symbols" in text and "fills OAS/JPY: count=6" in text


def test_parse_errors_name_the_field_in_the_report():
    script = _check_script()
    script["depth"] = {"asks": [], "bids": [], "timestamp": 12}
    (lines, code), _ = _run(script)
    assert code == 1 and any("book OAS/JPY: FAILED ExchangeDataError(-) bad field: timestamp" in l for l in lines)


def test_the_report_ends_with_one_verdict_per_open_question():
    (lines, _), _ = _run()
    text = "\n".join(lines)
    for topic in ("since unit", "remaining_amount of an order stopped early", "maker fee / rebate sign", "fee currency",
                  "readable endpoints", "clock"):
        assert f"VERDICT {topic}" in text, topic
    assert "VERDICT since unit: ANSWERED, since is in MILLISECONDS" in text
    assert "VERDICT remaining_amount of an order stopped early: ANSWERED" in text
    assert "VERDICT maker fee / rebate sign: ANSWERED" in text


def test_an_account_without_history_gets_undetermined_with_what_would_settle_it():
    script = _check_script()
    script["trade_history"] = {"trades": []}
    (lines, _), _ = _run(script)
    text = "\n".join(lines)
    for expected in ("VERDICT since unit: UNDETERMINED - needs one symbol with at least 4 trades",
                     "VERDICT remaining_amount of an order stopped early: UNDETERMINED - no order in the trade history was stopped",
                     "VERDICT maker fee / rebate sign: UNDETERMINED - no maker fill",
                     "VERDICT fee currency (base or quote): UNDETERMINED"):
        assert expected in text, expected


def test_a_failed_build_says_what_kind_of_failure_it_was(state):
    _install_key(state)
    bad = ExchangeAmbiguous("x")
    bad.health_kind = "clock"
    with pytest.raises(ReaderUnavailable) as err:
        _open(state, transport=FakeTransport(default_script(assets=bad), skew_s=0.0))
    assert "ExchangeAmbiguous/clock" in str(err.value)


@pytest.mark.parametrize("skew_s,word", [(0.3, "-> ok"), (2.0, "OUTSIDE"), (-9.0, "OUTSIDE")])
def test_the_report_judges_the_clock_from_the_date_header_estimate(skew_s, word):
    transport = FakeTransport(_check_script(), skew_s=skew_s)
    lines, code = ck.run_check(transport, SYMBOLS, wall_clock=lambda: T0 / 1000)
    text = "\n".join(lines)
    assert word in text and (code == 0) == (word == "-> ok")


def test_empty_symbol_lists_are_refused_before_any_request(state):
    _install_key(state)
    made = FakeTransport(default_script())
    with pytest.raises(ReaderUnavailable):
        fc.open_bitbank_reader(state, symbols=(), clock=lambda: 1.0, transport_factory=lambda c: made)
    assert made.calls == []
    transport = FakeTransport(_check_script())
    assert ck.run_check(transport, (), wall_clock=lambda: T0 / 1000) == (["no symbols given"], 1)
    assert ck.run_check(transport, (), wall_clock=lambda: T0 / 1000, public_only=True)[1] == 1
    assert transport.calls == []
    with pytest.raises(SystemExit):
        ck.main(["--public-only", "--symbols"])


def test_smoke_test_failures_and_order_lookup_failures_fail_the_run():
    script = _check_script()
    script["depth"] = lambda params: {"asks": [["x", "1"]], "bids": [], "timestamp": T0}      # book unreadable
    (lines, code), _ = _run(script)
    assert code == 1
    script = _check_script()
    script["order"] = lambda params: (_ for _ in ()).throw(OrderNotFound("1")) if params["order_id"] == "1" \
        else (_ for _ in ()).throw(ExchangeAmbiguous("down"))
    (lines, code), _ = _run(script)
    assert code == 1 and "error:ExchangeAmbiguous" in "\n".join(lines)


def test_a_transport_without_any_date_header_is_reported_as_unable_to_check_the_clock():
    transport = FakeTransport(_check_script(), skew_s=None)
    lines, code = ck.run_check(transport, SYMBOLS, wall_clock=lambda: T0 / 1000)
    assert code == 1 and "no response carried a Date header" in "\n".join(lines)


def test_public_only_never_touches_a_private_endpoint():
    (lines, code), transport = _run(public_only=True)
    assert code == 0 and "public-only: private endpoints were not touched" in "\n".join(lines)
    assert {name for name, _ in transport.calls} == {"depth", "pairs"}


def test_a_failing_endpoint_is_reported_and_the_exit_code_says_so():
    script = _check_script()
    script["deposit_history"] = ExchangeAmbiguous("no permission")
    (lines, code), _ = _run(script)
    assert code == 1 and any(line.startswith("endpoint deposit_history: FAILED") for line in lines)


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
    assert ck.main([]) == 1 and "--state-dir is required" in capsys.readouterr().out


def test_main_with_a_key_but_no_ccxt_prints_one_plain_line(state, capsys, monkeypatch):
    _install_key(state)
    import docich.trading.exchanges.bitbank_live.transport as tr

    def no_ccxt(config):
        raise ModuleNotFoundError("No module named 'ccxt'")

    monkeypatch.setattr(tr, "_ccxt_factory", no_ccxt)
    assert ck.main(["--state-dir", str(state)]) == 1
    out = capsys.readouterr().out
    assert out.strip() == "ccxt is not installed here: pip install -r requirements-trading.txt"
    assert SENTINEL_KEY not in out


def test_no_symbols_is_a_config_kind_and_reads_nothing(state):
    _install_key(state)
    made = FakeTransport(default_script())
    with pytest.raises(ReaderUnavailable) as err:
        fc.open_bitbank_reader(state, symbols=(), clock=lambda: 1.0, transport_factory=lambda c: made)
    assert err.value.kind == "config" and "no symbols" in str(err.value)
    assert made.calls == []


def test_missing_credentials_is_a_config_kind(state):
    with pytest.raises(ReaderUnavailable) as err:
        fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0,
                               transport_factory=lambda c: FakeTransport(default_script()))
    assert err.value.kind == "config" and "file_missing" in str(err.value)
    assert SENTINEL_KEY not in str(err.value)


def test_a_transport_construction_failure_is_a_config_kind(state):
    _install_key(state)

    def broken(creds):
        raise RuntimeError(f"cannot import ccxt {SENTINEL_KEY}")

    with pytest.raises(ReaderUnavailable) as err:
        fc.open_bitbank_reader(state, symbols=SYMBOLS, clock=lambda: 1.0, transport_factory=broken)
    assert err.value.kind == "config" and "RuntimeError" in str(err.value)
    assert SENTINEL_KEY not in str(err.value)


def test_a_clock_judgement_is_a_clock_kind(state):
    _install_key(state)
    with pytest.raises(ReaderUnavailable) as err:
        _open(state, skew_s=2.0)
    assert err.value.kind == "clock" and "clock" in str(err.value)


def test_a_failed_clock_check_is_a_clock_kind(state):
    _install_key(state)
    with pytest.raises(ReaderUnavailable) as err:
        _open(state, skew_s=None)
    assert err.value.kind == "clock" and "clock check failed" in str(err.value)


@pytest.mark.parametrize("health_kind,kind", [("auth", "auth"), ("clock", "clock"), ("network", None)])
def test_the_rule_load_kind_passes_through_the_factory(state, health_kind, kind):
    _install_key(state)
    bad = ExchangeAmbiguous("x")
    bad.health_kind = health_kind
    with pytest.raises(ReaderUnavailable) as err:
        _open(state, transport=FakeTransport(default_script(assets=bad), skew_s=0.0))
    assert err.value.kind == kind
    assert SENTINEL_KEY not in str(err.value)


def test_unclassified_data_error_during_clock_check_is_reader_unavailable(state, monkeypatch):
    _install_key(state)

    def malformed(*args, **kwargs):
        raise ExchangeDataError(SENTINEL_KEY)

    monkeypatch.setattr(fc, "clock_bounds", malformed)
    with pytest.raises(ReaderUnavailable) as err:
        _open(state)
    assert err.value.kind is None
    assert "ExchangeDataError" in str(err.value)
    assert SENTINEL_KEY not in str(err.value)
