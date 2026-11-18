"""Shadow simulation and its read-only comparison (Issue #3623, L4c).

Everything here is synthetic: the real LIVE ledger, lease, policy storage and mock
exchange from the shared harness, plus a PAPER SQLite file created with the real
``PaperLedger`` schema.  No test opens a socket, sends an order or writes a real
intent/fill/position.
"""
from __future__ import annotations

import dataclasses
import io
import sqlite3
from decimal import Decimal as D

import pytest

from docich.trading.ledger import PaperLedger
from docich.trading.live import decide as decide_module
from docich.trading.live import shadow, shadow_report, views
from docich.trading.live.act import decide_and_act as act_decide
from docich.trading.live.cycle import CycleState
from docich.trading.live.lease import LeaseLost
from docich.trading.live.sizing import OrderPlan, SymbolMarket
from docich.trading.live.signals import LiveSignals
from docich.trading.live_owner import cli as owner_cli
from docich.trading.models import AllocationDecision

from test_trading_live_support import Harness, Workspace, block_network
import test_trading_live_act as act_tests
import test_trading_live_decide as decide_tests

NOW = decide_tests.NOW


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def harnesses(ws):
    made = []

    def make(**kw):
        harness = Harness(ws, **kw)
        made.append(harness)
        return harness

    yield make
    for harness in made:
        harness.close()


def _counts(ledger) -> dict[str, int]:
    with ledger.read_cursor() as cur:
        return {
            "intents": cur.execute("SELECT COUNT(*) FROM intents").fetchone()[0],
            "orders": cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "fills": cur.execute("SELECT COUNT(*) FROM fills").fetchone()[0],
            "positions": cur.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
        }


def _rig(ws, h, *, sender=None, symbols=("OAS/JPY",), rules=None, stop_requested=None):
    ps = decide_tests.policy_set_for(*symbols)
    decide_tests.store(h, ps)
    rules = rules or {symbol: decide_tests.rule(symbol) for symbol in symbols}
    return act_tests.make_rig(ws, h, ps=ps, rules=rules, symbols=symbols, sender=sender,
                              stop_requested=stop_requested)


def _buy_rig(ws, h, *, sender=None):
    rig = _rig(ws, h, sender=sender)
    buy = decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1")
    return rig, buy


# ---- a. the normal worker hook writes one simulated fill, no real row -----------------
def test_shadow_hook_records_a_fill_without_real_intents_fills_or_positions(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    run = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))

    assert run is not None and len(run.fills) == 1
    fill = run.fills[0]
    assert (fill.symbol, fill.side, fill.amount, fill.price, fill.status) == (
        "OAS/JPY", "buy", D("5"), D("100"), "filled")
    assert fill.notional == D("500") and fill.fee == D("0.5")
    assert fill.decision_key.startswith("shadow1:")
    assert fill.policy_digest == rig.ps.policy_for("OAS/JPY").digest
    assert fill.experiment_digest == rig.ps.policy_for("OAS/JPY").experiment_digest
    assert len(fill.position_id.split(":")[0]) == 8
    assert int(fill.position_id.split(":")[0], 16) >= 0
    assert fill.bar_ts_ms == int(NOW * 1000) and fill.timeframe_s == 300

    rows = views.events_of_kind(h.ledger, "shadow_fill")
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["mode"] == "shadow"
    assert payload["decision_key"] == fill.decision_key
    assert payload["policy_digest"] == fill.policy_digest
    assert payload["experiment_digest"] == fill.experiment_digest
    assert payload["position_id"] == fill.position_id
    assert payload["levels_used"] == 1
    assert payload["status"] == "filled"
    for value in payload.values():
        assert isinstance(value, (str, int)) and not isinstance(value, bool)
    assert h.ledger.verify_event_chain()

    assert _counts(h.ledger) == {"intents": 0, "orders": 0, "fills": 0, "positions": 0}


