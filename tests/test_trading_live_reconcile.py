"""Crash injection and restart reconciliation of the LIVE executor (Issue #3623, L2b)."""
import json
import os
import signal
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import executor as live_executor
from docich.trading.live import ingest as live_ingest
from docich.trading.live import ledger as live_ledger
from docich.trading.live import reconcile, views
from docich.trading.live import safety as sf
from docich.trading.live.exchange import Capabilities, OrderRequest
from test_trading_live_support import Harness, SimulatedCrash, Workspace, block_network

SRC = Path(live_ledger.__file__).parents[3]
TESTS = Path(__file__).parent

CAPS = {"client_id": None,   # the Harness default: client order ids, lookup by id
        "list_match": Capabilities(False, False, True)}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


def _harness(ws, caps_name, **kw):
    h = Harness(ws, caps=CAPS[caps_name], **kw)
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
    return h


def _two_levels(h):
    h.mock.set_book("OAS/JPY", asks=[(D("100"), D("4")), (D("101"), D("1000"))], bids=[(D("99"), D("1000"))])


def _die_at(point):
    def hook(p):
        if p == point:
            raise SimulatedCrash(point)
    live_executor.FAULT_HOOK = live_ingest.FAULT_HOOK = live_ledger.FAULT_HOOK = hook


def _settle(h, rounds=4):
    for _ in range(rounds):
        h.ex.step()
        h.advance(121)


def _crash_during_send(h, point, **kw):
    _die_at(point)
    with pytest.raises(SimulatedCrash):
        h.send(**kw)
    live_executor.FAULT_HOOK = live_ingest.FAULT_HOOK = live_ledger.FAULT_HOOK = None
    h.crash()
    return reconcile.on_startup(h.ex)


def _only_intent(h):
    intents = h.ledger.list_intents(states=tuple(live_ledger._TRANSITIONS) + (
        "FILLED", "CANCELED", "EXPIRED", "REJECTED", "ABANDONED", "NOT_CREATED"))
    assert len(intents) == 1
    return intents[0]


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_before_mark_submitting_leaves_a_stale_plan_that_is_abandoned(ws, caps):
    h = _harness(ws, caps)
    try:
        counts = _crash_during_send(h, "before_mark_submitting")
        assert counts["abandoned"] == 1 and _only_intent(h).state == "ABANDONED"
        assert h.mock.submissions == [] and h.trips() == []
        again, outcome = h.send(key="k2")                       # a new decision key goes out normally
        assert h.state(again.intent_id) == "FILLED"
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_right_after_mark_submitting_is_unknown_then_not_created_never_resent(ws, caps):
    h = _harness(ws, caps)
    try:
        counts = _crash_during_send(h, "after_submitting_commit")
        intent = _only_intent(h)
        assert counts["unknown"] == 1 and intent.state == "UNKNOWN_SUBMISSION"
        assert sf.read_safety_state(h.ledger).block_new_buys
        h.ex.step()
        assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"        # not before the settle protocol
        _settle(h)
        assert h.state(intent.intent_id) == "NOT_CREATED" and h.mock.submissions == []
        assert not sf.read_safety_state(h.ledger).block_new_buys and h.trips() == []
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_after_the_send_before_the_record_finds_the_order_and_never_sends_twice(ws, caps):
    h = _harness(ws, caps)
    try:
        counts = _crash_during_send(h, "after_send_before_record")
        intent = _only_intent(h)
        assert counts["acked"] == 1 and h.state(intent.intent_id) == "FILLED"
        assert len(h.mock.submissions) == 1 and h.trips() == []
        assert sum(f.amount for f in views.all_fills(h.ledger)) == 10
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_after_the_order_was_recorded_but_before_the_state_moved(ws, caps):
    h = _harness(ws, caps)
    try:
        counts = _crash_during_send(h, "after_order_recorded")
        intent = _only_intent(h)
        assert h.state(intent.intent_id) == "FILLED" and len(h.mock.submissions) == 1
        assert h.trips() == []                                   # the ledger's own link: no UNKNOWN_ORDER
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_after_the_ack_transition_continues_polling(ws, caps):
    h = _harness(ws, caps)
    try:
        _crash_during_send(h, "after_ack_transition")
        assert h.state(_only_intent(h).intent_id) == "FILLED" and h.trips() == []
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_between_two_fills_records_each_exactly_once(ws, caps):
    h = _harness(ws, caps)
    _two_levels(h)
    try:
        _crash_during_send(h, "fill_recorded:1", price="101")   # 4 @100 and 6 @101: two fills
        assert len(views.all_fills(h.ledger)) in (1, 2)
        intent = _only_intent(h)
        h.ex.step()
        assert h.state(intent.intent_id) == "FILLED"
        fills = views.all_fills(h.ledger)
        assert sorted(f.amount for f in fills) == [4, 6] and h.trips() == []
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_crash_before_the_terminal_transition(ws, caps):
    h = _harness(ws, caps)
    try:
        _crash_during_send(h, "before_terminal_transition")
        assert h.state(_only_intent(h).intent_id) == "FILLED"
        assert len(views.all_fills(h.ledger)) == 1 and h.trips() == []
    finally:
        h.close()


