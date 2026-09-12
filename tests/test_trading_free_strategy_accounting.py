"""Pure host-side PAPER accounting contracts for free strategies."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.free_strategy.broker import settle_observation  # noqa: E402
from docich.trading.free_strategy.contract import Artifact, StrategyError  # noqa: E402
from docich.trading.free_strategy.evaluation import evaluate  # noqa: E402
from docich.trading.free_strategy.generation import generate  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import CircuitBreakStatus  # noqa: E402

D = Decimal
IMAGE = "sha256:" + "a" * 64


def _artifact() -> Artifact:
    return Artifact.create(
        source="def decide(context): return {'schema_version': 1, 'target_positions': [], 'state': {}, 'reason': 'wait'}",
        image=IMAGE,
        name="paper-contract",
        family="contract-test",
        thesis="host-side paper accounting contract",
        symbols=["BTC/JPY"],
    )


def _experiment(tmp_path, *, now=1_000.0):
    store = LabStore(tmp_path / "lab.sqlite3")
    candidate = _artifact()
    store.register(candidate)
    identity = store.create(candidate.digest, now=now, capital="10000", days=1)
    return store, identity


def _market(*, price=D("1000000")):
    market = MarketInfo(
        symbol="BTC/JPY", base="BTC", quote="JPY", spot=True, active=True,
        amount_step=D("0.0001"), min_amount=D("0.0001"), min_cost=D("1"),
        taker_fee_rate_base=D("0"), taker_fee_rate_quote=D("0"),
    )
    book = DepthBook(
        "BTC/JPY",
        bids=(DepthLevel(price - D("1000"), D("1")),),
        asks=(DepthLevel(price + D("1000"), D("1")),),
        as_of=0.0,
    )
    return market, book


def _observation(price: Decimal, *, book_at: float, fetched_at: float):
    market, template = _market(price=price)
    book = DepthBook(template.symbol, template.bids, template.asks, as_of=book_at)
    status = CircuitBreakStatus("BTC/JPY", "NONE", "NORMAL", book_at, fetched_at=fetched_at)
    return {"BTC/JPY": market}, {"BTC/JPY": book}, {"BTC/JPY": status}


def test_duplicate_run_id_returns_receipt_without_advancing_state_twice(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        decision = {"schema_version": 1, "target_positions": [], "state": {"step": 1}, "reason": "wait"}
        first = store.accept(
            identity, 0, bar=900.0, accepted_at=1_000.0, run_id="run-1",
            decision=decision, prices={"BTC/JPY": D("1000000")},
        )
        before = store.experiment(identity)
        replay = store.accept(
            identity, 999, bar=999.0, accepted_at=9_999.0, run_id="run-1",
            decision={**decision, "state": {"step": 999}}, prices={"BTC/JPY": D("1")},
        )
        after = store.experiment(identity)
        assert replay == first
        assert after["revision"] == before["revision"] == 1
        assert after["strategy_state"] == before["strategy_state"] == {"step": 1}
        assert store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    finally:
        store.close()


def test_target_waits_for_post_decision_book_then_fills_once(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        decision = {
            "schema_version": 1,
            "target_positions": [{"symbol": "BTC/JPY", "target_base_quantity": "0.001"}],
            "state": {"step": 1},
            "reason": "paper target",
        }
        store.accept(
            identity, 0, bar=900.0, accepted_at=1_000.0, run_id="run-fill",
            decision=decision, prices={"BTC/JPY": D("1000000")},
        )

        markets, books, statuses = _observation(D("1000000"), book_at=1_000.0, fetched_at=1_001.0)
        cached = settle_observation(
            store, identity, markets=markets, books=books, statuses=statuses, now=1_001.0,
        )
        assert "BTC/JPY" in cached["pending"]
        assert store.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0

        markets, books, statuses = _observation(D("1000000"), book_at=1_002.0, fetched_at=1_002.0)
        filled = settle_observation(
            store, identity, markets=markets, books=books, statuses=statuses, now=1_002.0,
        )
        assert "BTC/JPY" not in filled["pending"]
        assert D(filled["account"]["positions"]["BTC/JPY"]) > 0
        assert store.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1

        settle_observation(
            store, identity, markets=markets, books=books, statuses=statuses, now=1_002.0,
        )
        assert store.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    finally:
        store.close()


def test_evaluation_never_grants_live_authority(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        with store.transaction():
            store.db.execute(
                "INSERT INTO samples(experiment,bucket,observed_at,equity) VALUES (?,?,?,?)",
                (identity, 0, 1_100.0, "10100"),
            )
        report = evaluate(store, identity, now=1_100.0)
        assert report["net_pnl_jpy"] == "100"
        assert report["live_eligible"] is False
        assert "live_broker_not_implemented" in report["blockers"]
    finally:
        store.close()


def test_generation_daily_budget_allows_only_one_provider_request(tmp_path):
    calls = []

    def provider(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({
            "source": "def decide(context): return {'schema_version': 1, 'target_positions': [], 'state': {}, 'reason': 'wait'}",
            "name": "generated",
            "family": "daily-budget",
            "thesis": "one provider request per UTC day",
        })

    first = generate(
        tmp_path, image=IMAGE, symbols=["BTC/JPY"], brief="test daily budget",
        now_fn=lambda: 10 * 86400 + 100.0, text_fn=provider,
    )
    second = generate(
        tmp_path, image=IMAGE, symbols=["BTC/JPY"], brief="test daily budget",
        now_fn=lambda: 10 * 86400 + 200.0, text_fn=provider,
    )
    assert first["status"] == "registered"
    assert second == {"status": "skipped", "reason": "generation_daily_budget"}
    assert len(calls) == 1
