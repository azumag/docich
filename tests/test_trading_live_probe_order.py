"""The isolated Phase E single-buy operator command (Issue #3623).  Synthetic only."""
from __future__ import annotations

import io
from decimal import Decimal as D

import pytest

from docich.trading.exchanges.bitbank_live import parse
from docich.trading.live import views
from docich.trading.live.exchange import (ExchangeAmbiguous, ExchangeDataError, ExchangeRejected,
                                          OrderRequest, SpotStatus)
from docich.trading.live.ledger import Cashflow, IntentRequest, LiveLedger
from docich.trading.live.lease import acquire_writer_lease, release_writer_lease
from docich.trading.live_worker import probe_order
from test_trading_live_support import Clock, Workspace, make_mock, policy_set

NOW = 1_800_000_000.0
SYMBOL = "OAS/JPY"


def _setup():
    ws = Workspace()
    ledger = ws.open_ledger(now=NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=NOW)
    ps = policy_set(("OAS/JPY", "XYM/JPY"))
    ledger.store_policy_set(lease, ps, now=NOW)
    ledger.record_initial_activation(lease, ps, operator="synthetic", note="probe test", now=NOW)
    ledger.record_cashflow(lease, Cashflow("cf1", "deposit", "JPY", "10000", "owner_manual", NOW), now=NOW)
    release_writer_lease(ledger, lease, now=NOW)
    ledger.close()
    ws.write_control(state="off", mode="trade", now=NOW)
    clock = Clock(int(NOW * 1000))
    mock, _ = make_mock(clock, symbols=("OAS/JPY", "XYM/JPY"), jpy="10000")
    mock.set_book(SYMBOL, bids=(("99", "20"),), asks=(("99", "20"),))
    return ws, clock, mock, ps


class ProbeReader:
    """A real MockExchange plus the two read methods the probe adds (status, optional hook)."""

    def __init__(self, inner, *, status="NORMAL", min_amount="1"):
        self.inner = inner
        self.status = status
        self.min_amount = min_amount
        self.after_book = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def fetch_spot_status(self, symbol):
        return SpotStatus(symbol, self.status, D(self.min_amount))

    def fetch_order_book(self, symbol, *, depth=20):
        book = self.inner.fetch_order_book(symbol, depth=depth)
        if self.after_book is not None:
            self.after_book()
        return book


class OneShotAmbiguousSender:
    kind = "mock"

    def __init__(self):
        self.calls = 0

    def submit_order(self, req):
        self.calls += 1
        raise ExchangeAmbiguous("synthetic ambiguous submit")

    def cancel_order(self, symbol, exchange_order_id):
        raise AssertionError("nothing was acknowledged, so nothing may be canceled")


def _opener(reader, sender):
    def open_session(_state_dir, *, symbols, clock):
        return reader, sender
    return open_session


def _plan(**over):
    values = dict(symbol=SYMBOL, amount=D("10"), price=D("100"), max_quote=D("1010"),
                  fee=D("2"), reserve=D("1"), monitor_s=1)
    values.update(over)
    return probe_order.ProbePlan(**values)


def _preview(ws, clock, mock, *, plan=None, reader=None, sender=None):
    reader = reader or ProbeReader(mock)
    sender = sender or mock
    return probe_order.preview(ws.state, plan or _plan(), clock=clock.seconds,
                               session_opener=_opener(reader, sender))


def _run(ws, clock, mock, token, *, plan=None, reader=None, sender=None):
    reader = reader or ProbeReader(mock)
    sender = sender or mock
    return probe_order.run(ws.state, plan or _plan(), token, clock=clock.seconds,
                           sleep=lambda seconds: clock.advance(seconds),
                           session_opener=_opener(reader, sender))


def _intent(ws):
    ledger = LiveLedger.open_readonly(ws.layout)
    try:
        return views.intent_by_decision_key(ledger, probe_order.DECISION_KEY)
    finally:
        ledger.close()


def _cleanup(ws):
    ws.cleanup()


