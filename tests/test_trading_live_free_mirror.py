"""LIVE orders of a promoted free strategy by weight mirroring (Issue #3623, 2026-11-27).

Mock exchange and synthetic weights only.  No network.
"""
from __future__ import annotations

import dataclasses
import json
from decimal import Decimal as D

import pytest

from docich.trading.live import act, free_mirror, promotion
from docich.trading.live import decide as decide_module
from docich.trading.live.inputs import build_cycle_inputs
from docich.trading.live.ledger import FillRecord, IntentRequest, OrderObservation
from docich.trading.live.policy import build_free_policy, policy_set_from_policies
from docich.trading.live.signals import LiveSignals

from test_trading_live_support import Harness, Workspace, block_network
import test_trading_live_act as act_tests
import test_trading_live_decide as decide_tests

ARTIFACT = "a" * 64


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)
    # The per-symbol strategy stays quiet: only the free strategy decides in these tests.
    monkeypatch.setattr(decide_module, "scan_live", lambda *a, **k: LiveSignals((), (), (), {}))


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def h(ws):
    harness = Harness(ws, symbols=("OAS/JPY", "XYM/JPY"))
    yield harness
    harness.close()


def _free(experiment="mirror-1", promoted_at="1"):
    return build_free_policy(artifact=ARTIFACT, experiment=experiment,
                             symbols=["OAS/JPY", "XYM/JPY"], promoted_at=promoted_at)


def _sets(free=None):
    base = decide_tests.policy_set_for("OAS/JPY")
    return base, policy_set_from_policies(base.policies, free or _free())


def _weights(ws, now, weights, *, artifact=ARTIFACT, experiment="mirror-1"):
    path = ws.state / free_mirror.WEIGHTS_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "artifact": artifact, "experiment": experiment,
                                "observed_at": now, "symbols": ["OAS/JPY", "XYM/JPY"],
                                "weights": weights}))


def _fill(h, digest, *, symbol="OAS/JPY", amount="10", price="100", key="free-1"):
    intent = h.ledger.create_intent(h.lease, IntentRequest(
        decision_key=f"live1f:{key}", symbol=symbol, side="buy", order_type="limit",
        amount=amount, price=price, policy_digest=digest, rule_id="free-entry"), now=h.clock.seconds())
    h.ledger.record_order_observation(h.lease, OrderObservation(
        exchange_order_id=f"o-{key}", intent_id=intent.intent_id, symbol=symbol, side="buy",
        order_type="limit", amount=amount, status="FULLY_FILLED", executed_amount=amount,
        remaining_amount="0", price=price), now=h.clock.seconds())
    h.ledger.record_fill(h.lease, FillRecord(
        fill_id=f"f-{key}", exchange_order_id=f"o-{key}", symbol=symbol, side="buy", amount=amount,
        price=price, quote_notional=str(D(amount) * D(price)), executed_at=h.clock.seconds(),
        fee_status="known", fee_amount="0", fee_currency="JPY"), now=h.clock.seconds())
    for before, after in (("PLANNED", "SUBMITTING"), ("SUBMITTING", "ACKED"), ("ACKED", "FILLED")):
        h.ledger.transition_intent(h.lease, intent.intent_id, expected_state=before,
                                   to_state=after, now=h.clock.seconds())
    h.mock.adjust_balance(symbol.split("/")[0], amount)
    h.mock.adjust_balance("JPY", str(-D(amount) * D(price)))
    return intent


def _rig(ws, h, ps, *, prices=None):
    symbols = ("OAS/JPY", "XYM/JPY")
    rules = {symbol: decide_tests.rule(symbol) for symbol in symbols}
    rig = act_tests.make_rig(ws, h, ps=ps, rules=rules, symbols=symbols)
    rig.deps = dataclasses.replace(rig.deps, state_dir=ws.state)
    frames = {symbol: decide_tests.frame(symbol, price=(prices or {}).get(symbol, "100"))
              for symbol in symbols}
    return rig, decide_tests.make_ctx(rig, mode="trade", frames=frames)


def _free_intents(h, free):
    return [intent for intent in act_tests.all_intents(h.ledger) if intent.policy_digest == free.digest]


