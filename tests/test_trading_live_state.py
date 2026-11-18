"""LIVE L1: ledger, on/off control file, lease, safety latch.  No network, nothing sent."""
import ast
import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from docich.trading.live import ledger as lg
from docich.trading.live import safety as sf
from docich.trading.live.lease import LeaseHeld, LeaseLost, acquire_writer_lease, release_writer_lease
from docich.trading.live.paths import LiveLayoutError, ensure_live_root, live_layout

from test_trading_live_support import Workspace, block_network, policy_set

SRC = Path(lg.__file__).parents[3]
LIVE_SRC = Path(lg.__file__).parent
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def ready(ws):
    """Open ledger + lease + stored policy set."""
    ledger = ws.open_ledger()
    lease = acquire_writer_lease(ledger, ws.layout, now=NOW)
    ps = policy_set()
    ledger.store_policy_set(lease, ps, now=NOW)
    yield ws, ledger, lease, ps
    try:
        release_writer_lease(ledger, lease, now=NOW)
    except Exception:
        pass
    ledger.close()


def _req(ps, symbol="OAS/JPY", side="buy", key="k1", **kw):
    return lg.IntentRequest(decision_key=key, symbol=symbol, side=side, order_type=kw.pop("order_type", "limit"),
                            amount="10", price="100", policy_digest=ps.policy_for(symbol).digest,
                            rule_id="entry-1", **kw)


# ---- layout / inertness -------------------------------------------------
def test_layout_is_pure_and_separate_from_paper(ws):
    layout = live_layout(ws.state)
    assert layout.root == ws.state / "trading-live" and not layout.root.exists()
    assert layout.root != ws.paper and ws.paper not in layout.root.parents


def test_live_root_rejects_symlink_and_paper_overlap(ws):
    with pytest.raises(LiveLayoutError):
        ensure_live_root(ws.layout, paper_dir=ws.layout.root)
    (ws.state / "elsewhere").mkdir()
    ws.layout.root.symlink_to(ws.state / "elsewhere")
    with pytest.raises(LiveLayoutError):
        ensure_live_root(ws.layout, paper_dir=ws.paper)