# ---- b. replay + duplicate suppression across a fresh CycleState -----------------------
def test_a_fresh_state_replays_events_and_does_not_fill_the_same_plan_twice(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    first = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    assert first is not None and len(first.fills) == 1

    second = shadow.run_shadow(rig.deps, CycleState(cycle_number=1),
                               decide_tests.make_ctx(rig))
    assert second is not None and second.fills == ()
    assert len(views.events_of_kind(h.ledger, "shadow_fill")) == 1
    assert len(second.book.positions) == 1
    assert len(second.book.fill_ids) == 1
    assert not hasattr(second.book, "fills")


# ---- c. price-limited VWAP, partial depth, rounding, no fake full fill ----------------
def _plan(*, side="buy", amount="4", price="102", key="shadow1:test:buy", required="1000",
          position_id=None):
    return OrderPlan(
        decision_key=key, opportunity_id="opp-1", symbol="OAS/JPY", side=side,
        amount=D(amount), price=D(price), order_type="limit", policy_digest="a" * 64,
        rule_id="entry-1", position_id=position_id, required_quote=D(required), ttl_s=45,
        reason_code="entry_signal", notes=())


def _market(*, asks=(("100", "1"), ("101", "2")), bids=(("100", "1"), ("99", "2")),
            fee="0", step="0.01"):
    return SymbolMarket(
        rules=decide_tests.rule("OAS/JPY", fee=fee, step=step),
        book=decide_tests.book("OAS/JPY", bids=bids, asks=asks),
        last_close=D("100"))


def test_simulate_is_a_partial_multilevel_vwap_stopped_by_depth():
    execution = shadow.simulate_plan(_plan(), _market(), available_quote=D("10000"))
    assert execution is not None
    assert execution.status == "partial" and execution.amount == D("3")
    assert execution.remaining == D("1") and execution.levels_used == 2
    assert execution.notional == D("302")
    assert execution.price == D("302") / D("3")


def test_levels_used_counts_only_levels_the_rounded_quantity_touches():
    execution = shadow.simulate_plan(
        _plan(amount="2.5", price="102"),
        _market(asks=(("100", "2"), ("101", "10")), step="1"),
        available_quote=D("10000"))
    assert execution is not None
    assert execution.amount == D("2") and execution.notional == D("200")
    assert execution.levels_used == 1  # the 101 level is not touched after flooring


def test_simulate_respects_the_limit_price_and_skips_no_liquidity():
    limited = shadow.simulate_plan(_plan(price="100"), _market(), available_quote=D("10000"))
    assert limited is not None and limited.amount == D("1")
    assert limited.notional == D("100") and limited.status == "partial"

    assert shadow.simulate_plan(_plan(price="99"), _market(),
                                available_quote=D("10000")) is None


def test_simulate_caps_a_buy_by_the_spendable_quote_and_charges_the_fee():
    execution = shadow.simulate_plan(
        _plan(amount="5", price="100", required="1000"),
        _market(asks=(("100", "100"),), fee="0.001"), available_quote=D("200.5"))
    assert execution is not None
    # 2 * 100 + 0.2 fee = 200.2 is affordable; 3 units (300.3) is not.
    assert execution.amount == D("2")
    assert execution.notional == D("200") and execution.fee == D("0.2")
    assert execution.status == "partial"


def test_sell_vwap_and_fee_use_the_consumed_bid_levels():
    execution = shadow.simulate_plan(
        _plan(side="sell", amount="4", price="99", position_id="p1"),
        _market(bids=(("100", "2"), ("99", "2")), asks=(("100", "10"),), fee="0.001"),
        available_base=D("5"))
    assert execution is not None
    assert execution.amount == D("4") and execution.notional == D("398")
    assert execution.price == D("398") / D("4")
    assert execution.fee == D("0.398") and execution.levels_used == 2
    assert execution.status == "filled" and execution.position_id == "p1"


# ---- d. an exit uses the simulated holding, not a real position -----------------------
def test_a_shadow_exit_uses_the_simulated_holding_and_removes_it(ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    first = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    assert first is not None and first.fills and first.fills[0].side == "buy"

    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((), (sell,), (), {}))
    rig.state.cycle_number += 1
    h.clock.advance(1)
    second = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))

    assert second is not None and len(second.fills) == 1
    exit_fill = second.fills[0]
    assert exit_fill.side == "sell" and exit_fill.amount == D("5")
    assert exit_fill.position_id == first.fills[0].position_id
    assert exit_fill.decision_key.endswith(":a0")
    assert exit_fill.decision_key.split(":")[3] == exit_fill.position_id[:8]
    assert second.book.positions == {}
    assert [row.payload["side"] for row in views.events_of_kind(h.ledger, "shadow_fill")] == [
        "buy", "sell"]


