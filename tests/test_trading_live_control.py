"""The owner's LIVE on/off control file (Issue #3623): schema, fail-closed reads, atomic writes.

The file is a plain switch: ``on``/``off`` plus ``shadow``/``trade``.  There is no expiry, no
policy digest and no code revision.  A switched-off bot abandons both buys and sells, writes no
safety trip, and recovers when switched back on without a human safety clear.  TTL cancels keep
working.  Every test is local and uses no network and no sleep.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich.trading.live import control
from docich.trading.live import executor as live_executor
from docich.trading.live import safety as live_safety
from docich.trading.live import views
from docich.trading.live.cycle import CycleDeps, CycleOutcome, CycleState, run_cycle
from docich.trading.live.enable import EnableReason
from docich.trading.live.executor import OrderExecutor, SubmitOutcome
from docich.trading.market_data import MarketFrame

from test_trading_live_support import Harness, Workspace, block_network

NOW = 1_800_000_000.0                      # 2027-01-15T08:00:00+00:00
SRC = Path(control.__file__).parents[3]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


def _payload(**changes):
    data = {"schema_version": 1, "kind": "docich-live-control", "state": "on",
            "mode": "trade", "updated_at": "2027-01-15T08:00:00+00:00",
            "updated_by": "owner"}
    data.update(changes)
    return data


def _raw(ws, value, *, perm=0o600, root_perm=0o700, owner_perm=0o700):
    """Write a raw document (a dict is encoded as JSON) at the control path."""
    layout = ws.layout
    layout.root.mkdir(mode=root_perm, exist_ok=True)
    os.chmod(layout.root, root_perm)
    layout.owner_dir.mkdir(mode=owner_perm, exist_ok=True)
    os.chmod(layout.owner_dir, owner_perm)
    text = value if isinstance(value, str) else json.dumps(value)
    layout.control_path.write_text(text, encoding="utf-8")
    os.chmod(layout.control_path, perm)
    return layout.control_path


def _reasons(result):
    return tuple(reason.value for reason in result.reasons)


# ---- reading: on / off / missing -----------------------------------------------------------
def test_a_missing_file_reads_as_off_with_file_missing_and_creates_nothing(ws):
    result = control.read_control(ws.state, now=NOW)
    assert result.enabled is False and _reasons(result) == ("file_missing",)
    assert result.mode is None and result.file_sha256 is None
    assert not ws.layout.root.exists()


def test_on_trade_enables_orders_and_hashes_the_bytes(ws):
    path = ws.write_control(mode="trade")
    result = control.read_control(ws.state, now=NOW)
    assert result.enabled is True and result.reasons == () and result.allows_orders
    assert result.mode == "trade"
    assert result.file_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_on_shadow_is_enabled_but_never_allows_orders(ws):
    ws.write_control(mode="shadow")
    result = control.read_control(ws.state, now=NOW)
    assert result.enabled is True and result.mode == "shadow"
    assert result.allows_orders is False


def test_off_reads_as_off_with_switched_off_and_keeps_the_file_mode(ws):
    ws.write_control(state="off", mode="trade")
    result = control.read_control(ws.state, now=NOW)
    assert result.enabled is False and _reasons(result) == ("switched_off",)
    assert result.mode == "trade" and result.allows_orders is False


def test_round_trip_read_write_read(ws):
    ws.write_control(state="off", mode="shadow", updated_by="alice", now=NOW)
    first = control.read_control(ws.state, now=NOW)
    assert (first.enabled, first.mode, _reasons(first)) == (False, "shadow", ("switched_off",))
    ws.write_control(state="on", mode="trade", updated_by="bob", now=NOW + 60)
    second = control.read_control(ws.state, now=NOW + 60)
    assert second.enabled and second.mode == "trade" and second.allows_orders
    view = control.describe_control(ws.state, now=NOW + 60)
    assert view["state"] == "on" and view["mode"] == "trade"
    assert view["updated_by"] == "bob"


# ---- reading: every bad document fails closed with a specific reason ----------------------
@pytest.mark.parametrize("document,reason", [
    (_payload(kind="docich-live-enable"), EnableReason.SCHEMA_INVALID),
    (_payload(extra="x"), EnableReason.UNKNOWN_KEYS),
    (_payload(state="maybe"), EnableReason.SCHEMA_INVALID),
    (_payload(mode="monitor"), EnableReason.SCHEMA_INVALID),
    (_payload(updated_at="2027-01-15T08:00:00"), EnableReason.SCHEMA_INVALID),
    (_payload(updated_at=17), EnableReason.SCHEMA_INVALID),
    (_payload(schema_version=True), EnableReason.SCHEMA_INVALID),
    (_payload(schema_version=2), EnableReason.SCHEMA_INVALID),
    (_payload(updated_by=""), EnableReason.SCHEMA_INVALID),
    (_payload(updated_by="x" * 81), EnableReason.SCHEMA_INVALID),
    ({k: v for k, v in _payload().items() if k != "updated_by"}, EnableReason.SCHEMA_INVALID),
])
def test_bad_documents_read_as_off_never_raise(ws, document, reason):
    _raw(ws, document)
    result = control.read_control(ws.state, now=NOW)
    assert result.enabled is False and _reasons(result) == (reason.value,)
    assert result.mode is None


def test_corrupt_json_and_non_dict_read_as_off(ws):
    _raw(ws, "{not json")
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("corrupt_json",)
    _raw(ws, ["not", "an", "object"])
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("schema_invalid",)


def test_too_large_wrong_mode_and_symlink_are_refused(ws):
    _raw(ws, " " * 5000)
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("too_large",)
    _raw(ws, _payload(), perm=0o644)
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("mode_not_0600",)
    real = ws.layout.control_path.with_name("real.json")
    ws.layout.control_path.rename(real)
    ws.layout.control_path.symlink_to(real)
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("not_regular",)


def test_wrong_root_or_owner_dir_mode_is_refused(ws):
    _raw(ws, _payload(), owner_perm=0o755)
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("owner_dir_invalid",)
    os.chmod(ws.layout.owner_dir, 0o700)
    os.chmod(ws.layout.root, 0o755)
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("root_invalid",)


def test_a_foreign_owner_is_refused(ws, monkeypatch):
    _raw(ws, _payload())
    real_fstat = os.fstat

    class _Foreign:
        def __init__(self, info):
            self.info = info

        @property
        def st_uid(self):
            return self.info.st_uid + 1

        def __getattr__(self, name):
            return getattr(self.info, name)

    def foreign(fd):
        return _Foreign(real_fstat(fd))

    monkeypatch.setattr(control.os, "fstat", foreign)
    assert _reasons(control.read_control(ws.state, now=NOW)) == ("owner_mismatch",)


def test_read_never_raises(ws, monkeypatch):
    _raw(ws, _payload())
    monkeypatch.setattr(control, "_dir_ok", lambda path: (_ for _ in ()).throw(RuntimeError("boom")))
    result = control.read_control(ws.state, now=NOW)
    assert result.enabled is False and _reasons(result) == ("check_error",)


# ---- describe_control: allow-listed fields only -------------------------------------------
def test_describe_control_returns_exactly_the_allow_listed_fields(ws):
    ws.write_control(mode="trade", updated_by="owner", now=NOW)
    view = control.describe_control(ws.state, now=NOW)
    assert set(view) == {"state", "mode", "updated_at", "updated_by", "reasons"}
    assert view["state"] == "on" and view["mode"] == "trade"
    assert view["updated_by"] == "owner" and view["reasons"] == []
    assert dt.datetime.fromisoformat(view["updated_at"]).tzinfo is not None
    missing = control.describe_control(ws.state / "elsewhere", now=NOW)
    assert missing["state"] == "unknown" and missing["reasons"] == ["file_missing"]


# ---- writing: modes, atomic replace, validation -------------------------------------------
def test_write_creates_0700_dirs_and_a_0600_file_with_exactly_the_schema(ws):
    path = ws.write_control(state="on", mode="trade", updated_by="owner", now=NOW)
    assert oct(ws.layout.root.stat().st_mode & 0o777) == "0o700"
    assert oct(ws.layout.owner_dir.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"schema_version", "kind", "state", "mode", "updated_at", "updated_by"}
    assert data["schema_version"] == 1 and data["kind"] == "docich-live-control"
    assert data["state"] == "on" and data["mode"] == "trade" and data["updated_by"] == "owner"
    moment = dt.datetime.fromisoformat(data["updated_at"])
    assert moment.tzinfo is not None and moment.timestamp() == NOW


def test_write_replaces_atomically_and_leaves_no_temp_file(ws):
    ws.write_control(state="on", mode="trade", now=NOW)
    path = ws.layout.control_path
    before = path.read_bytes()
    ws.write_control(state="off", mode="shadow", now=NOW + 1)
    assert path.read_bytes() != before
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "off"
    assert [p.name for p in ws.layout.owner_dir.iterdir() if p.name.startswith(".")] == []


def test_a_failed_replace_keeps_the_old_file_and_removes_the_temp(ws, monkeypatch):
    ws.write_control(state="on", mode="trade", now=NOW)
    path = ws.layout.control_path
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(control.os, "replace", boom)
    with pytest.raises(OSError):
        control.write_control(ws.state, state="off", mode="trade", updated_by="owner", now=NOW + 1)
    assert path.read_bytes() == before
    assert [p.name for p in ws.layout.owner_dir.iterdir() if p.name.startswith(".")] == []


@pytest.mark.parametrize("kwargs", [
    dict(state="maybe", mode="trade", updated_by="owner"),
    dict(state="on", mode="monitor", updated_by="owner"),
    dict(state="on", mode="trade", updated_by=""),
    dict(state="on", mode="trade", updated_by="x" * 81),
    dict(state="on", mode="trade", updated_by=7),
])
def test_write_validates_state_mode_and_updated_by(ws, kwargs):
    with pytest.raises(ValueError):
        control.write_control(ws.state, now=NOW, **kwargs)
    assert not ws.layout.root.exists()


def test_write_validates_now(ws):
    for bad in (float("nan"), float("inf"), "yesterday", None):
        with pytest.raises(ValueError):
            control.write_control(ws.state, state="on", mode="trade", updated_by="owner", now=bad)
    assert not ws.layout.root.exists()


def test_write_refuses_existing_dirs_with_the_wrong_mode_or_owner(ws, monkeypatch):
    ws.layout.root.mkdir(mode=0o755)
    os.chmod(ws.layout.root, 0o755)
    with pytest.raises(ValueError, match="mode 0700"):
        control.write_control(ws.state, state="on", mode="trade", updated_by="owner", now=NOW)

    os.chmod(ws.layout.root, 0o700)
    ws.layout.owner_dir.mkdir(mode=0o700)
    os.chmod(ws.layout.owner_dir, 0o755)
    with pytest.raises(ValueError, match="mode 0700"):
        control.write_control(ws.state, state="on", mode="trade", updated_by="owner", now=NOW)

    os.chmod(ws.layout.owner_dir, 0o700)
    real_uid = os.stat(ws.layout.root).st_uid
    monkeypatch.setattr(os, "geteuid", lambda: real_uid + 1)
    with pytest.raises(ValueError, match="not owned"):
        control.write_control(ws.state, state="on", mode="trade", updated_by="owner", now=NOW)


# ---- cycle / executor: OFF blocks both sides and trips nothing ----------------------------
class _Reader:
    def __init__(self, mock):
        self._inner = mock

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health(self):
        return SimpleNamespace(last_error_kind=None, consecutive_failures=0)

    def reload_rules(self):
        return None


class _Market:
    def __init__(self, clock):
        self.clock = clock

    def fetch_frame(self, symbol, now):
        return MarketFrame(symbol=symbol, timeframe_seconds=60, timestamps=(float(self.clock()),),
                           closes=(D("100"),), volumes=(D("1"),))


def _rig(ws, h, *, gate=None):
    clock = h.clock.seconds
    reader = _Reader(h.mock)
    gate = gate or (lambda: control.read_control(ws.state, now=clock()))
    executor = OrderExecutor(h.ledger, h.lease, reader=reader, sender=h.mock,
                             enable_check=gate, clock=clock, symbols=h.symbols,
                             config=h.config)
    hook = []

    def decide(deps, state, ctx):
        hook.append(ctx)

    deps = CycleDeps(ledger=h.ledger, lease=h.lease, executor=executor, reader=reader,
                     market_data=_Market(clock), gate=gate, policy_set=h.ps,
                     symbols=h.symbols, clock=clock,
                     stop_requested=lambda: False, decide=decide)
    return deps, hook


def _safety_events(ledger):
    return views.events_of_kind(ledger, "safety_trip")


def test_switched_off_abandons_a_planned_buy_and_sell_and_trips_nothing(ws):
    h = Harness(ws)
    try:
        buy = h.ex.plan(h.req(side="buy", key="b1"))
        sell = h.ex.plan(h.req(side="sell", key="s1"))
        ws.write_control(state="off", mode="trade")
        assert h.ex.submit(buy.intent_id) is SubmitOutcome.BLOCKED
        assert h.ex.submit(sell.intent_id) is SubmitOutcome.BLOCKED
        for intent in (buy, sell):
            row = h.ledger.get_intent(intent.intent_id)
            assert row.state == "ABANDONED" and row.reason_code == "gate_closed"
        assert h.mock.submissions == []
        assert h.trips() == [] and _safety_events(h.ledger) == []
    finally:
        h.close()


def test_a_missing_control_file_sends_nothing_and_trips_nothing(ws):
    h = Harness(ws, gate_open=False)
    try:
        buy = h.ex.plan(h.req(side="buy", key="b1"))
        sell = h.ex.plan(h.req(side="sell", key="s1"))
        assert h.ex.submit(buy.intent_id) is SubmitOutcome.BLOCKED
        assert h.ex.submit(sell.intent_id) is SubmitOutcome.BLOCKED
        assert h.mock.submissions == []
        assert h.trips() == [] and _safety_events(h.ledger) == []
    finally:
        h.close()


def test_off_on_off_across_cycles_writes_zero_safety_trips(ws):
    h = Harness(ws)
    try:
        deps, hook = _rig(ws, h)
        state = CycleState()
        reports = []
        for switch in ("off", "on", "off"):
            ws.write_control(state=switch, mode="trade")
            reports.append(run_cycle(deps, state))
        assert [report.outcome for report in reports] == [CycleOutcome.CONTINUE] * 3
        assert [report.mode for report in reports] == [None, "trade", None]
        assert [report.control_state for report in reports] == ["off", "on", "off"]
        assert h.trips() == [] and _safety_events(h.ledger) == []
        assert len(hook) == 1                       # only the ON/trade cycle reaches decide
    finally:
        h.close()


def test_switched_off_still_cancels_an_expired_order(ws):
    h = Harness(ws)
    try:
        buy = h.ex.plan(h.req(side="buy", key="b1", price="90"))
        assert h.ex.submit(buy.intent_id) is SubmitOutcome.ACKED
        h.clock.advance(46)
        ws.write_control(state="off", mode="trade")
        deps, hook = _rig(ws, h)
        report = run_cycle(deps, CycleState())

        assert report.outcome is CycleOutcome.CONTINUE and report.mode is None
        assert h.ledger.get_intent(buy.intent_id).state == "CANCELED"
        rows = [event.payload for event in views.events_of_kind(h.ledger, "live_decision")
                if event.payload.get("action") == "cancel"]
        assert [row["reason"] for row in rows] == ["ttl_expired"]
        assert h.trips() == [] and _safety_events(h.ledger) == []
    finally:
        h.close()


def test_a_gate_that_raises_reads_as_closed_and_the_cycle_continues(ws):
    h = Harness(ws)
    try:
        def gate():
            raise RuntimeError("control read exploded")

        deps, hook = _rig(ws, h, gate=gate)
        before = len(views.events_of_kind(h.ledger, "worker_state"))
        report = run_cycle(deps, CycleState())
        assert report.outcome is CycleOutcome.CONTINUE
        assert report.mode is None and report.control_state == "unknown"
        assert report.control_mode is None and hook == []
        assert h.trips() == [] and _safety_events(h.ledger) == []
        assert len(views.events_of_kind(h.ledger, "worker_state")) == before + 1
    finally:
        h.close()


# ---- source pin: nothing writes the legacy ENABLE_MISSING trip ---------------------------
def test_nothing_under_src_writes_safety_reason_enable_missing():
    offenders = []
    for path in (SRC / "docich" / "trading").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "ENABLE_MISSING":
                offenders.append((path.relative_to(SRC).as_posix(), node.lineno))
    assert offenders == []
    assert live_safety.SafetyReason.ENABLE_MISSING.value == "enable_missing"
