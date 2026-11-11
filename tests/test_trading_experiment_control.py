from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.ai_text import AiTextError  # noqa: E402
from docich.trading.experiment_control import (  # noqa: E402
    CONTROL_FILENAME,
    assess_experiment,
    experiment_lock,
)
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import AllocationDecision, MarketInfo  # noqa: E402
from docich.trading.paper import PaperBroker, paper_execution_price  # noqa: E402
from docich.trading.paper_improve import STATUS_FILENAME, run_paper_improve  # noqa: E402
from docich.trading.strategy_lab import (  # noqa: E402
    EVALUATION_FILENAME,
    EVALUATION_HISTORY_FILENAME,
    EXPERIMENT_FILENAME,
    PENDING_FILENAME,
    PROMOTION_FILENAME,
    experiment_from_mapping,
    experiment_to_payload,
    load_pending_experiment,
    load_strategy_experiment,
    save_pending_experiment,
    save_strategy_experiment,
)
from docich.trading.strategy_runtime import set_active_experiment  # noqa: E402
from docich.trading.worker import run_worker_cycle  # noqa: E402

D = Decimal
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def reset_active_experiment():
    set_active_experiment(None)
    yield
    set_active_experiment(None)


def _spec(experiment_id="active", *, lookback=3, hold_minutes=60):
    return experiment_from_mapping({
        "experiment_id": experiment_id,
        "name": experiment_id,
        "thesis": "損失実績による停止と、旧条件での決済を検証する。",
        "entry_rules": [{
            "rule_id": "entry", "combine": "all", "max_notional_fraction": "0.10",
            "conditions": [{"feature": "return_bps", "lookback": lookback, "op": ">=", "threshold": 100}],
        }],
        "exit_rules": [{
            "rule_id": "exit", "combine": "all",
            "conditions": [{"feature": "hold_minutes", "op": ">=", "threshold": hold_minutes}],
        }],
    })


def _environment(root, *, active=True, age=4 * 3600, ledger=True):
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
    if ledger:
        PaperLedger(target / "paper.sqlite3").close()
    if active:
        save_strategy_experiment(target, _spec(), activated_at=NOW - age)
    return g, target


def _decision(oid, side, price, *, symbol="LOSS/JPY", experiment_id="active"):
    price = D(str(price))
    return AllocationDecision(
        opportunity_id=oid,
        strategy_id=f"{'lab' if side == 'buy' else 'lab-exit'}:{experiment_id}:{'entry' if side == 'buy' else 'exit'}",
        symbol=symbol, side=side, quote="JPY", amount=D("1"), price=price,
        quote_notional=price, reference_notional=price,
        reason_code="paper_lab_entry" if side == "buy" else "paper_lab_exit",
    )


def _seed_losses(target, *, count=8, open_at=None):
    ledger = PaperLedger(target / "paper.sqlite3")
    try:
        broker = PaperBroker(ledger)
        for index in range(count):
            stamp = NOW - 3 * 3600 + index * 120
            broker.fill(_decision(f"loss-buy-{index}", "buy", "100"), timestamp=stamp)
            broker.fill(_decision(f"loss-sell-{index}", "sell", "99"), timestamp=stamp + 60)
        if open_at is not None:
            broker.fill(_decision("still-open", "buy", "100", symbol="BTC/JPY"), timestamp=open_at)
    finally:
        ledger.close()


class HistoryGateway:
    """Public feed fixture honors the requested bar count instead of ignoring it."""

    def __init__(self, prices=None):
        self.prices = prices or {"NEW/JPY": [100] * 24 + [104]}
        self.requests = []

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
        self.requests.append((symbol, timeframe, limit, now))
        values = self.prices[symbol][-limit:]
        count = len(values)
        return {symbol: MarketFrame(
            symbol=symbol, timeframe_seconds=300,
            timestamps=tuple(now - (count - index - 1) * 300 for index in range(count)),
            closes=tuple(D(str(value)) for value in values), volumes=(D("1"),) * count,
        )}


def _cycle(g, gateway, *, now=NOW, index=1):
    return run_worker_cycle(
        g, gateway=gateway, cycle_index=index, now=now, observation_now_fn=lambda: now,
    )


def _account(target):
    ledger = PaperLedger(target / "paper.sqlite3")
    try:
        return ledger.recent_fills(limit=100), ledger.positions()
    finally:
        ledger.close()