def test_import_live_package_has_no_side_effects_or_network_modules(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.ledger, docich.trading.live.control, docich.trading.live.policy
        import docich.trading.live.lease, docich.trading.live.safety, docich.trading.live.paths
        import docich.trading.live.exchange, docich.trading.live.mock_exchange
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests") if m in sys.modules]
        assert not bad, bad
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert out.stdout.strip() == "clean", out.stderr


def test_live_sources_import_allowlist():
    banned = {"socket", "ssl", "ccxt", "http", "urllib", "requests", "subprocess", "importlib", "asyncio"}
    for path in LIVE_SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            assert not (set(names) & banned), (path.name, names)


def test_paper_worker_and_cli_do_not_import_live():
    for name in ("worker.py", "cli.py", "dashboard_snapshot.py", "slot_runtime.py"):
        assert ".live" not in (SRC / "docich" / "trading" / name).read_text(encoding="utf-8"), name


def test_human_only_apis_have_no_call_sites_in_src():
    for name in ("clear_safety_trip", "record_owner_activation", "clear_safety"):
        for path in (SRC / "docich").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if name in line and "def " not in line and not line.lstrip().startswith(("#", '"""')):
                    # the thin wrapper in safety.py is the only allowed reference
                    assert path.relative_to(SRC / "docich").as_posix() in (
                        "trading/live/safety.py", "trading/live/ledger.py", "trading/live_owner/cli.py"), (path, line)


# ---- ledger basics ------------------------------------------------------------
def test_open_modes_and_wal(ws):
    ledger = ws.open_ledger()
    assert oct(ws.layout.root.stat().st_mode & 0o777) == "0o700"
    assert oct(ws.layout.ledger_path.stat().st_mode & 0o777) == "0o600"
    with ledger.read_cursor() as cur:
        assert cur.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert cur.execute("PRAGMA user_version").fetchone()[0] == lg.LIVE_SCHEMA_VERSION
    ledger.close()


def test_open_is_idempotent_and_keeps_the_ledger_id(ws):
    first = ws.open_ledger()
    with first.read_cursor() as cur:
        ledger_id = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()[0]
    first.close()
    second = ws.open_ledger(now=2000.0)
    with second.read_cursor() as cur:
        assert cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()[0] == ledger_id
    second.close()


def test_open_require_new_is_exclusive_and_keeps_existing_ledger(ws):
    first = lg.LiveLedger.open(ws.layout, paper_dir=ws.paper, now=NOW, require_new=True)
    with first.read_cursor() as cur:
        ledger_id = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()[0]
    first.close()
    assert oct(ws.layout.ledger_path.stat().st_mode & 0o777) == "0o600"

    with pytest.raises(FileExistsError):
        lg.LiveLedger.open(ws.layout, paper_dir=ws.paper, now=NOW + 1, require_new=True)

    ledger = lg.LiveLedger.open_readonly(ws.layout)
    try:
        with ledger.read_cursor() as cur:
            assert cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()[0] == ledger_id
    finally:
        ledger.close()


def test_open_refuses_newer_schema_paper_and_foreign_db(ws):
    ws.layout.root.mkdir(mode=0o700)
    conn = sqlite3.connect(ws.layout.ledger_path)
    conn.execute("PRAGMA user_version=9")
    conn.commit()
    conn.close()
    with pytest.raises(lg.LiveLedgerError, match="schema_too_new"):
        ws.open_ledger()
    ws.layout.ledger_path.unlink()
    conn = sqlite3.connect(ws.layout.ledger_path)
    conn.execute("CREATE TABLE paper_fills(x)")
    conn.commit()
    conn.close()
    with pytest.raises(lg.LiveLedgerError, match="wrong_kind"):
        ws.open_ledger()


def test_readonly_open_never_creates(ws):
    with pytest.raises(lg.LiveLedgerError):
        lg.LiveLedger.open_readonly(ws.layout)
    assert not ws.layout.root.exists()


def test_append_only_tables_reject_update_and_delete(ready):
    ws, ledger, lease, ps = ready
    sf.trip(ledger, sf.SafetyReason.DATA_ANOMALY, detail="x", now=NOW)  # row triggers need a row
    for sql in ("UPDATE events SET kind='x'", "DELETE FROM events", "UPDATE policies SET payload='{}'",
                "DELETE FROM policy_sets", "DELETE FROM safety_trips"):
        with pytest.raises(sqlite3.DatabaseError):
            ledger._conn.execute(sql)


def test_event_hash_chain_detects_tamper(ready):
    ws, ledger, lease, ps = ready
    assert ledger.verify_event_chain() and len(ledger.events()) >= 3
    ledger._conn.execute("DROP TRIGGER events_no_update")
    ledger._conn.execute("UPDATE events SET payload='{}' WHERE event_id=2")
    assert not ledger.verify_event_chain()


def test_policy_store_roundtrip_and_tamper_detected(ready):
    ws, ledger, lease, ps = ready
    policy = ps.policy_for("OAS/JPY")
    assert ledger.load_policy(policy.digest).digest == policy.digest
    assert ledger.load_policy_set(ps.digest).digest == ps.digest
    ledger._conn.execute("DROP TRIGGER policies_no_update")
    ledger._conn.execute("UPDATE policies SET payload=replace(payload,'-120','-100') WHERE policy_digest=?",
                         (policy.digest,))
    with pytest.raises(Exception):
        ledger.load_policy(policy.digest)


# ---- intents ---------------------------------------------------------------------
def test_create_intent_is_idempotent_and_conflicts_raise(ready):
    ws, ledger, lease, ps = ready
    first = ledger.create_intent(lease, _req(ps), now=NOW)
    again = ledger.create_intent(lease, _req(ps), now=NOW + 1)
    assert first.intent_id == again.intent_id and first.state == "PLANNED"
    other = lg.IntentRequest("k1", "OAS/JPY", "buy", "limit", "11", ps.policy_for("OAS/JPY").digest, "entry-1", "100")
    with pytest.raises(lg.LiveLedgerError):
        ledger.create_intent(lease, other, now=NOW)


def test_one_open_intent_per_symbol_and_side(ready):
    ws, ledger, lease, ps = ready
    ledger.create_intent(lease, _req(ps, key="a"), now=NOW)
    with pytest.raises(lg.LiveLedgerError):
        ledger.create_intent(lease, _req(ps, key="b"), now=NOW)
    ledger.create_intent(lease, _req(ps, symbol="XYM/JPY", key="c"), now=NOW)


def test_unknown_policy_and_missing_limit_price_are_rejected(ready):
    ws, ledger, lease, ps = ready
    with pytest.raises(lg.LiveLedgerError):
        ledger.create_intent(lease, lg.IntentRequest("z", "OAS/JPY", "buy", "limit", "1", "f" * 64, "entry-1", "1"), now=NOW)
    with pytest.raises(lg.LiveLedgerError):
        ledger.create_intent(lease, lg.IntentRequest("y", "OAS/JPY", "buy", "limit", "1",
                                                     ps.policy_for("OAS/JPY").digest, "entry-1", None), now=NOW)


def test_intent_transitions_only_those_listed(ready):
    ws, ledger, lease, ps = ready
    states = list(lg._TRANSITIONS) + ["FILLED", "CANCELED", "EXPIRED", "REJECTED", "ABANDONED", "NOT_CREATED"]
    intent = ledger.create_intent(lease, _req(ps), now=NOW)
    for frm in states:
        for to in states + ["BOGUS"]:
            if to in lg._TRANSITIONS.get(frm, set()):
                continue
            ledger._conn.execute("DROP TRIGGER IF EXISTS never")
            with ledger._tx(lease) as cur:
                cur.execute("UPDATE intents SET state=? WHERE intent_id=?", (frm if frm != "BOGUS" else "PLANNED", intent.intent_id))
                with pytest.raises(lg.LiveLedgerError):
                    ledger._move(cur, lease, intent.intent_id, frm, to, NOW)


def test_happy_path_and_unknown_submission_needs_evidence(ready):
    ws, ledger, lease, ps = ready
    intent = ledger.create_intent(lease, _req(ps), now=NOW)
    ledger.mark_submitting(lease, intent.intent_id, now=NOW)
    assert ledger.mark_in_flight_unknown(lease, now=NOW) == (intent.intent_id,)
    assert sf.read_safety_state(ledger).block_new_buys
    with pytest.raises(lg.LiveLedgerError):
        ledger.transition_intent(lease, intent.intent_id, expected_state="UNKNOWN_SUBMISSION",
                                 to_state="NOT_CREATED", now=NOW)
    done = ledger.transition_intent(lease, intent.intent_id, expected_state="UNKNOWN_SUBMISSION",
                                    to_state="NOT_CREATED", now=NOW, evidence="exchange has no such order")
    assert done.state == "NOT_CREATED" and not sf.read_safety_state(ledger).block_new_buys


def test_mark_submitting_refused_while_tripped(ready):
    ws, ledger, lease, ps = ready
    intent = ledger.create_intent(lease, _req(ps), now=NOW)
    sf.trip(ledger, sf.SafetyReason.DATA_ANOMALY, detail="x", now=NOW)
    with pytest.raises(lg.SafetyBlocked):
        ledger.mark_submitting(lease, intent.intent_id, now=NOW)


@pytest.mark.parametrize("reason", list(sf.SafetyReason))
def test_sell_goes_out_during_a_safety_stop_but_buy_does_not(ready, reason):
    ws, ledger, lease, ps = ready
    buy = ledger.create_intent(lease, _req(ps), now=NOW)
    sell = ledger.create_intent(lease, lg.IntentRequest("kx-sell", "OAS/JPY", "sell", "limit", "10",
                                                        ps.policy_for("OAS/JPY").digest, "exit-1", "101"), now=NOW)
    sf.trip(ledger, reason, detail="x", now=NOW)
    with pytest.raises(lg.SafetyBlocked):
        ledger.mark_submitting(lease, buy.intent_id, now=NOW)
    assert ledger.mark_submitting(lease, sell.intent_id, now=NOW).state == "SUBMITTING"


def _sell(ps, key):
    return lg.IntentRequest(key, "OAS/JPY", "sell", "limit", "10", ps.policy_for("OAS/JPY").digest, "exit-1", "101")


def test_an_unknown_buy_does_not_pin_the_exit(ready):
    ws, ledger, lease, ps = ready
    buy = ledger.create_intent(lease, _req(ps), now=NOW)
    ledger.mark_submitting(lease, buy.intent_id, now=NOW)
    ledger.mark_in_flight_unknown(lease, now=NOW)
    sell = ledger.create_intent(lease, _sell(ps, "kx-sell"), now=NOW)
    assert ledger.mark_submitting(lease, sell.intent_id, now=NOW).state == "SUBMITTING"


def test_no_second_sell_can_be_planned_while_the_first_sell_is_unknown(ready):
    ws, ledger, lease, ps = ready
    ledger.create_intent(lease, _req(ps), now=NOW)
    first = ledger.create_intent(lease, _sell(ps, "kx-sell-1"), now=NOW)
    ledger.mark_submitting(lease, first.intent_id, now=NOW)
    ledger.mark_in_flight_unknown(lease, now=NOW)
    with pytest.raises(lg.LiveLedgerError):
        ledger.create_intent(lease, _sell(ps, "kx-sell-2"), now=NOW)


def _run_child(ws, body):
    code = textwrap.dedent(f"""
        import os, signal, sys
        sys.path[:0] = [{str(SRC)!r}, {str(Path(__file__).parent)!r}]
        from test_trading_live_support import Workspace, policy_set
        from docich.trading.live import ledger as lg
        from docich.trading.live.lease import acquire_writer_lease
        from pathlib import Path
        w = Workspace.__new__(Workspace)
        from docich.trading.live.paths import live_layout
        w.state = Path({str(ws.state)!r}); w.paper = w.state / "trading"; w.layout = live_layout(w.state)
        ledger = w.open_ledger(); lease = acquire_writer_lease(ledger, w.layout, now=1.0)
        ps = policy_set(); ledger.store_policy_set(lease, ps, now=1.0)
        def die(point):
            if point == {body!r}: os.kill(os.getpid(), signal.SIGKILL)
        lg.FAULT_HOOK = die
        req = lg.IntentRequest("kx", "OAS/JPY", "buy", "limit", "10", ps.policy_for("OAS/JPY").digest, "entry-1", "100")
        i = ledger.create_intent(lease, req, now=2.0)
        ledger.mark_submitting(lease, i.intent_id, now=3.0)
    """)
    return subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)