# ---- e. stale book and lease lost ------------------------------------------------------
def test_a_stale_book_never_reaches_shadow_sizing_or_a_fill(ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    rig.reader.books["OAS/JPY"] = decide_tests.book(ts=int(NOW * 1000) - 16_000)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))

    run = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))

    assert run is not None and run.fills == ()
    assert views.events_of_kind(h.ledger, "shadow_fill") == []
    skips = [row.payload for row in decide_tests.decision_rows(h.ledger)]
    assert [row["reason"] for row in skips] == ["stale_book"]


def test_lease_lost_propagates_from_a_shadow_fill_and_applies_nothing(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    real = h.ledger.record_live_event

    def record(lease, kind, payload, *, now):
        if kind == "shadow_fill":
            raise LeaseLost("writer lease lost")
        return real(lease, kind, payload, now=now)

    monkeypatch.setattr(h.ledger, "record_live_event", record)
    with pytest.raises(LeaseLost):
        shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    assert views.events_of_kind(h.ledger, "shadow_fill") == []


# ---- f. a crash after append is recovered by replay ------------------------------------
class _Crash(BaseException):
    pass


def test_a_crash_after_append_leaves_the_committed_fill_for_replay(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    real = h.ledger.record_live_event

    def crash(lease, kind, payload, *, now):
        written = real(lease, kind, payload, now=now)
        if kind == "shadow_fill":
            raise _Crash(kind)
        return written

    monkeypatch.setattr(h.ledger, "record_live_event", crash)
    with pytest.raises(_Crash):
        shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    assert len(views.events_of_kind(h.ledger, "shadow_fill")) == 1

    monkeypatch.setattr(h.ledger, "record_live_event", real)
    replayed = shadow.run_shadow(rig.deps, CycleState(cycle_number=1),
                                 decide_tests.make_ctx(rig))
    assert replayed is not None and replayed.book.positions
    assert len(replayed.book.fill_ids) == 1
    assert len(views.events_of_kind(h.ledger, "shadow_fill")) == 1


# ---- g. mode changes never cross-contaminate real state --------------------------------
def test_trade_mode_does_not_write_shadow_events_or_intents(ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    assert shadow.run_shadow(rig.deps, rig.state,
                             decide_tests.make_ctx(rig)).fills
    before = len(views.events_of_kind(h.ledger, "shadow_fill"))

    monkeypatch.setattr(shadow, "run_shadow",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("shadow ran")))
    act_decide(rig.deps, rig.state, decide_tests.make_ctx(rig, mode="trade"))

    assert len(views.events_of_kind(h.ledger, "shadow_fill")) == before
    assert _counts(h.ledger)["intents"] == 0


# ---- h. a depth-limited sell may not reuse the same displayed book, even after restart --
def test_a_depth_limited_sell_book_is_not_reused_after_restart_until_refreshed(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    assert shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig)).fills

    limited = decide_tests.book("OAS/JPY", bids=(("100", "3"),), asks=(("100", "1000"),))
    rig.reader.books["OAS/JPY"] = limited
    sell = decide_tests.opportunity("OAS/JPY", "sell", oid="exit-1")
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((), (sell,), (), {}))
    rig.state.cycle_number += 1
    h.clock.advance(1)
    first_sell = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    assert first_sell is not None and len(first_sell.fills) == 1
    limited_fill = first_sell.fills[0]
    assert limited_fill.side == "sell" and limited_fill.amount == D("3")
    assert limited_fill.status == "filled" and limited_fill.levels_used == 1
    assert first_sell.book.positions["OAS/JPY"].amount == D("2")

    # A fresh process replays the depth-limited fill and knows the book was used.
    fresh = CycleState(cycle_number=4)
    again = shadow.run_shadow(rig.deps, fresh, decide_tests.make_ctx(rig))
    assert again is not None and again.fills == ()
    assert (again.skipped[0][1] if again.skipped else None) == shadow.BOOK_EXHAUSTED
    assert len([row for row in views.events_of_kind(h.ledger, "shadow_fill")
                if row.payload["side"] == "sell"]) == 1

    # A refreshed book and bar is a new observation: the remaining inventory may sell.
    refreshed = decide_tests.book("OAS/JPY", bids=(("100", "3"),),
                                  asks=(("100", "1000"),), ts=int((NOW + 1) * 1000))
    rig.reader.books["OAS/JPY"] = refreshed
    frames = {"OAS/JPY": decide_tests.frame("OAS/JPY", at=NOW + 300)}
    h.clock.advance(1)
    second_sell = shadow.run_shadow(
        rig.deps, fresh, decide_tests.make_ctx(rig, frames=frames))
    assert second_sell is not None and len(second_sell.fills) == 1
    assert second_sell.fills[0].amount == D("2")
    assert second_sell.fills[0].status == "filled"
    assert second_sell.book.positions == {}


