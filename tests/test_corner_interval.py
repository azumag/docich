import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _make_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE paper_fills("
        "fill_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, amount TEXT, price TEXT, filled_at TEXT, reason_code TEXT)"
    )
    con.execute("INSERT INTO paper_fills VALUES('b1','X','buy','100','10','1000','')")
    con.execute("INSERT INTO paper_fills VALUES('s1','X','sell','100','12','1200','')")
    con.execute("INSERT INTO paper_fills VALUES('b2','Y','buy','50','20','1400','')")
    con.execute("INSERT INTO paper_fills VALUES('s2','Y','sell','50','18','1600','stop_loss')")
    con.execute("INSERT INTO paper_fills VALUES('b3','Z','buy','100','30','1600','')")
    con.execute("INSERT INTO paper_fills VALUES('s3','Z','sell','100','15','1800','risk_rejected')")
    con.commit()
    con.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return _make_db(tmp_path / "paper.sqlite3")


def test_first_cursor(db):
    from docich.trading import corner_interval as ci

    cursor = ci.first_cursor(db)
    assert cursor is not None
    assert cursor[0] == 1000.0


def test_cursor_roundtrip(db):
    from docich.trading import corner_interval as ci

    ci.set_cursor(db, "paper", 1200.0, "s1", 9999.0)
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


def test_select_picks_pick_main_and_state_cursor(db):
    from docich.trading import corner_interval as ci

    cursor = ci.first_cursor(db)
    fills = ci.fills_in_interval(db, cursor, now=5000.0)
    picks = ci.select_picks(db, fills, max_picks=2)
    picked_ids = [p["fill_id"] for p in picks]
    # Realized abs max: s3 (-1300) then s1 (+200).
    assert picked_ids == ["s3", "s1"]
