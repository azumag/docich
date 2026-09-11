from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.strategy_lab import experiment_from_mapping, save_strategy_experiment  # noqa: E402
from docich.trading.strategy_metrics import evaluate_strategy_experiment  # noqa: E402

NOW = 1_800_000_000.0


def _spec():
    return experiment_from_mapping({
        "experiment_id": "iso",
        "name": "isolated",
        "thesis": "実験約定だけで評価できるか検証する",
        "entry_rules": [{
            "rule_id": "entry-1", "combine": "all", "max_notional_fraction": 0.1,
            "conditions": [{"feature": "return_bps", "lookback": 3, "op": ">=", "threshold": 100}],
        }],
        "exit_rules": [{
            "rule_id": "exit-1", "combine": "all",
            "conditions": [{"feature": "pnl_bps", "op": ">=", "threshold": 100}],
        }],
        "max_pair_correlation": 0.8,
    })


def _db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE paper_fills (
        fill_id TEXT PRIMARY KEY, opportunity_id TEXT, strategy_id TEXT, symbol TEXT,
        side TEXT, quote TEXT, amount TEXT, price TEXT, quote_notional TEXT,
        reference_notional TEXT, reason_code TEXT, filled_at REAL
    )""")
    return conn


def _insert(conn, fill_id, strategy, side, price, at):
    conn.execute(
        "INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, fill_id, strategy, "BTC/JPY", side, "JPY", "1", str(price),
         str(price), str(price), "x", at),
    )


def test_experiment_metrics_ignore_other_paper_strategies(tmp_path):
    spec = _spec()
    save_strategy_experiment(tmp_path, spec, activated_at=NOW - 1000)
    active = __import__("docich.trading.strategy_lab", fromlist=["load_strategy_experiment"]).load_strategy_experiment(tmp_path)
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "other-buy", "momentum-v1", "buy", 100, NOW - 900)
    _insert(conn, "other-sell", "exit-v1", "sell", 140, NOW - 800)
    _insert(conn, "lab-buy", "lab:iso:entry-1", "buy", 100, NOW - 700)
    _insert(conn, "lab-sell", "lab-exit:iso:exit-1", "sell", 110, NOW - 600)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, active, capital_jpy="10000")
    assert result["closed_sells"] == 1
    assert result["realized_pnl_jpy"] == "10"
    assert result["promotion_ready"] is False


def test_unpaired_exit_from_preexisting_inventory_is_not_credited(tmp_path):
    spec = _spec()
    save_strategy_experiment(tmp_path, spec, activated_at=NOW - 1000)
    active = __import__("docich.trading.strategy_lab", fromlist=["load_strategy_experiment"]).load_strategy_experiment(tmp_path)
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "old-inventory-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, active, capital_jpy="10000")
    assert result["closed_sells"] == 0
    assert result["ignored_unpaired_exits"] == 1
    assert result["realized_pnl_jpy"] == "0"