# ---- i. malformed history fails closed ------------------------------------------------
def test_malformed_or_conflicting_shadow_history_fails_closed(ws, harnesses):
    h = harnesses()
    h.ledger.record_live_event(h.lease, "shadow_fill", {"mode": "shadow"}, now=NOW)
    with pytest.raises(shadow.ShadowBookError):
        shadow.project_shadow_book(h.ledger)
    with pytest.raises(shadow.ShadowBookError):
        shadow_report.build_shadow_comparison(h.ledger)


# ---- j. comparison/report: actual PAPER schema, digest and candidate identity ----------
def _paper_decision(fill, *, opportunity_id, strategy="lab:exp-1:entry-2",
                    amount="5", price="100"):
    amount_d, price_d = D(amount), D(price)
    return AllocationDecision(
        opportunity_id=opportunity_id, strategy_id=strategy, symbol=fill.symbol,
        side=fill.side, quote="JPY", amount=amount_d, price=price_d,
        quote_notional=amount_d * price_d, reference_notional=amount_d * price_d,
        reason_code="entry_signal")


def _write_paper(path, rows):
    ledger = PaperLedger(path)
    try:
        for decision, context in rows:
            ledger.record_fill(decision, timestamp=NOW, signal_context=context)
    finally:
        ledger.close()


def test_comparison_reads_actual_paper_schema_and_uses_the_matching_opportunity(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    run = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    fill = run.fills[0]
    paper_path = ws.paper / "paper.sqlite3"
    digest = fill.experiment_digest
    context = {"experiment_digest": digest, "bar_ts_ms": fill.bar_ts_ms}
    _write_paper(paper_path, [
        (_paper_decision(fill, opportunity_id="buy-1", amount="4.5", price="101"),
         context),
        (_paper_decision(fill, opportunity_id="buy-other"), context),
    ])

    paper = shadow_report.read_paper_fills(paper_path)
    assert paper.state == "ok" and len(paper.fills) == 2
    assert paper.fills[0].signal_context is not None
    compared = shadow_report.build_shadow_comparison(h.ledger, paper=paper)
    assert compared.evidence is True
    assert compared.paper_matched == 1 and compared.digest_verified == 1
    match = compared.matches[0]
    assert match.opportunity_id == "buy-1" and match.paper_fill_id == "paper:buy-1"
    assert match.amount_diff == D("0.5") and match.price_diff == D("-1")
    assert match.fee_diff is None
    assert [item["opportunity_id"] for item in compared.paper_without_shadow] == [
        "buy-other"]
    text = shadow_report.render_shadow_report(compared)
    assert "shadow: evidence" in text
    assert "fee.diff=unavailable" in text


def test_a_paper_row_without_experiment_digest_is_insufficient_evidence(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    run = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))
    fill = run.fills[0]
    paper_path = ws.paper / "paper.sqlite3"
    # A LIVE policy digest is not a comparable PAPER experiment digest.
    _write_paper(paper_path, [(_paper_decision(fill, opportunity_id="buy-1"),
                               {"policy_digest": fill.policy_digest})])

    paper = shadow_report.read_paper_fills(paper_path)
    compared = shadow_report.build_shadow_comparison(h.ledger, paper=paper)
    assert compared.paper_matched == 0 and compared.digest_missing == 1
    assert "paper_digest" in compared.missing_evidence
    assert compared.paper_without_shadow[0]["reason"] == "paper_digest_missing"
    assert "digest.missing: 1" in shadow_report.render_shadow_report(compared)