def _control(target):
    return json.loads((target / CONTROL_FILENAME).read_text(encoding="utf-8"))


def _assess(target, *, now=NOW):
    with experiment_lock(target):
        return assess_experiment(target, capital_jpy="10000", now=now)


def _pending(target, *, hold_minutes=600):
    save_pending_experiment(target, _spec("pending", hold_minutes=hold_minutes), proposed_at=NOW - 60)


def test_losing_exits_without_candidate_keep_collecting_paper_data(tmp_path):
    g, target = _environment(tmp_path)
    original = (target / EXPERIMENT_FILENAME).read_bytes()
    _seed_losses(target, open_at=NOW - 4000)
    gateway = HistoryGateway({"BTC/JPY": [100] * 25, "NEW/JPY": [100] * 24 + [104]})
    result = _cycle(g, gateway)
    fills, positions = _account(target)
    new_fills = [fill for fill in fills if fill.filled_at >= NOW]
    # PAPER losses are simulated: a losing sample keeps trading so the next
    # evaluation has data, instead of pausing until a daily candidate.
    assert result.new_fill_count == 2
    assert sorted((fill.side, fill.symbol, fill.strategy_id) for fill in new_fills) == [
        ("buy", "NEW/JPY", "lab:active:entry"),
        ("sell", "BTC/JPY", "lab-exit:active:exit"),
    ]
    assert positions["NEW/JPY"] > 0
    assert (target / EXPERIMENT_FILENAME).read_bytes() == original
    control = _control(target)
    assert control["status"] == "active"
    assert control["reason_code"] == "collecting"
    assert control["drain_reason_code"] is None
    assert control["entries_allowed"] is True
    evaluation = json.loads((target / EVALUATION_FILENAME).read_text(encoding="utf-8"))
    assert evaluation["closed_sells"] == 9
    assert D(evaluation["profit_factor"]) < D("0.75")


def test_worker_drains_before_pending_activation_and_consumes_candidate_when_flat(tmp_path):
    g, target = _environment(tmp_path)
    _seed_losses(target, count=20, open_at=NOW - 300)
    _pending(target, hold_minutes=600)
    original = (target / EXPERIMENT_FILENAME).read_bytes()
    queued = (target / PENDING_FILENAME).read_bytes()
    gateway = HistoryGateway({"BTC/JPY": [100] * 25, "NEW/JPY": [100] * 24 + [104]})

    first = _cycle(g, gateway)
    assert first.new_fill_count == 0
    assert _account(target)[1] == {"BTC/JPY": D("1")}
    assert (target / EXPERIMENT_FILENAME).read_bytes() == original
    assert (target / PENDING_FILENAME).read_bytes() == queued
    assert _control(target)["status"] == "draining"
    assert _control(target)["reason_code"] == "sample-complete"
    assert _control(target)["entries_allowed"] is False

    second = _cycle(g, gateway, now=NOW + 3600, index=2)
    fills, positions = _account(target)
    assert second.new_fill_count == 1 and positions == {}
    assert fills[0].side == "sell" and fills[0].strategy_id == "lab-exit:active:exit"
    active = load_strategy_experiment(target)
    assert active.experiment_id == "pending" and active.activated_at == NOW + 3600
    assert not (target / PENDING_FILENAME).exists()
    assert _control(target)["entries_allowed"] is True


def test_losing_sample_with_candidate_waits_for_sample_gate(tmp_path):
    _g, target = _environment(tmp_path)
    _seed_losses(target, count=19)
    _pending(target)
    step = _assess(target)
    assert step.entries_allowed is True and step.status == "active"
    assert step.evaluation["closed_sells"] == 19
    assert D(step.evaluation["profit_factor"]) < D("0.75")
    assert load_strategy_experiment(target).experiment_id == "active"
    assert load_pending_experiment(target).experiment_id == "pending"


