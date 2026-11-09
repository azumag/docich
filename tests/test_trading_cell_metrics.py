from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.cell_metrics import (  # noqa: E402
    aggregate_cell_metrics,
    aggregate_cell_metrics_from_rows,
    buy_fee_correction_rate,
    experiment_id_from_strategy_id,
)
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.performance import build_performance, realized_pnl_for_fill  # noqa: E402

D = Decimal
JST = dt.timezone(dt.timedelta(hours=9))
T0 = 1_800_000_000.0  # fixed, arbitrary
V1_ZERO = {"model": "paper-cost-v1", "fee_rate": "0", "fee_source": "market"}
V1_FEE = {"model": "paper-cost-v1", "fee_rate": "0.0012", "fee_source": "fallback"}
V2_FEE = {"model": "paper-cost-v2", "fee_rate": "0.0012", "fee_source": "market"}
V2_ZERO = {"model": "paper-cost-v2", "fee_rate": "0", "fee_source": "market"}


class Ledger:
    def __init__(self, path: Path):
        PaperLedger(path).close()  # real schema
        self.path = path
        self.conn = sqlite3.connect(path)
        self.n = 0

    def fill(self, strategy, side, amount, price, at, *, symbol="ETH/JPY", cost=None):
        self.n += 1
        oid = f"o{self.n}"
        self.conn.execute(
            """INSERT INTO paper_fills (fill_id, opportunity_id, strategy_id, symbol, side, quote,
               amount, price, quote_notional, reference_notional, reason_code, filled_at,
               signal_context, cost_context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"paper:{oid}", oid, strategy, symbol, side, "JPY", str(amount), str(price),
             "0", "0", "x", at, None, None if cost is None else json.dumps(cost)),
        )
        self.conn.commit()
        return f"paper:{oid}"

    def result(self):
        return aggregate_cell_metrics(self.path)

    def close(self):
        self.conn.close()


@pytest.fixture()
def ledger(tmp_path):
    led = Ledger(tmp_path / "trading" / "paper.sqlite3")
    yield led
    led.close()


def cell(result, experiment, symbol="ETH/JPY"):
    matches = [c for c in result["cells"] if (c["experiment_id"], c["symbol"]) == (experiment, symbol)]
    assert len(matches) == 1, result["cells"]
    return matches[0]


def now_of(date_jst: str, hour: int, minute: int = 0) -> float:
    y, m, d = map(int, date_jst.split("-"))
    return dt.datetime(y, m, d, hour, minute, tzinfo=JST).timestamp()


def test_experiment_id_parsing():
    assert experiment_id_from_strategy_id("lab-exit:exp1:exit-1", "lab-exit") == "exp1"
    assert experiment_id_from_strategy_id("lab:exp1:entry-1", "lab-exit") is None
    assert experiment_id_from_strategy_id("lab-exit::exit-1", "lab-exit") is None
    assert experiment_id_from_strategy_id("momentum-v1", "lab") is None


def test_consistency_with_build_performance_and_per_fill(ledger):
    # Mixed ledger: two experiments, two symbols, a non-lab strategy, partial sells.
    ledger.fill("lab:A:e1", "buy", 10, 1000, T0 + 1)
    ledger.fill("lab:B:e1", "buy", 10, 1200, T0 + 2)               # B adds to the same pool
    ledger.fill("lab-exit:A:x-1", "sell", 5, 1300, T0 + 3)
    ledger.fill("momentum-v1", "sell", 3, 900, T0 + 4)             # non-lab sell, consumes inventory
    ledger.fill("lab-exit:B:x-1", "sell", 4, 1500, T0 + 5)
    ledger.fill("lab:A:e1", "buy", 2, 50000, T0 + 6, symbol="BTC/JPY")
    ledger.fill("lab-exit:A:x-2", "sell", 2, 49000, T0 + 7, symbol="BTC/JPY")
    ledger.fill("lab-exit:A:x-1", "sell", 8, 1100, T0 + 8)         # 20-5-3-4 = 8 held, fully paired
    result = ledger.result()
    assert result["status"] == "ok"
    performance = build_performance(
        ledger.path, capital_reference="1000000", positions={}, prices={}, now=T0 + 100
    )
    total = D(performance["realized_total_jpy"])
    assert D(result["total_realized_pnl_jpy"]) == total
    assert D(result["cell_realized_pnl_jpy"]) + D(result["non_cell_realized_pnl_jpy"]) == total
    assert sum(D(c["realized_pnl_jpy"]) for c in result["cells"]) == D(result["cell_realized_pnl_jpy"])
    # the non-lab sell is the only non-cell P/L: 3 * (900 - 1100)
    assert D(result["non_cell_realized_pnl_jpy"]) == D("-600")
    # per-fill agreement with realized_pnl_for_fill
    per_fill = {
        fid: realized_pnl_for_fill(ledger.path, fid)
        for (fid,) in ledger.conn.execute(
            "SELECT fill_id FROM paper_fills WHERE strategy_id LIKE 'lab-exit:%'"
        )
    }
    assert sum(per_fill.values()) == D(result["cell_realized_pnl_jpy"])


def test_basic_cell_numbers_wins_and_symbols_split(ledger):
    ledger.fill("lab:A:e1", "buy", 10, 1000, T0)
    ledger.fill("lab-exit:A:x-1", "sell", 5, 1200, T0 + 1)   # +1000 win
    ledger.fill("lab-exit:A:x-2", "sell", 5, 900, T0 + 2)    # -500 loss
    ledger.fill("lab:A:e1", "buy", 1, 100, T0 + 3, symbol="XRP/JPY")
    ledger.fill("lab-exit:A:x-1", "sell", 1, 110, T0 + 4, symbol="XRP/JPY")
    result = ledger.result()
    eth = cell(result, "A")
    assert (eth["closed_sells"], eth["wins"]) == (2, 1)
    assert D(eth["realized_pnl_jpy"]) == D("500")
    xrp = cell(result, "A", "XRP/JPY")
    assert (xrp["closed_sells"], xrp["wins"]) == (1, 1)
    assert D(xrp["realized_pnl_jpy"]) == D("10")


def test_carry_in_fraction(ledger):
    ledger.fill("lab:A:e1", "buy", 6, 1000, T0)
    ledger.fill("lab:B:e1", "buy", 4, 1000, T0 + 1)          # pool: 60% A / 40% B
    ledger.fill("lab-exit:A:x", "sell", 5, 1000, T0 + 2)     # A sells 5: 3 own, 2 carried
    ledger.fill("lab-exit:B:x", "sell", 5, 1000, T0 + 3)     # remaining 5 = 3 A / 2 B -> B: 2 own, 3 carried
    result = ledger.result()
    a, b = cell(result, "A"), cell(result, "B")
    assert D(a["carry_in_fraction"]) == D("0.4")
    assert D(b["carry_in_fraction"]) == D("0.6")
    assert D(a["carry_in_quantity"]) == D("2")


def test_carry_in_zero_when_self_bought_and_full_when_foreign_or_non_lab(ledger):
    ledger.fill("lab:A:e1", "buy", 5, 1000, T0)
    ledger.fill("lab-exit:A:x", "sell", 5, 1000, T0 + 1)
    ledger.fill("momentum-v1", "buy", 5, 1000, T0 + 2)       # non-lab origin counts as foreign
    ledger.fill("lab-exit:A:x", "sell", 5, 1000, T0 + 3)
    result = ledger.result()
    a = cell(result, "A")
    assert a["closed_sells"] == 2
    assert D(a["carry_in_fraction"]) == D("0.5")


def test_non_lab_sells_excluded_from_cells_but_consume_inventory(ledger):
    ledger.fill("lab:A:e1", "buy", 10, 1000, T0)
    ledger.fill("manual-exit", "sell", 6, 1000, T0 + 1)      # not lab-exit
    ledger.fill("lab-exit:A:x", "sell", 4, 1100, T0 + 2)
    ledger.fill("lab-exit:A:x", "sell", 1, 1100, T0 + 3)     # only 0 left -> unpaired
    result = ledger.result()
    a = cell(result, "A")
    assert a["closed_sells"] == 1 and D(a["sold_quantity"]) == D("4")
    assert D(a["realized_pnl_jpy"]) == D("400")
    assert D(a["carry_in_fraction"]) == D("0")
    assert result["status"] == "partial" and result["ignored_unpaired_sells"] == 1
    assert D(result["non_cell_realized_pnl_jpy"]) == D("0")


def test_jst_day_boundary(ledger):
    ledger.fill("lab:A:e1", "buy", 100, 1000, T0)
    before = dt.datetime(2026, 11, 6, 14, 59, tzinfo=dt.timezone.utc).timestamp()   # 23:59 JST 11-06
    after = dt.datetime(2026, 11, 6, 15, 0, tzinfo=dt.timezone.utc).timestamp()     # 00:00 JST 11-07
    # buys must precede sells chronologically
    ledger.conn.execute("UPDATE paper_fills SET filled_at = ?", (before - 10,))
    ledger.conn.commit()
    ledger.fill("lab-exit:A:x", "sell", 10, 1100, before)    # +10%
    ledger.fill("lab-exit:A:x", "sell", 10, 1200, after)     # +20%
    ledger.fill("lab-exit:A:x", "sell", 10, 900, after + 60) # -10%
    blocks = {b["day"]: b for b in cell(ledger.result(), "A")["return_blocks"]}
    assert set(blocks) == {"2026-11-06", "2026-11-07"}
    assert blocks["2026-11-06"]["count"] == 1
    assert D(blocks["2026-11-06"]["mean_return"]) == D("0.1")
    assert blocks["2026-11-07"]["count"] == 2
    # pool average is 1000 for all three sells (sells keep the average)
    assert D(blocks["2026-11-07"]["mean_return"]) == D("0.05")


def test_dust_sells_excluded_from_return_blocks(ledger):
    ledger.fill("lab:A:e1", "buy", 1, 1, T0)                 # 1 JPY cost -> dust (<= 1)
    ledger.fill("lab-exit:A:x", "sell", 1, 5, T0 + 1)
    ledger.fill("lab:A:e1", "buy", 1, 2, T0 + 2)             # 2 JPY cost -> counted
    ledger.fill("lab-exit:A:x", "sell", 1, 3, T0 + 3)
    a = cell(ledger.result(), "A")
    assert a["closed_sells"] == 2 and a["dust_excluded_sells"] == 1
    assert sum(b["count"] for b in a["return_blocks"]) == 1
    assert D(a["return_blocks"][0]["mean_return"]) == D("0.5")
    # dust still counts in realized P/L
    assert D(a["realized_pnl_jpy"]) == D("5")


# --- buy-fee correction -------------------------------------------------------

def _round_trip(ledger, cost, *, symbol="ETH/JPY", buy_price=1000, sell_price=1100):
    ledger.fill("lab:A:e1", "buy", 10, buy_price, T0, symbol=symbol, cost=cost)
    ledger.fill("lab-exit:A:x", "sell", 10, sell_price, T0 + 1, symbol=symbol,
                cost={"model": "paper-cost-v2", "fee_rate": "0.0012"})
    return cell(ledger.result(), "A", symbol)


def test_v1_zero_fee_buy_is_corrected_jpy_pair(ledger):
    c = _round_trip(ledger, V1_ZERO)
    assert D(c["realized_pnl_jpy"]) == D("1000")
    # buy re-priced at 1000 * 1.0012 = 1001.2 -> 10 * (1100 - 1001.2)
    assert D(c["corrected_realized_pnl_jpy"]) == D("988")
    assert ledger.result()["buy_fee_correction"] == {
        "buy_fills": 1, "corrected": 1, "corrected_without_cost_context": 0, "already_fee_bearing": 0,
    }


def test_v1_zero_fee_buy_uses_btc_rate(ledger):
    c = _round_trip(ledger, V1_ZERO, symbol="BTC/JPY", buy_price=1000000, sell_price=1100000)
    assert D(c["realized_pnl_jpy"]) == D("1000000")
    # 0.0010 -> buy at 1,001,000
    assert D(c["corrected_realized_pnl_jpy"]) == D("990000")


def test_v2_fee_included_buy_is_not_double_counted(ledger):
    # v2 fill price already includes the 12 bps; recorded == corrected.
    c = _round_trip(ledger, V2_FEE, buy_price=D("1001.2"))
    assert c["corrected_realized_pnl_jpy"] == c["realized_pnl_jpy"]
    assert D(c["realized_pnl_jpy"]) == D("988")
    assert ledger.result()["buy_fee_correction"]["corrected"] == 0


def test_v1_buy_with_recorded_nonzero_fee_not_corrected(ledger):
    c = _round_trip(ledger, V1_FEE, buy_price=D("1001.2"))
    assert c["corrected_realized_pnl_jpy"] == c["realized_pnl_jpy"]


def test_v2_zero_fee_is_deliberate_and_not_corrected(ledger):
    c = _round_trip(ledger, V2_ZERO)
    assert c["corrected_realized_pnl_jpy"] == c["realized_pnl_jpy"]


def test_buy_without_cost_context_is_legacy_and_corrected(ledger):
    c = _round_trip(ledger, None)
    assert D(c["corrected_realized_pnl_jpy"]) == D("988")
    assert ledger.result()["buy_fee_correction"]["corrected_without_cost_context"] == 1


def test_mixed_v1_and_v2_buys_in_one_pool_charge_only_the_v1_part(ledger):
    ledger.fill("lab:A:e1", "buy", 10, 1000, T0, cost=V1_ZERO)
    ledger.fill("lab:A:e1", "buy", 10, D("1001.2"), T0 + 1, cost=V2_FEE)
    ledger.fill("lab-exit:A:x", "sell", 20, 1100, T0 + 2)
    c = cell(ledger.result(), "A")
    # recorded cost 10*1000 + 10*1001.2 ; corrected cost 10*1001.2 + 10*1001.2
    assert D(c["realized_pnl_jpy"]) == D("20") * D("1100") - D("10") * D("1000") - D("10") * D("1001.2")
    assert D(c["corrected_realized_pnl_jpy"]) == D("20") * D("1100") - D("20") * D("1001.2")


def test_recorded_quote_fee_in_context_is_preferred(ledger):
    ctx = dict(V1_ZERO, fee_rate_quote="0.002")
    c = _round_trip(ledger, ctx)
    assert D(c["corrected_realized_pnl_jpy"]) == D("1000") - D("10") * D("1000") * D("0.002")


def test_correction_rate_rule_directly():
    assert buy_fee_correction_rate("ETH/JPY", None) == D("0.0012")
    assert buy_fee_correction_rate("BTC/JPY", None) == D("0.0010")
    assert buy_fee_correction_rate("ETH/JPY", json.dumps(V1_ZERO)) == D("0.0012")
    assert buy_fee_correction_rate("ETH/JPY", json.dumps(V2_FEE)) is None
    assert buy_fee_correction_rate("ETH/JPY", json.dumps(V2_ZERO)) is None
    assert buy_fee_correction_rate("ETH/JPY", json.dumps({"fee_rate": "0"})) == D("0.0012")
    assert buy_fee_correction_rate("ETH/JPY", json.dumps({"model": "weird", "fee_rate": "0"})) == D("0.0012")


def test_return_blocks_carry_corrected_mean(ledger):
    ledger.fill("lab:A:e1", "buy", 10, 1000, T0, cost=V1_ZERO)
    ledger.fill("lab-exit:A:x", "sell", 10, 1100, T0 + 1)
    block = cell(ledger.result(), "A")["return_blocks"][0]
    assert D(block["mean_return"]) == D("0.1")
    assert D(block["mean_return_corrected"]) == D("988") / D("10012")


# --- failure policy -----------------------------------------------------------

def test_missing_ledger_is_unavailable_not_empty_success(tmp_path):
    result = aggregate_cell_metrics(tmp_path / "nope.sqlite3")
    assert result["status"] == "unavailable" and result["reason_code"] == "ledger_missing"
    assert result["cells"] is None


def test_non_ledger_file_is_unavailable(tmp_path):
    bad = tmp_path / "paper.sqlite3"
    bad.write_bytes(b"this is not sqlite" * 50)
    result = aggregate_cell_metrics(bad)
    assert result["status"] == "unavailable" and result["cells"] is None


def test_database_without_fills_table_is_unavailable(tmp_path):
    path = tmp_path / "paper.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE other (x)")
    conn.commit()
    conn.close()
    assert aggregate_cell_metrics(path)["status"] == "unavailable"


def test_empty_valid_ledger_is_ok_with_no_cells(ledger):
    result = ledger.result()
    assert result["status"] == "ok" and result["cells"] == []


def test_ledger_is_opened_read_only(ledger):
    ledger.fill("lab:A:e1", "buy", 1, 1000, T0)
    before = ledger.path.read_bytes()
    ledger.result()
    assert ledger.path.read_bytes() == before


def test_legacy_ledger_without_cost_context_column(tmp_path):
    path = tmp_path / "paper.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE paper_fills (
        fill_id TEXT PRIMARY KEY, opportunity_id TEXT, strategy_id TEXT, symbol TEXT,
        side TEXT, quote TEXT, amount TEXT, price TEXT, quote_notional TEXT,
        reference_notional TEXT, reason_code TEXT, filled_at REAL)""")
    for i, (strategy, side, price) in enumerate(
        [("lab:A:e", "buy", 1000), ("lab-exit:A:x", "sell", 1100)]
    ):
        conn.execute("INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (f"f{i}", f"o{i}", strategy, "ETH/JPY", side, "JPY", "10", str(price),
                      "0", "0", "x", T0 + i))
    conn.commit()
    conn.close()
    c = cell(aggregate_cell_metrics(path), "A")
    assert D(c["corrected_realized_pnl_jpy"]) == D("988")


