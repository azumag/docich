"""Opening balances recorded automatically by the worker (Issue #3623, PR-B).

Every number is hand derived; the ledger, lease and mock exchange are the real LIVE pieces
from the shared harness and the reader is a scripted balance snapshot source.  No network,
no sleeps, no floating point money.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from docich.trading.live import opening
from docich.trading.live.exchange import AssetBalance, BalanceSnapshot
from docich.trading.live.lease import LeaseLost, acquire_writer_lease, release_writer_lease
from docich.trading.live.ledger import Cashflow, FillRecord, OrderObservation
from docich.trading.live.opening import record_auto_opening

from test_trading_live_support import (Clock, Harness, Workspace, block_network, make_mock)

NOW = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def live(ws):
    ledger = ws.open_ledger(now=NOW)
    lease = acquire_writer_lease(ledger, ws.layout, now=NOW)
    try:
        yield ledger, lease
    finally:
        release_writer_lease(ledger, lease, now=NOW)
        ledger.close()


def snapshot(**assets) -> BalanceSnapshot:
    """``assets`` maps a currency to its exact total (free = total, nothing locked)."""
    return BalanceSnapshot(
        {currency: AssetBalance(D(total), D(total), D("0"))
         for currency, total in assets.items()},
        exchange_ts_ms=int(NOW * 1000), fetched_at=NOW)


class ScriptedReader:
    """Returns the scripted values in order; an Exception value is raised."""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def fetch_balance(self):
        self.calls += 1
        if not self.values:
            raise AssertionError("the reader was called more often than scripted")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def cashflow_rows(ledger) -> list[tuple]:
    with ledger.read_cursor() as cur:
        return cur.execute(
            "SELECT cashflow_id, kind, currency, amount, source, occurred_at, note "
            "FROM cashflows ORDER BY cashflow_id").fetchall()


def test_a_coin_with_a_balance_and_no_history_is_recorded_once(live):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="0")
    mock.adjust_balance("XRP", "3")

    assert record_auto_opening(ledger, lease, mock, ("XRP",), now=NOW) == ("XRP",)
    assert cashflow_rows(ledger) == [
        ("auto_opening:XRP", "deposit", "XRP", "3", "auto_opening", NOW,
         "opening balance recorded automatically"),
    ]
    # The ledger now has history for XRP: a second call touches nothing.
    assert record_auto_opening(ledger, lease, mock, ("XRP",), now=NOW + 60) == ()
    assert len(cashflow_rows(ledger)) == 1


def test_jpy_is_recorded_like_any_other_currency(live):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("OAS/JPY",), jpy="8000")

    assert record_auto_opening(ledger, lease, mock, ("JPY",), now=NOW) == ("JPY",)
    rows = cashflow_rows(ledger)
    assert rows == [("auto_opening:JPY", "deposit", "JPY", "8000", "auto_opening", NOW,
                     "opening balance recorded automatically")]


def test_dust_is_recorded_exactly_like_any_other_positive_balance(live):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="0")
    mock.adjust_balance("XRP", "0.00000001")

    assert record_auto_opening(ledger, lease, mock, ("XRP",), now=NOW) == ("XRP",)
    assert cashflow_rows(ledger)[0][3] == "0.00000001"


def test_a_zero_balance_is_not_recorded(live):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="0")

    assert record_auto_opening(ledger, lease, mock, ("XRP", "JPY"), now=NOW) == ()
    assert cashflow_rows(ledger) == []


def test_reads_that_differ_are_skipped_until_they_agree(live):
    ledger, lease = live
    reader = ScriptedReader(
        snapshot(XRP="3"),
        snapshot(XRP="4"),          # the second read disagrees: skip this call
        snapshot(XRP="3"),
        snapshot(XRP="3"),          # the next call agrees
    )

    assert record_auto_opening(ledger, lease, reader, ("XRP",), now=NOW) == ()
    assert cashflow_rows(ledger) == []
    assert record_auto_opening(ledger, lease, reader, ("XRP",), now=NOW + 60) == ("XRP",)
    assert cashflow_rows(ledger)[0][3] == "3"
    assert reader.calls == 4


def test_a_currency_with_a_cashflow_is_never_touched(live):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="0")
    mock.adjust_balance("XRP", "3")
    ledger.record_cashflow(lease, Cashflow("owner-xrp", "deposit", "XRP", "2", "owner_manual", NOW),
                           now=NOW)

    assert record_auto_opening(ledger, lease, mock, ("XRP",), now=NOW) == ()
    assert [row[0] for row in cashflow_rows(ledger)] == ["owner-xrp"]


def test_a_currency_with_a_fill_or_fee_history_is_never_touched(ws):
    h = Harness(ws, symbols=("OAS/JPY",))
    try:
        h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))],
                        bids=[(D("99"), D("1000"))])
        h.send(side="buy", key="seed", amount="1", price="100")
        h.mock.adjust_balance("OAS", "5")
        # The fill is history for OAS and JPY; the fee is history for JPY too.
        assert record_auto_opening(h.ledger, h.lease, h.mock, ("OAS", "JPY"), now=NOW) == ()
        assert [row[0] for row in cashflow_rows(h.ledger)] == ["cf1"]
    finally:
        h.close()


def test_a_fee_currency_is_history_so_it_is_never_touched(ws):
    h = Harness(ws, symbols=("OAS/JPY", "XRP/JPY"))
    try:
        intent = h.ledger.create_intent(
            h.lease, h.req(symbol="OAS/JPY", side="buy", key="fee-buy", amount="1",
                           price="100"), now=NOW)
        h.ledger.record_order_observation(h.lease, OrderObservation(
            exchange_order_id="fee-order", intent_id=intent.intent_id, symbol="OAS/JPY",
            side="buy", order_type="limit", amount="1", status="FULLY_FILLED",
            executed_amount="1", remaining_amount="0", price="100"), now=NOW)
        h.ledger.record_fill(h.lease, FillRecord(
            fill_id="fee-fill", exchange_order_id="fee-order", symbol="OAS/JPY", side="buy",
            amount="1", price="100", quote_notional="100", executed_at=NOW,
            fee_status="known", fee_amount="0.5", fee_currency="XRP"), now=NOW)
        h.mock.adjust_balance("XRP", "2")

        assert record_auto_opening(h.ledger, h.lease, h.mock, ("XRP",), now=NOW) == ()
        assert [row[0] for row in cashflow_rows(h.ledger)] == ["cf1"]
    finally:
        h.close()


def test_a_reader_failure_returns_without_raising_and_records_nothing(live):
    ledger, lease = live
    assert record_auto_opening(ledger, lease, ScriptedReader(RuntimeError("down")),
                               ("JPY",), now=NOW) == ()
    assert cashflow_rows(ledger) == []

    reader = ScriptedReader(snapshot(JPY="100"), RuntimeError("down again"))
    assert record_auto_opening(ledger, lease, reader, ("JPY",), now=NOW) == ()
    assert cashflow_rows(ledger) == []


def test_one_failed_write_returns_what_was_recorded_so_far(live, monkeypatch):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="100")
    mock.adjust_balance("XRP", "3")
    real = ledger.record_cashflow

    def flaky(lease_arg, flow, *, now):
        if flow.currency == "XRP":
            raise RuntimeError("write down")
        return real(lease_arg, flow, now=now)

    monkeypatch.setattr(ledger, "record_cashflow", flaky)
    # JPY (alphabetically first) is recorded, XRP fails and is left for the retry.
    assert record_auto_opening(ledger, lease, mock, ("JPY", "XRP"), now=NOW) == ("JPY",)
    assert [row[0] for row in cashflow_rows(ledger)] == ["auto_opening:JPY"]

    monkeypatch.setattr(ledger, "record_cashflow", real)
    assert record_auto_opening(ledger, lease, mock, ("JPY", "XRP"), now=NOW + 60) == ("XRP",)


def test_lease_lost_from_the_reader_propagates(live):
    ledger, lease = live
    with pytest.raises(LeaseLost):
        record_auto_opening(ledger, lease, ScriptedReader(LeaseLost("gone")),
                            ("JPY",), now=NOW)


def test_lease_lost_from_a_write_propagates_after_the_partial_result(live, monkeypatch):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="100")
    mock.adjust_balance("XRP", "3")
    real = ledger.record_cashflow

    def flaky(lease_arg, flow, *, now):
        if flow.currency == "XRP":
            raise LeaseLost("gone")
        return real(lease_arg, flow, now=now)

    monkeypatch.setattr(ledger, "record_cashflow", flaky)
    with pytest.raises(LeaseLost):
        record_auto_opening(ledger, lease, mock, ("JPY", "XRP"), now=NOW)
    assert [row[0] for row in cashflow_rows(ledger)] == ["auto_opening:JPY"]


def test_a_rejected_duplicate_id_is_swallowed(live, monkeypatch):
    ledger, lease = live
    mock, _ = make_mock(Clock(), symbols=("XRP/JPY",), jpy="0")
    mock.adjust_balance("XRP", "3")
    # Pretend the id is already taken without exposing it through currencies_with_history:
    # the ledger itself rejects the insert and the call must not raise.
    ledger.record_cashflow(lease, Cashflow("auto_opening:XRP", "deposit", "XRP", "3",
                                           "auto_opening", NOW), now=NOW)
    monkeypatch.setattr(opening.views, "currencies_with_history", lambda ledger: frozenset())

    assert record_auto_opening(ledger, lease, mock, ("XRP",), now=NOW) == ()
    assert len(cashflow_rows(ledger)) == 1