def test_crash_between_intent_insert_and_commit_leaves_nothing(ws):
    result = _run_child(ws, "intent_inserted")
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = ws.open_ledger()
    with ledger.read_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    ledger.close()


def test_crash_after_submitting_becomes_unknown_and_blocks_buys(ws):
    result = _run_child(ws, "after_submitting_commit")
    assert result.returncode == -signal.SIGKILL, result.stderr
    ledger = ws.open_ledger()
    lease = acquire_writer_lease(ledger, ws.layout, now=NOW)
    assert len(ledger.mark_in_flight_unknown(lease, now=NOW)) == 1
    assert sf.read_safety_state(ledger).block_new_buys
    ledger.close()


def test_sell_must_use_entry_policy_and_position_keeps_it(ready):
    ws, ledger, lease, ps = ready
    buy = ledger.create_intent(lease, _req(ps), now=NOW)
    old_digest = ps.policy_for("OAS/JPY").digest
    sell = ledger.create_intent(lease, lg.IntentRequest("s1", "OAS/JPY", "sell", "market", "10", old_digest,
                                                        "exit-1"), now=NOW)
    assert sell.position_id == buy.position_id
    other = policy_set(("OAS/JPY",))
    other_spec = __import__("test_trading_live_support").synthetic_mapping()
    other_spec["exit_rules"][0]["conditions"][0]["threshold"] = "70"
    from docich.trading.live.policy import build_policy_set
    from docich.trading.strategy_lab import experiment_from_mapping
    other_ps = build_policy_set(experiment_from_mapping(other_spec), ("OAS/JPY",))
    ledger.store_policy_set(lease, other_ps, now=NOW)
    with pytest.raises(lg.LiveLedgerError):  # no open position under the new policy
        ledger.create_intent(lease, lg.IntentRequest("s2", "OAS/JPY", "sell", "market", "1",
                                                     other_ps.policy_for("OAS/JPY").digest, "exit-1"), now=NOW)
    with pytest.raises(sqlite3.DatabaseError, match="entry under|entered under"):
        ledger._conn.execute(
            "INSERT INTO intents(intent_id, decision_key, symbol, side, order_type, amount, policy_digest, "
            "position_id, rule_id, state, lease_epoch, created_at, updated_at) VALUES ('x','dk','OAS/JPY','sell',"
            "'market','1',?,?,'exit-1','PLANNED',1,1,1)", (other_ps.policy_for("OAS/JPY").digest, buy.position_id))