def test_sample_gate_reached_in_current_cycle_cancels_already_planned_new_buys(tmp_path):
    g, target = _environment(tmp_path)
    _seed_losses(target, count=19, open_at=NOW - 4000)
    _pending(target, hold_minutes=600)
    gateway = HistoryGateway({"BTC/JPY": [100] * 25, "NEW/JPY": [100] * 24 + [104]})
    result = _cycle(g, gateway)
    fills, positions = _account(target)
    current_fills = [fill for fill in fills if fill.filled_at >= NOW]
    assert result.new_fill_count == 1
    assert [(fill.side, fill.symbol) for fill in current_fills] == [("sell", "BTC/JPY")]
    assert positions == {}
    # The 20th exit met the gate with a candidate: the old plan's buy is
    # dropped and the flat account switches to the candidate in this cycle.
    assert load_strategy_experiment(target).experiment_id == "pending"
    assert load_strategy_experiment(target).activated_at == NOW
    control = _control(target)
    assert control["status"] == "active" and control["experiment_id"] == "pending"
    assert control["entries_allowed"] is True


def test_drain_latch_survives_unavailable_evaluation_and_lost_candidate(tmp_path):
    _g, target = _environment(tmp_path)
    _seed_losses(target, count=20, open_at=NOW - 300)
    _pending(target)
    initial = _assess(target)
    assert initial.entries_allowed is False and initial.status == "draining"
    assert initial.reason_code == "sample-complete"
    ledger_path = target / "paper.sqlite3"
    temporarily_unavailable = target / "paper.sqlite3.unavailable"
    ledger_path.rename(temporarily_unavailable)
    try:
        unavailable = _assess(target, now=NOW + 60)
        assert unavailable.entries_allowed is False and unavailable.status == "blocked"
        assert unavailable.reason_code == "evaluation-unavailable"
    finally:
        temporarily_unavailable.rename(ledger_path)

    # Losing the candidate after the drain began must not reopen entries
    # while the old strategy still owns inventory, nor once it is flat.
    (target / PENDING_FILENAME).unlink()
    recovered = _assess(target, now=NOW + 120)
    assert recovered.status == "draining" and recovered.entries_allowed is False
    assert recovered.reason_code == "sample-complete"

    active_path = target / EXPERIMENT_FILENAME
    active_bytes = active_path.read_bytes()
    active_path.unlink()
    try:
        missing = _assess(target, now=NOW + 180)
        assert missing.status == "blocked" and missing.entries_allowed is False
        assert missing.reason_code == "invalid-active"
    finally:
        active_path.write_bytes(active_bytes)
    ledger = PaperLedger(ledger_path)
    try:
        PaperBroker(ledger).fill(
            _decision("drain-exit", "sell", "100", symbol="BTC/JPY"), timestamp=NOW + 200,
        )
    finally:
        ledger.close()
    restored = _assess(target, now=NOW + 240)
    assert restored.status == "waiting-candidate" and restored.entries_allowed is False
    assert restored.reason_code == "sample-complete"


def test_legacy_early_stop_latch_still_drains_to_next_candidate(tmp_path):
    _g, target = _environment(tmp_path)
    _seed_losses(target, count=8)
    assert _assess(target).status == "active"
    control = _control(target)
    control.update({"status": "waiting-candidate", "reason_code": "early-stop",
                    "drain_reason_code": "early-stop", "entries_allowed": False})
    (target / CONTROL_FILENAME).write_text(json.dumps(control), encoding="utf-8")

    waiting = _assess(target, now=NOW + 60)
    assert waiting.status == "waiting-candidate" and waiting.entries_allowed is False
    assert waiting.reason_code == "early-stop"

    _pending(target)
    activated = _assess(target, now=NOW + 120)
    assert activated.activated is True and activated.entries_allowed is True
    assert load_strategy_experiment(target).experiment_id == "pending"


def test_missing_active_file_cannot_return_to_legacy_entries_on_later_cycles(tmp_path):
    g, target = _environment(tmp_path)
    assert _assess(target).entries_allowed is True
    (target / EXPERIMENT_FILENAME).unlink()
    gateway = HistoryGateway()
    for index in (1, 2):
        result = _cycle(g, gateway, now=NOW + index * 300, index=index)
        assert result.new_fill_count == 0
        control = _control(target)
        assert control["entries_allowed"] is False
        assert control["reason_code"] == "invalid-active"
    assert _account(target)[0] == []
    assert not (target / EXPERIMENT_FILENAME).exists()


