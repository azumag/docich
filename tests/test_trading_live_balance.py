"""Balance reconciliation: ledger-expected vs exchange balances (Issue #3623, L2b)."""
from decimal import Decimal as D

import pytest

from docich.trading.live import balance as bal
from docich.trading.live.mock_exchange import MockFees
from test_trading_live_support import Harness, Workspace, block_network


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


def _harness(ws, **kw):
    h = Harness(ws, **kw)
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
    return h


def _check(h):
    snapshot = h.mock.fetch_balance()
    return bal.check_balances(h.ledger, snapshot, h.ex.rules(), currencies=h.ex.watched_currencies())


def test_expected_balance_follows_cashflows_fills_and_known_fees(ws):
    h = _harness(ws)
    try:
        assert _check(h).proven(["JPY", "OAS"])
        h.send(side="buy", key="b1", amount="10", price="100")
        result = _check(h)
        assert result.proven(["JPY", "OAS"])
        jpy = {c.currency: c for c in result.checks}["JPY"]
        assert jpy.expected == D("10000") - D("1000") - D("1.0")
        h.send(side="sell", key="s1", amount="10", price="99")
        assert _check(h).proven(["JPY", "OAS"])
    finally:
        h.close()


@pytest.mark.parametrize("fee_currency", ["quote", "base"])
def test_unknown_fee_widens_both_currencies_symmetrically_and_stays_ok(ws, fee_currency):
    h = _harness(ws, fees=MockFees(maker_rate=D("0"), taker_rate=D("0.001"), currency=fee_currency))
    try:
        h.mock.faults.push("fill", "FEE_MISSING")
        h.send(side="buy", key="b1", amount="50", price="100")
        for _ in range(5):
            h.ex.step()
        assert _check(h).proven(["JPY", "OAS"])
    finally:
        h.close()


def test_a_real_discrepancy_beyond_the_fee_slack_is_a_mismatch_even_with_unknown_fees(ws):
    h = _harness(ws)
    try:
        h.mock.faults.push("fill", "FEE_MISSING")
        h.send(side="buy", key="b1", amount="50", price="100")
        for _ in range(5):
            h.ex.step()
        h.mock.adjust_balance("JPY", "-30")      # slack is 0.2% of 5000 = 10 JPY
        assert {c.currency: c.status for c in _check(h).checks}["JPY"] == "mismatch"
    finally:
        h.close()


def test_without_a_max_fee_rate_unknown_fees_make_the_currency_indeterminate(ws):
    h = _harness(ws)
    try:
        h.mock.faults.push("fill", "FEE_MISSING")
        h.send(side="buy", key="b1", amount="10", price="100")
        for _ in range(5):
            h.ex.step()
        rules = {s: r.__class__(**{**r.__dict__, "max_fee_rate": None}) for s, r in h.ex.rules().items()}
        result = bal.check_balances(h.ledger, h.mock.fetch_balance(), rules, currencies=["JPY", "OAS"])
        assert {c.status for c in result.checks} == {"indeterminate"} and not result.proven(["JPY"])
    finally:
        h.close()


def test_the_tolerance_is_a_rounding_unit_not_a_percentage(ws):
    h = _harness(ws)
    try:
        h.send(side="buy", key="b1", amount="10", price="100")
        h.mock.adjust_balance("JPY", "0.5")
        assert _check(h).mismatches()                # half a yen is a mismatch
        h.mock.adjust_balance("JPY", "-0.4999")
        assert not _check(h).mismatches()            # within the rounding units
    finally:
        h.close()


def test_a_balance_that_moves_during_the_check_is_not_judged(ws):
    h = _harness(ws)
    try:
        h.send(side="buy", key="b1", amount="10", price="100")

        def between():
            h.mock.adjust_balance("JPY", "1")

        result, stable = bal.stable_check(h.ledger, h.mock, h.ex.rules(), currencies=["JPY"], between=between)
        assert result is None and stable is False
    finally:
        h.close()


def test_a_failing_order_lookup_is_retried_and_never_trips(ws):
    h = _harness(ws)
    try:
        h.send(side="buy", key="b1", amount="10", price="90")          # resting
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
        for _ in range(20):
            h.mock.faults.push("fetch_order", "READ_TIMEOUT")
        h.ex.step()
        h.advance(40)
        h.ex.step()
        assert h.trips() == []                       # the ledger is behind, so nothing is judged
        h.mock.faults._items.clear()
        h.advance(40)
        h.ex.step()
        h.advance(40)
        h.ex.step()
        assert h.trips() == []
    finally:
        h.close()
