"""Worker behaviour with dedicated per-symbol PAPER slots (Issue #3623 §2.9)."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.arbitrage import TopOfBook  # noqa: E402
from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.experiment_control import (  # noqa: E402
    CONTROL_FILENAME, assess_experiment, experiment_lock,
)
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import AllocationDecision, MarketInfo, Opportunity  # noqa: E402
from docich.trading.paper import PaperBroker  # noqa: E402
from docich.trading.strategies import select_diversified_opportunities  # noqa: E402
from docich.trading.strategy_lab import (  # noqa: E402
    EVALUATION_FILENAME, EXPERIMENT_FILENAME, PENDING_FILENAME, experiment_from_mapping,
    load_pending_experiment, load_strategy_experiment, save_pending_experiment,
    save_strategy_experiment,
)
from docich.trading.strategy_runtime import set_active_experiment  # noqa: E402
from docich.trading.symbol_slots import (  # noqa: E402
    SLOTS_DIRNAME, create_slot, read_slot_layout, slot_dir,
)
from docich.trading.worker import run_worker_cycle  # noqa: E402

D = Decimal
NOW = 1_800_000_000.0
RISING = [100] * 24 + [104]      # +400 bps over 3 bars: the common entry fires
FLAT = [100] * 25


@pytest.fixture(autouse=True)
def reset_active_experiment():
    set_active_experiment(None)
    yield
    set_active_experiment(None)


def _spec(experiment_id="active", *, entry_bps=100, hold_minutes=60):
    return experiment_from_mapping({
        "experiment_id": experiment_id,
        "name": experiment_id,
        "thesis": "銘柄別スロットが自分のルールで売買することを検証する。",
        "entry_rules": [{
            "rule_id": "entry", "combine": "all", "max_notional_fraction": "0.10",
            "conditions": [{"feature": "return_bps", "lookback": 3, "op": ">=", "threshold": entry_bps}],
        }],
        "exit_rules": [{
            "rule_id": "exit", "combine": "all",
            "conditions": [{"feature": "hold_minutes", "op": ">=", "threshold": hold_minutes}],
        }],
    })


def _environment(root):
    root.mkdir(parents=True, exist_ok=True)
    cfg = root / "docich.toml"
    cfg.write_text(
        '[paths]\nstate_dir = "run"\n'
        '[trading]\npaper_worker_enabled = true\ninterval_s = 60\npaper_capital_jpy = 10000\n',
        encoding="utf-8",
    )
    g = config.load_global(root, config_path=cfg)
    target = g.state_dir / "trading"
    target.mkdir(parents=True)
    (target / "status.json").write_text(json.dumps({
        "worker_state": "paper_worker_idle", "capital_reference": "10000",
        "deployed_reference": "0", "open_positions": {}, "recent_fills": [],
        "skipped_reason_codes": [], "signal_summary": {"candidate_count": 0},
    }), encoding="utf-8")
    PaperLedger(target / "paper.sqlite3").close()
    save_strategy_experiment(target, _spec(), activated_at=NOW - 4 * 3600)
    return g, target


def _slot(target, symbol, *, active=None, pending=None, activated_at=NOW - 3600):
    directory = create_slot(target, symbol, now=NOW - 7200)
    if active is not None:
        save_strategy_experiment(directory, active, activated_at=activated_at)
    if pending is not None:
        save_pending_experiment(directory, pending, proposed_at=NOW - 60)
    return directory


def _fill(target, oid, side, price, *, symbol, at, experiment_id="active"):
    price = D(str(price))
    decision = AllocationDecision(
        opportunity_id=oid,
        strategy_id=f"{'lab' if side == 'buy' else 'lab-exit'}:{experiment_id}:{'entry' if side == 'buy' else 'exit'}",
        symbol=symbol, side=side, quote="JPY", amount=D("1"), price=price,
        quote_notional=price, reference_notional=price,
        reason_code="paper_lab_entry" if side == "buy" else "paper_lab_exit",
    )
    ledger = PaperLedger(target / "paper.sqlite3")
    try:
        PaperBroker(ledger).fill(
            decision, timestamp=at,
            book=TopOfBook(symbol, price, price, float(at),
                           bid_amount=decision.amount, ask_amount=decision.amount),
        )
    finally:
        ledger.close()


def _round_trips(target, symbol, count, *, experiment_id, start):
    for index in range(count):
        stamp = start + index * 120
        _fill(target, f"{experiment_id}-{symbol}-b{index}", "buy", 100, symbol=symbol, at=stamp,
              experiment_id=experiment_id)
        _fill(target, f"{experiment_id}-{symbol}-s{index}", "sell", 101, symbol=symbol, at=stamp + 60,
              experiment_id=experiment_id)


class Gateway:
    def __init__(self, prices):
        self.prices = prices

    def discover_markets(self):
        return {
            symbol: MarketInfo(
                symbol=symbol, base=symbol.split("/")[0], quote="JPY", spot=True, active=True,
                amount_step=D("1"), min_amount=D("1"), min_cost=D("1"), market_order_enabled=True,
            )
            for symbol in self.prices
        }

    def fetch_market_frames(self, symbols, *, timeframe, limit, now):
        symbol = list(symbols)[0]
        values = self.prices[symbol][-limit:]
        count = len(values)
        return {symbol: MarketFrame(
            symbol=symbol, timeframe_seconds=300,
            timestamps=tuple(now - (count - index - 1) * 300 for index in range(count)),
            closes=tuple(D(str(value)) for value in values), volumes=(D("1"),) * count,
        )}

    def fetch_top_books(self, symbols, *, now, limit=20):
        books = {}
        for symbol in symbols:
            price = D(str(self.prices[symbol][-1]))
            books[symbol] = TopOfBook(
                symbol, price * D("0.999"), price * D("1.001"), now,
                bid_amount=D("100000000"), ask_amount=D("100000000"),
            )
        return books

    def fetch_depth_books(self, symbols, *, now, limit=20):
        books = {}
        for symbol in symbols:
            price = D(str(self.prices[symbol][-1]))
            books[symbol] = DepthBook(
                symbol,
                (DepthLevel(price * D("0.999"), D("100000000")),),
                (DepthLevel(price * D("1.001"), D("100000000")),),
                now,
            )
        return books


def _cycle(g, prices, *, now=NOW, index=1):
    return run_worker_cycle(
        g, gateway=Gateway(prices), cycle_index=index, now=now, observation_now_fn=lambda: now,
    )


def _new_fills(target, since=NOW):
    ledger = PaperLedger(target / "paper.sqlite3")
    try:
        fills = [fill for fill in ledger.recent_fills(limit=100) if fill.filled_at >= since]
        return sorted((fill.side, fill.symbol, fill.strategy_id) for fill in fills), ledger.positions()
    finally:
        ledger.close()


def _control(directory):
    return json.loads((directory / CONTROL_FILENAME).read_text(encoding="utf-8"))


def _assess(target, slot, *, now=NOW):
    with experiment_lock(target):
        return assess_experiment(
            target, capital_jpy="10000", now=now, slot=slot, layout=read_slot_layout(target))


# --- no dedicated slot: the cycle is the old cycle --------------------------

def test_empty_slots_directory_trades_exactly_like_no_slots(tmp_path):
    plain_g, plain = _environment(tmp_path / "plain")
    slots_g, slotted = _environment(tmp_path / "slotted")
    (slotted / SLOTS_DIRNAME).mkdir()
    prices = {"BTC/JPY": FLAT, "NEW/JPY": RISING}
    first = _cycle(plain_g, prices)
    second = _cycle(slots_g, prices)
    assert first.new_fill_count == second.new_fill_count == 1
    assert _new_fills(plain) == _new_fills(slotted)
    assert _new_fills(plain)[0] == [("buy", "NEW/JPY", "lab:active:entry")]
    assert _control(plain) == _control(slotted)
    plain_eval = json.loads((plain / EVALUATION_FILENAME).read_text(encoding="utf-8"))
    assert "symbol_scope" not in plain_eval
    assert plain_eval == json.loads((slotted / EVALUATION_FILENAME).read_text(encoding="utf-8"))


# --- a dedicated slot trades its symbol with its own rules ------------------

def test_slot_entry_rules_replace_the_common_rules_for_that_symbol(tmp_path):
    g, target = _environment(tmp_path)
    # The common rule would buy NEW (+400 bps); the slot demands +1000 bps.
    _slot(target, "NEW/JPY", active=_spec("new-v1", entry_bps=1000))
    result = _cycle(g, {"BTC/JPY": FLAT, "NEW/JPY": RISING, "OLD/JPY": RISING})
    fills, positions = _new_fills(target)
    assert fills == [("buy", "OLD/JPY", "lab:active:entry")]
    assert "NEW/JPY" not in positions and result.new_fill_count == 1


def test_slot_can_buy_where_the_common_rules_would_not(tmp_path):
    g, target = _environment(tmp_path)
    slot = _slot(target, "NEW/JPY", active=_spec("new-v1", entry_bps=10))
    mild = [100] * 24 + [100.5]  # +50 bps: below the common 100 bps rule
    _cycle(g, {"BTC/JPY": FLAT, "NEW/JPY": mild, "OLD/JPY": mild})
    fills, positions = _new_fills(target)
    assert fills == [("buy", "NEW/JPY", "lab:new-v1:entry")]
    assert positions["NEW/JPY"] > 0 and "OLD/JPY" not in positions
    assert _control(slot)["experiment_id"] == "new-v1"
    assert _control(slot)["entries_allowed"] is True
    assert _control(target)["experiment_id"] == "active"


def test_slot_exit_rules_own_the_symbol_holdings(tmp_path):
    g, target = _environment(tmp_path)
    for symbol in ("BTC/JPY", "ETH/JPY"):
        _fill(target, f"held-{symbol}", "buy", 100, symbol=symbol, at=NOW - 4000)
    # Held 66 minutes: the common 60-minute exit fires, the slot's 600 does not.
    _slot(target, "BTC/JPY", active=_spec("btc-v1", hold_minutes=600))
    _cycle(g, {"BTC/JPY": FLAT, "ETH/JPY": FLAT})
    fills, positions = _new_fills(target)
    assert fills == [("sell", "ETH/JPY", "lab-exit:active:exit")]
    assert positions == {"BTC/JPY": D("1")}

    _cycle(g, {"BTC/JPY": FLAT, "ETH/JPY": FLAT}, now=NOW + 600 * 60, index=2)
    fills, positions = _new_fills(target, since=NOW + 1)
    assert fills == [("sell", "BTC/JPY", "lab-exit:btc-v1:exit")]
    assert positions == {}


def test_new_slot_drains_under_common_rules_then_starts_its_first_experiment(tmp_path):
    g, target = _environment(tmp_path)
    _fill(target, "held", "buy", 100, symbol="NEW/JPY", at=NOW - 300)
    slot = _slot(target, "NEW/JPY", pending=_spec("new-v1", entry_bps=10, hold_minutes=600))
    prices = {"BTC/JPY": FLAT, "NEW/JPY": RISING}

    first = _cycle(g, prices)
    assert first.new_fill_count == 0  # held 5 minutes, and the slot allows no buy yet
    assert _control(slot)["status"] == "draining"
    assert _control(slot)["reason_code"] == "initial-experiment"
    assert _control(slot)["entries_allowed"] is False
    assert load_strategy_experiment(slot) is None

    second = _cycle(g, prices, now=NOW + 3600, index=2)
    fills, positions = _new_fills(target, since=NOW + 1)
    # The common experiment that bought it sells it; the slot starts flat.
    assert fills == [("sell", "NEW/JPY", "lab-exit:active:exit")]
    assert positions == {} and second.new_fill_count == 1
    active = load_strategy_experiment(slot)
    assert active.experiment_id == "new-v1" and active.activated_at == NOW + 3600
    assert load_pending_experiment(slot) is None
    assert _control(slot)["entries_allowed"] is True

    _cycle(g, prices, now=NOW + 3660, index=3)
    fills, _ = _new_fills(target, since=NOW + 3601)
    assert fills == [("buy", "NEW/JPY", "lab:new-v1:entry")]


def test_slot_rotation_drains_only_its_own_symbol(tmp_path):
    g, target = _environment(tmp_path)
    slot = _slot(target, "NEW/JPY", active=_spec("new-v1", entry_bps=10, hold_minutes=600),
                 pending=_spec("new-v2", entry_bps=10), activated_at=NOW - 8 * 3600)
    _round_trips(target, "NEW/JPY", 10, experiment_id="new-v1", start=NOW - 7 * 3600)
    _fill(target, "open", "buy", 100, symbol="NEW/JPY", at=NOW - 300, experiment_id="new-v1")
    prices = {"BTC/JPY": FLAT, "NEW/JPY": RISING, "OLD/JPY": RISING}

    _cycle(g, prices)
    fills, positions = _new_fills(target)
    # NEW stops buying and keeps its old exit rule; OLD keeps trading.
    assert fills == [("buy", "OLD/JPY", "lab:active:entry")]
    assert positions["NEW/JPY"] == D("1")
    assert _control(slot)["status"] == "draining"
    assert _control(slot)["reason_code"] == "sample-complete"
    assert _control(target)["status"] == "active" and _control(target)["entries_allowed"] is True
    assert load_strategy_experiment(slot).experiment_id == "new-v1"


def test_dedicated_slot_gate_is_ten_exits_or_seven_days(tmp_path):
    _g, target = _environment(tmp_path)
    _slot(target, "NEW/JPY", active=_spec("new-v1"), pending=_spec("new-v2"),
          activated_at=NOW - 6 * 86400)
    _round_trips(target, "NEW/JPY", 9, experiment_id="new-v1", start=NOW - 7 * 3600)
    waiting = _assess(target, "NEW/JPY")
    assert waiting.status == "active" and waiting.entries_allowed is True
    assert waiting.evaluation["closed_sells"] == 9
    aged = _assess(target, "NEW/JPY", now=NOW + 86400)
    assert aged.activated is True and load_strategy_experiment(
        slot_dir(target, "NEW/JPY")).experiment_id == "new-v2"


def test_common_slot_ignores_dedicated_symbols_and_keeps_its_own_gate(tmp_path):
    _g, target = _environment(tmp_path)
    _slot(target, "NEW/JPY", active=_spec("new-v1"))
    _round_trips(target, "NEW/JPY", 25, experiment_id="active", start=NOW - 3 * 3600)
    _round_trips(target, "OLD/JPY", 3, experiment_id="active", start=NOW - 3 * 3600)
    save_pending_experiment(target, _spec("common-v2"), proposed_at=NOW - 60)
    with experiment_lock(target):
        step = assess_experiment(
            target, capital_jpy="10000", now=NOW, layout=read_slot_layout(target))
    assert step.evaluation["closed_sells"] == 3
    assert step.evaluation["symbol_scope"] == {"mode": "exclude", "symbols": ["NEW/JPY"]}
    assert step.status == "active" and step.entries_allowed is True
    assert load_strategy_experiment(target).experiment_id == "active"


# --- failures stay inside the slot ------------------------------------------

def test_unreadable_slot_experiment_freezes_only_that_symbol(tmp_path):
    g, target = _environment(tmp_path)
    for symbol in ("NEW/JPY", "ETH/JPY"):
        _fill(target, f"held-{symbol}", "buy", 100, symbol=symbol, at=NOW - 4000)
    slot = _slot(target, "NEW/JPY", active=_spec("new-v1"))
    (slot / EXPERIMENT_FILENAME).write_text("{broken", encoding="utf-8")
    _cycle(g, {"BTC/JPY": FLAT, "NEW/JPY": RISING, "ETH/JPY": FLAT, "OLD/JPY": RISING})
    fills, positions = _new_fills(target)
    # Unknown rules: NEW is neither bought nor sold under someone else's rules.
    assert fills == [("buy", "OLD/JPY", "lab:active:entry"), ("sell", "ETH/JPY", "lab-exit:active:exit")]
    assert positions["NEW/JPY"] == D("1")
    assert _control(slot)["status"] == "blocked"
    assert _control(slot)["reason_code"] == "invalid-active"


def test_empty_dedicated_slot_blocks_buys_but_common_rules_still_exit(tmp_path):
    g, target = _environment(tmp_path)
    _fill(target, "held", "buy", 100, symbol="NEW/JPY", at=NOW - 4000)
    _slot(target, "NEW/JPY")
    _cycle(g, {"BTC/JPY": FLAT, "NEW/JPY": RISING})
    fills, positions = _new_fills(target)
    assert fills == [("sell", "NEW/JPY", "lab-exit:active:exit")] and positions == {}
    _cycle(g, {"BTC/JPY": FLAT, "NEW/JPY": RISING}, now=NOW + 60, index=2)
    assert _new_fills(target, since=NOW + 1)[0] == []


def test_invalid_slot_layout_stops_all_buys_and_keeps_exits(tmp_path):
    g, target = _environment(tmp_path)
    _fill(target, "held", "buy", 100, symbol="ETH/JPY", at=NOW - 4000)
    _slot(target, "NEW/JPY", active=_spec("new-v1", entry_bps=10))
    (target / SLOTS_DIRNAME / "OLD_JPY").mkdir()  # no slot.json: owner unknown
    _cycle(g, {"BTC/JPY": FLAT, "NEW/JPY": RISING, "ETH/JPY": FLAT, "OLD/JPY": RISING})
    fills, positions = _new_fills(target)
    assert fills == [("sell", "ETH/JPY", "lab-exit:active:exit")]
    assert positions == {}
    assert not (target / PENDING_FILENAME).exists()


def test_dedicated_slot_without_layout_is_rejected(tmp_path):
    _g, target = _environment(tmp_path)
    _slot(target, "NEW/JPY", active=_spec("new-v1"))
    with experiment_lock(target), pytest.raises(ValueError):
        assess_experiment(target, capital_jpy="10000", now=NOW, slot="NEW/JPY")


# --- correlation cap per slot -----------------------------------------------

def test_symbol_thresholds_override_the_cap_for_that_symbol_only():
    def frame(symbol, closes):
        return MarketFrame(
            symbol=symbol, timeframe_seconds=300,
            timestamps=tuple(NOW - (len(closes) - i - 1) * 300 for i in range(len(closes))),
            closes=tuple(D(str(value)) for value in closes), volumes=(D("1"),) * len(closes),
        )

    def opportunity(symbol, score):
        return Opportunity(
            opportunity_id=f"o-{symbol}", strategy_id="lab:x:entry", symbol=symbol, side="buy",
            score=D(str(score)), expected_edge_bps=D("1"), max_notional_fraction=D("0.1"),
            expires_at=NOW + 600, reason_code="paper_lab_entry",
        )

    series = [100, 101, 103, 102, 105, 107, 106, 109]
    similar = [100, 102, 103, 104, 104, 108, 105, 110]  # close to, not equal to, series
    frames = {"A/JPY": frame("A/JPY", series), "B/JPY": frame("B/JPY", similar)}
    entries = (opportunity("A/JPY", "0.9"), opportunity("B/JPY", "0.5"))
    capped = select_diversified_opportunities(entries, frames, max_pair_correlation=D("0.1"))
    assert [item.symbol for item in capped.selected] == ["A/JPY"]
    assert [item.reason_code for item in capped.rejected] == ["correlated_exposure"]
    loose = select_diversified_opportunities(
        entries, frames, max_pair_correlation=D("0.1"), symbol_thresholds={"B/JPY": D("1")})
    assert [item.symbol for item in loose.selected] == ["A/JPY", "B/JPY"]
    # The override belongs to the candidate's symbol, not to the one already selected.
    other = select_diversified_opportunities(
        entries, frames, max_pair_correlation=D("0.1"), symbol_thresholds={"A/JPY": D("1")})
    assert [item.symbol for item in other.selected] == ["A/JPY"]
    with pytest.raises(Exception):
        select_diversified_opportunities(entries, frames, symbol_thresholds={"B/JPY": D("2")})