def test_paper_read_states_distinguish_missing_table_and_empty(ws):
    missing = shadow_report.read_paper_fills(ws.paper / "missing.sqlite3")
    assert missing.state == "missing" and missing.available is False

    empty_path = ws.paper / "empty.sqlite3"
    ledger = PaperLedger(empty_path)
    ledger.close()
    assert shadow_report.read_paper_fills(empty_path).state == "empty"

    no_table = ws.paper / "no-table.sqlite3"
    conn = sqlite3.connect(no_table)
    conn.execute("CREATE TABLE other(value TEXT)")
    conn.commit()
    conn.close()
    assert shadow_report.read_paper_fills(no_table).state == "no_table"

    broken = ws.paper / "broken.sqlite3"
    broken.write_bytes(b"not a sqlite database")
    assert shadow_report.read_paper_fills(broken).state == "read_error"


def test_comparison_distinguishes_no_evidence_from_zero_matches(ws, harnesses):
    h = harnesses()
    paper = shadow_report.read_paper_fills(ws.paper / "paper.sqlite3")
    empty = shadow_report.build_shadow_comparison(h.ledger, paper=paper)
    assert empty.evidence is False and empty.paired == 0
    text = shadow_report.render_shadow_report(empty)
    assert "shadow: no evidence" in text
    assert "paper: missing" in text
    assert "comparison.paired: 0" in text




def test_the_owner_cli_shadow_summary_reads_the_same_evidence(ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    fill = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig)).fills[0]
    paper_path = ws.paper / "paper.sqlite3"
    _write_paper(paper_path, [(_paper_decision(fill, opportunity_id="buy-1"),
                               {"experiment_digest": fill.experiment_digest,
                                "bar_ts_ms": fill.bar_ts_ms})])

    out = io.StringIO()
    code = owner_cli.main(["--state-dir", str(ws.state), "shadow", "summary"],
                          out=out, clock=lambda: NOW)
    text = out.getvalue()
    assert code == 0
    assert "shadow: evidence" in text and "paper.matched: 1" in text
    assert "digest.verified: 1" in text and "fee.diff=unavailable" in text


# ---- k. bounded pages and status counts ------------------------------------------------
def test_projection_reads_all_events_in_bounded_pages(ws, harnesses):
    h = harnesses()
    fills = []
    for index, symbol in enumerate(("OAS/JPY", "XYM/JPY", "ZEC/JPY")):
        fill = shadow.ShadowFill(
            fill_id=f"shadow:fill-{index}", decision_key=f"shadow1:digest:opp-{index}",
            opportunity_id=f"opp-{index}", position_id=f"pos-{index}", symbol=symbol,
            side="buy", amount=D("1"), price=D("100"), notional=D("100"), fee=D("0"),
            fee_currency="JPY", fee_rate=D("0"), planned_amount=D("1"),
            planned_price=D("100"), status="filled", remaining=D("0"),
            policy_digest="b" * 64, experiment_digest="c" * 64,
            executed_at_ms=int(NOW * 1000), levels_used=1,
            book_ts_ms=int(NOW * 1000), bar_ts_ms=int(NOW * 1000), timeframe_s=300)
        h.ledger.record_live_event(h.lease, "shadow_fill", fill.payload(), now=NOW)
        fills.append(fill)

    book = shadow.project_shadow_book(h.ledger, page_size=1)
    assert list(book.fill_ids) == [item.fill_id for item in fills]
    assert len(book.positions) == 3
    evidence, plans, count, positions = shadow_report.shadow_counts(h.ledger)
    assert evidence is True and plans == 0 and count == 3 and positions == 3


