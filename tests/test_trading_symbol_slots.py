from __future__ import annotations

import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.strategy_lab import (  # noqa: E402
    EXPERIMENT_FILENAME, experiment_from_mapping, load_strategy_experiment,
    save_pending_experiment, save_strategy_experiment,
)
from docich.trading.strategy_metrics import evaluate_strategy_experiment  # noqa: E402
from docich.trading.symbol_slots import (  # noqa: E402
    COMMON_SLOT, SLOT_META_FILENAME, SLOTS_DIRNAME, SlotLayout, SymbolSlotError,
    create_slot, evaluation_scope, experiment_ids_in_use, read_slot_layout,
    slot_dir, slot_dirname, validate_symbol,
)

NOW = 1_800_000_000.0
D = Decimal


def _spec(experiment_id="iso"):
    return experiment_from_mapping({
        "experiment_id": experiment_id,
        "name": experiment_id,
        "thesis": "銘柄別スロットの評価範囲を検証する",
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


def _ledger(root: Path, fills):
    conn = sqlite3.connect(root / "paper.sqlite3")
    conn.execute("""CREATE TABLE paper_fills (
        fill_id TEXT PRIMARY KEY, opportunity_id TEXT, strategy_id TEXT, symbol TEXT,
        side TEXT, quote TEXT, amount TEXT, price TEXT, quote_notional TEXT,
        reference_notional TEXT, reason_code TEXT, filled_at REAL
    )""")
    for index, (symbol, strategy, side, price, at) in enumerate(fills):
        conn.execute(
            "INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"f{index}", f"f{index}", strategy, symbol, side, "JPY", "1", str(price),
             str(price), str(price), "x", at),
        )
    conn.commit()
    conn.close()


def _mixed_ledger(root: Path):
    _ledger(root, [
        ("BTC/JPY", "lab:iso:entry-1", "buy", 100, NOW - 900),
        ("BTC/JPY", "lab-exit:iso:exit-1", "sell", 110, NOW - 800),
        ("ETH/JPY", "lab:iso:entry-1", "buy", 100, NOW - 700),
        ("ETH/JPY", "lab-exit:iso:exit-1", "sell", 95, NOW - 600),
        ("XRP/JPY", "lab:iso:entry-1", "buy", 100, NOW - 500),
        ("XRP/JPY", "lab-exit:iso:exit-1", "sell", 103, NOW - 400),
        # Another strategy's inventory that the experiment has not exited yet.
        ("ETH/JPY", "relative-value-v1", "buy", 100, NOW - 300),
    ])


def _evaluate(root, **scope):
    return evaluate_strategy_experiment(root, _spec(), capital_jpy="10000", now=NOW, **scope)


# --- evaluation scope -------------------------------------------------------

def test_unscoped_evaluation_is_unchanged_and_carries_no_scope_key(tmp_path):
    _mixed_ledger(tmp_path)
    result = _evaluate(tmp_path)
    assert result["status"] == "ok"
    assert result["closed_sells"] == 3 and D(result["realized_pnl_jpy"]) == D("8")
    assert result["open_position_count"] == 1
    assert "symbol_scope" not in result


def test_include_scope_counts_only_that_symbol(tmp_path):
    _mixed_ledger(tmp_path)
    result = _evaluate(tmp_path, symbols={"ETH/JPY"})
    assert result["status"] == "ok"
    assert result["closed_sells"] == 1 and D(result["realized_pnl_jpy"]) == D("-5")
    assert result["open_position_count"] == 1  # the other strategy's ETH buy
    assert result["symbol_scope"] == {"mode": "include", "symbols": ["ETH/JPY"]}
    flat = _evaluate(tmp_path, symbols={"BTC/JPY"})
    assert flat["closed_sells"] == 1 and D(flat["realized_pnl_jpy"]) == D("10")
    assert flat["open_position_count"] == 0 and flat["inventory_complete"] is True


def test_exclude_scope_is_the_complement_and_scopes_partition_the_total(tmp_path):
    _mixed_ledger(tmp_path)
    total = _evaluate(tmp_path)
    eth = _evaluate(tmp_path, symbols={"ETH/JPY"})
    rest = _evaluate(tmp_path, exclude_symbols={"ETH/JPY"})
    assert rest["symbol_scope"] == {"mode": "exclude", "symbols": ["ETH/JPY"]}
    assert rest["closed_sells"] == 2 and rest["open_position_count"] == 0
    assert eth["closed_sells"] + rest["closed_sells"] == total["closed_sells"]
    assert (D(eth["realized_pnl_jpy"]) + D(rest["realized_pnl_jpy"])
            == D(total["realized_pnl_jpy"]))
    assert eth["wins"] + rest["wins"] == total["wins"]


def test_empty_exclude_scope_equals_unscoped_numbers(tmp_path):
    _mixed_ledger(tmp_path)
    total, scoped = _evaluate(tmp_path), _evaluate(tmp_path, exclude_symbols=())
    scoped.pop("symbol_scope")
    assert scoped == total


def test_scope_does_not_hide_an_incomplete_inventory_inside_it(tmp_path):
    _ledger(tmp_path, [
        ("BTC/JPY", "lab:iso:entry-1", "buy", 100, NOW - 900),
        ("BTC/JPY", "lab-exit:iso:exit-1", "sell", 110, NOW - 800),
        ("ETH/JPY", "lab-exit:iso:exit-1", "sell", 95, NOW - 600),  # never bought
    ])
    assert _evaluate(tmp_path, symbols={"ETH/JPY"})["inventory_complete"] is False
    clean = _evaluate(tmp_path, symbols={"BTC/JPY"})
    assert clean["inventory_complete"] is True and clean["status"] == "ok"


@pytest.mark.parametrize("scope", [
    {"symbols": {"BTC/JPY"}, "exclude_symbols": {"ETH/JPY"}},
    {"symbols": "BTC/JPY"},
    {"symbols": ()},
    {"symbols": {""}},
    {"exclude_symbols": {1}},
    {"exclude_symbols": 5},
])
def test_malformed_scope_is_invalid_not_a_whole_account_evaluation(tmp_path, scope):
    _mixed_ledger(tmp_path)
    result = _evaluate(tmp_path, **scope)
    assert result["status"] == "invalid"
    assert result["reason_code"] == "evaluation_invalid_symbol_scope"
    assert result["closed_sells"] is None and result["promotion_ready"] is False


# --- slot layout ------------------------------------------------------------

def test_no_slots_directory_means_only_the_common_slot(tmp_path):
    layout = read_slot_layout(tmp_path)
    assert layout == SlotLayout() and layout.ok
    assert layout.slot_of("BTC/JPY") == COMMON_SLOT
    assert slot_dir(tmp_path) == tmp_path
    assert evaluation_scope(layout) == {}


def test_create_slot_is_idempotent_and_listed(tmp_path):
    first = create_slot(tmp_path, "ETH/JPY", now=NOW)
    assert first == tmp_path / SLOTS_DIRNAME / "ETH_JPY"
    meta = (first / SLOT_META_FILENAME).read_bytes()
    assert create_slot(tmp_path, "ETH/JPY", now=NOW + 60) == first
    assert (first / SLOT_META_FILENAME).read_bytes() == meta
    create_slot(tmp_path, "BTC/JPY", now=NOW)
    layout = read_slot_layout(tmp_path)
    assert layout.dedicated == ("BTC/JPY", "ETH/JPY") and layout.ok
    assert layout.slot_of("ETH/JPY") == "ETH/JPY" and layout.slot_of("XRP/JPY") == COMMON_SLOT
    assert evaluation_scope(layout) == {"exclude_symbols": frozenset({"BTC/JPY", "ETH/JPY"})}
    assert evaluation_scope(layout, "ETH/JPY") == {"symbols": frozenset({"ETH/JPY"})}


@pytest.mark.parametrize("symbol", [
    "btc/jpy", "BTC", "BTC/JPY/X", "../x/JPY", "BTC_X/JPY", "", None, "BTC/ JPY", "common",
])
def test_unsafe_symbols_are_rejected(tmp_path, symbol):
    with pytest.raises(SymbolSlotError):
        validate_symbol(symbol)
    with pytest.raises(SymbolSlotError):
        create_slot(tmp_path, symbol, now=NOW)
    assert not (tmp_path / SLOTS_DIRNAME).exists()


def test_slot_dirname_round_trips_without_path_separators():
    assert slot_dirname("1INCH/JPY") == "1INCH_JPY"
    assert "/" not in slot_dirname("BTC/JPY")


def test_unreadable_slot_entries_make_the_layout_invalid(tmp_path):
    create_slot(tmp_path, "ETH/JPY", now=NOW)
    root = tmp_path / SLOTS_DIRNAME
    (root / "BTC_JPY").mkdir()  # no slot.json
    (root / "XRP_JPY").mkdir()
    (root / "XRP_JPY" / SLOT_META_FILENAME).write_text(
        json.dumps({"schema_version": 1, "symbol": "ETH/JPY", "created_at": NOW}), encoding="utf-8")
    (root / "stray.txt").write_text("x", encoding="utf-8")
    layout = read_slot_layout(tmp_path)
    assert layout.dedicated == ("ETH/JPY",)
    assert layout.invalid == ("BTC_JPY", "XRP_JPY", "stray.txt") and not layout.ok
    # An unreadable directory may own a symbol: never fall back to "common".
    with pytest.raises(SymbolSlotError):
        evaluation_scope(layout)
    with pytest.raises(SymbolSlotError):
        experiment_ids_in_use(tmp_path, layout)
    with pytest.raises(SymbolSlotError):
        create_slot(tmp_path, "BTC/JPY", now=NOW)


def test_symlinked_slots_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    trading = tmp_path / "trading"
    trading.mkdir()
    (trading / SLOTS_DIRNAME).symlink_to(outside, target_is_directory=True)
    assert read_slot_layout(trading).invalid == (SLOTS_DIRNAME,)
    (trading / SLOTS_DIRNAME).unlink()
    create_slot(trading, "ETH/JPY", now=NOW)
    (trading / SLOTS_DIRNAME / "BTC_JPY").symlink_to(trading / SLOTS_DIRNAME / "ETH_JPY")
    assert read_slot_layout(trading).invalid == ("BTC_JPY",)


def test_evaluation_scope_rejects_an_unknown_slot(tmp_path):
    create_slot(tmp_path, "ETH/JPY", now=NOW)
    with pytest.raises(SymbolSlotError):
        evaluation_scope(read_slot_layout(tmp_path), "BTC/JPY")


def test_invalid_clock_cannot_create_a_slot(tmp_path):
    for clock in (float("nan"), -1.0, True):
        with pytest.raises(SymbolSlotError):
            create_slot(tmp_path, "ETH/JPY", now=clock)


# --- slot state files and experiment ids ------------------------------------

def test_slot_state_files_use_the_existing_readers_without_touching_common(tmp_path):
    save_strategy_experiment(tmp_path, _spec("common-v1"), activated_at=NOW - 100)
    common_bytes = (tmp_path / EXPERIMENT_FILENAME).read_bytes()
    slot = create_slot(tmp_path, "ETH/JPY", now=NOW)
    save_strategy_experiment(slot, _spec("eth-v1"), activated_at=NOW)
    assert load_strategy_experiment(slot).experiment_id == "eth-v1"
    assert load_strategy_experiment(slot_dir(tmp_path, "ETH/JPY")).activated_at == NOW
    assert (tmp_path / EXPERIMENT_FILENAME).read_bytes() == common_bytes
    assert read_slot_layout(tmp_path).ok


def test_experiment_ids_in_use_reports_cross_slot_collisions(tmp_path):
    save_strategy_experiment(tmp_path, _spec("common-v1"), activated_at=NOW - 100)
    save_pending_experiment(tmp_path, _spec("common-v2"), proposed_at=NOW)
    eth = create_slot(tmp_path, "ETH/JPY", now=NOW)
    btc = create_slot(tmp_path, "BTC/JPY", now=NOW)
    save_strategy_experiment(eth, _spec("eth-v1"), activated_at=NOW)
    save_pending_experiment(btc, _spec("eth-v1"), proposed_at=NOW)
    assert experiment_ids_in_use(tmp_path) == {
        "common-v1": (COMMON_SLOT,),
        "common-v2": (COMMON_SLOT,),
        "eth-v1": ("BTC/JPY", "ETH/JPY"),
    }


def test_scoped_evaluation_through_the_layout_matches_the_slot(tmp_path):
    _mixed_ledger(tmp_path)
    create_slot(tmp_path, "ETH/JPY", now=NOW)
    layout = read_slot_layout(tmp_path)
    slot = _evaluate(tmp_path, **evaluation_scope(layout, "ETH/JPY"))
    common = _evaluate(tmp_path, **evaluation_scope(layout))
    assert slot["closed_sells"] == 1 and common["closed_sells"] == 2
    assert common["open_position_count"] == 0 and slot["open_position_count"] == 1