def test_buys_under_a_new_policy_open_a_separate_position(ready):
    ws, ledger, lease, ps = ready
    a = ledger.create_intent(lease, _req(ps), now=NOW)
    ledger.transition_intent(lease, a.intent_id, expected_state="PLANNED", to_state="ABANDONED", now=NOW)
    mapping = __import__("test_trading_live_support").synthetic_mapping()
    mapping["entry_rules"][0]["conditions"][0]["threshold"] = "-130"
    from docich.trading.live.policy import build_policy_set
    from docich.trading.strategy_lab import experiment_from_mapping
    new_ps = build_policy_set(experiment_from_mapping(mapping), ("OAS/JPY",))
    ledger.store_policy_set(lease, new_ps, now=NOW)
    b = ledger.create_intent(lease, lg.IntentRequest("n1", "OAS/JPY", "buy", "limit", "5",
                                                     new_ps.policy_for("OAS/JPY").digest, "entry-1", "100"), now=NOW)
    assert a.position_id != b.position_id


# ---- exchange facts ------------------------------------------------------------------
def _order(eid="o1", intent_id=None):
    return lg.OrderObservation(eid, "OAS/JPY", "buy", "limit", "10", "UNFILLED", "0", "10", intent_id=intent_id, price="100")


def test_unknown_exchange_order_trips_unknown_order(ready):
    ws, ledger, lease, ps = ready
    ledger.record_order_observation(lease, _order(), now=NOW)
    assert [t.reason for t in sf.read_safety_state(ledger).trips] == ["unknown_order"]