# ---- preview ----------------------------------------------------------------------------
def test_preview_is_read_only_and_prints_only_the_public_plan_and_token():
    ws, clock, mock, _ = _setup()
    try:
        before = (ws.state / "trading-live" / "live.sqlite3").stat().st_mtime_ns
        view = _preview(ws, clock, mock)
        out = io.StringIO()
        probe_order._print_preview(view, out)
        text = out.getvalue()
        assert "probe: preview" in text and "token:" in text
        assert "OAS/JPY" in text and "amount: 10" in text and "limit: 100" in text
        assert "SENTINEL" not in text and "balance" not in text and "order_id" not in text
        assert len(view["token"]) == 10 and set(view["token"]) <= set("0123456789abcdef")
        assert mock.submissions == []
        assert _intent(ws) is None
        assert (ws.state / "trading-live" / "live.sqlite3").stat().st_mtime_ns == before
    finally:
        _cleanup(ws)


def test_exact_confirmation_mismatch_refuses_before_any_intent_or_post():
    ws, clock, mock, _ = _setup()
    try:
        _preview(ws, clock, mock)
        with pytest.raises(probe_order.ProbeRefused) as err:
            _run(ws, clock, mock, "0000000000")
        assert err.value.code == "stale_preview"
        assert _intent(ws) is None
        assert mock.submissions == []
    finally:
        _cleanup(ws)


@pytest.mark.parametrize("field,value,code", [
    ("amount", "0", "invalid_amount"),
    ("amount", "-1", "invalid_amount"),
    ("amount", "1e1", "invalid_amount"),
    ("price", "0.0000", "invalid_price"),
    ("max_quote", "0", "invalid_max_quote"),
    ("fee", "-1", "invalid_fee"),
    ("reserve", "-1", "invalid_reserve"),
])
def test_cli_refuses_non_positive_or_non_decimal_money(field, value, code):
    args = ["--state-dir", "/tmp/does-not-matter", "--symbol", SYMBOL, "--amount", "10",
            "--price", "100", "--max-quote", "1010", "--fee", "2", "--reserve", "1"]
    args[args.index(f"--{field.replace('_', '-')}") + 1] = value
    out = io.StringIO()
    assert probe_order.main(args, out=out) == probe_order.EXIT_REFUSED
    assert code in out.getvalue()


def test_dynamic_bounds_step_min_fee_reserve_and_cap_are_enforced():
    ws, clock, mock, _ = _setup()
    try:
        for plan, reader, code in [
            (_plan(amount=D("10.5")), ProbeReader(mock), "amount_step"),
            (_plan(price=D("100.0005")), ProbeReader(mock), "price_tick"),
            (_plan(amount=D("1")), ProbeReader(mock, min_amount="2"), "amount_below_min"),
            (_plan(fee=D("1.5")), ProbeReader(mock), "fee_below_max"),
            (_plan(reserve=D("0.5")), ProbeReader(mock), "reserve_too_small"),
            (_plan(max_quote=D("1002")), ProbeReader(mock), "max_quote_exceeded"),
        ]:
            with pytest.raises(probe_order.ProbeRefused) as err:
                _preview(ws, clock, mock, plan=plan, reader=reader)
            assert err.value.code == code
        assert mock.submissions == []
    finally:
        _cleanup(ws)


# ---- no automatic trade / global gate ---------------------------------------------------
def test_global_control_on_is_refused_and_never_falls_back_to_the_normal_gate():
    ws, clock, mock, _ = _setup()
    try:
        ws.write_control(state="on", mode="trade", now=NOW)
        with pytest.raises(probe_order.ProbeRefused) as err:
            _preview(ws, clock, mock)
        assert err.value.code == "control_on"
        with pytest.raises(probe_order.ProbeRefused) as err:
            _run(ws, clock, mock, "0000000000")
        assert err.value.code == "control_on"
        assert mock.submissions == [] and _intent(ws) is None
    finally:
        _cleanup(ws)


def test_control_turned_on_after_preflight_blocks_the_post():
    ws, clock, mock, _ = _setup()
    try:
        view = _preview(ws, clock, mock)
        reader = ProbeReader(mock)
        reader.after_book = lambda: ws.write_control(state="on", mode="trade", now=NOW)
        with pytest.raises(probe_order.ProbeRefused) as err:
            _run(ws, clock, mock, view["token"], reader=reader)
        assert err.value.code == "control_on"
        assert mock.submissions == []
        assert _intent(ws).state == "ABANDONED"
    finally:
        _cleanup(ws)


