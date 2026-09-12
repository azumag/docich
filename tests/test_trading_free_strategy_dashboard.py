from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.dashboard_server import ASSETS  # noqa: E402
from docich.trading.dashboard_snapshot import build_dashboard_snapshot  # noqa: E402
from docich.trading.free_strategy.contract import Artifact, encode  # noqa: E402
from docich.trading.free_strategy.evaluation import evaluate  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402

IMAGE = "sha256:" + "a" * 64


def _write_existing_status(trading_dir: Path) -> None:
    trading_dir.mkdir(parents=True, exist_ok=True)
    (trading_dir / "status.json").write_text(json.dumps({
        "schema_version": 1,
        "mode": "paper",
        "worker_state": "paper_worker_idle",
        "last_cycle_at": 1000.0,
        "eligible_symbols": [],
        "capital_reference": "10000",
        "deployed_reference": "0",
        "open_positions": {},
        "recent_fills": [],
        "skipped_reason_codes": [],
        "signal_summary": {},
        "snapshot_seq": 1,
        "snapshot_generated_at": 1000.0,
        "market_freshness": {},
        "coverage": {},
    }), encoding="utf-8")


def test_dashboard_does_not_create_free_strategy_database_when_absent(tmp_path):
    trading_dir = tmp_path / "trading"
    _write_existing_status(trading_dir)
    lab = trading_dir / "free-strategies" / "lab.sqlite3"

    snapshot = build_dashboard_snapshot(trading_dir, now=1100.0)

    assert snapshot["free_strategies"] == {
        "mode": "PAPER",
        "experiments": [],
        "health": {
            "status": "absent", "heartbeat_at": None, "age_sec": None,
            "completed_experiments": 0, "error_codes": [],
        },
    }
    assert snapshot["portfolio"]["capital_jpy"] == "10000"
    assert snapshot["portfolio"]["deployed_jpy"] == "0"
    assert not lab.exists()
    assert not (trading_dir / "free-strategies").exists()


def test_dashboard_exposes_only_allowlisted_free_strategy_summary_without_merging_portfolio(tmp_path):
    trading_dir = tmp_path / "trading"
    _write_existing_status(trading_dir)
    lab = trading_dir / "free-strategies" / "lab.sqlite3"
    secret_marker = "SOURCE-MUST-NEVER-REACH-DASHBOARD"
    artifact = Artifact.create(
        source=f"# {secret_marker}\ndef decide(context): return {{'schema_version':1,'target_positions':[],'state':{{}},'reason':'wait'}}",
        image=IMAGE,
        name="独立候補",
        family="dashboard-test",
        thesis="表示はallowlistだけ",
        symbols=["BTC/JPY"],
        parameters={"private_parameter": secret_marker},
        initial_state={"private_state": secret_marker},
    )
    store = LabStore(lab)
    try:
        store.register(artifact)
        identity = store.create(artifact.digest, now=1000.0, capital="50000", days=1)
        with store.transaction():
            store.db.execute(
                "INSERT INTO samples(experiment,bucket,observed_at,equity) VALUES (?,?,?,?)",
                (identity, 0, 1050.0, "50500"),
            )
        evaluate(store, identity, now=1050.0)
    finally:
        store.close()

    snapshot = build_dashboard_snapshot(trading_dir, now=1100.0)
    experiments = snapshot["free_strategies"]["experiments"]
    assert len(experiments) == 1
    item = experiments[0]
    assert item["name"] == "独立候補"
    assert item["family"] == "dashboard-test"
    assert item["net_pnl_jpy"] == "500"
    assert item["live_eligible"] is False

    # Existing PAPER totals remain the existing worker's values.  The free
    # strategy's 50,000 JPY virtual account is intentionally not aggregated.
    assert snapshot["portfolio"]["capital_jpy"] == "10000"
    assert snapshot["portfolio"]["deployed_jpy"] == "0"
    assert snapshot["portfolio"]["position_count"] == 0

    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert secret_marker not in raw
    assert "source" not in item
    assert "parameters" not in item
    assert "initial_state" not in item


def test_dashboard_health_is_allowlisted_and_redacted(tmp_path):
    trading_dir = tmp_path / "trading"
    _write_existing_status(trading_dir)
    directory = trading_dir / "free-strategies"
    directory.mkdir(parents=True)
    (directory / "health.json").write_bytes(encode({
        "schema_version": 1,
        "mode": "PAPER",
        "status": "degraded",
        "heartbeat_at": 1090.0,
        "completed_experiments": 1,
        "error_codes": ["public_data_unavailable"],
    }))

    snapshot = build_dashboard_snapshot(trading_dir, now=1100.0)
    assert snapshot["free_strategies"]["health"] == {
        "status": "degraded",
        "heartbeat_at": 1090.0,
        "age_sec": 10,
        "completed_experiments": 1,
        "error_codes": ["public_data_unavailable"],
    }


def test_dashboard_asset_has_separate_free_strategy_panel():
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    assert 'id="free-strategy-panel"' in html
    assert 'id="free-strategies"' in html
    assert 'id="free-strategy-health"' in html
    assert "AI戦略研究" in html and "独立PAPER" in html
    assert "render.lastData.free_strategies" in html
    assert "#free-strategies" in css