def test_known_order_does_not_trip(ready):
    ws, ledger, lease, ps = ready
    intent = ledger.create_intent(lease, _req(ps), now=NOW)
    ledger.record_order_observation(lease, _order(intent_id=intent.intent_id), now=NOW)
    ledger.record_order_observation(lease, _order(), now=NOW + 1)  # later poll without the link
    assert not sf.read_safety_state(ledger).trips


def _fill(fid="f1", **kw):
    base = dict(fill_id=fid, exchange_order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
                quote_notional="1000", executed_at=NOW, fee_status="known", fee_amount="1.2", fee_currency="JPY")
    base.update(kw)
    return lg.FillRecord(**base)


def test_unknown_fee_is_recorded_null_and_trips_fee_missing(ready):
    ws, ledger, lease, ps = ready
    intent = ledger.create_intent(lease, _req(ps), now=NOW)
    ledger.record_order_observation(lease, _order(intent_id=intent.intent_id), now=NOW)
    assert ledger.record_fill(lease, _fill(fee_status="unknown", fee_amount=None, fee_currency=None), now=NOW)
    assert [t.reason for t in sf.read_safety_state(ledger).trips] == ["fee_missing"]
    with ledger.read_cursor() as cur:
        assert cur.execute("SELECT fee_amount FROM fills").fetchone()[0] is None


