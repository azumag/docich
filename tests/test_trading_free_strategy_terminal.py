from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.free_strategy.broker import settle_observation  # noqa: E402
from docich.trading.free_strategy.contract import Artifact, StrategyError, encode  # noqa: E402
from docich.trading.free_strategy.evaluation import evaluate  # noqa: E402
from docich.trading.free_strategy.service import run_cycle  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import CircuitBreakStatus  # noqa: E402

D = Decimal
IMAGE = "sha256:" + "a" * 64


def _artifact() -> Artifact:
    return Artifact.create(
        source="def decide(context): return {'schema_version':1,'target_positions':[],'state':{},'reason':'wait'}",
        image=IMAGE,
        name="terminal-test",
        family="terminal",
        thesis="fresh terminal valuation",
        symbols=["BTC/JPY"],
    )


def _market():
    return MarketInfo(
        symbol="BTC/JPY", base="BTC", quote="JPY", spot=True, active=True,
        amount_step=D("0.0001"), min_amount=D("0.0001"), min_cost=D("1"),
        taker_fee_rate_base=D("0"), taker_fee_rate_quote=D("0"),
    )


def _book(price: str, now: float) -> DepthBook:
    p = D(price)
    return DepthBook(
        "BTC/JPY",
        bids=(DepthLevel(p - D("1000"), D("1")),),
        asks=(DepthLevel(p + D("1000"), D("1")),),
        as_of=now,
    )


def _expired_experiment(path: Path):
    store = LabStore(path)
    artifact = _artifact()
    store.register(artifact)
    identity = store.create(artifact.digest, now=1000.0, capital="10000", days=1)
    account = {"cash_jpy": "8000", "positions": {"BTC/JPY": "0.002"}, "peak_equity": "10000"}
    pending = {"BTC/JPY": {"quantity": "0.003", "accepted_at": 1099.0, "run_id": "must-not-fill"}}
    with store.transaction():
        store.db.execute(
            "UPDATE experiments SET end_at=?,account=?,pending=? WHERE id=?",
            (1100.0, encode(account), encode(pending), identity),
        )
    return store, identity


def test_expiry_records_fresh_valuation_without_filling_pending_target(tmp_path):
    store, identity = _expired_experiment(tmp_path / "lab.sqlite3")
    try:
        market = _market()
        book = _book("900000", 1100.0)
        status = CircuitBreakStatus("BTC/JPY", "NONE", "NORMAL", 1100.0, fetched_at=1100.0)
        exp = settle_observation(
            store, identity,
            markets={"BTC/JPY": market}, books={"BTC/JPY": book},
            statuses={"BTC/JPY": status}, now=1100.0,
        )
        assert exp["phase"] == "review_due"
        assert exp["pending"] == {}
        assert exp["account"]["positions"] == {"BTC/JPY": "0.002"}
        assert store.db.execute("SELECT COUNT(*) FROM fills WHERE experiment=?", (identity,)).fetchone()[0] == 0
        row = store.db.execute(
            "SELECT observed_at,equity FROM samples WHERE experiment=? ORDER BY observed_at DESC LIMIT 1",
            (identity,),
        ).fetchone()
        assert row["observed_at"] == 1100.0
        assert D(row["equity"]) < D("10000")
        report = evaluate(store, identity, now=1100.0)
        assert report["observation_at"] == 1100.0
        assert "valuation_stale" not in report["blockers"]
        assert report["live_eligible"] is False
    finally:
        store.close()


