from __future__ import annotations

import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.strategy_lab import experiment_from_mapping  # noqa: E402
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
    }, activated_at=NOW - 1000)


def _db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE paper_fills (
        fill_id TEXT PRIMARY KEY, opportunity_id TEXT, strategy_id TEXT, symbol TEXT,
        side TEXT, quote TEXT, amount TEXT, price TEXT, quote_notional TEXT,
        reference_notional TEXT, reason_code TEXT, filled_at REAL
    )""")
    return conn


def _insert(conn, fill_id, strategy, side, price, at, *, amount="1", symbol="BTC/JPY"):
    conn.execute(
        "INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, fill_id, strategy, symbol, side, "JPY", str(amount), str(price),
         str(price), str(price), "x", at),
    )


def test_experiment_metrics_ignore_other_paper_strategies(tmp_path):
    spec = _spec()
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "other-buy", "momentum-v1", "buy", 100, NOW - 900)
    _insert(conn, "other-sell", "exit-v1", "sell", 140, NOW - 800)
    _insert(conn, "lab-buy", "lab:iso:entry-1", "buy", 100, NOW - 700)
    _insert(conn, "lab-sell", "lab-exit:iso:exit-1", "sell", 110, NOW - 600)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, spec, capital_jpy="10000", now=NOW)
    assert result["status"] == "ok"
    assert result["closed_sells"] == 1
    assert result["realized_pnl_jpy"] == "10"
    assert result["self_entry_closed_sells"] == 1
    assert result["self_entry_realized_pnl_jpy"] == "10"
    assert result["carry_in_closed_sells"] == 0
    assert result["carry_in_realized_pnl_jpy"] == "0"
    assert result["open_position_count"] == 0
    assert result["inventory_complete"] is True
    assert result["promotion_ready"] is False


def test_unpaired_exit_from_preexisting_inventory_is_not_credited(tmp_path):
    spec = _spec()
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "old-inventory-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, spec, capital_jpy="10000", now=NOW)
    assert result["status"] == "partial"
    assert result["reason_code"] == "ledger_incomplete_basis"
    assert result["closed_sells"] == 0
    assert result["ignored_unpaired_exits"] == 1
    assert result["realized_pnl_jpy"] == "0"
    assert result["open_position_count"] == 1
    assert result["inventory_complete"] is False
    assert result["promotion_ready"] is False


def test_historical_acquisition_supports_current_carry_in_exit(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "old-buy", "lab:previous:entry", "buy", 100, NOW - 5000)
    _insert(conn, "current-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["schema_version"] == 2
    assert result["status"] == "ok"
    assert result["ignored_unpaired_exits"] == 0
    assert result["closed_sells"] == result["carry_in_closed_sells"] == 1
    assert result["realized_pnl_jpy"] == result["carry_in_realized_pnl_jpy"] == "20"
    assert result["self_entry_realized_pnl_jpy"] == "0"
    assert result["self_entry_closed_sells"] == 0
    assert result["open_position_count"] == 0
    assert result["inventory_complete"] is True
    assert result["accounting_scope"] == "experiment_exit_decisions"


def test_reused_experiment_id_does_not_credit_previous_activation(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "old-buy", "lab:iso:entry-1", "buy", 100, NOW - 5000)
    _insert(conn, "old-exit", "lab-exit:iso:exit-1", "sell", 900, NOW - 4000)
    _insert(conn, "carry-in", "lab:iso:entry-1", "buy", 150, NOW - 3000)
    _insert(conn, "current-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "ok"
    assert result["closed_sells"] == result["carry_in_closed_sells"] == 1
    assert result["realized_pnl_jpy"] == result["carry_in_realized_pnl_jpy"] == "-30"
    assert result["self_entry_closed_sells"] == 0


def test_mixed_inventory_uses_shared_average_cost_after_unrelated_partial_sale(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "old-buy", "lab:previous:entry", "buy", 100, NOW - 5000, amount="2")
    _insert(conn, "own-buy", "lab:iso:entry-1", "buy", 200, NOW - 900, amount="2")
    _insert(conn, "unrelated-exit", "exit-v1", "sell", 900, NOW - 800)
    _insert(conn, "own-exit-1", "lab-exit:iso:exit-1", "sell", 180, NOW - 700)
    _insert(conn, "own-exit-2", "lab-exit:iso:exit-1", "sell", 140, NOW - 600)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "ok"
    assert result["closed_sells"] == result["mixed_origin_closed_sells"] == 2
    assert result["self_entry_closed_sells"] == result["carry_in_closed_sells"] == 2
    assert Decimal(result["realized_pnl_jpy"]) == Decimal("20")
    assert Decimal(result["self_entry_realized_pnl_jpy"]) == Decimal("-40")
    assert Decimal(result["carry_in_realized_pnl_jpy"]) == Decimal("60")
    assert (
        Decimal(result["self_entry_realized_pnl_jpy"]) + Decimal(result["carry_in_realized_pnl_jpy"])
        == Decimal(result["realized_pnl_jpy"])
    )
    assert result["wins"] == 1
    assert Decimal(result["profit_factor"]) == Decimal("3")
    assert Decimal(result["max_realized_drawdown_pct"]) == Decimal("0.1")
    assert result["open_position_count"] == 1
    assert result["inventory_complete"] is True
    assert result["promotion_ready"] is False


def test_other_strategy_buy_after_activation_is_carry_in_not_self_entry(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "other-buy", "lab:iso-other:entry-1", "buy", 100, NOW - 900)
    _insert(conn, "own-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["self_entry_closed_sells"] == 0
    assert result["carry_in_closed_sells"] == 1
    assert result["carry_in_realized_pnl_jpy"] == "20"


def test_unrelated_sale_consumes_self_inventory_before_experiment_can_reuse_it(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "own-buy", "lab:iso:entry-1", "buy", 100, NOW - 900)
    _insert(conn, "other-sell", "exit-v1", "sell", 200, NOW - 800)
    _insert(conn, "own-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "partial"
    assert result["ignored_unpaired_exits"] == 1
    assert result["closed_sells"] == 0
    assert result["realized_pnl_jpy"] == "0"


def test_partial_missing_basis_credits_only_known_quantity(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "own-buy", "lab:iso:entry-1", "buy", 100, NOW - 900)
    _insert(conn, "own-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500, amount="2")
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "partial"
    assert result["closed_sells"] == 1
    assert result["ignored_unpaired_exits"] == 1
    assert result["realized_pnl_jpy"] == result["self_entry_realized_pnl_jpy"] == "20"
    assert result["open_position_count"] == 1
    assert result["inventory_complete"] is False


def test_historical_unrelated_missing_basis_does_not_mark_current_results_partial(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "old-unpaired", "exit-v1", "sell", 200, NOW - 5000, symbol="ETH/JPY")
    _insert(conn, "old-offset", "momentum-v1", "buy", 200, NOW - 4000, symbol="ETH/JPY")
    _insert(conn, "own-buy", "lab:iso:entry-1", "buy", 100, NOW - 900)
    _insert(conn, "own-exit", "lab-exit:iso:exit-1", "sell", 120, NOW - 500)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "ok"
    assert result["ignored_unpaired_exits"] == 0
    assert result["realized_pnl_jpy"] == "20"
    # Signed net inventory is flat, but the historical basis mismatch remains
    # explicit so rotation cannot claim a complete account snapshot.
    assert result["open_position_count"] == 0
    assert result["inventory_complete"] is False


def test_now_bounds_results_and_global_inventory_to_one_snapshot(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "own-buy", "lab:iso:entry-1", "buy", 100, NOW - 1000)
    _insert(conn, "now-exit", "lab-exit:iso:exit-1", "sell", 120, NOW)
    _insert(conn, "future-buy", "lab:iso:entry-1", "buy", 100, NOW + 1, symbol="ETH/JPY")
    _insert(conn, "future-exit", "lab-exit:iso:exit-1", "sell", 1, NOW + 2, symbol="ETH/JPY")
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["evaluated_at"] == NOW
    assert result["closed_sells"] == 1
    assert result["realized_pnl_jpy"] == "20"
    assert result["open_position_count"] == 0
    assert result["inventory_complete"] is True


def test_equal_timestamp_fills_follow_ledger_row_order(tmp_path):
    conn = _db(tmp_path / "paper.sqlite3")
    _insert(conn, "z-buy", "lab:iso:entry-1", "buy", 100, NOW - 900)
    _insert(conn, "a-sell", "lab-exit:iso:exit-1", "sell", 120, NOW - 900)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "ok"
    assert result["realized_pnl_jpy"] == "20"


def test_healthy_empty_ledger_has_zero_results_and_default_clock(tmp_path, monkeypatch):
    _db(tmp_path / "paper.sqlite3").close()
    monkeypatch.setattr("docich.trading.strategy_metrics.time.time", lambda: NOW)
    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000")
    assert result["status"] == "ok"
    assert result["evaluated_at"] == NOW
    assert result["closed_sells"] == 0
    assert result["realized_pnl_jpy"] == "0"
    assert result["open_position_count"] == 0
    assert result["inventory_complete"] is True


@pytest.mark.parametrize("ledger_state,reason", [
    ("missing", "ledger_missing"),
    ("corrupt", "ledger_read_error"),
    ("missing_table", "ledger_read_error"),
])
def test_unavailable_ledger_is_not_a_successful_zero_trade_evaluation(tmp_path, ledger_state, reason):
    path = tmp_path / "paper.sqlite3"
    if ledger_state == "corrupt":
        path.write_bytes(b"not a SQLite database")
    elif ledger_state == "missing_table":
        sqlite3.connect(path).close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "unavailable"
    assert result["reason_code"] == reason
    assert result["closed_sells"] is None
    assert result["realized_pnl_jpy"] is None
    assert result["open_position_count"] is None
    assert result["inventory_complete"] is False
    assert result["promotion_ready"] is False
    if ledger_state == "missing":
        assert not path.exists()


def _profitable_round_trips(conn, *, own=True):
    for index in range(20):
        _insert(conn, f"buy-{index}", "lab:iso:entry-1" if own else "momentum-v1",
                "buy", 100, NOW - 900 + index * 2)
        _insert(conn, f"sell-{index}", "lab-exit:iso:exit-1",
                "sell", 95 if index == 19 else 110, NOW - 899 + index * 2)


@pytest.mark.parametrize("own,expected", [(True, True), (False, False)])
def test_promotion_requires_complete_self_entry_evidence(tmp_path, own, expected):
    conn = _db(tmp_path / "paper.sqlite3")
    _profitable_round_trips(conn, own=own)
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["closed_sells"] == 20
    assert result["realized_pnl_jpy"] == "185"
    assert result["profit_factor"] == "38"
    assert result["promotion_ready"] is expected


@pytest.mark.parametrize("column,value", [
    ("amount", "NaN"), ("amount", "Infinity"), ("amount", "-1"), ("amount", "0"),
    ("price", "NaN"), ("price", "Infinity"), ("price", "-1"), ("price", "0"),
    ("side", "hold"), ("side", "BUY"), ("symbol", None), ("symbol", ""),
    ("strategy_id", None), ("strategy_id", ""),
    ("filled_at", float("nan")), ("filled_at", float("inf")),
    ("filled_at", -1), ("filled_at", "broken"),
])
def test_malformed_historical_fill_invalidates_otherwise_promotable_evaluation(tmp_path, column, value):
    conn = _db(tmp_path / "paper.sqlite3")
    _profitable_round_trips(conn)
    _insert(conn, "bad-history", "old-strategy", "buy", 100, NOW - 5000, symbol="ETH/JPY")
    conn.execute(f"UPDATE paper_fills SET {column} = ? WHERE fill_id = 'bad-history'", (value,))
    conn.commit(); conn.close()

    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=NOW)
    assert result["status"] == "invalid"
    assert result["reason_code"] == "ledger_invalid_row"
    assert result["realized_pnl_jpy"] is None
    assert result["closed_sells"] is None
    assert result["promotion_ready"] is False


@pytest.mark.parametrize("capital", ["0", "-1", "NaN", "Infinity", True])
def test_invalid_capital_cannot_publish_zero_drawdown(tmp_path, capital):
    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy=capital, now=NOW)
    assert result["status"] == "invalid"
    assert result["reason_code"] == "evaluation_invalid_capital"
    assert result["max_realized_drawdown_pct"] is None
    assert result["promotion_ready"] is False


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -1, NOW - 1001, True])
def test_invalid_evaluation_clock_fails_closed(tmp_path, now):
    result = evaluate_strategy_experiment(tmp_path, _spec(), capital_jpy="10000", now=now)
    assert result["status"] == "invalid"
    assert result["reason_code"] == "evaluation_invalid_clock"
    assert result["evaluated_at"] is None
    assert result["promotion_ready"] is False
