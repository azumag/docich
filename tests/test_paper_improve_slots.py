"""Per-symbol PAPER improvement (Issue #3623 §2.9, stage 3)."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, paper_improve_retry  # noqa: E402
from docich.trading import paper_improve_slots as slots  # noqa: E402
from docich.trading.ai_text import AiTextError  # noqa: E402
from docich.trading.arbitrage import TopOfBook  # noqa: E402
from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.experiment_control import CONTROL_FILENAME  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import AllocationDecision, MarketInfo  # noqa: E402
from docich.trading.paper import PaperBroker  # noqa: E402
from docich.trading.paper_improve import run_paper_improve  # noqa: E402
from docich.trading.strategy_lab import (  # noqa: E402
    EVALUATION_FILENAME, experiment_from_mapping, experiment_to_payload, load_pending_experiment,
    load_strategy_experiment, save_pending_experiment, save_strategy_experiment,
)
from docich.trading.strategy_runtime import set_active_experiment  # noqa: E402
from docich.trading.symbol_slots import SLOTS_DIRNAME, create_slot, read_slot_layout, slot_dir  # noqa: E402
from docich.trading.worker import run_worker_cycle  # noqa: E402

D = Decimal
NOW = 1_800_000_000.0
DAY = 86400.0
UNIVERSE = ["AAA/JPY", "BBB/JPY", "CCC/JPY"]


@pytest.fixture(autouse=True)
def reset_active_experiment():
    set_active_experiment(None)
    yield
    set_active_experiment(None)


def _rules(entry_bps=100, hold_minutes=60, *, time_stop=True):
    exits = [{"rule_id": "exit-loss", "combine": "all",
              "conditions": [{"feature": "pnl_bps", "op": "<=", "threshold": -80}]}]
    if time_stop:
        exits.append({"rule_id": "exit-time", "combine": "all",
                      "conditions": [{"feature": "hold_minutes", "op": ">=", "threshold": hold_minutes}]})
    return {
        "name": "銘柄向け", "thesis": "銘柄ごとの値動きに合わせた条件を検証する。",
        "entry_rules": [{
            "rule_id": "entry", "combine": "all", "max_notional_fraction": "0.10",
            "conditions": [{"feature": "return_bps", "lookback": 3, "op": ">=", "threshold": entry_bps}],
        }],
        "exit_rules": exits,
    }


def _spec(experiment_id="active", **kwargs):
    return experiment_from_mapping({"experiment_id": experiment_id, **_rules(**kwargs)})


def _environment(root, *, universe=UNIVERSE, history=None):
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
        "eligible_symbols": list(universe),
    }), encoding="utf-8")
    closes = history if history is not None else {symbol: [100] * 40 for symbol in universe}
    (target / "market_cache.json").write_text(json.dumps({"schema_version": 1, "symbols": {
        symbol: {"history": [{"timestamp": NOW - (len(values) - i) * 300, "close": str(value)}
                             for i, value in enumerate(values)]}
        for symbol, values in closes.items()
    }}), encoding="utf-8")
    PaperLedger(target / "paper.sqlite3").close()
    save_strategy_experiment(target, _spec(), activated_at=NOW - 4 * 3600)
    return g, target


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


def _round_trips(target, symbol, count, *, start, experiment_id="active"):
    for index in range(count):
        stamp = start + index * 120
        _fill(target, f"{symbol}-b{index}-{start}", "buy", 100, symbol=symbol, at=stamp, experiment_id=experiment_id)
        _fill(target, f"{symbol}-s{index}-{start}", "sell", 101, symbol=symbol, at=stamp + 60,
              experiment_id=experiment_id)


def _answer(symbols, **kwargs):
    return json.dumps({"symbol_experiments": {symbol: _rules(**kwargs) for symbol in symbols}})


def _llm(calls, **kwargs):
    def call(prompt):
        symbols = [symbol for symbol in UNIVERSE + ["DDD/JPY", "EEE/JPY", "FFF/JPY", "GGG/JPY"]
                   if f'"{symbol}"' in prompt.split("対象の銘柄は")[0]]
        calls.append(symbols)
        return _answer(symbols, **kwargs)
    return call


def _run(g, target, *, llm, now=NOW, **kwargs):
    return slots.run_slot_improve(g, trading_dir=target, agents="fixture", llm=llm, now=now, **kwargs)


def _status(target):
    return json.loads((target / slots.STATUS_FILENAME).read_text(encoding="utf-8"))


# --- which symbols are due ---------------------------------------------------

def test_every_symbol_is_due_until_it_received_a_candidate(tmp_path):
    _g, target = _environment(tmp_path)
    _round_trips(target, "BBB/JPY", 3, start=NOW - 3600)
    due = slots.select_slot_targets(target, now=NOW)
    assert [(item.symbol, item.reason, item.exits_since) for item in due] == [
        ("BBB/JPY", "never-improved", 3), ("AAA/JPY", "never-improved", 0), ("CCC/JPY", "never-improved", 0),
    ]
    assert all(not item.dedicated and item.baseline_key == "legacy" for item in due)


def test_improved_symbol_is_due_again_after_ten_exits_or_seven_days(tmp_path):
    _g, target = _environment(tmp_path)
    slots._save_improve_state(target, {
        symbol: {"last_candidate_at": NOW - DAY, "last_experiment_id": "x"} for symbol in UNIVERSE})
    assert slots.select_slot_targets(target, now=NOW) == []
    _round_trips(target, "AAA/JPY", 9, start=NOW - 3600)
    _round_trips(target, "BBB/JPY", 10, start=NOW - 3600)
    _round_trips(target, "CCC/JPY", 30, start=NOW - 3 * DAY)  # before its last candidate
    due = slots.select_slot_targets(target, now=NOW)
    assert [(item.symbol, item.reason) for item in due] == [("BBB/JPY", "sample-complete")]
    # A symbol that hardly trades still becomes due by age alone.
    aged = slots.select_slot_targets(target, now=NOW + 6 * DAY)
    assert {item.symbol: item.reason for item in aged} == {
        "AAA/JPY": "max-age", "BBB/JPY": "sample-complete", "CCC/JPY": "max-age"}


def test_slot_with_a_queued_candidate_is_not_due(tmp_path):
    _g, target = _environment(tmp_path)
    queued = create_slot(target, "AAA/JPY", now=NOW - 100)
    save_pending_experiment(queued, _spec("aaa-old"), proposed_at=NOW - 50)
    running = create_slot(target, "BBB/JPY", now=NOW - 100)
    save_strategy_experiment(running, _spec("bbb-v1"), activated_at=NOW - 50)
    due = {item.symbol: item for item in slots.select_slot_targets(target, now=NOW)}
    assert sorted(due) == ["BBB/JPY", "CCC/JPY"]
    assert due["BBB/JPY"].dedicated and due["BBB/JPY"].active.experiment_id == "bbb-v1"
    assert due["BBB/JPY"].baseline_key != "legacy"


def test_invalid_layout_has_no_targets(tmp_path):
    _g, target = _environment(tmp_path)
    (target / SLOTS_DIRNAME / "AAA_JPY").mkdir(parents=True)
    with pytest.raises(ValueError):
        slots.select_slot_targets(target, now=NOW)


# --- evidence ----------------------------------------------------------------

def test_entry_signal_bars_replays_the_rules_over_cached_history():
    spec = _spec(entry_bps=100)
    flat = [D("100")] * 40
    assert slots.entry_signal_bars(flat, spec) == {"bars": 16, "entry_signal_bars": 0}
    jumpy = [D("100")] * 30 + [D("103")] + [D("103")] * 9   # +300 bps visible for 3 bars
    assert slots.entry_signal_bars(jumpy, spec) == {"bars": 16, "entry_signal_bars": 3}
    assert slots.entry_signal_bars(flat[:10], spec) is None
    assert slots.entry_signal_bars(flat, None) is None


def test_symbol_facts_carry_frequency_and_market_shape(tmp_path):
    _g, target = _environment(tmp_path, history={
        "AAA/JPY": [100] * 30 + [103] * 10, "BBB/JPY": [100] * 40, "CCC/JPY": [100] * 5})
    _round_trips(target, "AAA/JPY", 7, start=NOW - 3600)
    targets = slots.select_slot_targets(target, now=NOW)
    from docich.trading.strategy_metrics import read_ledger_rows
    facts = slots.build_symbol_facts(target, targets, now=NOW, rows=read_ledger_rows(target, now=NOW))
    aaa = facts["AAA/JPY"]
    assert aaa["exits_14d"] == 7 and aaa["exits_per_day_14d"] == 0.5 and aaa["wins_14d"] == 7
    assert aaa["current_rules"] == "common" and "active_experiment" not in aaa
    assert aaa["entry_signal"] == {"bars": 16, "entry_signal_bars": 3}
    assert aaa["market"]["bars"] == 40 and aaa["market"]["largest_bar_rise_bps"] == 300.0
    assert len(aaa["recent_round_trips"]) == 6
    assert aaa["recent_round_trips"][-1]["experiment_id"] == "active"
    assert facts["BBB/JPY"]["exits_14d"] == 0 and facts["BBB/JPY"]["recent_round_trips"] == []
    assert "entry_signal" not in facts["CCC/JPY"]  # too little history to replay
    prompt = slots.build_slot_prompt(facts, common_experiment=load_strategy_experiment(target))
    assert "決済が少なすぎる戦略" in prompt and '"AAA/JPY"' in prompt and "symbol_experiments" in prompt


def test_common_trade_activity_counts_only_common_symbols(tmp_path):
    _g, target = _environment(tmp_path, history={
        "AAA/JPY": [100] * 30 + [103] * 10, "BBB/JPY": [100] * 30 + [103] * 10, "CCC/JPY": [100] * 40})
    _round_trips(target, "AAA/JPY", 4, start=NOW - 3600)
    _round_trips(target, "BBB/JPY", 6, start=NOW - 3600)
    active = load_strategy_experiment(target)
    whole = slots.common_trade_activity(target, active, read_slot_layout(target), now=NOW)
    assert whole["exits"] == 10 and whole["entry_signal_bars"] == 6 and whole["symbols_replayed"] == 3
    create_slot(target, "BBB/JPY", now=NOW)
    part = slots.common_trade_activity(target, active, read_slot_layout(target), now=NOW)
    assert part["exits"] == 4 and part["entry_signal_bars"] == 3 and part["symbols_replayed"] == 2
    assert part["exits_per_day"] == round(4 / (4 * 3600 / DAY), 2)


# --- candidates --------------------------------------------------------------

def test_candidates_are_validated_per_symbol_and_get_unique_ids():
    text = json.dumps({"symbol_experiments": {
        "AAA/JPY": _rules(),
        "BBB/JPY": {**_rules(), "entry_rules": []},
        "DDD/JPY": _rules(),   # not asked for
    }})
    taken = {"aaa_270115"}
    accepted, rejected = slots.parse_symbol_candidates(
        text, ["AAA/JPY", "BBB/JPY", "CCC/JPY"], now=NOW, taken=taken)
    assert rejected == {"BBB/JPY": "candidate-invalid", "CCC/JPY": "missing"}
    assert list(accepted) == ["AAA/JPY"]
    assert accepted["AAA/JPY"].experiment_id == "aaa_270115b"  # date stem was taken
    assert "aaa_270115b" in taken


def test_ai_supplied_experiment_id_is_ignored():
    text = json.dumps({"symbol_experiments": {"AAA/JPY": {**_rules(), "experiment_id": "active"}}})
    accepted, _ = slots.parse_symbol_candidates(text, ["AAA/JPY"], now=NOW, taken=set())
    assert accepted["AAA/JPY"].experiment_id == "aaa_270115"


def test_missing_time_stop_is_appended_or_rejected_when_full():
    text = json.dumps({"symbol_experiments": {"AAA/JPY": _rules(time_stop=False)}})
    accepted, rejected = slots.parse_symbol_candidates(text, ["AAA/JPY"], now=NOW, taken=set())
    assert rejected == {}
    last = experiment_to_payload(accepted["AAA/JPY"])["exit_rules"][-1]
    assert last["rule_id"] == "exit-timecap"
    assert last["conditions"][0]["feature"] == "hold_minutes"
    assert D(str(last["conditions"][0]["threshold"])) == D(slots.DEFAULT_TIME_STOP_MINUTES)
    full = _rules(time_stop=False)
    full["exit_rules"] = [{**full["exit_rules"][0], "rule_id": f"exit-{i}"} for i in range(8)]
    accepted, rejected = slots.parse_symbol_candidates(
        json.dumps({"symbol_experiments": {"AAA/JPY": full}}), ["AAA/JPY"], now=NOW, taken=set())
    assert accepted == {} and rejected == {"AAA/JPY": "no-time-stop"}


@pytest.mark.parametrize("text", ["", "not json", "{}", '{"symbol_experiments": []}'])
def test_unusable_answer_rejects_every_symbol(text):
    accepted, rejected = slots.parse_symbol_candidates(text, ["AAA/JPY", "BBB/JPY"], now=NOW, taken=set())
    assert accepted == {} and rejected == {"AAA/JPY": "missing", "BBB/JPY": "missing"}


def test_chunks_hold_at_most_four_symbols():
    targets = [slots.SlotTarget(f"S{i}/JPY", False, None, "legacy", "never-improved", 0) for i in range(12)]
    facts = {item.symbol: {"x": 1} for item in targets}
    assert [len(chunk) for chunk in slots.chunk_targets(targets, facts)] == [4, 4, 4]
    big = {item.symbol: {"x": "y" * 13_000} for item in targets[:3]}
    assert [len(chunk) for chunk in slots.chunk_targets(targets[:3], big)] == [1, 1, 1]


# --- the run -----------------------------------------------------------------

def test_run_creates_slots_and_queues_or_starts_each_candidate(tmp_path):
    g, target = _environment(tmp_path)
    _fill(target, "held", "buy", 100, symbol="BBB/JPY", at=NOW - 300)
    calls = []
    summary = _run(g, target, llm=_llm(calls))
    assert calls == [["BBB/JPY", "AAA/JPY", "CCC/JPY"]] or sorted(calls[0]) == UNIVERSE
    assert summary["status"] == "improved" and summary["calls"] == 1
    assert summary["results"] == {"AAA/JPY": "activated", "BBB/JPY": "queued", "CCC/JPY": "activated"}
    assert read_slot_layout(target).dedicated == tuple(UNIVERSE)
    # Flat symbols start at once; a held symbol waits for the common rules to sell it.
    assert load_strategy_experiment(slot_dir(target, "AAA/JPY")).experiment_id == "aaa_270115"
    held = slot_dir(target, "BBB/JPY")
    assert load_strategy_experiment(held) is None
    assert load_pending_experiment(held).experiment_id == "bbb_270115"
    control = json.loads((held / CONTROL_FILENAME).read_text(encoding="utf-8"))
    assert control["status"] == "draining" and control["entries_allowed"] is False
    assert load_strategy_experiment(target).experiment_id == "active"  # common untouched
    state = slots.load_improve_state(target)
    assert state["BBB/JPY"] == {"last_candidate_at": NOW, "last_experiment_id": "bbb_270115"}
    status = _status(target)
    assert status["status"] == "improved" and status["queued"] == 1 and status["activated"] == 2
    assert status["completed_at"] == NOW


def test_second_run_the_same_day_calls_no_ai(tmp_path):
    g, target = _environment(tmp_path)
    calls = []
    _run(g, target, llm=_llm(calls))
    again = _run(g, target, llm=_llm(calls), now=NOW + 3600)
    assert again == {"status": "skipped", "reason_code": "daily-limit"} and len(calls) == 1
    next_day = _run(g, target, llm=_llm(calls), now=NOW + DAY)
    assert next_day["status"] == "skipped" and next_day["reason_code"] == "no-targets"
    assert len(calls) == 1


def test_emergency_stop_file_blocks_the_slot_phase(tmp_path):
    g, target = _environment(tmp_path)
    (target / slots.DISABLED_FILENAME).write_text("", encoding="utf-8")
    calls = []
    assert _run(g, target, llm=_llm(calls)) == {"status": "skipped", "reason_code": "disabled"}
    assert calls == [] and not (target / SLOTS_DIRNAME).exists()


def test_one_bad_candidate_does_not_block_the_others(tmp_path):
    g, target = _environment(tmp_path)

    def llm(_prompt):
        return json.dumps({"symbol_experiments": {
            "AAA/JPY": _rules(), "BBB/JPY": {"name": "x"}, "CCC/JPY": _rules(entry_bps=50)}})

    summary = _run(g, target, llm=llm)
    assert summary["results"] == {
        "AAA/JPY": "activated", "BBB/JPY": "candidate-invalid", "CCC/JPY": "activated"}
    assert summary["rejected"] == 1 and summary["status"] == "improved"
    assert read_slot_layout(target).dedicated == ("AAA/JPY", "CCC/JPY")
    assert "BBB/JPY" not in slots.load_improve_state(target)  # still due tomorrow
    assert [item.symbol for item in slots.select_slot_targets(target, now=NOW + DAY)] == ["BBB/JPY"]


def test_many_symbols_are_split_into_calls_and_ai_failures_defer_the_rest(tmp_path, monkeypatch):
    universe = [f"{name}/JPY" for name in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")]
    g, target = _environment(tmp_path, universe=universe)
    calls = []
    assert _run(g, target, llm=_llm(calls))["calls"] == 2
    assert [len(chunk) for chunk in calls] == [4, 3]

    g2, target2 = _environment(tmp_path / "failing", universe=universe)
    monkeypatch.setattr(slots, "CHUNK_MAX_SYMBOLS", 2)
    attempts = []

    def broken(_prompt):
        attempts.append(1)
        raise AiTextError("timeout", "x")

    summary = _run(g2, target2, llm=broken)
    assert len(attempts) == 2  # stops after two consecutive failures
    assert summary["status"] == "failed" and summary["deferred"] == 3 and summary["rejected"] == 4
    assert not (target2 / SLOTS_DIRNAME).exists()


def test_time_budget_defers_remaining_symbols(tmp_path, monkeypatch):
    universe = [f"{name}/JPY" for name in ("AAA", "BBB", "CCC", "DDD")]
    g, target = _environment(tmp_path, universe=universe)
    monkeypatch.setattr(slots, "CHUNK_MAX_SYMBOLS", 2)
    ticks = iter([0.0, 0.0, slots.RUN_BUDGET_S + 1.0, slots.RUN_BUDGET_S + 2.0])
    calls = []
    summary = _run(g, target, llm=_llm(calls), monotonic=lambda: next(ticks))
    assert len(calls) == 1 and summary["deferred"] == 2 and summary["activated"] == 2
    assert summary["status"] == "improved"


def test_dry_run_reports_prompts_and_writes_nothing(tmp_path):
    g, target = _environment(tmp_path)
    def state_files():
        return sorted(path.name for path in target.iterdir() if path.name != ".paper-experiment.lock")

    before = state_files()
    summary = slots.run_slot_improve(g, trading_dir=target, agents="fixture", dry_run=True, now=NOW,
                                     llm=lambda _p: pytest.fail("dry-run must not call the AI"))
    assert summary["status"] == "dry-run" and summary["targets"] == 3 and summary["chunks"] == 1
    assert summary["due"] == {symbol: "never-improved" for symbol in UNIVERSE}
    assert state_files() == before  # no slots, no status, no state


def test_invalid_layout_fails_the_phase_without_an_ai_call(tmp_path):
    g, target = _environment(tmp_path)
    (target / SLOTS_DIRNAME / "AAA_JPY").mkdir(parents=True)
    summary = _run(g, target, llm=lambda _p: pytest.fail("no AI call on an invalid layout"))
    assert summary["status"] == "failed" and summary["reason_code"] == "slot-layout-invalid"


def test_no_agents_is_a_skip_that_does_not_use_the_daily_attempt(tmp_path):
    g, target = _environment(tmp_path)
    summary = slots.run_slot_improve(g, trading_dir=target, agents="", now=NOW)
    assert summary["status"] == "skipped" and summary["reason_code"] == "no-agents"
    calls = []
    assert _run(g, target, llm=_llm(calls), now=NOW + 60)["status"] == "improved"


# --- end to end with the worker and the common improvement ---------------------

class Gateway:
    def __init__(self, prices):
        self.prices = prices

    def discover_markets(self):
        return {symbol: MarketInfo(
            symbol=symbol, base=symbol.split("/")[0], quote="JPY", spot=True, active=True,
            amount_step=D("1"), min_amount=D("1"), min_cost=D("1"), market_order_enabled=True,
        ) for symbol in self.prices}

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


def test_worker_trades_the_symbol_with_the_candidate_from_the_slot_phase(tmp_path):
    g, target = _environment(tmp_path)
    _run(g, target, llm=_llm([], entry_bps=10))
    mild = [100] * 24 + [100.5]   # +50 bps: only the per-symbol rule (10 bps) buys
    run_worker_cycle(g, gateway=Gateway({symbol: mild for symbol in UNIVERSE}),
                     cycle_index=1, now=NOW + 60, observation_now_fn=lambda: NOW + 60)
    ledger = PaperLedger(target / "paper.sqlite3")
    try:
        bought = sorted((fill.symbol, fill.strategy_id) for fill in ledger.recent_fills(limit=20))
    finally:
        ledger.close()
    assert bought and all(strategy.split(":")[1].endswith("_270115") for _symbol, strategy in bought)
    assert {symbol for symbol, _ in bought} <= set(UNIVERSE)


def test_common_improvement_evaluates_only_symbols_without_a_slot(tmp_path):
    g, target = _environment(tmp_path)
    _round_trips(target, "AAA/JPY", 4, start=NOW - 3600)
    _round_trips(target, "BBB/JPY", 6, start=NOW - 3600)
    directory = create_slot(target, "BBB/JPY", now=NOW - 10)
    save_strategy_experiment(directory, _spec("bbb-v1"), activated_at=NOW - 5)
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        return json.dumps({"strategy_experiment": experiment_to_payload(_spec("common-v2", entry_bps=50))})

    result = run_paper_improve(g, trading_dir=target, agents="fixture", llm=llm, now=NOW)
    assert result["status"] == "improved"
    evaluation = json.loads((target / EVALUATION_FILENAME).read_text(encoding="utf-8"))
    assert evaluation["closed_sells"] == 4
    assert evaluation["symbol_scope"] == {"mode": "exclude", "symbols": ["BBB/JPY"]}
    assert '"trade_activity"' in prompts[0] and "exits_per_day" in prompts[0]
    assert load_pending_experiment(target).experiment_id == "common-v2"


def test_scheduled_run_skips_the_common_call_once_every_symbol_has_a_slot(tmp_path, monkeypatch):
    g, target = _environment(tmp_path)
    for symbol in UNIVERSE:
        directory = create_slot(target, symbol, now=NOW - 10)
        save_pending_experiment(directory, _spec(symbol.split("/")[0].lower()), proposed_at=NOW - 5)
    assert slots.common_slot_is_empty(target) is True
    monkeypatch.setattr(paper_improve_retry, "run_improve_with_validation_retry",
                        lambda *_a, **_k: pytest.fail("the common improvement must not run"))
    summary = paper_improve_retry.run_scheduled_improve(g, target, "", False, now=NOW)
    assert summary["status"] == "skipped" and summary["reason"] == "common-slot-empty"
    assert summary["slots"]["reason_code"] == "no-targets"


def test_scheduled_run_keeps_the_common_result_when_the_slot_phase_breaks(tmp_path, monkeypatch):
    g, target = _environment(tmp_path)
    assert slots.common_slot_is_empty(target) is False
    monkeypatch.setattr(paper_improve_retry, "run_improve_with_validation_retry",
                        lambda *_a, **_k: {"status": "improved", "changed": True})

    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(slots, "run_slot_improve", boom)
    summary = paper_improve_retry.run_scheduled_improve(g, target, "fixture", False, now=NOW)
    assert summary["status"] == "improved" and summary["scheduled"] is True
    assert summary["slots"] == {"status": "failed", "reason_code": "slots-error"}


def test_busy_improvement_lane_defers_symbols_without_an_ai_call(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from docich import corner_improve

    @contextmanager
    def busy(_state_dir, **_kwargs):
        yield False

    monkeypatch.setattr(corner_improve, "improve_lane", busy)
    g, target = _environment(tmp_path)
    summary = _run(g, target, llm=lambda _p: pytest.fail("no AI call while the lane is busy"))
    assert summary["status"] == "skipped" and summary["reason_code"] == "lane-busy"
    assert summary["calls"] == 0 and summary["deferred"] == 3
    assert not (target / SLOTS_DIRNAME).exists()
    monkeypatch.undo()
    assert _run(g, target, llm=_llm([]), now=NOW + 600)["status"] == "improved"  # same day retry is allowed