def _holding_and_stopped(h):
    """10 OAS held, and a safety stop is on (so only exits are allowed)."""
    buy, _ = h.send(side="buy", key="b0", amount="10", price="100")
    assert h.state(buy.intent_id) == "FILLED"
    sf.trip(h.ledger, sf.SafetyReason.DATA_ANOMALY, detail="test", now=h.clock.seconds())
    return buy


def _sell_intent(h):
    return [i for i in h.ledger.list_intents(states=("UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED", "FILLED",
                                                      "NOT_CREATED", "ABANDONED", "CANCELED")) if i.side == "sell"][0]


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
@pytest.mark.parametrize("point", ["after_send_before_record", "after_order_recorded", "after_ack_transition",
                                   "before_terminal_transition"])
def test_a_sell_that_crashes_during_a_safety_stop_still_completes_and_closes_the_position(ws, caps, point):
    h = _harness(ws, caps)
    try:
        buy = _holding_and_stopped(h)
        _crash_during_send(h, point, side="sell", key="s1", amount="10", price="99")
        sell = _sell_intent(h)
        assert h.state(sell.intent_id) == "FILLED" and len(
            [s for s in h.mock.submissions if s["side"] == "sell"]) == 1
        with h.ledger.read_cursor() as cur:
            assert cur.execute("SELECT status FROM positions WHERE position_id=?",
                               (buy.position_id,)).fetchone()[0] == "closed"
        assert h.trips() == ["data_anomaly"]            # the owner's stop is untouched
    finally:
        h.close()


@pytest.mark.parametrize("caps", ["client_id", "list_match"])
def test_a_sell_lost_before_the_exchange_is_proven_not_created_by_base_alone_and_a_new_sell_goes_out(ws, caps):
    h = _harness(ws, caps)
    try:
        _holding_and_stopped(h)
        h.mock.adjust_balance("JPY", "-50")             # a JPY discrepancy must not pin the exit
        _crash_during_send(h, "after_submitting_commit", side="sell", key="s1", amount="10", price="99")
        sell = _sell_intent(h)
        assert sell.state == "UNKNOWN_SUBMISSION"
        _settle(h)
        assert h.state(sell.intent_id) == "NOT_CREATED"
        again, outcome = h.send(side="sell", key="s2", amount="10", price="99")
        assert h.state(again.intent_id) == "FILLED"
    finally:
        h.close()


def test_a_sell_killed_with_sigkill_after_the_send_completes_after_restart(ws, tmp_path):
    mock_state = tmp_path / "mock.json"
    code_result = _run_child_sell(ws, mock_state)
    assert code_result.returncode == -signal.SIGKILL, code_result.stderr
    h = Harness(ws, gate_open=False, opening=False, state_path=mock_state)
    try:
        reconcile.on_startup(h.ex)
        _settle(h)
        sell = [i for i in h.ledger.list_intents(states=("FILLED", "UNKNOWN_SUBMISSION", "ACKED")) if i.side == "sell"][0]
        assert sell.state == "FILLED" and len([s for s in h.mock.submissions if s["side"] == "sell"]) == 1
    finally:
        h.close()


