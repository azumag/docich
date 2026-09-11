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
                {"fill_id": "paper:x", "symbol": "btc_jpy", "side": "buy", "amount": "0.001",
                 "price": "10000000", "quote": "btc_jpy", "filled_at": 990.0,
                 "reason_code": "momentum_breakout"}
            ],
            "skipped_reason_codes": ["correlated_exposure"],
            "skipped_decisions": [
                {"symbol": "xrp_jpy", "side": "buy", "reason_code": "below_min_amount"}
            ],
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
    assert snap["portfolio"]["position_count"] == 1
    assert snap["portfolio"]["positions"][0]["symbol"] == "btc_jpy"
    assert snap["portfolio"]["positions"][0]["amount"] == "0.001"
    assert snap["portfolio"]["fresh_markets"] == 1
    assert snap["portfolio"]["total_markets"] == 1
    assert snap["chart"]["symbol"] == "btc_jpy"
    assert snap["chart"]["closes"] == [1.0, 2.0, 3.0, 4.0]
    assert snap["decision"]["candidate_count"] == 2
    assert snap["decision"]["reasons"][0]["label"] == "モメンタム上振れ"
    assert snap["decision"]["skipped"][0]["symbol"] == "xrp_jpy"
    assert snap["decision"]["skipped"][0]["side_label"] == "買い"
    assert snap["decision"]["skipped"][0]["label"] == "最小数量未満"
    assert snap["fills"][0]["symbol"] == "btc_jpy"
    assert "performance" in snap
    assert "模擬取引" in snap["disclaimer"]


def test_snapshot_reports_true_position_count_when_display_is_capped(tmp_path):
    trading_dir = tmp_path / "trading"
    positions = {f"coin{i:02d}_jpy": "1" for i in range(14)}
    cache = {
        "symbols": {
            symbol: {"closes": [100 + i, 101 + i], "fetched_at": 1000.0}
            for i, symbol in enumerate(positions)
        }
    }
    _write(
        trading_dir,
        {
            "worker_state": "running",
            "snapshot_generated_at": 1000.0,
            "capital_reference": "100000",
            "deployed_reference": "50000",
            "open_positions": positions,
            "eligible_symbols": list(positions),
            "recent_fills": [],
            "skipped_reason_codes": [],
            "signal_summary": {},
            "market_freshness": {symbol: {"quality": "fresh"} for symbol in positions},
        },
        cache,
    )
    snap = build_dashboard_snapshot(trading_dir, now=1010.0)
    assert snap["portfolio"]["position_count"] == 14
    assert snap["portfolio"]["displayed_position_count"] == 8
    assert len(snap["portfolio"]["positions"]) == 8


def test_snapshot_survives_missing_and_malformed(tmp_path):
    trading_dir = tmp_path / "trading"
    trading_dir.mkdir(parents=True)
    (trading_dir / "status.json").write_text("{not json", encoding="utf-8")
    snap = build_dashboard_snapshot(trading_dir, now=5.0)
    assert snap["header"]["worker_state"] == "unknown"
    assert snap["portfolio"]["positions"] == []
    assert snap["portfolio"]["position_count"] == 0
    assert snap["chart"]["closes"] == []
    assert snap["chart"]["symbol"] is None
    text = json.dumps(snap, ensure_ascii=False)
    assert len(text) < 24000