# ---- weights file ----------------------------------------------------------------------------
def test_weights_are_validated_and_must_be_fresh(ws):
    now = 1_800_000_000.0
    _weights(ws, now, {"XYM/JPY": "0.2"})
    assert free_mirror.read_weights(ws.state, now=now + 60).weights == {"XYM/JPY": D("0.2")}
    assert free_mirror.read_weights(ws.state, now=now + free_mirror.MAX_WEIGHTS_AGE_S + 1) is None
    _weights(ws, now, {"XYM/JPY": "0.7", "OAS/JPY": "0.6"})
    assert free_mirror.read_weights(ws.state, now=now) is None          # sums above 1
    _weights(ws, now, {"BTC/JPY": "0.1"})
    assert free_mirror.read_weights(ws.state, now=now) is None          # not a strategy symbol


# ---- both strategies may hold one symbol ------------------------------------------------------
def test_a_free_position_never_makes_a_per_symbol_position_ambiguous(ws, h):
    base, ps = _sets()
    decide_tests.store(h, ps)
    decide_tests.fill_position(h, base, amount="10", price="100")
    _fill(h, ps.free.digest, amount="5", price="100")
    inputs = build_cycle_inputs(h.ledger, rules={"OAS/JPY": decide_tests.rule()},
                                balance=h.mock.fetch_balance(), policy_set=ps,
                                free_digests=h.ledger.free_policy_digests())
    assert inputs.ambiguous_position_symbols == frozenset()
    assert inputs.held["OAS/JPY"].net_base == D("10")
    assert inputs.free_held["OAS/JPY"].net_base == D("5")
    assert inputs.free_held["OAS/JPY"].entry_policy_digest == ps.free.digest


# ---- buys toward the mirror's weight, as a share of C ----------------------------------------
def test_the_free_strategy_buys_toward_weight_times_capital(ws, h):
    _base, ps = _sets()
    decide_tests.store(h, ps)
    _weights(ws, h.clock.seconds(), {"XYM/JPY": "0.2"})
    rig, ctx = _rig(ws, h, ps)
    act.decide_and_act(rig.deps, rig.state, ctx)
    [intent] = _free_intents(h, ps.free)
    # C = 10000, weight 0.2 -> 2000 JPY at 100: 20 XYM.
    assert (intent.symbol, intent.side, D(intent.amount)) == ("XYM/JPY", "buy", D("20"))


def test_a_wide_spread_blocks_a_free_buy(ws, h):
    _base, ps = _sets()
    decide_tests.store(h, ps)
    _weights(ws, h.clock.seconds(), {"XYM/JPY": "0.2"})
    rig, ctx = _rig(ws, h, ps)
    rig.reader.books["XYM/JPY"] = decide_tests.book(
        "XYM/JPY", bids=(("100", "1000"),), asks=(("101", "1000"),))
    act.decide_and_act(rig.deps, rig.state, ctx)
    assert _free_intents(h, ps.free) == []
    skips = act_tests.skip_rows(h.ledger, "spread_too_wide")
    assert any(row["symbol"] == "XYM/JPY" and row["side"] == "buy" for row in skips)


def test_a_symbol_the_mirror_left_is_sold_out_under_the_free_digest(ws, h):
    _base, ps = _sets()
    decide_tests.store(h, ps)
    _fill(h, ps.free.digest, amount="5", price="100")
    _weights(ws, h.clock.seconds(), {})
    rig, ctx = _rig(ws, h, ps)
    act.decide_and_act(rig.deps, rig.state, ctx)
    sells = [intent for intent in _free_intents(h, ps.free) if intent.side == "sell"]
    assert [(intent.symbol, D(intent.amount)) for intent in sells] == [("OAS/JPY", D("5"))]


def test_stale_weights_place_no_free_orders(ws, h):
    _base, ps = _sets()
    decide_tests.store(h, ps)
    _weights(ws, h.clock.seconds() - 3600, {"XYM/JPY": "0.2"})
    rig, ctx = _rig(ws, h, ps)
    act.decide_and_act(rig.deps, rig.state, ctx)
    assert _free_intents(h, ps.free) == []


