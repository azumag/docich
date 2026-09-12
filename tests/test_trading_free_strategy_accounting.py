"""Pure host-side PAPER accounting contracts for free strategies."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.free_strategy.broker import settle_observation  # noqa: E402
from docich.trading.free_strategy.contract import Artifact, StrategyError  # noqa: E402
from docich.trading.free_strategy.evaluation import evaluate  # noqa: E402
from docich.trading.free_strategy.generation import generate  # noqa: E402
from docich.trading.free_strategy.service import build_context, run_cycle  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
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


def _market(*, price=D("1000000"), base_fee=D("0"), quote_fee=D("0")):
    market = MarketInfo(
        symbol="BTC/JPY", base="BTC", quote="JPY", spot=True, active=True,
        amount_step=D("0.0001"), min_amount=D("0.0001"), min_cost=D("1"),
        taker_fee_rate_base=base_fee, taker_fee_rate_quote=quote_fee,
    )
    book = DepthBook(
        "BTC/JPY",
        bids=(DepthLevel(price - D("1000"), D("1")),),
        asks=(DepthLevel(price + D("1000"), D("1")),),
        as_of=0.0,
    )
    return market, book


def _observation(
    price: Decimal, *, book_at: float, fetched_at: float,
    base_fee=D("0"), quote_fee=D("0"),
):
    market, template = _market(price=price, base_fee=base_fee, quote_fee=quote_fee)
    book = DepthBook(template.symbol, template.bids, template.asks, as_of=book_at)
    status = CircuitBreakStatus("BTC/JPY", "NONE", "NORMAL", book_at, fetched_at=fetched_at)
    return {"BTC/JPY": market}, {"BTC/JPY": book}, {"BTC/JPY": status}


def _target(quantity: str, *, step=1):
    return {
        "schema_version": 1,
        "target_positions": [{"symbol": "BTC/JPY", "target_base_quantity": quantity}],
        "state": {"step": step},
        "reason": "paper target",
    }


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
        store.accept(
            identity, 0, bar=900.0, accepted_at=1_000.0, run_id="run-fill",
            decision=_target("0.001"), prices={"BTC/JPY": D("1000000")},
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


def test_fill_records_fees_and_account_is_net_of_costs(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        store.accept(
            identity, 0, bar=900.0, accepted_at=1_000.0, run_id="run-fee",
            decision=_target("0.001"), prices={"BTC/JPY": D("1000000")},
        )
        markets, books, statuses = _observation(
            D("1000000"), book_at=1_002.0, fetched_at=1_002.0,
            base_fee=D("0.001"), quote_fee=D("0.002"),
        )
        exp = settle_observation(
            store, identity, markets=markets, books=books, statuses=statuses, now=1_002.0,
        )
        fill = store.recent_fills(identity)[0]
        fee_base = D(fill["fee_base"])
        fee_jpy = D(fill["fee_jpy"])
        amount = D(fill["amount"])
        gross = amount * D(fill["price"])
        assert fee_base > 0 and fee_jpy > 0
        assert D(exp["account"]["positions"]["BTC/JPY"]) == amount - fee_base
        assert D(exp["account"]["cash_jpy"]) == D("10000") - gross - fee_jpy
    finally:
        store.close()


def test_drawdown_pauses_experiment_and_clears_pending_target(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        store.accept(
            identity, 0, bar=900.0, accepted_at=1_000.0, run_id="run-entry",
            decision=_target("0.003"), prices={"BTC/JPY": D("1000000")},
        )
        markets, books, statuses = _observation(D("1000000"), book_at=1_002.0, fetched_at=1_002.0)
        exp = settle_observation(
            store, identity, markets=markets, books=books, statuses=statuses, now=1_002.0,
        )
        assert exp["account"]["positions"]

        store.accept(
            identity, exp["revision"], bar=1_200.0, accepted_at=1_201.0, run_id="run-rebalance",
            decision=_target("0.003", step=2), prices={"BTC/JPY": D("1000000")},
        )
        assert store.experiment(identity)["pending"]

        markets, books, statuses = _observation(D("100000"), book_at=1_202.0, fetched_at=1_202.0)
        stopped = settle_observation(
            store, identity, markets=markets, books=books, statuses=statuses, now=1_202.0,
        )
        assert stopped["phase"] == "paused"
        assert stopped["last_error"] == "drawdown_limit"
        assert stopped["pending"] == {}
    finally:
        store.close()


def test_build_context_rejects_stale_completed_history(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        exp = store.experiment(identity)
        frame = MarketFrame(
            symbol="BTC/JPY", timeframe_seconds=300,
            timestamps=(0.0, 300.0), closes=(D("100"), D("101")), volumes=(D("1"), D("1")),
        )
        with pytest.raises(StrategyError, match="history_stale"):
            build_context(store, exp, {"BTC/JPY": frame}, now=1_301.0)
    finally:
        store.close()


def test_service_uses_gateway_fetched_at_contract(tmp_path):
    trading_dir = tmp_path / "trading"
    store = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    candidate = _artifact()
    try:
        store.register(candidate)
        store.create(candidate.digest, now=1_000.0, capital="10000", days=1)
    finally:
        store.close()

    market, template = _market()
    frame = MarketFrame(
        symbol="BTC/JPY", timeframe_seconds=300,
        timestamps=(1_200.0, 1_500.0),
        closes=(D("999000"), D("1000000")),
        volumes=(D("1"), D("1")),
    )

    class Gateway:
        circuit_fetched_at = None

        def discover_markets(self):
            return {"BTC/JPY": market}

        def fetch_market_frames(self, symbols, *, timeframe, limit, now):
            assert list(symbols) == ["BTC/JPY"]
            assert timeframe == "5m" and limit == 144 and now == 2_000.0
            return {"BTC/JPY": frame}

        def fetch_circuit_break_statuses(self, symbols, *, fetched_at=None):
            self.circuit_fetched_at = fetched_at
            return {"BTC/JPY": CircuitBreakStatus(
                "BTC/JPY", "NONE", "NORMAL", fetched_at, fetched_at=fetched_at,
            )}

        def fetch_depth_books(self, symbols, *, now, limit=20):
            return {"BTC/JPY": DepthBook(
                template.symbol, template.bids, template.asks, as_of=now,
            )}

    class Runner:
        def preflight(self):
            return {"runtime": "test", "image": IMAGE, "abi": 1}

        def run(self, artifact, context):
            assert artifact.digest == candidate.digest
            return {
                "schema_version": 1,
                "target_positions": [],
                "state": {"ran": True},
                "reason": "wait",
            }

    gateway = Gateway()
    result = run_cycle(
        trading_dir, image=IMAGE, gateway=gateway, runner=Runner(), now_fn=lambda: 2_000.0,
    )
    assert result == {"mode": "PAPER", "status": "idle", "completed": 1, "error_codes": []}
    assert gateway.circuit_fetched_at == 2_000.0


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


def test_evaluation_keeps_intrabucket_drawdown_after_recovery(tmp_path):
    store, identity = _experiment(tmp_path)
    try:
        with store.transaction():
            store.db.execute(
                "INSERT INTO samples(experiment,bucket,observed_at,equity) VALUES (?,?,?,?)",
                (identity, 0, 1_100.0, "10000"),
            )
            for observed_at, equity in ((1_200.0, "9500"), (1_300.0, "10000")):
                store.db.execute("""INSERT INTO samples(experiment,bucket,observed_at,equity) VALUES (?,?,?,?)
                    ON CONFLICT(experiment,bucket) DO UPDATE SET observed_at=excluded.observed_at,equity=excluded.equity
                    WHERE excluded.observed_at>samples.observed_at""",
                    (identity, 0, observed_at, equity))
        assert store.db.execute(
            "SELECT COUNT(*) FROM samples WHERE experiment=?", (identity,)
        ).fetchone()[0] == 1
        assert store.db.execute(
            "SELECT COUNT(*) FROM equity_observations WHERE experiment=?", (identity,)
        ).fetchone()[0] == 3
        report = evaluate(store, identity, now=1_300.0)
        assert report["sample_count"] == 1
        assert D(report["max_drawdown_fraction"]) == D("0.05")
        assert report["equity_jpy"] == "10000"
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