def test_duplicate_fill_is_a_noop_and_conflicting_fill_trips_anomaly(ready):
    ws, ledger, lease, ps = ready
    intent = ledger.create_intent(lease, _req(ps), now=NOW)
    ledger.record_order_observation(lease, _order(intent_id=intent.intent_id), now=NOW)
    assert ledger.record_fill(lease, _fill(), now=NOW) is True
    assert ledger.record_fill(lease, _fill(), now=NOW + 5) is False
    with pytest.raises(lg.LiveLedgerError):
        ledger.record_fill(lease, _fill(price="101"), now=NOW + 6)
    assert [t.reason for t in sf.read_safety_state(ledger).trips] == ["data_anomaly"]


def test_fill_for_an_unknown_order_is_rejected(ready):
    ws, ledger, lease, ps = ready
    with pytest.raises(lg.LiveLedgerError):
        ledger.record_fill(lease, _fill(), now=NOW)


def test_cashflows_are_append_only_and_validated(ready):
    ws, ledger, lease, ps = ready
    ledger.record_cashflow(lease, lg.Cashflow("c1", "deposit", "JPY", "10000", "owner_manual", NOW), now=NOW)
    with pytest.raises(lg.LiveLedgerError):
        ledger.record_cashflow(lease, lg.Cashflow("c1", "deposit", "JPY", "10000", "owner_manual", NOW), now=NOW)
    with pytest.raises(lg.LiveLedgerError):
        ledger.record_cashflow(lease, lg.Cashflow("c2", "deposit", "JPY", "-5", "owner_manual", NOW), now=NOW)
    with pytest.raises(sqlite3.DatabaseError):
        ledger._conn.execute("DELETE FROM cashflows")


# ---- policy activation -------------------------------------------------------------------
def test_owner_activation_needs_operator_and_note_and_promotion_needs_owner_initial(ready):
    ws, ledger, lease, ps = ready
    with pytest.raises(lg.LiveLedgerError):
        ledger.record_activation(lease, ps, reason="promotion", evidence_digest="e" * 64, now=NOW)
    with pytest.raises(ValueError):
        ledger.record_owner_activation(lease, ps, operator="", note="n", now=NOW)
    with pytest.raises(ValueError):
        ledger.record_owner_activation(lease, ps, operator="owner", note="", now=NOW)
    with ledger.read_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM policy_activations").fetchone()[0] == 0
    ledger.record_owner_activation(lease, ps, operator="owner", note="initial", now=NOW)
    assert ledger.active_policy_set_digest() == ps.digest
    ledger.record_activation(lease, ps, reason="promotion", evidence_digest="e" * 64, now=NOW + 1)
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT reason, actor FROM policy_activations ORDER BY activation_id").fetchall()
    assert rows == [("owner_initial", "owner"), ("promotion", "promotion_judge")]


