from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.free_strategy.contract import Artifact, StrategyError, encode  # noqa: E402
from docich.trading.free_strategy.service import run_cycle  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import CircuitBreakStatus  # noqa: E402

D = Decimal
IMAGE = "sha256:" + "a" * 64


def _artifact() -> Artifact:
    return Artifact.create(
        source="def decide(context): return {'schema_version':1,'target_positions':[],'state':{},'reason':'wait'}",
        image=IMAGE,
        name="paused-test",
        family="paused",
        thesis="paused holdings remain observed",
        symbols=["BTC/JPY"],
    )


def _market() -> MarketInfo:
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


def _store_with_experiment(trading_dir: Path, *, phase="research", with_position=False):
    store = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    artifact = _artifact()
    store.register(artifact)
    identity = store.create(artifact.digest, now=1000.0, capital="10000", days=1)
    if phase != "research" or with_position:
        account = {
            "cash_jpy": "8000" if with_position else "10000",
            "positions": {"BTC/JPY": "0.002"} if with_position else {},
            "peak_equity": "10000",
        }
        with store.transaction():
            store.db.execute(
                "UPDATE experiments SET phase=?,account=?,pending=? WHERE id=?",
                (phase, encode(account), encode({}), identity),
            )
    store.close()
    return identity


def test_paused_position_is_marked_to_market_without_history_or_guest_execution(tmp_path):
    trading_dir = tmp_path / "trading"
    identity = _store_with_experiment(trading_dir, phase="paused", with_position=True)
    market = _market()
    calls = []

    class Gateway:
        def discover_markets(self):
            calls.append("markets")
            return {"BTC/JPY": market}

        def fetch_market_frames(self, *args, **kwargs):
            raise AssertionError("paused strategy must not fetch candidate history")

        def fetch_circuit_break_statuses(self, symbols, *, fetched_at=None):
            calls.append("status")
            return {"BTC/JPY": CircuitBreakStatus(
                "BTC/JPY", "NONE", "NORMAL", 1100.0, fetched_at=1100.0,
            )}

        def fetch_depth_books(self, symbols, *, now, limit=20):
            calls.append("depth")
            return {"BTC/JPY": _book("900000", 1100.0)}

    class Runner:
        def preflight(self):
            raise AssertionError("paused strategy must not require gVisor")

        def run(self, *args, **kwargs):
            raise AssertionError("paused strategy must not execute guest code")

    result = run_cycle(
        trading_dir, image=IMAGE, gateway=Gateway(), runner=Runner(), now_fn=lambda: 1100.0,
    )
    assert result == {"mode": "PAPER", "status": "idle", "completed": 1, "error_codes": []}
    assert calls == ["markets", "status", "depth"]

    store = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    try:
        exp = store.experiment(identity)
        assert exp["phase"] == "paused"
        assert exp["account"]["positions"] == {"BTC/JPY": "0.002"}
        row = store.db.execute(
            "SELECT observed_at,equity FROM samples WHERE experiment=? ORDER BY observed_at DESC LIMIT 1",
            (identity,),
        ).fetchone()
        assert row["observed_at"] == 1100.0
        assert D(row["equity"]) < D("10000")
    finally:
        store.close()


def test_paused_position_can_escalate_to_drawdown_stop_without_resuming_trades(tmp_path):
    trading_dir = tmp_path / "trading"
    identity = _store_with_experiment(trading_dir, phase="paused", with_position=True)
    market = _market()

    class Gateway:
        def discover_markets(self):
            return {"BTC/JPY": market}

        def fetch_circuit_break_statuses(self, symbols, *, fetched_at=None):
            return {"BTC/JPY": CircuitBreakStatus(
                "BTC/JPY", "NONE", "NORMAL", 1100.0, fetched_at=1100.0,
            )}

        def fetch_depth_books(self, symbols, *, now, limit=20):
            return {"BTC/JPY": _book("100000", 1100.0)}

    class Runner:
        def preflight(self):
            raise AssertionError("paused strategy must not require gVisor")

    result = run_cycle(
        trading_dir, image=IMAGE, gateway=Gateway(), runner=Runner(), now_fn=lambda: 1100.0,
    )
    assert result["status"] == "idle"
    store = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    try:
        exp = store.experiment(identity)
        assert exp["phase"] == "paused"
        assert exp["last_error"] == "drawdown_limit"
        try:
            store.resume(identity, now=1101.0)
        except StrategyError as error:
            assert str(error) == "resume_not_allowed"
        else:
            raise AssertionError("drawdown-stopped strategy must not resume")
    finally:
        store.close()


def test_gvisor_failure_blocks_new_decision_but_not_host_owned_valuation(tmp_path):
    trading_dir = tmp_path / "trading"
    identity = _store_with_experiment(trading_dir)
    market = _market()
    frame = MarketFrame(
        symbol="BTC/JPY", timeframe_seconds=300,
        timestamps=(1200.0, 1500.0), closes=(D("999000"), D("1000000")),
        volumes=(D("1"), D("1")),
    )

    class Gateway:
        def discover_markets(self):
            return {"BTC/JPY": market}

        def fetch_market_frames(self, symbols, *, timeframe, limit, now):
            return {"BTC/JPY": frame}

        def fetch_circuit_break_statuses(self, symbols, *, fetched_at=None):
            return {"BTC/JPY": CircuitBreakStatus(
                "BTC/JPY", "NONE", "NORMAL", 2000.0, fetched_at=2000.0,
            )}

        def fetch_depth_books(self, symbols, *, now, limit=20):
            return {"BTC/JPY": _book("1000000", 2000.0)}

    class Runner:
        def preflight(self):
            raise StrategyError("gvisor_required")

        def run(self, *args, **kwargs):
            raise AssertionError("guest must not run after failed preflight")

    result = run_cycle(
        trading_dir, image=IMAGE, gateway=Gateway(), runner=Runner(), now_fn=lambda: 2000.0,
    )
    assert result == {
        "mode": "PAPER", "status": "degraded", "completed": 0,
        "error_codes": ["gvisor_required"],
    }
    store = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    try:
        exp = store.experiment(identity)
        assert exp["last_error"] == "gvisor_required"
        sample = store.db.execute(
            "SELECT observed_at,equity FROM samples WHERE experiment=? ORDER BY observed_at DESC LIMIT 1",
            (identity,),
        ).fetchone()
        assert sample["observed_at"] == 2000.0
        assert sample["equity"] == "10000"
        assert store.db.execute("SELECT COUNT(*) FROM decisions WHERE experiment=?", (identity,)).fetchone()[0] == 0
    finally:
        store.close()
