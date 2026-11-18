"""Deposit-history reconciliation while LIVE runs (Issue #4587, under #3623). Synthetic only.

An exchange deposit the ledger does not count is, in order: classified as part of the opening
snapshot (baseline), still in flight (pending), already ingested (duplicate), already covered
by an owner cashflow (already_counted), conflicting under its id (conflict) — or recorded once
under the exchange's own id.  A truncated history is flagged: absence then proves nothing.
Everything is read-only on the exchange and warning-only on the ledger: nothing stops trading.
"""
from decimal import Decimal as D

import pytest

from docich.trading.live import balance as bal
from docich.trading.live import deposits, reconcile, views
from docich.trading.live.exchange import Capabilities
from docich.trading.live.ledger import Cashflow
from test_trading_live_support import Harness, Workspace, block_network

CAPS = Capabilities(client_order_id=True, fetch_by_client_id=True, fills_by_order=True,
                    client_order_id_max_len=32, deposit_history=True)
NO_CAPS = Capabilities(client_order_id=True, fetch_by_client_id=True, fills_by_order=True,
                       client_order_id_max_len=32)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


def _harness(ws, **kw):
    h = Harness(ws, caps=kw.pop("caps", CAPS), **kw)
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
    return h


def _check(h):
    snapshot = h.mock.fetch_balance()
    return bal.check_balances(h.ledger, snapshot, h.ex.rules(), currencies=h.ex.watched_currencies())


def _cashflow_ids(h):
    return sorted(row.cashflow_id for row in views.cashflow_rows(h.ledger))


