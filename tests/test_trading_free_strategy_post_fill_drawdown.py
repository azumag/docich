"""Risk-stop regression for fills that create an immediate PAPER drawdown."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.free_strategy.broker import settle_observation  # noqa: E402
from docich.trading.free_strategy.contract import Artifact  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import CircuitBreakStatus  # noqa: E402

D = Decimal
IMAGE = "sha256:" + "a" * 64


def test_fill_crossing_drawdown_limit_pauses_before_next_decision(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    artifact = Artifact.create(
        source="def decide(context): return {'schema_version': 1, 'target_positions': [], 'state': {}, 'reason': 'wait'}",
        image=IMAGE,
        name="post-fill-risk-stop",
        family="contract-test",
        thesis="a fill that immediately breaches the PAPER drawdown limit must pause",
        symbols=["BTC/JPY"],
    )
    try:
        store.register(artifact)
        identity = store.create(artifact.digest, now=1_000.0, capital="10000", days=1)
        store.accept(
            identity,
            0,
            bar=900.0,
            accepted_at=1_000.0,
            run_id="post-fill-drawdown",
            decision={
                "schema_version": 1,
                "target_positions": [{"symbol": "BTC/JPY", "target_base_quantity": "0.003"}],
                "state": {"step": 1},
                "reason": "enter",
            },
            prices={"BTC/JPY": D("1000000")},
        )
        market = MarketInfo(
            symbol="BTC/JPY",
            base="BTC",
            quote="JPY",
            spot=True,
            active=True,
            amount_step=D("0.0001"),
            min_amount=D("0.0001"),
            min_cost=D("1"),
            taker_fee_rate_base=D("0"),
            taker_fee_rate_quote=D("0"),
        )
        book = DepthBook(
            "BTC/JPY",
            bids=(DepthLevel(D("400000"), D("1")),),
            asks=(DepthLevel(D("1000000"), D("1")),),
            as_of=1_002.0,
        )
        status = CircuitBreakStatus(
            "BTC/JPY", "NONE", "NORMAL", 1_002.0, fetched_at=1_002.0,
        )

        exp = settle_observation(
            store,
            identity,
            markets={"BTC/JPY": market},
            books={"BTC/JPY": book},
            statuses={"BTC/JPY": status},
            now=1_002.0,
        )

        assert store.db.execute("SELECT COUNT(*) FROM fills WHERE experiment=?", (identity,)).fetchone()[0] == 1
        assert exp["account"]["positions"]
        assert exp["phase"] == "paused"
        assert exp["last_error"] == "drawdown_limit"
        assert exp["pending"] == {}
    finally:
        store.close()