def test_terminal_valuation_uses_same_bounded_book_participation_as_paper_execution(tmp_path):
    store, identity = _expired_experiment(tmp_path / "lab.sqlite3")
    try:
        # Visible bid depth is 1 BTC, but the experiment is allowed to model
        # only 1% participation per observation.  A 0.02 BTC position must not
        # be valued as if the whole visible book were available to it.
        with store.transaction():
            store.db.execute(
                "UPDATE experiments SET account=? WHERE id=?",
                (encode({"cash_jpy": "0", "positions": {"BTC/JPY": "0.02"}, "peak_equity": "20000"}), identity),
            )
        market = _market()
        book = _book("1000000", 1100.0)
        status = CircuitBreakStatus("BTC/JPY", "NONE", "NORMAL", 1100.0, fetched_at=1100.0)
        with pytest.raises(StrategyError, match="valuation_depth_insufficient"):
            settle_observation(
                store, identity,
                markets={"BTC/JPY": market}, books={"BTC/JPY": book},
                statuses={"BTC/JPY": status}, now=1100.0,
            )
        assert store.experiment(identity)["phase"] in {"research", "paper_validating"}
    finally:
        store.close()


def test_service_finalizes_with_depth_status_and_never_runs_guest_after_deadline(tmp_path):
    trading_dir = tmp_path / "trading"
    store, identity = _expired_experiment(trading_dir / "free-strategies" / "lab.sqlite3")
    store.close()
    market = _market()
    calls = []

    class Gateway:
        def discover_markets(self):
            calls.append("markets")
            return {"BTC/JPY": market}

        def fetch_market_frames(self, *args, **kwargs):
            raise AssertionError("expired experiment must not fetch strategy history")

        def fetch_circuit_break_statuses(self, symbols, *, fetched_at=None):
            calls.append(("status", tuple(symbols), fetched_at))
            return {"BTC/JPY": CircuitBreakStatus(
                "BTC/JPY", "NONE", "NORMAL", 1100.0, fetched_at=1100.0,
            )}

        def fetch_depth_books(self, symbols, *, now, limit=20):
            calls.append(("depth", tuple(symbols), now, limit))
            return {"BTC/JPY": _book("900000", 1100.0)}

    class Runner:
        def preflight(self):
            raise AssertionError("expired experiment must not require gVisor")

        def run(self, *args, **kwargs):
            raise AssertionError("expired experiment must never execute guest strategy")

    result = run_cycle(
        trading_dir, image=IMAGE, gateway=Gateway(), runner=Runner(), now_fn=lambda: 1100.0,
    )
    assert result == {"mode": "PAPER", "status": "idle", "completed": 1, "error_codes": []}
    assert "markets" in calls
    assert any(isinstance(call, tuple) and call[0] == "status" for call in calls)
    assert any(isinstance(call, tuple) and call[0] == "depth" for call in calls)

    reopened = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    try:
        assert reopened.experiment(identity)["phase"] == "review_due"
    finally:
        reopened.close()


def test_service_does_not_mark_review_due_when_terminal_market_data_is_unavailable(tmp_path):
    trading_dir = tmp_path / "trading"
    store, identity = _expired_experiment(trading_dir / "free-strategies" / "lab.sqlite3")
    store.close()
    market = _market()

    class Gateway:
        def discover_markets(self):
            return {"BTC/JPY": market}

        def fetch_circuit_break_statuses(self, symbols, *, fetched_at=None):
            return {"BTC/JPY": CircuitBreakStatus(
                "BTC/JPY", "NONE", "NORMAL", 1100.0, fetched_at=1100.0,
            )}

        def fetch_depth_books(self, *args, **kwargs):
            raise RuntimeError("private provider diagnostic must not escape")

    class Runner:
        def preflight(self):
            raise AssertionError("expired experiment must not require gVisor")

    result = run_cycle(
        trading_dir, image=IMAGE, gateway=Gateway(), runner=Runner(), now_fn=lambda: 1100.0,
    )
    assert result == {
        "mode": "PAPER", "status": "degraded", "completed": 0,
        "error_codes": ["public_data_unavailable"],
    }
    reopened = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    try:
        exp = reopened.experiment(identity)
        assert exp["phase"] in {"research", "paper_validating"}
        assert exp["last_error"] == "public_data_unavailable"
    finally:
        reopened.close()