def test_reused_id_with_different_rules_cannot_rotate_or_pass_generation_validation(tmp_path):
    g, target = _environment(tmp_path)
    _seed_losses(target, count=19, open_at=NOW - 4000)
    collision = _spec("active", hold_minutes=600)
    save_pending_experiment(target, collision, proposed_at=NOW - 60)
    original = (target / EXPERIMENT_FILENAME).read_bytes()
    gateway = HistoryGateway({"BTC/JPY": [100] * 25})
    result = _cycle(g, gateway)
    assert result.new_fill_count == 1
    assert (target / EXPERIMENT_FILENAME).read_bytes() == original
    # The sample gate is met, but a reused id is not a usable candidate.
    assert _control(target)["status"] == "active"
    assert _control(target)["reason_code"] == "sample-complete"
    assert _control(target)["pending_available"] is False
    assert _account(target)[1] == {}

    called = []

    def invalid_candidate(_prompt):
        called.append(True)
        return json.dumps({"strategy_experiment": experiment_to_payload(collision)})

    generated = run_paper_improve(
        g, trading_dir=target, agents="fixture", llm=invalid_candidate, now=NOW + 60,
    )
    assert called == [True]  # An unusable pending file must not block new research.
    assert generated["status"] == "failed"
    assert generated["reason_code"] == "candidate-invalid"
    assert json.loads((target / STATUS_FILENAME).read_text(encoding="utf-8"))["phase"] == "validate"
    assert generated.get("changed", False) is False
    assert not generated.get("activated", False)
    assert (target / EXPERIMENT_FILENAME).read_bytes() == original
    assert _account(target)[1] == {}

    replacement = run_paper_improve(
        g, trading_dir=target, agents="fixture", now=NOW + 120,
        llm=lambda _: json.dumps({"strategy_experiment": experiment_to_payload(_spec("new-id"))}),
    )
    assert replacement["activated"] is True
    assert load_strategy_experiment(target).experiment_id == "new-id"
    assert not (target / PENDING_FILENAME).exists()
    assert _account(target)[1] == {}


def test_identical_generated_experiment_does_not_reset_activation_or_queue(tmp_path):
    g, target = _environment(tmp_path, age=49 * 3600)
    original = (target / EXPERIMENT_FILENAME).read_bytes()
    response = json.dumps({"strategy_experiment": experiment_to_payload(_spec())})
    result = run_paper_improve(
        g, trading_dir=target, agents="fixture", llm=lambda _: response, now=NOW,
    )
    assert result["changed"] is False and result["activated"] is False
    assert (target / EXPERIMENT_FILENAME).read_bytes() == original
    assert load_pending_experiment(target) is None


def test_ready_pending_activates_before_ai_timeout(tmp_path):
    g, target = _environment(tmp_path, age=49 * 3600)
    _pending(target)
    called = []

    def timeout(_prompt):
        called.append(True)
        assert load_strategy_experiment(target).experiment_id == "pending"
        assert not (target / PENDING_FILENAME).exists()
        raise AiTextError("fixture timeout", kind="timeout")

    result = run_paper_improve(g, trading_dir=target, agents="fixture", llm=timeout, now=NOW)
    assert called == [True]
    assert result["status"] == "improved" and result["activated"] is True
    assert result["activated_from"] == "pending"
    assert result["generation_status"] == "failed" and result["reason_code"] == "timeout"
    assert load_strategy_experiment(target).activated_at == NOW
    assert _account(target)[0] == []


def test_ready_pending_activates_when_agents_are_disabled(tmp_path):
    g, target = _environment(tmp_path, age=49 * 3600)
    _pending(target)

    def unexpected_llm(_prompt):
        pytest.fail("Candidate activation must not require an AI call")

    result = run_paper_improve(g, trading_dir=target, agents="", llm=unexpected_llm, now=NOW)
    assert result["status"] == "improved" and result["activated"] is True
    assert load_strategy_experiment(target).experiment_id == "pending"
    assert not (target / PENDING_FILENAME).exists()
    assert _account(target)[0] == []


def test_generation_against_changed_baseline_cannot_overwrite_current_experiment(tmp_path):
    g, target = _environment(tmp_path)

    def concurrent_change(_prompt):
        # An independently validated update lands while the slow AI is running.
        save_strategy_experiment(target, _spec("concurrent"), activated_at=NOW)
        return json.dumps({"strategy_experiment": experiment_to_payload(_spec("stale-generated"))})

    result = run_paper_improve(g, trading_dir=target, agents="fixture", llm=concurrent_change, now=NOW)
    assert result["status"] == "skipped" and result["reason"] == "baseline-changed"
    assert result["changed"] is False
    assert load_strategy_experiment(target).experiment_id == "concurrent"
    assert not (target / PENDING_FILENAME).exists()
    assert _account(target)[0] == []