# ---- lease -------------------------------------------------------------------------------
def test_second_writer_cannot_acquire_in_process_or_in_another_process(ready):
    ws, ledger, lease, ps = ready
    with pytest.raises(LeaseHeld):
        acquire_writer_lease(ledger, ws.layout, now=NOW)
    code = textwrap.dedent(f"""
        import sys
        sys.path[:0] = [{str(SRC)!r}, {str(Path(__file__).parent)!r}]
        from pathlib import Path
        from docich.trading.live.paths import live_layout
        from docich.trading.live import ledger as lg
        from docich.trading.live.lease import acquire_writer_lease, LeaseHeld
        layout = live_layout(Path({str(ws.state)!r}))
        ledger = lg.LiveLedger.open(layout, paper_dir=Path({str(ws.paper)!r}), now=5.0)
        try:
            acquire_writer_lease(ledger, layout, now=5.0)
        except LeaseHeld:
            print("held")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "held", out.stderr


def test_reacquire_after_release_increments_epoch(ws):
    ledger = ws.open_ledger()
    first = acquire_writer_lease(ledger, ws.layout, now=NOW)
    release_writer_lease(ledger, first, now=NOW)
    second = acquire_writer_lease(ledger, ws.layout, now=NOW)
    assert second.epoch == first.epoch + 1
    release_writer_lease(ledger, second, now=NOW)
    ledger.close()


def test_stale_epoch_write_raises_lease_lost(ready):
    ws, ledger, lease, ps = ready
    ledger._conn.execute("UPDATE writer_lease SET epoch=epoch+1")
    with pytest.raises(LeaseLost):
        ledger.create_intent(lease, _req(ps), now=NOW)


def test_replaced_lock_file_is_lease_lost(ready):
    ws, ledger, lease, ps = ready
    os.rename(ws.layout.lock_path, ws.layout.lock_path.with_name("old.lock"))
    ws.layout.lock_path.write_text("")
    with pytest.raises(LeaseLost):
        ledger.create_intent(lease, _req(ps), now=NOW)


def test_trip_is_allowed_without_a_lease(ws):
    ledger = ws.open_ledger()
    assert sf.trip(ledger, sf.SafetyReason.LEASE_LOST, detail="x", now=NOW)
    ledger.close()


# ---- safety latch -----------------------------------------------------------------------
@pytest.mark.parametrize("reason", list(sf.SafetyReason))
def test_each_reason_blocks_new_buys(ws, reason):
    ledger = ws.open_ledger()
    sf.trip(ledger, reason, detail="d", now=NOW)
    state = sf.read_safety_state(ledger)
    assert state.block_new_buys
    ledger.close()


def test_trip_is_idempotent_and_survives_reopen(ws):
    ledger = ws.open_ledger()
    assert sf.trip(ledger, sf.SafetyReason.DATA_ANOMALY, detail="a", now=NOW)
    assert not sf.trip(ledger, sf.SafetyReason.DATA_ANOMALY, detail="b", now=NOW)
    ledger.close()
    again = ws.open_ledger(now=NOW + 5)
    assert len(sf.read_safety_state(again).trips) == 1
    again.close()


def test_clear_needs_operator_note_evidence_and_keeps_other_trips(ws):
    ledger = ws.open_ledger()
    sf.trip(ledger, sf.SafetyReason.DATA_ANOMALY, detail="a", now=NOW)
    sf.trip(ledger, sf.SafetyReason.FEE_MISSING, detail="b", now=NOW)
    with pytest.raises(ValueError):
        sf.clear_safety_trip(ledger, sf.SafetyReason.DATA_ANOMALY, operator="", note="n", evidence="e", now=NOW)
    sf.clear_safety_trip(ledger, sf.SafetyReason.DATA_ANOMALY, operator="owner", note="checked", evidence="ticket", now=NOW)
    assert [t.reason for t in sf.read_safety_state(ledger).trips] == ["fee_missing"]
    assert any(e.kind == "safety_cleared" for e in ledger.events())
    with pytest.raises(sqlite3.DatabaseError):
        ledger._conn.execute("UPDATE safety_trips SET cleared_at=NULL WHERE reason='data_anomaly'")
    ledger.close()


def test_unreadable_ledger_reads_as_blocked():
    class Broken:
        def read_cursor(self):
            raise sqlite3.DatabaseError("corrupt")
    state = sf.read_safety_state(Broken())
    assert state.block_new_buys and not state.ledger_readable


def test_list_intents_is_read_only_and_ordered_and_intents_carry_times(ready):
    ws, ledger, lease, ps = ready
    first = ledger.create_intent(lease, _req(ps), now=NOW)
    second = ledger.create_intent(lease, _req(ps, symbol="XYM/JPY", key="k2"), now=NOW + 1)
    listed = ledger.list_intents(states=("PLANNED",))
    assert [i.intent_id for i in listed] == [first.intent_id, second.intent_id]
    assert listed[0].created_at == NOW and listed[0].updated_at == NOW and listed[0].client_order_id is None
    assert ledger.list_intents(states=()) == [] and ledger.list_intents(states=("FILLED",)) == []