# ---- one request, durable intent, rerun and ambiguity ------------------------------------
def test_run_posts_exactly_once_and_leaves_a_confirmed_position():
    ws, clock, mock, _ = _setup()
    try:
        view = _preview(ws, clock, mock)
        result = _run(ws, clock, mock, view["token"])
        assert result["outcome"] == "filled" and result["exit_code"] == probe_order.EXIT_OK
        intent = _intent(ws)
        assert intent is not None and intent.state == "FILLED"
        assert intent.decision_key == probe_order.DECISION_KEY
        assert len(mock.submissions) == 1
        ledger = LiveLedger.open_readonly(ws.layout)
        try:
            order = views.order_for_intent(ledger, intent.intent_id)
            fills = views.fills_for_order(ledger, order.exchange_order_id)
            assert order.status == "FULLY_FILLED"
            assert sum((fill.amount for fill in fills), D(0)) == D("10")
            assert views.position_net_base(ledger, intent.position_id) == D("10")
            assert [(row.symbol, row.entry_policy_digest) for row in views.open_position_rows(ledger)] == [
                (SYMBOL, intent.policy_digest)]
        finally:
            ledger.close()
        out = io.StringIO()
        probe_order._print_run(result, out)
        assert "order_id" not in out.getvalue() and "exchange" not in out.getvalue()
    finally:
        _cleanup(ws)


def test_rerun_never_posts_again_and_different_parameters_are_refused():
    ws, clock, mock, _ = _setup()
    try:
        view = _preview(ws, clock, mock)
        assert _run(ws, clock, mock, view["token"])["outcome"] == "filled"
        assert _run(ws, clock, mock, view["token"])["outcome"] == "filled"
        assert len(mock.submissions) == 1
        with pytest.raises(probe_order.ProbeRefused) as err:
            _preview(ws, clock, mock, plan=_plan(amount=D("11")))
        assert err.value.code == "existing_intent_mismatch"
        assert len(mock.submissions) == 1
    finally:
        _cleanup(ws)


def test_ambiguous_submit_is_not_resent_on_rerun():
    ws, clock, mock, _ = _setup()
    try:
        view = _preview(ws, clock, mock)
        sender = OneShotAmbiguousSender()
        result = _run(ws, clock, mock, view["token"], sender=sender)
        assert result["outcome"] == "unresolved" and result["exit_code"] == probe_order.EXIT_UNRESOLVED
        assert sender.calls == 1 and _intent(ws).state == "UNKNOWN_SUBMISSION"
        assert _run(ws, clock, mock, view["token"], sender=sender)["outcome"] == "unresolved"
        assert sender.calls == 1
    finally:
        _cleanup(ws)


# ---- preconditions ----------------------------------------------------------------------
def test_foreign_writer_lease_is_refused_without_a_post(monkeypatch):
    ws, clock, mock, _ = _setup()
    try:
        view = _preview(ws, clock, mock)

        def held(*_args, **_kwargs):
            raise probe_order.LeaseHeld("synthetic foreign lease")

        monkeypatch.setattr(probe_order, "acquire_writer_lease", held)
        with pytest.raises(probe_order.ProbeRefused) as err:
            _run(ws, clock, mock, view["token"])
        assert err.value.code == "lease_held" and err.value.exit_code == probe_order.EXIT_LEASE_HELD
        assert mock.submissions == []
    finally:
        _cleanup(ws)


def test_an_unrelated_open_intent_blocks_the_trial():
    ws, clock, mock, ps = _setup()
    try:
        ledger = ws.open_ledger(now=NOW)
        lease = acquire_writer_lease(ledger, ws.layout, now=NOW)
        ledger.create_intent(lease, IntentRequest(
            decision_key="other", symbol="XYM/JPY", side="buy", order_type="limit", amount="1",
            price="100", policy_digest=ps.policy_for("XYM/JPY").digest, rule_id="entry-1"), now=NOW)
        release_writer_lease(ledger, lease, now=NOW)
        ledger.close()
        with pytest.raises(probe_order.ProbeRefused) as err:
            _preview(ws, clock, mock)
        assert err.value.code == "active_intents"
        assert mock.submissions == []
    finally:
        _cleanup(ws)