def _run_child_sell(ws, mock_state):
    code = textwrap.dedent(f"""
        import os, signal, sys
        sys.path[:0] = [{str(SRC)!r}, {str(TESTS)!r}]
        from decimal import Decimal as D
        from pathlib import Path
        from test_trading_live_support import Workspace, Harness
        from docich.trading.live import executor as ex_mod, safety as sf
        from docich.trading.live.paths import live_layout
        w = Workspace.__new__(Workspace)
        w.state = Path({str(ws.state)!r}); w.paper = w.state / "trading"; w.layout = live_layout(w.state)
        h = Harness(w, state_path=Path({str(mock_state)!r}))
        h.mock.set_book("OAS/JPY", asks=[(D("100"), D("1000"))], bids=[(D("99"), D("1000"))])
        h.send(side="buy", key="b0", amount="10", price="100")
        sf.trip(h.ledger, sf.SafetyReason.DATA_ANOMALY, detail="t", now=h.clock.seconds())
        def die(p):
            if p == "after_send_before_record":
                os.kill(os.getpid(), signal.SIGKILL)
        ex_mod.FAULT_HOOK = die
        h.send(side="sell", key="s1", amount="10", price="99")
    """)
    return subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)


def test_startup_is_idempotent(ws):
    h = _harness(ws, "client_id")
    try:
        _crash_during_send(h, "after_send_before_record")
        before = [(i.intent_id, i.state) for i in h.ledger.list_intents(states=("FILLED",))]
        again = reconcile.on_startup(h.ex)
        assert again == {"abandoned": 0, "unknown": 0, "acked": 0, "not_created": 0}
        assert [(i.intent_id, i.state) for i in h.ledger.list_intents(states=("FILLED",))] == before
        assert len(views.all_fills(h.ledger)) == 1
    finally:
        h.close()


def test_startup_with_an_unreachable_exchange_leaves_the_unknown_in_place_and_buys_stopped(ws):
    h = _harness(ws, "client_id")
    try:
        _die_at("after_send_before_record")
        with pytest.raises(SimulatedCrash):
            h.send()
        live_executor.FAULT_HOOK = live_ingest.FAULT_HOOK = live_ledger.FAULT_HOOK = None
        h.crash()
        h.mock.faults.push("fetch_balance", "READ_TIMEOUT")   # the first read in the resolution attempt fails
        reconcile.on_startup(h.ex)
        assert _only_intent(h).state == "UNKNOWN_SUBMISSION" and sf.read_safety_state(h.ledger).block_new_buys
        reconcile.on_startup(h.ex)
        assert _only_intent(h).state == "FILLED"
    finally:
        h.close()


def test_startup_finds_the_owners_manual_order_and_stops_buys(ws):
    h = _harness(ws, "client_id")
    try:
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", D("5"), D("50"), None))
        reconcile.on_startup(h.ex)
        assert h.trips() == ["unknown_order"]
    finally:
        h.close()


def test_an_acknowledged_order_survives_a_restart_and_keeps_filling(ws):
    h = _harness(ws, "client_id")
    try:
        intent, _ = h.send(price="90")
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="3")
        h.crash()
        reconcile.on_startup(h.ex)
        assert h.state(intent.intent_id) == "PARTIALLY_FILLED"
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="7")
        h.ex.step()
        assert h.state(intent.intent_id) == "FILLED" and len(views.all_fills(h.ledger)) == 2 and h.trips() == []
    finally:
        h.close()


def test_two_orders_that_both_match_an_unknown_submission_are_an_anomaly_not_a_guess(ws):
    h = _harness(ws, "list_match")
    try:
        h.mock.faults.push("submit", "LOST_RESPONSE")
        intent, _ = h.send(price="90")
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", D("10"), D("90"), None))
        h.ex.step()
        assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION" and "data_anomaly" in h.trips()
    finally:
        h.close()


def test_an_unknown_order_that_does_not_match_stays_unlinked_and_the_submission_is_not_guessed(ws):
    h = _harness(ws, "list_match")
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(price="90")
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", D("7"), D("90"), None))
        _settle(h)
        assert h.state(intent.intent_id) == "NOT_CREATED" and "unknown_order" in h.trips()
    finally:
        h.close()


# ---- a real process death (SIGKILL), the mock "exchange" survives in its state file -------------
def _run_child(ws, mock_state, point):
    code = textwrap.dedent(f"""
        import os, signal, sys
        sys.path[:0] = [{str(SRC)!r}, {str(TESTS)!r}]
        from decimal import Decimal as D
        from pathlib import Path
        from test_trading_live_support import Workspace, Harness
        from docich.trading.live import executor as ex_mod, ingest as ing_mod, ledger as lg_mod
        from docich.trading.live.paths import live_layout
        w = Workspace.__new__(Workspace)
        w.state = Path({str(ws.state)!r}); w.paper = w.state / "trading"; w.layout = live_layout(w.state)
        h = Harness(w, state_path=Path({str(mock_state)!r}))
        h.mock.set_book("OAS/JPY", asks=[(D("100"), D("4")), (D("101"), D("1000"))])
        def die(p):
            if p == {point!r}:
                os.kill(os.getpid(), signal.SIGKILL)
        ex_mod.FAULT_HOOK = ing_mod.FAULT_HOOK = lg_mod.FAULT_HOOK = die
        h.send(price="101")
    """)
    return subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)