@pytest.mark.parametrize(
    "bad_row",
    [
        ("ETH/JPY", "buy", "lab:A:e", "abc", "1000", T0, None),       # amount not a number
        ("ETH/JPY", "buy", "lab:A:e", "1", "0", T0, None),            # price <= 0
        ("ETH/JPY", "buy", "lab:A:e", "-1", "1000", T0, None),        # amount <= 0
        ("ETH/JPY", "hold", "lab:A:e", "1", "1000", T0, None),        # bad side
        ("", "buy", "lab:A:e", "1", "1000", T0, None),                # empty symbol
        ("ETH/JPY", "buy", "", "1", "1000", T0, None),                # empty strategy
        ("ETH/JPY", "buy", "lab:A:e", "1", "1000", None, None),       # no timestamp
        ("ETH/JPY", "buy", "lab:A:e", "1", "NaN", T0, None),          # non-finite
        ("ETH/JPY", "buy", "lab:A:e", "1", "1000", T0, "{not json"),  # unreadable cost_context
        ("ETH/JPY", "buy", "lab:A:e", "1", "1000", T0, "[1]"),        # cost_context not an object
        ("ETH/JPY", "buy", "lab:A:e", "1", "1000", T0, '{"model":"paper-cost-v2"}'),  # no fee_rate
        ("ETH/JPY", "buy", "lab:A:e", "1", "1000", T0, '{"fee_rate":"x"}'),
    ],
)
def test_malformed_rows_return_cannot_aggregate(bad_row):
    good = ("ETH/JPY", "buy", "lab:A:e", "1", "1000", T0 - 1, None)
    sell = ("ETH/JPY", "sell", "lab-exit:A:x", "1", "1100", T0 + 1, None)
    result = aggregate_cell_metrics_from_rows([good, bad_row, sell])
    assert result["status"] == "invalid"
    assert result["cells"] is None
    assert result["total_realized_pnl_jpy"] is None


def test_out_of_range_sell_timestamp_returns_cannot_aggregate():
    good = ("ETH/JPY", "buy", "lab:A:e", "1", "1000", T0 - 1, None)
    huge = ("ETH/JPY", "sell", "lab-exit:A:x", "1", "1100", 1e30, None)
    result = aggregate_cell_metrics_from_rows([good, huge])
    assert result["status"] == "invalid" and result["cells"] is None


def test_malformed_row_in_db_returns_cannot_aggregate(ledger):
    ledger.fill("lab:A:e1", "buy", 1, 1000, T0)
    ledger.fill("lab-exit:A:x", "sell", 1, 1100, T0 + 1)
    ledger.conn.execute("UPDATE paper_fills SET amount = 'oops' WHERE side = 'sell'")
    ledger.conn.commit()
    result = ledger.result()
    assert result["status"] == "invalid" and result["reason_code"] == "ledger_invalid_row"
    assert result["cells"] is None