def test_stopped_pair_bad_balance_and_missing_liquidity_are_refused():
    ws, clock, mock, _ = _setup()
    try:
        with pytest.raises(probe_order.ProbeRefused) as err:
            _preview(ws, clock, mock, reader=ProbeReader(mock, status="STOP"))
        assert err.value.code == "pair_stopped"

        mock.adjust_balance("JPY", "1")
        with pytest.raises(probe_order.ProbeRefused) as err:
            _preview(ws, clock, mock)
        assert err.value.code == "balance_inconsistent"
        mock.adjust_balance("JPY", "-1")

        mock.set_book(SYMBOL, bids=(("99", "20"),), asks=(("99", "4"),))
        with pytest.raises(probe_order.ProbeRefused) as err:
            _preview(ws, clock, mock)
        assert err.value.code == "liquidity_insufficient"
        assert mock.submissions == []
    finally:
        _cleanup(ws)


# ---- cancellation / partial fill --------------------------------------------------------
def test_open_remainder_is_canceled_and_a_partial_fill_is_not_success():
    ws, clock, mock, _ = _setup()
    try:
        view = _preview(ws, clock, mock)
        reader = ProbeReader(mock)
        # The preflight sees enough depth, then the book shrinks before the POST.
        reader.after_book = lambda: mock.set_book(SYMBOL, bids=(("99", "20"),), asks=(("99", "4"),))
        result = _run(ws, clock, mock, view["token"], reader=reader)
        assert result["outcome"] == "partial" and result["exit_code"] == probe_order.EXIT_UNRESOLVED
        assert result["state"] == "CANCELED" and D(result["filled_amount"]) == D("4")
        assert len(mock.submissions) == 1 and mock.submissions[0]["side"] == "buy"
        intent = _intent(ws)
        ledger = LiveLedger.open_readonly(ws.layout)
        try:
            order = views.order_for_intent(ledger, intent.intent_id)
            assert order.status == "CANCELED_PARTIALLY_FILLED"
            assert views.position_net_base(ledger, intent.position_id) == D("4")
            assert [row.status for row in views.open_position_rows(ledger)] == ["open"]
        finally:
            ledger.close()
    finally:
        _cleanup(ws)


# ---- the locally scoped sender wrapper ------------------------------------------------
def test_sender_wrapper_refuses_a_different_order_and_only_posts_once():
    ws, clock, mock, _ = _setup()
    try:
        class Recorder:
            kind = "mock"

            def __init__(self):
                self.calls = []

            def submit_order(self, req):
                self.calls.append(req)
                return type("Snap", (), {"exchange_order_id": "7"})()

        inner = Recorder()
        wrapper = probe_order._ProbeSender(inner, _plan(), state_dir=ws.state, clock=clock.seconds)
        with pytest.raises(ExchangeRejected):
            wrapper.submit_order(OrderRequest(SYMBOL, "sell", "limit", D("10"), D("100"), None))
        assert inner.calls == []
        wrapper.submit_order(OrderRequest(SYMBOL, "buy", "limit", D("10"), D("100"), None))
        with pytest.raises(ExchangeRejected):
            wrapper.submit_order(OrderRequest(SYMBOL, "buy", "limit", D("10"), D("100"), None))
        assert len(inner.calls) == 1
    finally:
        _cleanup(ws)


# ---- the GET-only spot status parser ---------------------------------------------------
def test_spot_status_parser_requires_the_dynamic_min_amount_and_a_known_pair():
    statuses = parse.spot_statuses(
        {"statuses": [{"pair": "btc_jpy", "status": "NORMAL", "min_amount": "0.0001"},
                      {"pair": "oas_jpy", "status": "NORMAL", "min_amount": "1"}]},
        {"oas_jpy": "OAS/JPY"})
    assert statuses["OAS/JPY"] == SpotStatus("OAS/JPY", "NORMAL", D("1"))
    with pytest.raises(ExchangeDataError):
        parse.spot_statuses({"statuses": [{"pair": "oas_jpy", "status": "NORMAL"}]},
                            {"oas_jpy": "OAS/JPY"})