# ---- l. invalid fills write nothing; a residual closes replayed dust only --------------
def test_an_invalid_shadow_fill_never_reaches_the_ledger(ws, harnesses):
    h = harnesses()
    rig, _buy = _buy_rig(ws, h)
    ctx = decide_tests.make_ctx(rig)
    fill = shadow.ShadowFill(
        fill_id="shadow:invalid", decision_key="shadow1:digest:invalid",
        opportunity_id="opp-invalid", position_id="pos-invalid", symbol="OAS/JPY",
        side="buy", amount=D("1"), price=D("100"), notional=D("100"), fee=D("0.1"),
        fee_currency="JPY", fee_rate=D("0.001"), planned_amount=D("1"),
        planned_price=D("100"), status="filled", remaining=D("0"),
        policy_digest="a" * 64, experiment_digest="c" * 64,
        executed_at_ms=int(NOW * 1000), levels_used=1,
        book_ts_ms=int(NOW * 1000), bar_ts_ms=int(NOW * 1000), timeframe_s=300)
    book = shadow.ShadowBook()

    with pytest.raises(shadow.ShadowBookError):
        shadow._append_fill(rig.deps, ctx, book,
                            dataclasses.replace(fill, fee_currency="USD"))
    with pytest.raises(shadow.ShadowBookError):
        shadow._append_fill(rig.deps, ctx, book, dataclasses.replace(fill, side="sell"))

    assert views.events_of_kind(h.ledger, "shadow_fill") == []
    assert views.events_of_kind(h.ledger, "shadow_residual") == []
    assert book.positions == {} and book.fill_ids == {}
    assert _counts(h.ledger) == {"intents": 0, "orders": 0, "fills": 0, "positions": 0}


def test_a_valid_shadow_residual_clears_dust_on_replay_only(ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    ctx = decide_tests.make_ctx(rig)
    run = shadow.run_shadow(rig.deps, rig.state, ctx)
    assert run is not None and len(run.fills) == 1
    fill = run.fills[0]
    assert fill.symbol in run.book.positions

    residual = shadow.ShadowResidual(position_id=fill.position_id,
                                     symbol=fill.symbol, amount=fill.amount)
    assert shadow._append_residual(rig.deps, ctx, run.book, residual) is True
    assert run.book.positions == {}

    replayed = shadow.project_shadow_book(h.ledger)
    assert replayed.positions == {}
    assert fill.position_id in replayed.residual_ids
    assert len(views.events_of_kind(h.ledger, "shadow_residual")) == 1
    assert _counts(h.ledger) == {"intents": 0, "orders": 0, "fills": 0, "positions": 0}


# ---- m. status reads shadow counts before the ledger is closed --------------------------
def test_the_owner_cli_status_reads_shadow_counts_before_closing_the_ledger(
        ws, harnesses, monkeypatch):
    h = harnesses()
    rig, buy = _buy_rig(ws, h)
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals((buy,), (), (), {}))
    shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))

    out = io.StringIO()
    code = owner_cli.main(["--state-dir", str(ws.state), "status"],
                          out=out, clock=lambda: NOW)
    text = out.getvalue()
    assert code == 0
    assert "shadow: evidence plans=1 fills=1 positions=1" in text


# ---- n. a stop after the first shadow fill keeps earlier events only -------------------
def test_a_stop_after_the_first_shadow_fill_prevents_the_second(ws, harnesses,
                                                                monkeypatch):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    flag = {"stop": False}
    rig = _rig(ws, h, symbols=("OAS/JPY", "XYM/JPY"),
               stop_requested=lambda: flag["stop"])
    entries = (decide_tests.opportunity("OAS/JPY", "buy", oid="buy-1"),
               decide_tests.opportunity("XYM/JPY", "buy", oid="buy-2"))
    monkeypatch.setattr(decide_module, "scan_live",
                        lambda *a, **k: LiveSignals(entries, (), (), {}))
    real_append = shadow._append_fill
    appended = []

    def append_then_stop(*args, **kwargs):
        recorded = real_append(*args, **kwargs)
        appended.append(recorded)
        flag["stop"] = True
        return recorded

    monkeypatch.setattr(shadow, "_append_fill", append_then_stop)
    run = shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig))

    assert run is not None and len(run.fills) == 1
    assert len(appended) == 1 and appended[0] is not None
    assert len(views.events_of_kind(h.ledger, "shadow_fill")) == 1
    assert run.record_failures == 0


def test_a_stop_before_deciding_leaves_no_shadow_events(ws, harnesses):
    h = harnesses()
    rig, _buy = _buy_rig(ws, h)
    rig.deps = dataclasses.replace(rig.deps, stop_requested=lambda: True)

    assert shadow.run_shadow(rig.deps, rig.state, decide_tests.make_ctx(rig)) is None
    assert views.events_of_kind(h.ledger, "shadow_fill") == []