def test_a_deposit_the_exchange_reports_is_recorded_and_the_books_close(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        record_id = h.mock.external_deposit("JPY", "5000")          # the owner wires money in, live
        checks = deposits.run(h.ex)
        assert checks["JPY"].recorded == 1 and checks["JPY"].complete
        row = [r for r in views.cashflow_rows(h.ledger) if r.cashflow_id == record_id][0]
        assert row.kind == "deposit" and row.amount == D("5000") and row.source == "exchange_history"
        assert _check(h).proven(["JPY", "OAS"])                     # expected caught up with actual
    finally:
        h.close()


def test_reconciliation_is_idempotent_across_a_restart(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000")
        assert deposits.run(h.ex)["JPY"].recorded == 1
        assert deposits.run(h.ex)["JPY"].duplicate == 1             # a second look writes nothing
        h.crash()                                                  # the process dies; the ledger persists
        reconcile.on_startup(h.ex)                                 # the startup path reconciles again
        assert len(views.cashflow_rows(h.ledger)) == 2             # opening + the one deposit
        assert deposits.run(h.ex)["JPY"].duplicate == 1
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_an_owner_recorded_deposit_is_recognized_and_not_counted_twice(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000")
        h.deposit("JPY", "5000")                                   # the owner already wrote it up
        checks = deposits.run(h.ex)
        assert checks["JPY"].already_counted == 1 and checks["JPY"].recorded == 0
        assert len(views.cashflow_rows(h.ledger)) == 2
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_a_deposit_inside_the_opening_snapshot_is_baseline_not_double_counted(ws):
    h = _harness(ws)
    try:
        # This deposit predates the books: its money is part of the recorded opening 10000 JPY,
        # so the exchange does not credit it again (credit=False keeps the balance at 10000).
        h.mock.external_deposit("JPY", "10000", occurred_ms=1_700_000_000_000 - 3_600_000,
                                uid="old-1", credit=False)
        checks = deposits.run(h.ex)
        assert checks["JPY"].baseline == 1 and checks["JPY"].recorded == 0
        assert len(views.cashflow_rows(h.ledger)) == 1
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_a_deposit_the_exchange_has_not_credited_is_pending_not_books(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000", status="CONFIRMED", credit=False)
        checks = deposits.run(h.ex)
        assert checks["JPY"].pending == 1 and checks["JPY"].recorded == 0
        assert len(views.cashflow_rows(h.ledger)) == 1
        h.mock._deposits[0].status = "DONE"                        # the exchange confirms it later
        h.mock.adjust_balance("JPY", "5000")
        assert deposits.run(h.ex)["JPY"].recorded == 1
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_a_truncated_history_is_flagged_so_a_missing_row_proves_nothing(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000", uid="d1", occurred_ms=h.clock.ms + 1)
        h.mock.external_deposit("JPY", "2000", uid="d2", occurred_ms=h.clock.ms + 2000)
        h.mock.faults.push("fetch_deposits", "TRUNCATED_PAGE")     # the page may be cut off
        checks = deposits.run(h.ex)
        assert checks["JPY"].complete is False and checks["JPY"].recorded == 1
        assert not _check(h).proven(["JPY"])                       # d2 still unexplained: stays a warning
        checks = deposits.run(h.ex)                                # full read next pass: no double count
        assert checks["JPY"].complete and checks["JPY"].duplicate == 1 and checks["JPY"].recorded == 1
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_a_record_id_that_reappears_with_different_content_is_a_conflict_not_an_overwrite(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.ledger.record_cashflow(h.lease, Cashflow("mock:deposit:d1", "deposit", "JPY", "4000",
                                                   "owner_manual", h.clock.seconds()),
                                 now=h.clock.seconds())
        h.mock.external_deposit("JPY", "5000", uid="d1")           # the exchange says 5000 under the same id
        checks = deposits.run(h.ex)
        assert checks["JPY"].conflict == 1 and checks["JPY"].recorded == 0
        row = [r for r in views.cashflow_rows(h.ledger) if r.cashflow_id == "mock:deposit:d1"][0]
        assert row.amount == D("4000")                             # append-only: never overwritten
    finally:
        h.close()


def test_an_unexplained_withdrawal_stays_a_balance_warning_and_never_stops_trading(ws):
    h = _harness(ws)
    try:
        h.send(side="buy", key="b1", amount="10", price="100")
        h.mock.adjust_balance("OAS", "-3")                         # an outside withdrawal, no history for it
        assert deposits.run(h.ex)["OAS"].seen == 0
        assert h.ex.check_balance()
        h.advance(30)
        h.ex.check_balance()
        assert h.ex.monitor.warning_currencies == ("OAS",)         # the difference is reported, not guessed
        assert h.trips() == []
        sell, _ = h.send(side="sell", key="s1", amount="5", price="99")
        assert h.state(sell.intent_id) == "FILLED"                 # nothing blocks the exit
    finally:
        h.close()


def test_a_currency_with_no_books_is_left_to_the_opening_balance_path(ws):
    h = _harness(ws, opening=False)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000", credit=False)
        assert deposits.run(h.ex) == {}                            # no history: opening owns the baseline
        assert views.cashflow_rows(h.ledger) == []
    finally:
        h.close()


def test_a_reader_without_the_capability_is_never_polled(ws):
    h = _harness(ws, caps=NO_CAPS)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000")
        assert deposits.run(h.ex) == {}
        assert len(views.cashflow_rows(h.ledger)) == 1             # only the opening cashflow
    finally:
        h.close()


def test_fills_fees_and_a_deposit_reconcile_to_the_exchange_balance(ws):
    h = _harness(ws)
    try:
        h.send(side="buy", key="b1", amount="10", price="100")     # 1000 JPY + 1.0 JPY taker fee
        h.advance(120)
        h.mock.external_deposit("JPY", "5000")
        checks = deposits.run(h.ex)
        assert checks["JPY"].recorded == 1
        jpy = {c.currency: c for c in _check(h).checks}["JPY"]
        assert jpy.expected == D("10000") - D("1000") - D("1.0") + D("5000")
        assert _check(h).proven(["JPY", "OAS"])
    finally:
        h.close()


def test_each_pass_writes_one_cashflow_check_event(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000")
        deposits.run(h.ex)
        deposits.run(h.ex)
        events = views.events_of_kind(h.ledger, "cashflow_check")
        assert len(events) == 2
        last = events[-1].payload["currencies"]["JPY"]
        assert last["recorded"] == 0 and last["duplicate"] == 1 and last["complete"] == 1
    finally:
        h.close()


def test_the_step_reconciles_and_the_report_carries_the_classification(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "5000")
        report = h.ex.step()
        assert report.deposits["JPY"].recorded == 1
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_a_deposit_found_before_the_opening_but_credited_after_it_is_not_baseline(ws):
    h = _harness(ws)
    try:
        # The exchange saw the deposit five minutes before the books opened, but the money only
        # hit the balance two minutes after (found_at before, confirmed_at after the baseline).
        # It is not part of the opening snapshot, so it is recorded, not absorbed by it (#4617).
        h.advance(120)
        h.mock.external_deposit("JPY", "5000", occurred_ms=1_700_000_000_000 - 300_000,
                                confirmed_ms=1_700_000_000_000 + 120_000, uid="cross")
        checks = deposits.run(h.ex)
        assert checks["JPY"].baseline == 0 and checks["JPY"].recorded == 1
        assert len(views.cashflow_rows(h.ledger)) == 2
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_a_deposit_with_no_reported_credit_time_is_not_guessed_into_the_baseline(ws):
    h = _harness(ws)
    try:
        # An exchange that says DONE but reports no confirmed_at: nothing proves the money was
        # inside the opening snapshot, so the safe side is to count it (#4617).
        h.advance(120)
        h.mock.external_deposit("JPY", "5000", occurred_ms=1_700_000_000_000 - 300_000,
                                confirmed_ms=0, uid="nograded")
        checks = deposits.run(h.ex)
        assert checks["JPY"].baseline == 0 and checks["JPY"].recorded == 1
        assert len(views.cashflow_rows(h.ledger)) == 2
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_one_owner_cashflow_covers_one_deposit_not_every_equal_one(ws):
    h = _harness(ws)
    try:
        # Two equal deposits three seconds apart, one owner cashflow: the earlier one is covered,
        # the later one is a real deposit the books do not count yet and is recorded (#4617).
        h.advance(120)
        now_ms = h.clock.ms
        h.mock.external_deposit("JPY", "5000", occurred_ms=now_ms, uid="first")
        h.mock.external_deposit("JPY", "5000", occurred_ms=now_ms + 3000, uid="second")
        h.deposit("JPY", "5000")                                    # the owner wrote one of them up
        checks = deposits.run(h.ex)
        assert checks["JPY"].already_counted == 1 and checks["JPY"].recorded == 1
        assert len(views.cashflow_rows(h.ledger)) == 3              # opening + owner + the second
        assert _check(h).proven(["JPY"])
        h.crash()                                                   # the same assignment replays
        reconcile.on_startup(h.ex)
        checks = deposits.run(h.ex)
        assert (checks["JPY"].already_counted, checks["JPY"].duplicate, checks["JPY"].recorded) == (1, 1, 0)
        assert len(views.cashflow_rows(h.ledger)) == 3
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_two_owner_cashflows_cover_two_equal_deposits_in_a_deterministic_order(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        now_ms = h.clock.ms
        h.mock.external_deposit("JPY", "5000", occurred_ms=now_ms, uid="first")
        h.mock.external_deposit("JPY", "5000", occurred_ms=now_ms + 3000, uid="second")
        h.deposit("JPY", "5000")
        h.deposit("JPY", "5000")
        checks = deposits.run(h.ex)
        assert checks["JPY"].already_counted == 2 and checks["JPY"].recorded == 0
        assert len(views.cashflow_rows(h.ledger)) == 3              # opening + the two owner rows
        assert _check(h).proven(["JPY"])
    finally:
        h.close()


def test_one_pass_distinguishes_baseline_duplicate_pending_and_a_cut_page(ws):
    h = _harness(ws)
    try:
        h.advance(120)
        h.mock.external_deposit("JPY", "10000", occurred_ms=1_700_000_000_000 - 3_600_000,
                                uid="d-old", credit=False)          # inside the opening snapshot
        h.mock.external_deposit("JPY", "5000", uid="d-seen")        # ingested once already
        deposits.run(h.ex)
        h.mock.external_deposit("JPY", "7000", uid="d-wait", status="FOUND", credit=False)
        checks = deposits.run(h.ex)
        jpy = checks["JPY"]
        assert (jpy.baseline, jpy.duplicate, jpy.pending, jpy.recorded) == (1, 1, 1, 0)
        h.mock.faults.push("fetch_deposits", "TRUNCATED_PAGE")
        assert deposits.run(h.ex)["JPY"].complete is False          # incomplete: absence proves nothing
    finally:
        h.close()
