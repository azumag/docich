"""Automatic PAPER -> LIVE promotion (Issue #3623 §2.6, owner decision 2026-11-27).

Synthetic cells, slot files and LIVE fills only.  No network.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from docich.trading.live import promotion
from docich.trading.live.decide import entry_exit_policies
from docich.trading.live.ledger import FillRecord, OrderObservation
from docich.trading.live.policy import build_policy
from docich.trading.live.signals import scan_live
from docich.trading.live.views import CostBasis
from docich.trading.market_data import MarketFrame
from docich.trading.strategy_lab import experiment_from_mapping
from docich.trading.symbol_slots import slot_dir
from docich.trading.live_worker import runner

from test_trading_live_support import Harness, Workspace, block_network, synthetic_mapping

NOW = 1795748400.0          # 2026-11-27 12:00 JST
DAY = 86400.0
STRONG_DAYS = [f"2026-11-{day}" for day in range(17, 23)]   # six blocks, E ~= 19


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def h(ws):
    harness = Harness(ws, symbols=("OAS/JPY", "XYM/JPY"), jpy="10000")
    harness.ledger.record_owner_activation(harness.lease, harness.ps, operator="test",
                                           note="initial", now=NOW - 10 * DAY)
    yield harness
    harness.close()


def _mapping(experiment_id, *, fraction="0.07", take_profit="80"):
    data = synthetic_mapping()
    data["experiment_id"] = experiment_id
    data["name"] = f"slot {experiment_id}"
    data["entry_rules"][0]["max_notional_fraction"] = fraction
    data["exit_rules"][0]["conditions"][0]["threshold"] = take_profit
    return data


def _write_slot(ws, symbol, mapping, *, history=False):
    directory = slot_dir(ws.paper, symbol)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "slot.json").write_text(json.dumps({"schema_version": 1, "symbol": symbol}))
    if history:
        (directory / "paper_strategy_evaluation_history.json").write_text(json.dumps(
            {"schema_version": 1, "entries": [{"experiment_id": mapping["experiment_id"],
                                               "experiment": mapping}]}))
    else:
        (directory / "paper_strategy_experiment.json").write_text(json.dumps(mapping))


def _cell(experiment_id, symbol, value="0.02", days=STRONG_DAYS, v3=True):
    blocks = [{"day": day, "count": 5, "mean_return": value,
               "mean_return_corrected": value} for day in days]
    cell = {"experiment_id": experiment_id, "symbol": symbol, "closed_sells": 30,
            "return_blocks": blocks}
    if v3:
        cell["return_blocks_v3"] = blocks
    return cell


def _judge(h, cells, *, now=NOW, safety=False):
    return promotion.judge(h.ws.state, h.ledger, h.ps, now=now, safety_blocked=safety, cells=cells)


# ---- judge ---------------------------------------------------------------------------------
def test_strong_cell_replaces_the_live_policy_of_its_symbol(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY")])
    assert decision.status == "ok"
    [change] = decision.changes
    assert change.symbol == "OAS/JPY" and change.experiment_id == "oas_261117"
    assert change.previous_digest == h.ps.policy_for("OAS/JPY").digest
    assert change.live_mean == 0.0 and change.e_value >= 5


def test_weak_cell_and_common_cells_are_not_promoted(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    weak = _cell("oas_261117", "OAS/JPY", value="0.002")
    common = _cell("snapback_harvest_v30", "OAS/JPY")
    assert _judge(h, [weak, common]).changes == ()


def test_a_cell_that_does_not_beat_live_on_the_same_days_is_rejected(h, monkeypatch):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    monkeypatch.setattr(promotion, "live_return_blocks",
                        lambda ledger: {"OAS/JPY": {day: 0.03 for day in STRONG_DAYS}})
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY")])
    assert decision.changes == ()
    assert decision.rejected[0]["reason"] == "not_better_than_live"


def test_live_days_without_a_sale_count_as_zero(h, monkeypatch):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    # LIVE earned 5% on one day only: mean over the six candidate days is 0.05/6 < 0.02.
    monkeypatch.setattr(promotion, "live_return_blocks",
                        lambda ledger: {"OAS/JPY": {STRONG_DAYS[0]: 0.05}})
    [change] = _judge(h, [_cell("oas_261117", "OAS/JPY")]).changes
    assert change.live_mean == pytest.approx(0.05 / 6)


def test_min_hold_blocks_a_symbol_changed_less_than_seven_days_ago(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY")], now=NOW - 4 * DAY)
    assert decision.changes == () and decision.rejected[0]["reason"] == "min_hold"


def test_a_new_symbol_is_added_on_rule_v4_alone(h):
    _write_slot(h.ws, "BAT/JPY", _mapping("bat_261117"))
    [change] = _judge(h, [_cell("bat_261117", "BAT/JPY")]).changes
    assert change.symbol == "BAT/JPY" and change.previous_digest is None and change.live_mean is None


def test_a_cell_with_only_v2_blocks_is_never_promoted(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY", v3=False)])
    assert decision.changes == ()
    assert decision.rejected == ()


def test_rules_come_from_the_slot_history_when_the_slot_rotated(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"), history=True)
    [change] = _judge(h, [_cell("oas_261117", "OAS/JPY")]).changes
    assert change.policy.experiment.experiment_id == "oas_261117"


def test_missing_rules_and_out_of_bounds_rules_are_rejected(h):
    # A fraction above 0.30 is refused by the strict round trip before the LIVE bounds.
    _write_slot(h.ws, "XYM/JPY", _mapping("xym_261117", fraction="0.40"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY"), _cell("xym_261117", "XYM/JPY")])
    assert decision.changes == ()
    assert [item["reason"] for item in decision.rejected] == ["rules_unavailable"] * 2


def test_owner_stop_switch_and_safety_trip_stop_promotion(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    cells = [_cell("oas_261117", "OAS/JPY")]
    assert _judge(h, cells, safety=True).status == "safety"
    h.ws.layout.owner_dir.mkdir(parents=True, exist_ok=True)
    (h.ws.layout.owner_dir / promotion.STOP_FILENAME).write_text("")
    decision = _judge(h, cells)
    assert decision.status == "stopped" and decision.changes == ()


def test_the_already_live_experiment_is_not_promoted_again(h):
    mapping = _mapping("oas_261117")
    _write_slot(h.ws, "OAS/JPY", mapping)
    spec = experiment_from_mapping(mapping)
    policies = dict(h.ps.policies)
    policies["OAS/JPY"] = build_policy(spec, "OAS/JPY")
    from docich.trading.live.policy import policy_set_from_policies
    active = policy_set_from_policies(policies)
    decision = promotion.judge(h.ws.state, h.ledger, active, now=NOW, safety_blocked=False,
                               cells=[_cell("oas_261117", "OAS/JPY")])
    assert decision.changes == ()


# ---- apply ---------------------------------------------------------------------------------
def test_apply_records_a_promotion_activation(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY")])
    new_set = promotion.apply(h.ledger, h.lease, h.ps, decision, now=NOW)
    assert new_set is not None and h.ledger.active_policy_set_digest() == new_set.digest
    assert new_set.policies["XYM/JPY"] == h.ps.policies["XYM/JPY"]
    assert new_set.policies["OAS/JPY"].experiment.experiment_id == "oas_261117"
    with h.ledger.read_cursor() as cur:
        reason, actor, changed, evidence = cur.execute(
            "SELECT reason, actor, changed_symbols, evidence_digest FROM policy_activations "
            "ORDER BY activation_id DESC LIMIT 1").fetchone()
    assert (reason, actor, changed) == ("promotion", "promotion_judge", "OAS/JPY")
    assert evidence == decision.evidence_digest()
    # The just-promoted symbol is now inside its minimum hold.
    assert _judge(h, [_cell("oas_261117", "OAS/JPY")], now=NOW + 1).changes == ()


def test_apply_refuses_a_decision_judged_against_another_set(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY")])
    stale = promotion.PromotionDecision("ok", changes=decision.changes, base_digest="0" * 64,
                                        judged_at=NOW)
    assert promotion.apply(h.ledger, h.lease, h.ps, stale, now=NOW) is None
    assert h.ledger.active_policy_set_digest() == h.ps.digest


# ---- LIVE return blocks ----------------------------------------------------------------------
def _trade(h, *, key, side, amount, price, at, order_id, fee="0"):
    intent = h.ledger.create_intent(h.lease, h.req(symbol="OAS/JPY", side=side, key=key,
                                                   amount=amount, price=price), now=at)
    h.ledger.record_order_observation(h.lease, OrderObservation(
        exchange_order_id=order_id, intent_id=intent.intent_id, symbol="OAS/JPY", side=side,
        order_type="limit", amount=amount, status="FULLY_FILLED", executed_amount=amount,
        remaining_amount="0", price=price), now=at)
    h.ledger.record_fill(h.lease, FillRecord(
        fill_id=f"f-{order_id}", exchange_order_id=order_id, symbol="OAS/JPY", side=side,
        amount=amount, price=price, quote_notional=str(D(amount) * D(price)), executed_at=at,
        fee_status="known", fee_amount=fee, fee_currency="JPY"), now=at)
    for before, after in (("PLANNED", "SUBMITTING"), ("SUBMITTING", "ACKED"), ("ACKED", "FILLED")):
        h.ledger.transition_intent(h.lease, intent.intent_id, expected_state=before,
                                   to_state=after, now=at)
    return intent


def test_live_return_blocks_are_net_of_fees_per_jst_day(h):
    at = NOW - 3 * DAY
    _trade(h, key="b1", side="buy", amount="10", price="100", at=at, order_id="o1")
    _trade(h, key="s1", side="sell", amount="10", price="110", at=at + 60, order_id="o2", fee="10")
    blocks = promotion.live_return_blocks(h.ledger)
    # (1100 - 10 - 1000) / 1000 = 9%
    assert blocks == {"OAS/JPY": {"2026-11-24": pytest.approx(0.09)}}


# ---- exits keep the entry policy after a promotion -----------------------------------------
def _frame(symbol, prices):
    return MarketFrame(symbol=symbol, timeframe_seconds=300,
                       timestamps=tuple(NOW - (len(prices) - i - 1) * 300 for i in range(len(prices))),
                       closes=tuple(D(str(p)) for p in prices), volumes=tuple(D("1") for _ in prices))


def test_a_held_position_exits_under_the_policy_it_was_entered_with(h):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117", take_profit="1000"))
    decision = _judge(h, [_cell("oas_261117", "OAS/JPY")])
    new_set = promotion.apply(h.ledger, h.lease, h.ps, decision, now=NOW)
    old = h.ps.policy_for("OAS/JPY")

    class Held:
        entry_policy_digest = old.digest

    exits = entry_exit_policies(h.ledger, {"OAS/JPY": Held()}, new_set)
    assert {symbol: policy.digest for symbol, policy in exits.items()} == {"OAS/JPY": old.digest}
    frames = {"OAS/JPY": _frame("OAS/JPY", [100] * 24 + [101])}
    basis = {"OAS/JPY": CostBasis(D("1"), D("100"), D("100"), NOW - 3600)}
    # +100 bps: the old take profit (60 bps) fires, the new one (1000 bps) would not.
    assert scan_live(frames, basis, new_set, now=NOW, exit_policies=exits).exits
    assert not scan_live(frames, basis, new_set, now=NOW).exits


# ---- runner step ---------------------------------------------------------------------------
def test_runner_step_promotes_and_writes_the_status(h, monkeypatch):
    _write_slot(h.ws, "OAS/JPY", _mapping("oas_261117"))
    real = promotion.judge
    monkeypatch.setattr(promotion, "judge", lambda *a, **k: real(
        *a, **{**k, "cells": [_cell("oas_261117", "OAS/JPY")]}))
    ps = runner._promotion_step(h.ws.state, h.ledger, h.lease, h.ps, lambda: NOW)
    assert ps is not h.ps and ps.policies["OAS/JPY"].experiment.experiment_id == "oas_261117"
    status = promotion.read_status(h.ws.state)
    assert status["applied"] is True and status["changes"][0]["symbol"] == "OAS/JPY"


def test_runner_step_survives_a_broken_judge(h, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(promotion, "judge", boom)
    assert runner._promotion_step(h.ws.state, h.ledger, h.lease, h.ps, lambda: NOW) is h.ps
    assert promotion.read_status(h.ws.state)["status"] == "failed"