@pytest.mark.parametrize("point", ["after_submitting_commit", "after_send_before_record", "after_order_recorded",
                                   "fill_recorded:1"])
def test_sigkill_at_a_named_point_then_restart_recovers_without_a_second_order(ws, tmp_path, point):
    mock_state = tmp_path / "mock.json"
    result = _run_child(ws, mock_state, point)
    assert result.returncode == -signal.SIGKILL, result.stderr
    h = Harness(ws, gate_open=False, opening=False, state_path=mock_state)
    try:
        reconcile.on_startup(h.ex)
        _settle(h)
        intent = _only_intent(h)
        if point == "after_submitting_commit":                   # nothing reached the exchange
            assert intent.state == "NOT_CREATED" and h.mock.submissions == []
        else:
            assert intent.state == "FILLED" and len(h.mock.submissions) == 1
            assert sorted(f.amount for f in views.all_fills(h.ledger)) == [4, 6]
        assert h.trips() == [] and h.ledger.verify_event_chain()
    finally:
        h.close()


# ---- Devin review of L2b ------------------------------------------------------------------
def test_a_cut_off_order_list_never_links_a_manual_order_to_a_lost_submission(ws):
    h = _harness(ws, "list_match")
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(price="90")
        for _ in range(2):   # two manual orders with exactly the attributes of the lost one
            h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", D("10"), D("90"), None))
        h.mock.faults.push("fetch_open_orders", "TRUNCATED_PAGE")
        h.ex.step()
        assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"
        assert views.order_for_intent(h.ledger, intent.intent_id) is None
        assert all(i.state != "ACKED" for i in h.ledger.list_intents(states=("ACKED",)))
    finally:
        h.close()


def test_a_skipped_symbol_keeps_its_own_history_cursor_so_manual_trades_are_still_found(ws):
    from docich.trading.live.mock_exchange import MockFees
    h = Harness(ws, symbols=("OAS/JPY", "XYM/JPY"), fees=MockFees(D("0"), D("0")))
    for symbol in h.symbols:
        h.mock.set_book(symbol, asks=[(D("100"), D("1000"))], bids=[(D("100"), D("1000"))])
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(symbol="OAS/JPY", price="90")
        for _ in range(6):                                  # the lookups fail for ~12 minutes (> skew margin)
            h.mock.faults.push("fetch_order_by_client_id", "READ_TIMEOUT")
        h.ex.step()                                         # OAS is now skipped by the sweep; XYM is read
        h.advance(30)
        # a manual round trip on OAS (balances end where they started) while OAS is skipped
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", D("1"), D("100"), None))
        h.mock.place_external_order(OrderRequest("OAS/JPY", "sell", "limit", D("1"), D("100"), None))
        for _ in range(12):
            h.advance(121)
            h.ex.step()
        assert h.state(intent.intent_id) == "NOT_CREATED"
        assert "unknown_order" in h.trips()                 # found later from OAS's own, unmoved cursor
    finally:
        h.close()


def test_fill_history_is_read_from_a_fixed_start_even_without_order_times(ws):
    h = _harness(ws, "client_id")
    try:
        real = h.mock.fetch_order

        def no_times(symbol, eid):
            from dataclasses import replace
            return replace(real(symbol, eid), ordered_at_ms=None)

        h.mock.fetch_order = no_times
        intent, _ = h.send(price="90")
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="4")
        for _ in range(3):
            h.mock.faults.push("fetch_fills", "READ_TIMEOUT")
        h.ex.step()
        h.advance(400)                          # longer than the skew margin
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="6")
        for _ in range(3):
            h.ex.step()
        assert h.state(intent.intent_id) == "FILLED" and sorted(f.amount for f in views.all_fills(h.ledger)) == [4, 6]
        assert h.trips() == []
    finally:
        h.close()


