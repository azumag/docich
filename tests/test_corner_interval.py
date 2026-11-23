import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _create_db(path: Path, rows) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE paper_fills("
        "fill_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, amount TEXT, price TEXT, filled_at TEXT, reason_code TEXT)"
    )
    con.executemany("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def _make_db(path: Path) -> Path:
    return _create_db(path, [
        ("b1", "X", "buy", "100", "10", "1000", ""),
        ("s1", "X", "sell", "100", "12", "1200", ""),
        ("b2", "Y", "buy", "50", "20", "1400", ""),
        ("s2", "Y", "sell", "50", "18", "1600", "stop_loss"),
        ("b3", "Z", "buy", "100", "30", "1600", ""),
        ("s3", "Z", "sell", "100", "15", "1800", "risk_rejected"),
    ])


@pytest.fixture()
def db(tmp_path):
    return _make_db(tmp_path / "paper.sqlite3")


def test_first_cursor(db):
    from docich.trading import corner_interval as ci

    cursor = ci.first_cursor(db)
    assert cursor is not None
    assert cursor[0] == 1000.0


def test_first_run_interval_includes_the_first_fill(db):
    from docich.trading import corner_interval as ci

    fills = ci.fills_in_interval(db, None, now=5000.0)
    assert [f["fill_id"] for f in fills] == ["b1", "s1", "b2", "b3", "s2", "s3"]
    assert ci.interval_realized(db, None, now=5000.0) == Decimal("-1400")


def test_cursor_roundtrip(db):
    from docich.trading import corner_interval as ci

    ci.set_cursor(db, "paper", 1200.0, "s1", 9999.0)
    ci.set_cursor(db, "paper", 1000.0, "b1", 10000.0)
    assert ci.get_cursor(db, "paper") == (1200.0, "s1")


def test_fills_in_interval_excludes_cursor_and_before(db):
    from docich.trading import corner_interval as ci

    cursor = (1000.0, "b1")
    fills = ci.fills_in_interval(db, cursor, now=5000.0)
    ids = [f["fill_id"] for f in fills]
    assert ids == ["s1", "b2", "b3", "s2", "s3"]


def test_interval_realized(db):
    from docich.trading import corner_interval as ci

    cursor = (1200.0, "s1")
    assert ci.interval_realized(db, cursor, now=5000.0) == Decimal("-1600")


def test_same_second_cursor_carries_pre_cursor_fifo_basis(tmp_path):
    from docich.trading import corner_interval as ci
    from docich.trading.performance import build_performance

    db = _create_db(tmp_path / "same-second.sqlite3", [
        # Deliberately insert the sell first: FIFO must use the canonical
        # (filled_at, fill_id) order, not SQLite rowid insertion order.
        ("b_sell", "X", "sell", "10", "12", "1000", ""),
        ("a_buy", "X", "buy", "10", "10", "1000", ""),
    ])
    cursor = (1000.0, "a_buy")
    fills = ci.fills_in_interval(db, cursor, now=1000.0)
    assert [row["fill_id"] for row in fills] == ["b_sell"]
    assert ci.interval_realized(db, cursor, now=1000.0) == Decimal("20")

    performance = build_performance(
        db,
        capital_reference="1000",
        positions={},
        prices={},
        now=1000.0,
        interval_start=cursor,
    )
    assert performance["interval_realized_pnl_jpy"] == "20"
    assert performance["theoretical_benchmark"]["reason"] == "history_not_configured"


def _tier_db(path: Path) -> Path:
    return _create_db(path, [
        ("a_v", "V", "buy", "1", "10", "1000", ""),
        ("a_w", "W", "buy", "8", "10", "1000", ""),
        ("a_x", "X", "buy", "1", "10", "1000", ""),
        ("a_y", "Y", "buy", "5", "10", "1000", ""),
        ("a_z", "Z", "buy", "6", "10", "1000", ""),
        ("s1", "X", "sell", "1", "20", "1100", ""),
        ("s2", "Y", "sell", "2", "8", "1200", ""),
        ("s3", "Z", "sell", "3", "12", "1300", ""),
        ("s4", "W", "sell", "4", "9", "1400", ""),
        ("s5", "V", "sell", "1", "9", "1500", "stop_loss"),
    ])


def test_select_picks_skip_empty_state_change_tier_and_use_cost_risk_when_present(tmp_path):
    from docich.trading import corner_interval as ci

    db = _tier_db(tmp_path / "tiers.sqlite3")
    interval = ci.fills_in_interval(db, (1000.0, "a_z"), now=5000.0)
    picks = ci.select_picks(db, interval, max_picks=4)
    assert [row["fill_id"] for row in picks] == ["s1", "s4", "s5"]

    # With no cost/risk candidate either, empty tiers must not fill the
    # remaining slots with arbitrary score-zero sells.
    picks_without_risk = ci.select_picks(db, interval[:-1], max_picks=4)
    assert [row["fill_id"] for row in picks_without_risk] == ["s1", "s4"]


def test_select_picks_pick_main_and_state_cursor(db):
    from docich.trading import corner_interval as ci

    # First run has no saved cursor: include the entire ledger, including b1.
    fills = ci.fills_in_interval(db, None, now=5000.0)
    picks = ci.select_picks(db, fills, max_picks=2)
    picked_ids = [p["fill_id"] for p in picks]
    # Realized abs max: s3 (-1500), then amount max: earliest 100-unit fill b1.
    assert picked_ids == ["s3", "b1"]
