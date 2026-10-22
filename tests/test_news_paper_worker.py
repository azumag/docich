"""Offline tests for the N07 news-paper worker.

No network, credentials, broker, database fixture outside ``tmp_path`` or LLM
call. All prices/limits below are FIXTURES, not approved operational values.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import docich.trading.news_paper.worker as worker_module
from docich.trading.news_paper.execution import (
    ClockQuality,
    InstrumentConstraints,
    MarketObservation,
)
from docich.trading.news_paper.ledger import LedgerError, NewsLedger
from docich.trading.news_paper.manager import DecisionRequest, ManagerPolicy, PaperManager
from docich.trading.news_paper.risk import RiskPolicy
from docich.trading.news_paper.worker import (
    NewsPaperWorker,
    ObservationQueue,
    WorkerConfigError,
    WorkerLock,
    load_config,
    main,
)

T0 = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
ASSET = "stocks:XTKS:DEMO"


def T(seconds: int = 0) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _constraints() -> InstrumentConstraints:
    return InstrumentConstraints(
        version="fixture-constraints-v1",
        market="stocks",
        quote_currency="JPY",
        lot=Decimal("100"),
        min_quantity=Decimal("100"),
        min_notional=Decimal("100000"),
        price_tick=Decimal("1"),
        fee_bps=Decimal("1"),
        fee_min=Decimal("1"),
        slippage_bps=Decimal("2"),
        spread_limit_bps=Decimal("100"),
        participation_rate=Decimal("1"),
        max_quote_age_seconds=Decimal("60"),
    )


_TEMPLATE = """\
[worker]
enabled = {enabled}
mode = "{mode}"
run_id = "{run_id}"
scenario_id = "{scenario_id}"
account_id = "{account_id}"
state_dir = "{state_dir}"
cycle_seconds = {cycle_seconds}
queue_size = {queue_size}
queue_deadline_seconds = {queue_deadline_seconds}
initial_cash_jpy = "{initial_cash_jpy}"
entries_disabled = {entries_disabled}
execution_delay_ms = {execution_delay_ms}
worker_lock_timeout_seconds = {worker_lock_timeout_seconds}

[worker.clock_quality]
synced = {synced}
source = "{clock_source}"
max_error_ms = {max_error_ms}

[risk]
version = "fixture-risk-v1"
max_positions = 3
max_gross_exposure = "3000000"
per_asset_exposure_cap = "1000000"
daily_loss_limit = "20000"
drawdown_limit = "50000"