def test_a_crash_between_the_terminal_state_and_closing_the_position_is_healed_at_startup(ws):
    h = _harness(ws, "client_id")
    try:
        buy, _ = h.send(side="buy", key="b0", amount="10", price="100")
        _crash_during_send(h, "before_close_position", side="sell", key="s1", amount="10", price="99")
        with h.ledger.read_cursor() as cur:
            assert cur.execute("SELECT status FROM positions WHERE position_id=?",
                               (buy.position_id,)).fetchone()[0] == "closed"
        again, _ = h.send(side="buy", key="b2", amount="5", price="100")
        assert again.position_id != buy.position_id
    finally:
        h.close()


# ---- linking an order found by hand ---------------------------------------------------------
def test_link_found_order_records_the_owner_reason_and_evidence(ws):
    h = _harness(ws, "client_id")
    try:
        h.mock.faults.push("submit", "LOST_RESPONSE")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="100")
        assert outcome is live_executor.SubmitOutcome.UNKNOWN and h.state(intent.intent_id) == "UNKNOWN_SUBMISSION"
        snap = h.mock.all_orders()[0]
        assert reconcile.link_found_order(h.ex, h.ledger.get_intent(intent.intent_id), snap, "owner",
                                          evidence="checked by hand") == "acked"
        after = h.ledger.get_intent(intent.intent_id)
        assert after.state in ("ACKED", "PARTIALLY_FILLED", "FILLED")
        assert after.reason_code == "found_by_owner"
        with h.ledger.read_cursor() as cur:
            evidence = cur.execute("SELECT evidence FROM intents WHERE intent_id=?",
                                   (intent.intent_id,)).fetchone()[0]
        reasons = [json.loads(e.payload)["payload"].get("reason") for e in h.ledger.events(limit=1000)
                   if e.kind == "intent_state" and e.entity_id == intent.intent_id]
        assert evidence == "checked by hand" and "found_by_owner" in reasons
    finally:
        h.close()


def test_link_found_order_refuses_a_snapshot_that_changed_the_recorded_order(ws):
    h = _harness(ws, "client_id")
    try:
        # the order row exists (the ack was recorded), but the crash and an unreadable lookup keep the
        # intent UNKNOWN: nothing has been linked to it yet
        _die_at("after_order_recorded")
        with pytest.raises(SimulatedCrash):
            h.send(side="buy", key="b1", amount="10", price="90")
        live_executor.FAULT_HOOK = live_ingest.FAULT_HOOK = live_ledger.FAULT_HOOK = None
        h.crash()
        h.mock.faults.push("fetch_order", "READ_TIMEOUT")
        reconcile.on_startup(h.ex)
        intent = _only_intent(h)
        assert intent.state == "UNKNOWN_SUBMISSION" and h.trips() == []
        assert views.order_for_intent(h.ledger, intent.intent_id) is not None
        snap = h.mock.all_orders()[0]
        changed = replace(snap, amount=snap.amount + 1, remaining_amount=snap.remaining_amount + 1)
        assert reconcile.link_found_order(h.ex, h.ledger.get_intent(intent.intent_id), changed, "owner") == "anomaly"
        assert h.state(intent.intent_id) == "UNKNOWN_SUBMISSION" and "data_anomaly" in h.trips()
    finally:
        h.close()


# ---- the owner CLI matches orders with the resolver's own rule -------------------------------
def _snapshot(**kw):
    values = dict(symbol="OAS/JPY", side="buy", order_type="limit", amount=D("10"), price=D("90"),
                  ordered_at_ms=5_000)
    values.update(kw)
    return SimpleNamespace(**values)


def test_matches_intent_is_the_public_name_of_the_existing_predicate():
    assert reconcile._matches is reconcile.matches_intent
    intent = SimpleNamespace(symbol="OAS/JPY", side="buy", order_type="limit", amount="10", price="90")
    assert reconcile.matches_intent(intent, _snapshot(), 0, 10_000) is True
    assert reconcile._matches(intent, _snapshot(), 0, 10_000) is True
    assert reconcile.matches_intent(intent, _snapshot(), 0, 1_000) is False          # outside the window
    assert reconcile.matches_intent(intent, _snapshot(amount=D("11")), 0, 10_000) is False
    assert reconcile.matches_intent(intent, _snapshot(side="sell"), 0, 10_000) is False
    assert reconcile.matches_intent(intent, _snapshot(price=D("91")), 0, 10_000) is False
    assert reconcile.matches_intent(intent, _snapshot(ordered_at_ms=None), 0, 0) is True
