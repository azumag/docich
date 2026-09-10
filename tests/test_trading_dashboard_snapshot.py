import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.dashboard_snapshot import build_dashboard_snapshot


def _write(trading_dir: Path, status: dict, cache: dict) -> None:
    trading_dir.mkdir(parents=True, exist_ok=True)
    (trading_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (trading_dir / "market_cache.json").write_text(json.dumps(cache), encoding="utf-8")


def test_snapshot_is_allowlisted_and_finite(tmp_path):
    trading_dir = tmp_path / "trading"
    _write(
        trading_dir,
        {
            "worker_state": "running",
            "snapshot_seq": 7,
            "snapshot_generated_at": 1000.0,
            "capital_reference": "10000",
            "deployed_reference": "3000",
            "open_positions": {"btc_jpy": "0.001", "eth_jpy": "0"},
            "eligible_symbols": ["btc_jpy"],
            "recent_fills": [
                {"symbol": "btc_jpy", "side": "buy", "amount": "0.001",
                 "price": "10000000", "quote": "btc_jpy", "filled_at": 990.0,
                 "reason_code": "momentum_breakout"}
            ],
            "skipped_reason_codes": ["correlated_exposure"],
            "signal_summary": {"candidate_count": 2,
                               "candidate_reason_codes": ["momentum_breakout"]},
            "market_freshness": {"btc_jpy": {"quality": "fresh"}},
        },
        {"symbols": {"btc_jpy": {"closes": [1, 2, 3, 4], "fetched_at": 1000.0}}},
    )
    snap = build_dashboard_snapshot(trading_dir, now=1010.0)
    assert snap["schema_version"] == 1
    assert snap["header"]["worker_state"] == "running"
    assert snap["header"]["data_age_sec"] == 10
    assert snap["portfolio"]["capital_jpy"] == "10000"
    assert snap["portfolio"]["positions"] == [{"symbol": "btc_jpy", "amount": "0.001"}]
    assert snap["portfolio"]["fresh_markets"] == 1
    assert snap["portfolio"]["total_markets"] == 1
    assert snap["chart"]["symbol"] == "btc_jpy"
    assert snap["chart"]["closes"] == [1.0, 2.0, 3.0, 4.0]
    assert snap["decision"]["candidate_count"] == 2
    assert snap["decision"]["reasons"][0]["label"] == "モメンタム上振れ"
    assert snap["decision"]["skipped"][0]["label"] == "相関過多" or snap["decision"]["skipped"][0]["code"] == "correlated_exposure"
    assert snap["fills"][0]["symbol"] == "btc_jpy"
    assert "模擬取引" in snap["disclaimer"]


def test_snapshot_survives_missing_and_malformed(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text("{not json", encoding="utf-8")
    snap = build_dashboard_snapshot(trading_dir, now=5.0)
    assert snap["header"]["worker_state"] == "unknown"
    assert snap["portfolio"]["positions"] == []
    assert snap["chart"]["closes"] == []
    assert snap["chart"]["symbol"] is None
    # JSON-serializable and bounded
    text = json.dumps(snap, ensure_ascii=False)
    assert len(text) < 20000