[instruments."{asset}"]
version = "fixture-constraints-v1"
market = "stocks"
quote_currency = "JPY"
lot = "100"
min_quantity = "100"
min_notional = "100000"
price_tick = "1"
fee_bps = "1"
fee_min = "1"
slippage_bps = "2"
spread_limit_bps = "100"
participation_rate = "1"
max_quote_age_seconds = "60"
"""


def _write_config(tmp_path: Path, **overrides: object) -> Path:
    values = {
        "enabled": "true",
        "mode": "paper",
        "run_id": "run1",
        "scenario_id": "scenario1",
        "account_id": "acct1",
        "state_dir": str(tmp_path / "state"),
        "cycle_seconds": 5,
        "queue_size": 4,
        "queue_deadline_seconds": 30,
        "initial_cash_jpy": "1000000",
        "entries_disabled": "false",
        "execution_delay_ms": 0,
        "worker_lock_timeout_seconds": 5,
        "synced": "true",
        "clock_source": "injected",
        "max_error_ms": 1000,
        "asset": ASSET,
    }
    values.update(overrides)
    path = tmp_path / "news-paper.toml"
    path.write_text(_TEMPLATE.format(**values), encoding="utf-8")
    return path


def _observation(
    *, seq: int = 1, event_seconds: int = -10, digest: str = "a", asset: str = ASSET
) -> MarketObservation:
    moment = T(event_seconds)
    return MarketObservation(
        observation_id=f"q{seq}",
        source="fixture",
        asset=asset,
        ingest_sequence=seq,
        market_event_at=moment,
        market_available_at=moment,
        available_at=moment,
        bid=Decimal("1000"),
        ask=Decimal("1000"),
        bid_size=Decimal("1000"),
        ask_size=Decimal("1000"),
        size_semantics="executable",
        tradeable=True,
        realtime=True,
        session_id="XTKS-AM",
        calendar_version="fixture-calendar-v1",
        quote_digest=digest * 64,
        provenance_class="test_fixture",
        instrument_constraints_version="fixture-constraints-v1",
    )


def _request(
    *,
    receipt_id: str = "receipt1",
    side: str = "buy",
    account_id: str = "acct1",
    decision_quote: tuple[str, str, datetime, str] | None = None,
    cutoff: int = 1,
    expires_seconds: int = 600,
    event_group: str = "evt1",
) -> DecisionRequest:
    return DecisionRequest(
        receipt_id=receipt_id,
        run_id="run1",
        scenario_id="scenario1",
        account_id=account_id,
        decision_at=T0,
        manager_seen_at=T0,
        input_cutoff_sequence=cutoff,
        signal_refs=("sig:1",),
        market_refs=("q0",),
        decision_quote=decision_quote,
        asset=ASSET,
        side=side,
        reference_price=Decimal("1000"),
        expires_at=T(expires_seconds),
        constraints=_constraints(),
        event_group_id=event_group,
    )


class _Candidates:
    def __init__(self, batches: list[list[DecisionRequest]]) -> None:
        self._batches = [list(batch) for batch in batches]
        self.calls = 0

    def candidates(self, now: datetime) -> list[DecisionRequest]:
        self.calls += 1
        return self._batches.pop(0) if self._batches else []


class _Source:
    def __init__(self, batches: list[list[MarketObservation]]) -> None:
        self._batches = [list(batch) for batch in batches]

    def poll(self) -> list[MarketObservation]:
        return self._batches.pop(0) if self._batches else []


def _rows(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def _db(tmp_path: Path) -> Path:
    return tmp_path / "state/news-paper/runs/run1/scenario1/paper.sqlite3"


def test_disabled_config_creates_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = _write_config(tmp_path, enabled="false")
    assert load_config(config_path) is None
    assert main(["--config", str(config_path), "--once"]) == 0
    output = json.loads(capsys.readouterr().out.strip())
    assert output["state"] == "disabled"
    assert output["mode"] == "paper"
    assert output["live_eligible"] is False
    assert not (tmp_path / "state").exists()


def test_missing_required_value_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('[worker]\nenabled = true\nmode = "paper"\n', encoding="utf-8")
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(path)
    assert excinfo.value.code == "VALUE_OUT_OF_RANGE"
    assert not (tmp_path / "state").exists()


def test_non_paper_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(_write_config(tmp_path, mode="live"))
    assert excinfo.value.code == "LIVE_ELIGIBILITY_FORBIDDEN"


def test_untrusted_clock_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(_write_config(tmp_path, synced="false"))
    assert excinfo.value.code == "CLOCK_UNTRUSTED"


def test_relative_state_dir_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(_write_config(tmp_path, state_dir="relative/state"))
    assert excinfo.value.code == "STATE_PATH_FORBIDDEN"


def test_queue_bounds_priority_dedup_deadline() -> None:
    queue = ObservationQueue(2)
    first = _observation(seq=1, event_seconds=-5, digest="a")
    second = _observation(seq=2, event_seconds=-4, digest="b")
    assert queue.push(first, priority=0, deadline=T(60)) is True
    assert queue.push(second, priority=5, deadline=T(60)) is True
    assert queue.push(_observation(seq=3, event_seconds=-3, digest="c"), deadline=T(60)) is False
    assert queue.dropped_full == 1
    # A duplicate identity is dropped even when capacity frees up again.
    assert queue.push(first, priority=9, deadline=T(60)) is False
    assert queue.dropped_duplicate == 1
    drained = queue.drain(T(0))
    assert [item.observation_id for item in drained] == ["q2", "q1"]

    expiring = ObservationQueue(4)
    assert expiring.push(_observation(seq=4, event_seconds=-2, digest="e"), deadline=T(-1)) is True
    assert expiring.drain(T(0)) == []
    assert expiring.dropped_expired == 1


def test_second_worker_lock_fails(tmp_path: Path) -> None:
    lock_one = WorkerLock(tmp_path / "state")
    lock_two = WorkerLock(tmp_path / "state")
    lock_one.acquire(0.05)
    try:
        with pytest.raises(LedgerError) as excinfo:
            lock_two.acquire(0.05)
        assert excinfo.value.code == "STORE_UNAVAILABLE"
    finally:
        lock_one.release()


def test_once_cycle_writes_health_and_valuation(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0)
    worker.start()
    try:
        assert worker.enqueue(_observation(seq=1, event_seconds=-10)) is True
        summary = worker.run_once(now=T0)
        health = json.loads((tmp_path / "state" / "news-paper" / "health.json").read_text())
    finally:
        worker.stop()
    assert summary["cycle"] == 1
    assert health["status"] == "running"
    assert health["heartbeat_at"] == "2026-10-22T12:00:00.000000Z"
    assert health["mode"] == "paper"
    assert health["live_eligible"] is False
    assert health["entries_disabled"] is False
    assert _db(tmp_path).is_file()
    assert _rows(_db(tmp_path), "SELECT total_equity, stale FROM valuations") == [("1000000", 0)]
    stopped = json.loads((tmp_path / "state" / "news-paper" / "health.json").read_text())
    assert stopped["status"] == "stopping"


def test_candidate_accept_then_fill_after_fence_budget(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    decision_quote = _observation(seq=1, event_seconds=-60, digest="d").identity
    worker = NewsPaperWorker(
        config,
        clock=lambda: T0,
        candidates=_Candidates([[_request(decision_quote=decision_quote)]]),
    )
    worker.start()
    try:
        # Cycle 1: the candidate is accepted (T2 commits; no order exists yet).
        worker.run_once(now=T0)
        assert _rows(_db(tmp_path), "SELECT status FROM decision_receipts") == [("accepted",)]
        assert _rows(_db(tmp_path), "SELECT status FROM paper_orders") == [("pending",)]
        # Cycle 2: this quote is inside the two-clock budget, so it cannot fill.
        worker.enqueue(_observation(seq=2, event_seconds=-5, digest="e"))
        assert worker.run_once(now=T(10))["fills"] == 0
        assert _rows(_db(tmp_path), "SELECT status FROM paper_orders") == [("pending",)]
        # Cycle 3: the first quote available after fence + 2x clock error fills.
        worker.enqueue(_observation(seq=3, event_seconds=15, digest="f"))
        summary = worker.run_once(now=T(20))
    finally:
        worker.stop()
    assert summary["fills"] == 1
    assert _rows(
        _db(tmp_path), "SELECT status, filled_quantity, remaining_quantity FROM paper_orders"
    ) == [("filled", "900", "0")]
    assert _rows(_db(tmp_path), "SELECT COUNT(*) FROM fills") == [(1,)]
    assert _rows(_db(tmp_path), "SELECT quantity FROM positions") == [("900",)]
    # A replayed quote cannot produce a second fill.
    worker2 = NewsPaperWorker(config, clock=lambda: T0)
    worker2.start()
    try:
        worker2.enqueue(_observation(seq=3, event_seconds=15, digest="f"))
        assert worker2.run_once(now=T(30))["fills"] == 0
    finally:
        worker2.stop()
    assert _rows(_db(tmp_path), "SELECT COUNT(*) FROM fills") == [(1,)]


def test_entries_disabled_cancels_pending_buy(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(
        config, clock=lambda: T0, candidates=_Candidates([[_request()]])
    )
    worker.start()
    try:
        worker.run_once(now=T0)
    finally:
        worker.stop()
    assert _rows(_db(tmp_path), "SELECT status FROM paper_orders") == [("pending",)]

    disabled = load_config(_write_config(tmp_path, entries_disabled="true"))
    assert disabled is not None
    members = _Candidates([])
    disabled_worker = NewsPaperWorker(disabled, clock=lambda: T0, candidates=members)
    disabled_worker.start()
    try:
        summary = disabled_worker.run_once(now=T(10))
    finally:
        disabled_worker.stop()
    assert summary["accepted"] == 0
    assert members.calls == 0  # new news decisions are not consulted
    assert _rows(_db(tmp_path), "SELECT status FROM paper_orders") == [("canceled",)]
    assert _rows(_db(tmp_path), "SELECT reason_code FROM cancel_events") == [("FEATURE_DISABLED",)]
    assert _rows(_db(tmp_path), "SELECT status FROM decision_receipts") == [("accepted",)]


def test_entries_disabled_keeps_pending_sell(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    decision_quote = _observation(seq=1, event_seconds=-60, digest="d").identity
    worker = NewsPaperWorker(
        config,
        clock=lambda: T0,
        candidates=_Candidates([[_request(decision_quote=decision_quote)]]),
    )
    worker.start()
    try:
        worker.run_once(now=T0)
        worker.enqueue(_observation(seq=3, event_seconds=15, digest="f"))
        assert worker.run_once(now=T(20))["fills"] == 1
    finally:
        worker.stop()
    sell_worker = NewsPaperWorker(
        config,
        clock=lambda: T0,
        candidates=_Candidates([[_request(receipt_id="sell1", side="sell", event_group="evt2")]]),
    )
    sell_worker.start()
    try:
        sell_worker.run_once(now=T(30))
    finally:
        sell_worker.stop()
    assert _rows(_db(tmp_path), "SELECT status FROM paper_orders WHERE side = 'sell'") == [
        ("pending",)
    ]

    disabled = load_config(_write_config(tmp_path, entries_disabled="true"))
    assert disabled is not None
    disabled_worker = NewsPaperWorker(disabled, clock=lambda: T0)
    disabled_worker.start()
    try:
        disabled_worker.run_once(now=T(40))
    finally:
        disabled_worker.stop()
    # Protective exits stay pending; only risk-increasing buys are canceled.
    assert _rows(_db(tmp_path), "SELECT status FROM paper_orders WHERE side = 'sell'") == [
        ("pending",)
    ]


def test_valuation_goes_stale_without_a_quote(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    decision_quote = _observation(seq=1, event_seconds=-60, digest="d").identity
    worker = NewsPaperWorker(
        config,
        clock=lambda: T0,
        candidates=_Candidates([[_request(decision_quote=decision_quote)]]),
    )
    worker.start()
    try:
        worker.run_once(now=T0)
        worker.enqueue(_observation(seq=3, event_seconds=15, digest="f"))
        worker.run_once(now=T(20))
    finally:
        worker.stop()
    # A fresh instance has no cached prices: valuation must be marked stale.
    fresh = NewsPaperWorker(config, clock=lambda: T0)
    fresh.start()
    try:
        fresh.run_once(now=T(30))
    finally:
        fresh.stop()
    latest = _rows(
        _db(tmp_path),
        "SELECT stale FROM valuations ORDER BY as_of DESC, rowid DESC LIMIT 1",
    )
    assert latest == [(1,)]


def test_source_seam_polls_without_network(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(
        config, clock=lambda: T0, source=_Source([[_observation(seq=1, event_seconds=-10)]])
    )
    worker._poll_source()  # N02 adapter seam: queue only, no I/O
    assert len(worker._queue) == 1


def test_second_start_in_same_process_fails(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    first = NewsPaperWorker(config, clock=lambda: T0)
    second = NewsPaperWorker(config, clock=lambda: T0)
    first.start()
    try:
        with pytest.raises(LedgerError) as excinfo:
            second.start()
        assert excinfo.value.code == "STORE_UNAVAILABLE"
    finally:
        first.stop()


class _FailingSource:
    def poll(self) -> list[MarketObservation]:
        raise RuntimeError("transport exploded")


def test_source_failure_is_a_fixed_code_and_does_not_queue(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0, source=_FailingSource())
    worker._poll_source()  # a source outage must not raise or stall the loop
    assert worker._last_error_code == "SOURCE_TIMEOUT"
    assert len(worker._queue) == 0


class _StreamFailSource:
    def poll(self):
        def stream():
            yield _observation(seq=1, event_seconds=-10)
            raise RuntimeError("mid-stream")

        return stream()


class _BrokenCandidates:
    def candidates(self, now):
        raise RuntimeError("n03 down")


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        version="fixture-risk-v1",
        max_positions=3,
        max_gross_exposure=Decimal("3000000"),
        per_asset_exposure_cap=Decimal("1000000"),
        daily_loss_limit=Decimal("20000"),
        drawdown_limit=Decimal("50000"),
    )


def test_valuation_goes_stale_when_the_last_quote_ages(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    decision_quote = _observation(seq=1, event_seconds=-60, digest="d").identity
    worker = NewsPaperWorker(
        config,
        clock=lambda: T0,
        candidates=_Candidates([[_request(decision_quote=decision_quote)]]),
    )
    worker.start()
    try:
        worker.run_once(now=T0)
        worker.enqueue(_observation(seq=3, event_seconds=15, digest="f"))
        assert worker.run_once(now=T(20))["fills"] == 1
        # Same instance: the cached quote is now older than max_quote_age_seconds.
        worker.run_once(now=T(200))
    finally:
        worker.stop()
    latest = _rows(_db(tmp_path), "SELECT stale FROM valuations ORDER BY rowid DESC LIMIT 1")
    assert latest == [(1,)]


def test_fatal_error_keeps_failed_heartbeat_and_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0)

    def boom(*args, **kwargs):
        raise LedgerError("SCHEMA_VERSION_UNSUPPORTED")

    monkeypatch.setattr(worker, "run_once", boom)
    worker.run()
    assert worker.failed is True
    health = json.loads((tmp_path / "state" / "news-paper" / "health.json").read_text())
    assert health["status"] == "failed"

    def fake_run(self) -> None:
        self._failed = True

    monkeypatch.setattr(worker_module.NewsPaperWorker, "run", fake_run)
    assert main(["--config", str(_write_config(tmp_path))]) == 1


def test_worker_only_touches_its_own_account(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    setup = NewsPaperWorker(config, clock=lambda: T0)
    setup.start()
    setup.stop()

    ledger = NewsLedger.open(
        tmp_path / "state", "run1", "scenario1", clock=lambda: T0, worker_id="test"
    )
    try:
        ledger.create_account(
            account_id="acct2", run_id="run1", scenario_id="scenario1", initial_cash="1000000"
        )
        manager = PaperManager(
            ledger,
            clock=lambda: T0,
            clock_quality=ClockQuality(synced=True, source="injected", max_error_ms=1000),
            risk_policy=_risk_policy(),
            policy=ManagerPolicy(execution_delay_ms=Decimal("0")),
        )
        assert (
            manager.submit_candidate(
                _request(receipt_id="r-acct2", account_id="acct2", event_group="evt-acct2")
            ).status
            == "accepted"
        )
    finally:
        ledger.close()

    disabled = load_config(_write_config(tmp_path, entries_disabled="true"))
    assert disabled is not None
    other = NewsPaperWorker(disabled, clock=lambda: T0)
    other.start()
    try:
        other.run_once(now=T(10))
    finally:
        other.stop()
    # The other account's pending buy must not be canceled by this worker.
    assert _rows(
        _db(tmp_path), "SELECT status FROM paper_orders WHERE account_id = 'acct2'"
    ) == [("pending",)]
    assert _rows(_db(tmp_path), "SELECT COUNT(*) FROM cancel_events") == [(0,)]


def test_invalid_clock_source_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(WorkerConfigError) as excinfo:
        load_config(_write_config(tmp_path, clock_source="gps"))
    assert excinfo.value.code == "VALUE_OUT_OF_RANGE"


def test_source_stream_failure_is_recorded(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0, source=_StreamFailSource())
    worker._poll_source()
    assert worker._last_error_code == "SOURCE_TIMEOUT"
    assert len(worker._queue) == 1  # the item yielded before the failure stays queued


def test_broken_candidate_seam_is_a_fixed_code(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0, candidates=_BrokenCandidates())
    worker.start()
    try:
        summary = worker.run_once(now=T0)
    finally:
        worker.stop()
    assert summary["accepted"] == 0
    assert summary["last_error_code"] == "CLASSIFIER_UNAVAILABLE"


def test_long_valid_ids_do_not_break_valuation(tmp_path: Path) -> None:
    long_id = "x" * 100
    config = load_config(
        _write_config(tmp_path, run_id=long_id, scenario_id=long_id, account_id=long_id)
    )
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0)
    worker.start()
    try:
        worker.run_once(now=T0)
    finally:
        worker.stop()
    db = tmp_path / "state" / "news-paper" / "runs" / long_id / long_id / "paper.sqlite3"
    assert _rows(db, "SELECT COUNT(*) FROM valuations") == [(1,)]


class _IteratorFailCandidates:
    def candidates(self, now):
        def stream():
            raise RuntimeError("iterator down")
            yield  # pragma: no cover

        return stream()


def test_candidate_iterator_failure_is_a_fixed_code(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0, candidates=_IteratorFailCandidates())
    worker.start()
    try:
        summary = worker.run_once(now=T0)
    finally:
        worker.stop()
    assert summary["accepted"] == 0
    assert summary["last_error_code"] == "CLASSIFIER_UNAVAILABLE"


def test_valuation_money_is_quantized_to_eight_decimals(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path))
    assert config is not None
    worker = NewsPaperWorker(config, clock=lambda: T0)

    class _FakeLedger:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def account_snapshot(self, account_id: str):
            from docich.trading.news_paper.ledger import AccountState

            return AccountState(
                account_id=account_id,
                run_id="run1",
                scenario_id="scenario1",
                quote_currency="JPY",
                initial_cash=Decimal("1000000"),
                cash=Decimal("1000000"),
                revision=1,
                created_at="2026-10-22T12:00:00.000000Z",
                positions={ASSET: (Decimal("6659.186"), Decimal("150.12345678"))},
            )

        def record_valuation(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    fake = _FakeLedger()
    worker._ledger = fake  # noqa: SLF001 - fixture ledger
    worker._last_prices = {ASSET: Decimal("150.12345678")}
    worker._last_quote_at = {ASSET: T0}
    worker._record_valuation(T0)
    values = fake.calls[0]
    for key in ("positions_value", "total_equity"):
        assert Decimal(str(values[key])).as_tuple().exponent >= -8, key