def test_dry_run_keeps_active_pending_evaluation_control_and_account_unchanged(tmp_path):
    g, target = _environment(tmp_path, age=49 * 3600)
    _assess(target)
    _pending(target)
    filenames = (
        EXPERIMENT_FILENAME, PENDING_FILENAME, EVALUATION_FILENAME,
        EVALUATION_HISTORY_FILENAME, PROMOTION_FILENAME, CONTROL_FILENAME, "paper.sqlite3",
    )
    before = {name: (target / name).read_bytes() if (target / name).exists() else None for name in filenames}

    def unexpected_llm(_prompt):
        pytest.fail("Dry run must not generate or stage a candidate")

    result = run_paper_improve(
        g, trading_dir=target, agents="fixture", llm=unexpected_llm, now=NOW, dry_run=True,
    )
    after = {name: (target / name).read_bytes() if (target / name).exists() else None for name in filenames}
    assert result["status"] == "dry-run"
    assert after == before


def test_worker_without_experiment_preserves_legacy_entries(tmp_path):
    g, target = _environment(tmp_path, active=False)
    result = _cycle(g, HistoryGateway({"BTC/JPY": [100] * 24 + [104]}))
    fills, positions = _account(target)
    assert result.new_fill_count == 1
    assert fills[0].strategy_id == "momentum-v1"
    assert positions["BTC/JPY"] > 0
    assert not (target / CONTROL_FILENAME).exists()


def test_worker_fetches_25_closes_for_all_24_period_features(tmp_path):
    g, target = _environment(tmp_path)
    payload = experiment_to_payload(_spec("lookback-24", lookback=24))
    payload["entry_rules"][0]["conditions"].extend([
        {"feature": "rsi", "lookback": 24, "op": ">=", "threshold": 60},
        {"feature": "volatility_bps", "lookback": 24, "op": ">=", "threshold": 10},
    ])
    save_strategy_experiment(target, experiment_from_mapping(payload), activated_at=NOW - 3600)
    gateway = HistoryGateway()
    result = _cycle(g, gateway)
    fills, positions = _account(target)
    assert gateway.requests == [("NEW/JPY", "5m", 25, NOW)]
    assert result.new_fill_count == 1
    assert fills[0].strategy_id == "lab:lookback-24:entry"
    assert positions["NEW/JPY"] > 0
    assert {condition["feature"] for condition in fills[0].signal_context["conditions"]} == {
        "return_bps", "rsi", "volatility_bps",
    }


def test_missing_evaluation_ledger_blocks_entries_and_preserves_pending(tmp_path):
    _g, target = _environment(tmp_path, age=49 * 3600, ledger=False)
    _pending(target)
    active_before = (target / EXPERIMENT_FILENAME).read_bytes()
    pending_before = (target / PENDING_FILENAME).read_bytes()
    step = _assess(target)
    assert step.entries_allowed is False and step.status == "blocked"
    assert step.reason_code == "evaluation-unavailable"
    assert step.evaluation["status"] == "unavailable"
    assert step.evaluation["closed_sells"] is None
    assert (target / EXPERIMENT_FILENAME).read_bytes() == active_before
    assert (target / PENDING_FILENAME).read_bytes() == pending_before
    assert not (target / "paper.sqlite3").exists()


def test_invalid_fill_cannot_be_treated_as_an_empty_evaluable_account(tmp_path):
    _g, target = _environment(tmp_path, age=49 * 3600)
    _seed_losses(target, count=1)
    _pending(target)
    with sqlite3.connect(target / "paper.sqlite3") as connection:
        connection.execute("UPDATE paper_fills SET amount = 'NaN' WHERE side = 'buy'")
    step = _assess(target)
    assert step.entries_allowed is False and step.status == "blocked"
    assert step.evaluation["status"] == "invalid"
    assert step.evaluation["closed_sells"] is None
    assert load_strategy_experiment(target).experiment_id == "active"
    assert load_pending_experiment(target).experiment_id == "pending"
