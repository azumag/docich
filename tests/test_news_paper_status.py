"""Offline tests for the read-only news-paper status projection (N07).

Fixtures only; no network/LLM. The projection must never create or modify a
store and must never aggregate another account.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from docich.trading.news_paper.execution import ClockQuality, InstrumentConstraints
from docich.trading.news_paper.ledger import NewsLedger
from docich.trading.news_paper.manager import DecisionRequest, ManagerPolicy, PaperManager
from docich.trading.news_paper.risk import RiskPolicy
from docich.trading.news_paper.status import build_status, main

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


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        version="fixture-risk-v1",
        max_positions=3,
        max_gross_exposure=Decimal("3000000"),
        per_asset_exposure_cap=Decimal("1000000"),
        daily_loss_limit=Decimal("20000"),
        drawdown_limit=Decimal("50000"),
    )


def _request(
    *, receipt_id: str, expires_seconds: int = 600, side: str = "buy", event_group: str = "evt1"
) -> DecisionRequest:
    return DecisionRequest(
        receipt_id=receipt_id,
        run_id="run1",
        scenario_id="scenario1",
        account_id="acct1",
        decision_at=T0,
        manager_seen_at=T0,
        input_cutoff_sequence=1,
        signal_refs=("sig:1",),
        market_refs=("q0",),
        decision_quote=None,
        asset=ASSET,
        side=side,
        reference_price=Decimal("1000"),
        expires_at=T(expires_seconds),
        constraints=_constraints(),
        event_group_id=event_group,
    )


def _prepare_ledger(tmp_path: Path) -> Path:
    ledger = NewsLedger.open(
        tmp_path / "state", "run1", "scenario1", clock=lambda: T0, worker_id="n07"
    )
    try:
        ledger.create_account(
            account_id="acct1",
            run_id="run1",
            scenario_id="scenario1",
            initial_cash="1000000",
        )
        manager = PaperManager(
            ledger,
            clock=lambda: T0,
            clock_quality=ClockQuality(synced=True, source="injected", max_error_ms=1000),
            risk_policy=_risk_policy(),
            policy=ManagerPolicy(execution_delay_ms=Decimal("0")),
        )
        assert manager.submit_candidate(_request(receipt_id="r-accepted")).status == "accepted"
        assert (
            manager.submit_candidate(_request(receipt_id="r-held", expires_seconds=-5)).status
            == "held"
        )
        assert (
            manager.submit_candidate(
                _request(receipt_id="r-rejected"),
                intent="rejected",
                reason_codes=("TYPE_INVALID",),
            ).status
            == "rejected"
        )
    finally:
        ledger.close()
    return tmp_path / "state/news-paper/runs/run1/scenario1/paper.sqlite3"


def test_empty_status_creates_nothing(tmp_path: Path) -> None:
    status = build_status(tmp_path / "state", "run1", "scenario1", "acct1")
    assert status["state"] == "empty"
    assert status["mode"] == "paper"
    assert status["live_eligible"] is False
    assert status["ages"]["news_seconds"] is None
    assert status["ages"]["classifier_seconds"] is None
    assert "news" in status["ages"]["not_connected"]
    assert not (tmp_path / "state").exists()


def test_relative_state_dir_is_blocked(tmp_path: Path) -> None:
    status = build_status("relative/state", "run1", "scenario1", "acct1")
    assert status["state"] == "blocked"
    assert status["reason_code"] == "STATE_PATH_FORBIDDEN"


def test_projection_counts_reasons_and_pending(tmp_path: Path) -> None:
    _prepare_ledger(tmp_path)
    health = {
        "schema_version": 1,
        "status": "running",
        "heartbeat_at": "2026-10-22T11:59:30.000000Z",
        "started_at": "2026-10-22T11:00:00.000000Z",
        "cycle_seq": 7,
        "entries_disabled": False,
        "queue_depth": 2,
        "queue_dropped_full": 0,
        "queue_dropped_expired": 1,
        "queue_dropped_duplicate": 3,
        "last_market_available_at": "2026-10-22T11:58:00.000000Z",
        "last_fill_at": None,
        "last_error_code": "SOURCE_TIMEOUT",
    }
    (tmp_path / "state" / "news-paper").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "news-paper" / "health.json").write_text(json.dumps(health))

    status = build_status(tmp_path / "state", "run1", "scenario1", "acct1", now=T0)
    assert status["state"] == "ok"
    assert status["mode"] == "paper"
    assert status["live_eligible"] is False
    assert status["account"]["cash"] == "1000000"
    assert status["receipts"]["counts"] == {"accepted": 1, "held": 1, "rejected": 1}
    assert status["receipts"]["accepted_uncommitted"] == 0
    codes = {item["code"] for item in status["receipts"]["reason_counts"]}
    assert {"SIGNAL_EXPIRED", "TYPE_INVALID"}.issubset(codes)
    assert status["orders"]["pending_buy"] == 1
    assert status["orders"]["pending_sell"] == 0
    assert status["pending_work"]["total"] == 1
    assert status["worker"]["status"] == "running"
    assert status["worker"]["queue"] == {
        "depth": 2,
        "dropped_full": 0,
        "dropped_expired": 1,
        "dropped_duplicate": 3,
    }
    assert status["worker"]["last_error_code"] == "SOURCE_TIMEOUT"
    assert status["ages"]["heartbeat_seconds"] == 30
    assert status["ages"]["market_seconds"] == 120
    # The projection is JSON-safe (no Decimal/NaN leaks).
    json.dumps(status, allow_nan=False)


def test_heartbeat_age_uses_as_of_not_mtime(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "news-paper").mkdir(parents=True)
    health = {
        "schema_version": 1,
        "status": "running",
        "heartbeat_at": "2026-10-22T11:00:00.000000Z",
        "last_market_available_at": "2026-10-22T11:58:00.000000Z",
    }
    (state / "news-paper" / "health.json").write_text(json.dumps(health))
    status = build_status(state, "run1", "scenario1", "acct1", now=T0)
    # The file mtime is "now", but freshness must come from heartbeat_at.
    assert status["worker"]["heartbeat_age_seconds"] == 3600
    assert status["ages"]["market_seconds"] == 120


def test_missing_account_returns_empty(tmp_path: Path) -> None:
    ledger = NewsLedger.open(
        tmp_path / "state", "run1", "scenario1", clock=lambda: T0, worker_id="n07"
    )
    ledger.close()  # schema exists, but no account
    status = build_status(tmp_path / "state", "run1", "scenario1", "acct1")
    assert status["state"] == "empty"
    assert status["reason_code"] == "UNKNOWN_KEY"
    assert status["account"] is None


def test_status_does_not_write_or_create_files(tmp_path: Path) -> None:
    _prepare_ledger(tmp_path)
    directory = tmp_path / "state/news-paper/runs/run1/scenario1"
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in directory.iterdir()
        if path.is_file()
    }
    build_status(tmp_path / "state", "run1", "scenario1", "acct1")
    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in directory.iterdir()
        if path.is_file()
    }
    # SQLite may attach its own WAL index sidecars for a read-only WAL read;
    # the database itself and every pre-existing file must stay untouched.
    sqlite_sidecars = {"paper.sqlite3-wal", "paper.sqlite3-shm"}
    assert set(after) - set(before) <= sqlite_sidecars
    for name, value in before.items():
        assert after[name] == value
    assert not any(path.is_dir() for path in directory.iterdir())


def test_cli_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "--state-dir",
                str(tmp_path / "state"),
                "--run-id",
                "run1",
                "--scenario-id",
                "scenario1",
                "--account-id",
                "acct1",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out.strip())
    assert output["state"] == "empty"
    assert output["mode"] == "paper"
    assert output["live_eligible"] is False