def test_positions_of_an_older_free_policy_are_sold_out(ws, h):
    old = _free(experiment="mirror-0", promoted_at="0")
    _base, ps = _sets()
    decide_tests.store(h, policy_set_from_policies(ps.policies, old))
    _fill(h, old.digest, amount="5", price="100")
    decide_tests.store(h, ps)
    _weights(ws, h.clock.seconds(), {"OAS/JPY": "0.1"})
    rig, ctx = _rig(ws, h, ps)
    act.decide_and_act(rig.deps, rig.state, ctx)
    sells = [intent for intent in act_tests.all_intents(h.ledger)
             if intent.side == "sell" and intent.policy_digest == old.digest]
    assert len(sells) == 1 and sells[0].symbol == "OAS/JPY"


# ---- loss stop ---------------------------------------------------------------------------------
def test_the_loss_stop_blocks_buys_at_ten_percent_and_resets_on_promotion(ws, h):
    _base, ps = _sets()
    decide_tests.store(h, ps)
    _fill(h, ps.free.digest, amount="20", price="100")            # 2000 JPY
    assert free_mirror.loss_stop(ws.state, h.ledger, ps.free, capital=D("10000"),
                                 last_closes={"OAS/JPY": D("100")}, now=1.0) is False
    # OAS at 50: -1000 = 10% of the 10000 baseline.
    assert free_mirror.loss_stop(ws.state, h.ledger, ps.free, capital=D("9000"),
                                 last_closes={"OAS/JPY": D("50")}, now=2.0) is True
    assert free_mirror.loss_stop(ws.state, h.ledger, ps.free, capital=D("9000"),
                                 last_closes={"OAS/JPY": D("100")}, now=3.0) is True   # latched
    new = _free(promoted_at="2")
    assert free_mirror.loss_stop(ws.state, h.ledger, new, capital=D("9000"),
                                 last_closes={"OAS/JPY": D("50")}, now=4.0) is False   # reset


def test_a_stopped_free_strategy_places_no_buys(ws, h):
    _base, ps = _sets()
    decide_tests.store(h, ps)
    _fill(h, ps.free.digest, amount="20", price="100")
    _weights(ws, h.clock.seconds(), {"OAS/JPY": "0.25", "XYM/JPY": "0.2"})
    free_mirror.loss_stop(ws.state, h.ledger, ps.free, capital=D("10000"),
                          last_closes={"OAS/JPY": D("100")}, now=1.0)
    rig, ctx = _rig(ws, h, ps, prices={"OAS/JPY": "50"})
    act.decide_and_act(rig.deps, rig.state, ctx)
    assert [intent for intent in _free_intents(h, ps.free) if intent.side == "buy"
            and intent.decision_key.startswith("live1f:")
            and intent.decision_key != "live1f:free-1"] == []


# ---- adoption through the promotion judge ------------------------------------------------------
def test_promotion_adopts_the_mirror_artifact_and_keeps_the_symbol_policies(ws, h):
    base = decide_tests.policy_set_for("OAS/JPY")
    decide_tests.store(h, base)
    h.ledger.record_owner_activation(h.lease, base, operator="test", note="initial", now=1.0)
    now = h.clock.seconds()
    _weights(ws, now, {"XYM/JPY": "0.2"})
    decision = promotion.judge(ws.state, h.ledger, base, now=now, safety_blocked=False, cells=[])
    assert decision.free is not None and decision.free.artifact == ARTIFACT
    new_set = promotion.apply(h.ledger, h.lease, base, decision, now=now)
    assert new_set.policies == base.policies and new_set.free == decision.free
    loaded = h.ledger.load_policy_set(h.ledger.active_policy_set_digest())
    assert loaded.digest == new_set.digest and loaded.free.digest == decision.free.digest
    assert new_set.all_symbols() == ("OAS/JPY", "XYM/JPY")
    # Same artifact again: nothing to adopt.
    again = promotion.judge(ws.state, h.ledger, new_set, now=now, safety_blocked=False, cells=[])
    assert again.free is None


def test_a_set_without_a_free_strategy_keeps_its_digest():
    base = decide_tests.policy_set_for("OAS/JPY")
    assert policy_set_from_policies(base.policies).digest == base.digest
    assert policy_set_from_policies(base.policies, _free()).digest != base.digest
