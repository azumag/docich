"""LIVE L4b-4: pure limit-order sizing (Issue #3623).

Every number is pinned against the hand calculation in the task; money is ``Decimal``
and compared with ``==``.  Nothing here touches the network or the filesystem.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from docich.trading.live import sizing
from docich.trading.live.exchange import MarketRules, OrderBook
from docich.trading.live.ledger import IntentRequest
from docich.trading.live.policy import build_policy, policy_set_from_policies
from docich.trading.live.signals import LiveSignals
from docich.trading.live.sizing import (
    SKIP_CODES,
    Funds,
    HeldPosition,
    SizingSettings,
    SymbolMarket,
    ceil_step,
    floor_step,
    floor_tick,
    size_orders,
)
from docich.trading.models import Opportunity
from docich.trading.strategy_lab import Condition, Rule, StrategyExperiment

from test_trading_live_support import block_network

D = Decimal
NOW = 1_800_000_000.0
SRC = Path(sizing.__file__).parents[3]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


# ---- builders -------------------------------------------------------------------------


def spec(experiment_id: str, *, entry_fraction: str = "0.30", exit_fraction: str = "0.10"):
    return StrategyExperiment(
        experiment_id=experiment_id,
        name=f"{experiment_id} name",
        thesis=f"{experiment_id} thesis",
        entry_rules=(
            Rule("entry-1", "all", (Condition("return_bps", ">=", D("100"), 3),), D(entry_fraction)),
        ),
        exit_rules=(
            Rule("exit-1", "all", (Condition("hold_minutes", ">=", D("100000")),), D(exit_fraction)),
        ),
        max_pair_correlation=D("0.85"),
    )


def policy_set_for(*pairs):
    return policy_set_from_policies(
        {symbol: build_policy(experiment, symbol) for experiment, symbol in pairs}
    )


def market(
    symbol: str = "OAS/JPY",
    *,
    last_close: str = "100",
    step: str = "0.01",
    minamt: str = "0.01",
    tick: str = "0.001",
    quote_unit: str = "1",
    fee: str | None = "0.0012",
    bids=(),
    asks=(),
) -> SymbolMarket:
    base, quote = symbol.split("/")
    rules = MarketRules(
        symbol=symbol,
        base=base,
        quote=quote,
        amount_step=D(step),
        min_amount=D(minamt),
        price_tick=D(tick),
        base_unit=D("0.0001"),
        quote_unit=D(quote_unit),
        max_fee_rate=None if fee is None else D(fee),
    )
    book = OrderBook(
        symbol=symbol,
        bids=tuple((D(price), D(amount)) for price, amount in bids),
        asks=tuple((D(price), D(amount)) for price, amount in asks),
        exchange_ts_ms=1_700_000_000_000,
    )
    return SymbolMarket(rules=rules, book=book, last_close=D(last_close))


def opportunity(
    symbol: str,
    side: str,
    *,
    oid: str = "opp-1",
    score: str = "0.5",
    fraction: str = "0.30",
    strategy_id: str = "lab:exp-1:entry-1",
    expires_at: float = NOW + 60,
) -> Opportunity:
    return Opportunity(
        opportunity_id=oid,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        score=D(score),
        expected_edge_bps=D("10"),
        max_notional_fraction=D(fraction),
        expires_at=expires_at,
        reason_code="entry_signal",
    )


def live_signals(entries=(), exits=()) -> LiveSignals:
    return LiveSignals(tuple(entries), tuple(exits), (), {})


def funds(capital="10000", deployed="0", free_quote="10000") -> Funds:
    return Funds(D(capital), D(deployed), D(free_quote))


def codes(result) -> list[str]:
    observed = [str(skip.reason_code) for skip in result.skipped]
    assert set(observed) <= SKIP_CODES
    return observed


# ---- (a) the hand-calculated examples --------------------------------------------------


def test_example_1_buy_walk():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    policy = ps.policies["OAS/JPY"]
    assert policy.max_opportunity_fraction == D("0.30")
    entry = opportunity("OAS/JPY", "buy", oid="opp-1", fraction="0.30")
    mark = market(
        last_close="100",
        step="0.01",
        minamt="0.01",
        tick="0.001",
        quote_unit="1",
        fee="0.0012",
        asks=(("100.000", "10"), ("100.100", "20"), ("100.200", "100")),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("10000", "0", "10000"), now=NOW
    )

    assert result.skipped == ()
    assert len(result.buys) == 1
    plan = result.buys[0]
    assert plan.decision_key == f"live1:{policy.digest[:16]}:opp-1"
    assert plan.amount == D("29.97")
    assert plan.price == D("100.100")
    assert plan.required_quote == D("3004.5969964")
    assert plan.notes == ()
    assert plan.order_type == "limit"
    assert plan.position_id is None
    assert plan.rule_id == "entry-1"
    assert plan.policy_digest == policy.digest
    assert plan.ttl_s == 45
    assert result.free_quote_left == D("6995.4030036")
    assert result.headroom_left == D("7000.003")


def test_example_2_round_up_to_the_exchange_minimum():
    ps = policy_set_for((spec("exp-2"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.05")
    mark = market(
        last_close="100",
        step="1",
        minamt="1",
        tick="1",
        quote_unit="1",
        fee="0.001",
        asks=(("100", "5"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.skipped == ()
    plan = result.buys[0]
    assert (plan.amount, plan.price) == (D("1"), D("100"))
    assert plan.required_quote == D("101.1")
    assert plan.notes == ("min_amount_rounded_up",)
    assert result.headroom_left == D("900")
    assert result.free_quote_left == D("898.9")


def test_example_2_variant_minimum_above_the_hard_cap():
    ps = policy_set_for((spec("exp-2"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.05")
    mark = market(
        last_close="100",
        step="1",
        minamt="4",
        tick="1",
        quote_unit="1",
        fee="0.001",
        asks=(("100", "5"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["below_min_amount"]
    assert result.skipped[0].side == "buy"


def test_example_2_variant_headroom_blocks_the_minimum():
    ps = policy_set_for((spec("exp-2"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.05")
    mark = market(
        last_close="100",
        step="1",
        minamt="1",
        tick="1",
        quote_unit="1",
        fee="0.001",
        asks=(("100", "5"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "910", "1000"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["total_cap_exhausted"]
    assert result.headroom_left == D("90")


def test_example_2_variant_free_quote_blocks_the_minimum():
    ps = policy_set_for((spec("exp-2"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.05")
    mark = market(
        last_close="100",
        step="1",
        minamt="1",
        tick="1",
        quote_unit="1",
        fee="0.001",
        asks=(("100", "5"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "100"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["insufficient_funds"]


def test_example_3_sell_walk_and_attempt_suffix():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    policy = ps.policies["OAS/JPY"]
    exit_ = opportunity("OAS/JPY", "sell", oid="opp-1", strategy_id="lab:exp-1:exit-1")
    held = {"OAS/JPY": HeldPosition("pos-abcdefgh", D("12.345"), D("12.3"))}
    mark = market(
        last_close="100",
        step="0.01",
        minamt="0.01",
        tick="0.001",
        bids=(("101.000", "5"), ("100.900", "10")),
    )

    result = size_orders(
        live_signals(exits=(exit_,)), ps, {"OAS/JPY": mark}, held, funds("5000", "2000", "5000"), now=NOW
    )

    assert result.skipped == ()
    plan = result.sells[0]
    assert plan.decision_key == f"live1:{policy.digest[:16]}:opp-1:pos-abcd:a0"
    assert plan.amount == D("12.30")
    assert plan.price == D("100.900")
    assert plan.required_quote == D("0")
    assert plan.side == "sell"
    assert plan.position_id == "pos-abcdefgh"
    assert plan.rule_id == "exit-1"
    assert plan.intent_request() == IntentRequest(
        decision_key=plan.decision_key,
        symbol="OAS/JPY",
        side="sell",
        order_type="limit",
        amount="12.3",
        price="100.9",
        policy_digest=policy.digest,
        rule_id="exit-1",
    )
    assert result.sells[0].amount * result.sells[0].price == D("1241.07")
    assert result.headroom_left == D("4241.07")

    retried = size_orders(
        live_signals(exits=(exit_,)),
        ps,
        {"OAS/JPY": mark},
        held,
        funds("5000", "2000", "5000"),
        now=NOW,
        sell_attempts={"opp-1": 2},
    )
    assert retried.sells[0].decision_key == f"live1:{policy.digest[:16]}:opp-1:pos-abcd:a2"

    exhausted = size_orders(
        live_signals(exits=(exit_,)),
        ps,
        {"OAS/JPY": mark},
        held,
        funds("5000", "2000", "5000"),
        now=NOW,
        sell_attempts={"opp-1": 5},
    )
    assert exhausted.sells == ()
    assert codes(exhausted) == ["sell_attempts_exhausted"]


def test_example_4_dust_versus_exchange_free_short():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    exit_ = opportunity("OAS/JPY", "sell", strategy_id="lab:exp-1:exit-1")
    mark = market(last_close="100", step="0.01", minamt="0.01", tick="0.001", bids=(("100", "10"),))

    dust = size_orders(
        live_signals(exits=(exit_,)),
        ps,
        {"OAS/JPY": mark},
        {"OAS/JPY": HeldPosition("dust-pos", D("0.005"), D("0.005"))},
        funds(),
        now=NOW,
    )
    assert dust.sells == ()
    assert codes(dust) == ["below_min_amount"]
    assert dust.residual_position_ids == ("dust-pos",)

    exchange_short = size_orders(
        live_signals(exits=(exit_,)),
        ps,
        {"OAS/JPY": mark},
        {"OAS/JPY": HeldPosition("short-pos", D("1.00"), D("0.005"))},
        funds(),
        now=NOW,
    )
    assert exchange_short.sells == ()
    assert codes(exchange_short) == ["exchange_free_short"]
    assert exchange_short.residual_position_ids == ()


# ---- (b) buy ordering and a funding cap that runs out mid-list --------------------------


def test_higher_score_buy_is_planned_first_and_consumes_the_funds():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    marks = {
        symbol: market(symbol, last_close="100", step="1", minamt="1", tick="1", fee="0.001",
                       asks=(("100", "10"),))
        for symbol in ("AAA/JPY", "BBB/JPY")
    }
    low = opportunity("BBB/JPY", "buy", oid="b-low", score="0.4")
    high = opportunity("AAA/JPY", "buy", oid="a-high", score="0.9")

    result = size_orders(
        live_signals(entries=(low, high)), ps, marks, {}, funds("1000", "0", "301.5"), now=NOW
    )

    assert [plan.opportunity_id for plan in result.buys] == ["a-high"]
    assert result.buys[0].required_quote == D("301.3")
    assert [(skip.opportunity_id, skip.reason_code) for skip in result.skipped] == [
        ("b-low", "insufficient_funds")
    ]
    assert result.headroom_left == D("700")


def test_equal_scores_tie_break_by_opportunity_id():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    marks = {
        symbol: market(symbol, last_close="100", step="1", minamt="1", tick="1", fee="0.001",
                       asks=(("100", "10"),))
        for symbol in ("AAA/JPY", "BBB/JPY")
    }
    second = opportunity("BBB/JPY", "buy", oid="b-second", score="0.5")
    first = opportunity("AAA/JPY", "buy", oid="a-first", score="0.5")

    result = size_orders(
        live_signals(entries=(second, first)), ps, marks, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert [plan.opportunity_id for plan in result.buys] == ["a-first", "b-second"]


# ---- (c) book cleaning -----------------------------------------------------------------


def test_equal_price_levels_are_merged():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    merged = market(last_close="100", asks=(("100", "3"), ("100.1", "5")))
    split = market(last_close="100", asks=(("100", "1"), ("100", "2"), ("100.1", "5")))

    a = size_orders(live_signals(entries=(entry,)), ps, {"OAS/JPY": merged}, {}, funds("1000", "0", "1000"), now=NOW)
    b = size_orders(live_signals(entries=(entry,)), ps, {"OAS/JPY": split}, {}, funds("1000", "0", "1000"), now=NOW)

    assert a.buys == b.buys
    assert a.buys[0].amount == D("3")
    assert a.buys[0].price == D("100")
    assert a.free_quote_left == b.free_quote_left


def test_unsorted_book_gives_the_same_plan():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    sorted_book = market(last_close="100", asks=(("100", "3"), ("100.1", "5"), ("100.2", "9")))
    shuffled = market(last_close="100", asks=(("100.2", "9"), ("100", "3"), ("100.1", "5")))

    a = size_orders(live_signals(entries=(entry,)), ps, {"OAS/JPY": sorted_book}, {}, funds("1000", "0", "1000"), now=NOW)
    b = size_orders(live_signals(entries=(entry,)), ps, {"OAS/JPY": shuffled}, {}, funds("1000", "0", "1000"), now=NOW)

    assert a.buys == b.buys
    assert a.buys[0].amount == D("3")
    assert a.buys[0].price == D("100")


def test_non_positive_amounts_are_ignored():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(last_close="100", asks=(("100", "0"), ("100.1", "5")))

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.skipped == ()
    assert result.buys[0].price == D("100.1")
    assert result.buys[0].amount == D("2.99")

    empty = market(last_close="100", asks=(("100", "0"),))
    none_left = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": empty}, {}, funds("1000", "0", "1000"), now=NOW
    )
    assert codes(none_left) == ["book_empty"]


# ---- (d) visible depth ----------------------------------------------------------------


def test_depth_exactly_meets_the_target_without_a_note():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(last_close="100", step="0.01", minamt="0.01", asks=(("100", "3"),))

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.skipped == ()
    assert result.buys[0].amount == D("3.00")
    assert result.buys[0].price == D("100")
    assert result.buys[0].notes == ()


def test_one_step_less_depth_reduces_the_amount_and_notes_it():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(last_close="100", step="0.01", minamt="0.01", asks=(("100", "2.99"),))

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.skipped == ()
    plan = result.buys[0]
    assert plan.amount == D("2.99")
    assert plan.price == D("100")
    assert plan.notes == ("depth_insufficient",)
    assert plan.required_quote == D("300.3588")  # 299 * 1.0012 + 1


def test_visible_depth_below_the_executable_minimum_is_skipped():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(last_close="100", step="1", minamt="5", tick="1", asks=(("100", "3"),))

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["depth_insufficient"]


# ---- (e) the premium cap ---------------------------------------------------------------


def test_levels_above_the_premium_cap_are_never_used():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(
        last_close="100", step="1", minamt="1", tick="0.5",
        asks=(("100.5", "1"), ("101.0", "100")),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.skipped == ()
    assert result.buys[0].price == D("100.5")  # floor_tick(100 * 1.005, 0.5)
    assert result.buys[0].amount == D("1")


def test_every_ask_above_the_cap_is_book_far_from_signal():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(last_close="100", step="1", minamt="1", tick="0.5", asks=(("101.0", "100"),))

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["book_far_from_signal"]


def test_off_tick_level_is_refused_even_at_the_cap():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(
        last_close="100", step="1", minamt="1", tick="0.5",
        asks=(("100.5", "1"), ("100.501", "1")),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["book_off_tick"]


def test_premium_cap_setting_changes_the_result():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")

    at_close = market(last_close="100", step="1", minamt="1", tick="0.5", asks=(("100", "5"),))
    at_cap = market(last_close="100", step="1", minamt="1", tick="0.5", asks=(("100.5", "5"),))

    default = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": at_cap}, {}, funds("1000", "0", "1000"), now=NOW
    )
    assert default.skipped == ()
    assert default.buys[0].price == D("100.5")

    zero_bps = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": at_cap}, {}, funds("1000", "0", "1000"), now=NOW,
        settings=SizingSettings(buy_premium_cap_bps=D("0")),
    )
    assert codes(zero_bps) == ["book_far_from_signal"]

    planned = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": at_close}, {}, funds("1000", "0", "1000"), now=NOW,
        settings=SizingSettings(buy_premium_cap_bps=D("0")),
    )
    assert planned.skipped == ()
    assert planned.buys[0].price == D("100")


# ---- (f) fees --------------------------------------------------------------------------


def test_negative_fee_rate_uses_the_absolute_value():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    asks = (("100.000", "10"), ("100.100", "20"), ("100.200", "100"))
    positive = market(last_close="100", fee="0.0012", asks=asks)
    rebate = market(last_close="100", fee="-0.0012", asks=asks)

    a = size_orders(live_signals(entries=(entry,)), ps, {"OAS/JPY": positive}, {}, funds("10000", "0", "10000"), now=NOW)
    b = size_orders(live_signals(entries=(entry,)), ps, {"OAS/JPY": rebate}, {}, funds("10000", "0", "10000"), now=NOW)

    assert a.buys == b.buys
    assert b.buys[0].required_quote == D("3004.5969964")
    assert a.free_quote_left == b.free_quote_left == D("6995.4030036")


def test_unknown_fee_rate_is_refused():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    entry = opportunity("OAS/JPY", "buy", fraction="0.30")
    mark = market(last_close="100", fee=None, asks=(("100", "5"),))

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert result.buys == ()
    assert codes(result) == ["fee_rate_unknown"]


# ---- (g) the per-symbol policy fraction ------------------------------------------------


def test_per_symbol_policy_fraction_sets_a_different_cap():
    ps = policy_set_for(
        (spec("exp-a", entry_fraction="0.10"), "AAA/JPY"),
        (spec("exp-b", entry_fraction="0.20"), "BBB/JPY"),
    )
    assert ps.policies["AAA/JPY"].max_opportunity_fraction == D("0.10")
    assert ps.policies["BBB/JPY"].max_opportunity_fraction == D("0.20")
    marks = {
        symbol: market(symbol, last_close="100", step="1", minamt="1", tick="1", fee="0.001",
                       asks=(("100", "10"),))
        for symbol in ("AAA/JPY", "BBB/JPY")
    }
    first = opportunity("AAA/JPY", "buy", oid="a", score="0.9", fraction="0.30")
    second = opportunity("BBB/JPY", "buy", oid="b", score="0.8", fraction="0.30")

    result = size_orders(
        live_signals(entries=(first, second)), ps, marks, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert [(plan.symbol, plan.amount) for plan in result.buys] == [
        ("AAA/JPY", D("1")),   # cap 1000 * min(0.10, 0.30) = 100
        ("BBB/JPY", D("2")),   # cap 1000 * min(0.20, 0.30) = 200
    ]
    assert result.headroom_left == D("700")
    assert result.free_quote_left == D("697.7")


# ---- (h) sell skips, duplicates, depth and proceeds ------------------------------------


def test_sell_skip_order_and_codes():
    ps = policy_set_for(
        (spec("exp-1"), "AAA/JPY"),
        (spec("exp-1"), "BBB/JPY"),
        (spec("exp-1"), "EEE/JPY"),
        (spec("exp-1"), "FFF/JPY"),
        (spec("exp-1"), "GGG/JPY"),
        (spec("exp-1"), "HHH/JPY"),
    )
    marks = {
        "AAA/JPY": market("AAA/JPY", bids=(("100", "10"),)),
        "BBB/JPY": market("BBB/JPY", bids=(("100", "10"),)),
        "FFF/JPY": market("FFF/JPY", bids=(("100", "10"),)),
        "GGG/JPY": market("GGG/JPY", bids=()),
        "HHH/JPY": market("HHH/JPY", tick="0.001", bids=(("100.0005", "10"),)),
    }
    held = {
        "EEE/JPY": HeldPosition("p-eee", D("1"), D("1")),
        "FFF/JPY": HeldPosition("p-fff", D("0"), D("0")),
        "GGG/JPY": HeldPosition("p-ggg", D("1"), D("1")),
        "HHH/JPY": HeldPosition("p-hhh", D("1"), D("1")),
    }
    exits = (
        opportunity("AAA/JPY", "sell", oid="e1", score="0.90", expires_at=NOW),
        opportunity("DDD/JPY", "sell", oid="e2", score="0.80"),
        opportunity("BBB/JPY", "sell", oid="e3", score="0.70"),
        opportunity("EEE/JPY", "sell", oid="e4", score="0.60"),
        opportunity("FFF/JPY", "sell", oid="e5", score="0.50"),
        opportunity("GGG/JPY", "sell", oid="e6", score="0.40"),
        opportunity("HHH/JPY", "sell", oid="e7", score="0.30"),
    )

    result = size_orders(live_signals(exits=exits), ps, marks, held, funds(), now=NOW)

    assert result.sells == ()
    assert [(skip.opportunity_id, str(skip.reason_code)) for skip in result.skipped] == [
        ("e1", "expired"),
        ("e2", "no_policy"),
        ("e3", "no_position"),
        ("e4", "market_unavailable"),
        ("e5", "no_inventory"),
        ("e6", "book_empty"),
        ("e7", "book_off_tick"),
    ]
    assert {skip.side for skip in result.skipped} == {"sell"}


def test_duplicate_exit_keeps_only_the_higher_score():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("5"), D("5"))}
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "10"),))
    high = opportunity("AAA/JPY", "sell", oid="high", score="0.9", strategy_id="lab:exp-1:exit-1")
    low = opportunity("AAA/JPY", "sell", oid="low", score="0.5", strategy_id="lab:exp-1:exit-1")

    result = size_orders(live_signals(exits=(low, high)), ps, {"AAA/JPY": mark}, held, funds(), now=NOW)

    assert [plan.opportunity_id for plan in result.sells] == ["high"]
    assert [(skip.opportunity_id, str(skip.reason_code)) for skip in result.skipped] == [
        ("low", "duplicate_exit")
    ]
    assert result.skipped[0].side == "sell"


def test_sell_depth_shortfall_reduces_the_amount_to_the_worst_level():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "5"), ("99", "4"),))

    result = size_orders(live_signals(exits=(opportunity("AAA/JPY", "sell"),)), ps,
                         {"AAA/JPY": mark}, held, funds(), now=NOW)

    assert result.skipped == ()
    plan = result.sells[0]
    assert (plan.amount, plan.price) == (D("9"), D("99"))
    assert plan.notes == ("depth_insufficient",)


def test_sell_depth_below_the_minimum_is_skipped():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("99", "0.5"),))

    result = size_orders(live_signals(exits=(opportunity("AAA/JPY", "sell"),)), ps,
                         {"AAA/JPY": mark}, held, funds(), now=NOW)

    assert result.sells == ()
    assert codes(result) == ["depth_insufficient"]
    assert result.residual_position_ids == ()


def test_sell_proceeds_raise_headroom_but_not_free_quote():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    marks = {
        "AAA/JPY": market("AAA/JPY", last_close="100", step="1", minamt="1", tick="1",
                          bids=(("100", "10"),)),
        "BBB/JPY": market("BBB/JPY", last_close="100", step="1", minamt="1", tick="1", fee="0.001",
                          asks=(("100", "5"),), quote_unit="1"),
    }
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}
    sell = opportunity("AAA/JPY", "sell", oid="exit-aaa", score="0.1", strategy_id="lab:exp-1:exit-1")
    buy = opportunity("BBB/JPY", "buy", oid="entry-bbb", score="0.9", fraction="0.30")

    # C=1000, D=950 -> headroom 50: the buy alone cannot reach the exchange minimum.
    without_sell = size_orders(
        live_signals(entries=(buy,)), ps, marks, held, funds("1000", "950", "1000"), now=NOW
    )
    assert codes(without_sell) == ["total_cap_exhausted"]

    with_sell = size_orders(
        live_signals(entries=(buy,), exits=(sell,)), ps, marks, held, funds("1000", "950", "1000"), now=NOW
    )
    assert [plan.symbol for plan in with_sell.sells] == ["AAA/JPY"]
    assert [plan.symbol for plan in with_sell.buys] == ["BBB/JPY"]
    assert with_sell.buys[0].required_quote == D("301.3")
    assert with_sell.headroom_left == D("700")

    # F=100 is the binding limit: proceeds must NOT raise free_quote.
    limited = size_orders(
        live_signals(entries=(buy,)), ps, marks, held, funds("1000", "0", "100"), now=NOW
    )
    assert codes(limited) == ["insufficient_funds"]

    still_limited = size_orders(
        live_signals(entries=(buy,), exits=(sell,)), ps, marks, held, funds("1000", "0", "100"), now=NOW
    )
    assert [plan.symbol for plan in still_limited.sells] == ["AAA/JPY"]
    assert codes(still_limited) == ["insufficient_funds"]
    assert still_limited.free_quote_left == D("100")
    assert still_limited.headroom_left == D("1000")


# ---- (i) buy skips ---------------------------------------------------------------------


def test_buys_blocked_skips_every_entry_but_still_plans_sells():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    marks = {
        "AAA/JPY": market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "10"),)),
        "BBB/JPY": market("BBB/JPY", step="1", minamt="1", tick="1", fee="0.001",
                          asks=(("100", "10"),)),
    }
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("5"), D("5"))}
    exit_ = opportunity("AAA/JPY", "sell", oid="exit-aaa", strategy_id="lab:exp-1:exit-1")
    first = opportunity("BBB/JPY", "buy", oid="b1", score="0.9")
    second = opportunity("BBB/JPY", "buy", oid="b2", score="0.8")

    result = size_orders(
        live_signals(entries=(first, second), exits=(exit_,)),
        ps, marks, held, funds(), now=NOW, buys_blocked=True,
    )

    assert [plan.opportunity_id for plan in result.sells] == ["exit-aaa"]
    assert result.buys == ()
    assert [(skip.opportunity_id, str(skip.reason_code)) for skip in result.skipped] == [
        ("b1", "buys_blocked_safety"),
        ("b2", "buys_blocked_safety"),
    ]
    assert {skip.side for skip in result.skipped} == {"buy"}


def test_already_decided_and_open_buy_exists():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    policy = ps.policies["AAA/JPY"]
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", fee="0.001", asks=(("100", "10"),))
    entry = opportunity("AAA/JPY", "buy", oid="opp-1")

    decided = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW,
        decided_keys={f"live1:{policy.digest[:16]}:opp-1"},
    )
    assert codes(decided) == ["already_decided"]
    assert decided.buys == ()

    open_symbol = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW,
        open_buy_symbols={"AAA/JPY"},
    )
    assert codes(open_symbol) == ["open_buy_exists"]

    twice = size_orders(
        live_signals(entries=(entry, opportunity("AAA/JPY", "buy", oid="opp-2", score="0.4"))),
        ps, {"AAA/JPY": mark}, {}, funds("1000", "0", "1000"), now=NOW,
    )
    assert [plan.opportunity_id for plan in twice.buys] == ["opp-1"]
    assert codes(twice) == ["open_buy_exists"]


def test_no_capital_recorded_and_market_unavailable():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", fee="0.001", asks=(("100", "10"),))

    no_capital = size_orders(
        live_signals(entries=(opportunity("AAA/JPY", "buy"),)), ps, {"AAA/JPY": mark}, {},
        funds("0", "0", "1000"), now=NOW,
    )
    assert codes(no_capital) == ["no_capital_recorded"]

    no_market = size_orders(
        live_signals(entries=(opportunity("BBB/JPY", "buy"),)), ps, {"AAA/JPY": mark}, {},
        funds("1000", "0", "1000"), now=NOW,
    )
    assert codes(no_market) == ["market_unavailable"]


def test_expired_boundary_is_inclusive():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    marks = {
        symbol: market(symbol, step="1", minamt="1", tick="1", fee="0.001", asks=(("100", "10"),))
        for symbol in ("AAA/JPY", "BBB/JPY")
    }
    expired = opportunity("AAA/JPY", "buy", oid="expired", score="0.9", expires_at=NOW)
    alive = opportunity("BBB/JPY", "buy", oid="alive", score="0.8", expires_at=NOW + 0.0001)

    result = size_orders(
        live_signals(entries=(expired, alive)), ps, marks, {}, funds("1000", "0", "1000"), now=NOW
    )

    assert [plan.opportunity_id for plan in result.buys] == ["alive"]
    assert [(skip.opportunity_id, str(skip.reason_code)) for skip in result.skipped] == [
        ("expired", "expired")
    ]


def test_zero_headroom_and_zero_last_close():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    entry = opportunity("AAA/JPY", "buy")
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", fee="0.001", asks=(("100", "10"),))

    no_headroom = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {}, funds("1000", "1000", "1000"), now=NOW
    )
    assert codes(no_headroom) == ["total_cap_exhausted"]
    assert no_headroom.headroom_left == D("0")

    zero_close = SymbolMarket(rules=mark.rules, book=mark.book, last_close=D("0"))
    buy = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": zero_close}, {}, funds("1000", "0", "1000"), now=NOW
    )
    assert codes(buy) == ["market_unavailable"]

    sell = size_orders(
        live_signals(exits=(opportunity("AAA/JPY", "sell"),)), ps, {"AAA/JPY": zero_close},
        {"AAA/JPY": HeldPosition("pos-aaa", D("1"), D("1"))}, funds(), now=NOW,
    )
    assert codes(sell) == ["market_unavailable"]


# ---- (j) intent_request and settings validation ----------------------------------------


def test_intent_request_carries_the_limit_order_fields():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    policy = ps.policies["OAS/JPY"]
    entry = opportunity("OAS/JPY", "buy", oid="opp-1", fraction="0.30")
    mark = market(
        last_close="100", fee="0.0012",
        asks=(("100.000", "10"), ("100.100", "20"), ("100.200", "100")),
    )
    result = size_orders(
        live_signals(entries=(entry,)), ps, {"OAS/JPY": mark}, {}, funds("10000", "0", "10000"), now=NOW
    )
    plan = result.buys[0]

    request = plan.intent_request()
    assert request == IntentRequest(
        decision_key=plan.decision_key,
        symbol="OAS/JPY",
        side="buy",
        order_type="limit",
        amount="29.97",
        price="100.1",
        policy_digest=policy.digest,
        rule_id="entry-1",
    )
    assert plan.ttl_s == 45


def test_ttl_comes_from_the_settings():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    mark = market(last_close="100", asks=(("100", "10"),))
    result = size_orders(
        live_signals(entries=(opportunity("OAS/JPY", "buy"),)), ps, {"OAS/JPY": mark}, {},
        funds(), now=NOW, settings=SizingSettings(order_ttl_s=7),
    )
    assert result.buys[0].ttl_s == 7
    assert result.buys[0].intent_request().order_type == "limit"


def test_sizing_settings_validation():
    assert SizingSettings().buy_premium_cap_bps == D("50")
    assert SizingSettings(order_ttl_s=1, max_sell_attempts=1).order_ttl_s == 1
    assert SizingSettings(buy_premium_cap_bps=D("0")).buy_premium_cap_bps == D("0")
    for bad in (
        dict(buy_premium_cap_bps=D("-1")),
        dict(buy_premium_cap_bps=D("NaN")),
        dict(buy_premium_cap_bps=D("Infinity")),
        dict(buy_premium_cap_bps=50.0),
        dict(buy_premium_cap_bps=True),
        dict(buy_premium_cap_bps="50"),
        dict(order_ttl_s=0),
        dict(order_ttl_s=-1),
        dict(order_ttl_s=True),
        dict(order_ttl_s=45.0),
        dict(max_sell_attempts=0),
        dict(max_sell_attempts=True),
    ):
        with pytest.raises(ValueError):
            SizingSettings(**bad)


def test_rounding_helpers():
    assert floor_step(D("2.345"), D("0.01")) == D("2.34")
    assert floor_step(D("-2.345"), D("0.01")) == D("0")
    assert floor_step(D("0"), D("0.01")) == D("0")
    assert ceil_step(D("2.345"), D("0.01")) == D("2.35")
    assert ceil_step(D("2.34"), D("0.01")) == D("2.34")
    assert floor_tick(D("100.501"), D("0.5")) == D("100.5")
    assert floor_tick(D("100.5"), D("0.5")) == D("100.5")


# ---- (k) purity ------------------------------------------------------------------------


def test_repeated_calls_are_equal_and_inputs_are_not_mutated(tmp_path, monkeypatch):
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"))
    marks = {
        "AAA/JPY": market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "10"),)),
        "BBB/JPY": market("BBB/JPY", step="1", minamt="1", tick="1", fee="0.001",
                          asks=(("100", "10"),)),
    }
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("5"), D("5"))}
    exit_ = opportunity("AAA/JPY", "sell", oid="exit-aaa", strategy_id="lab:exp-1:exit-1")
    entry = opportunity("BBB/JPY", "buy", oid="entry-bbb")
    live = live_signals(entries=(entry,), exits=(exit_,))
    cash = funds("1000", "500", "1000")
    decided = {"live1:x:y"}
    open_symbols = {"CCC/JPY"}
    attempts = {"exit-aaa": 1}

    markets_before = dict(marks)
    held_before = dict(held)
    decided_before = set(decided)
    open_before = set(open_symbols)
    attempts_before = dict(attempts)
    signals_before = live

    monkeypatch.chdir(tmp_path)
    before_files = sorted(os.listdir("."))

    first = size_orders(live, ps, marks, held, cash, now=NOW, decided_keys=decided,
                        open_buy_symbols=open_symbols, sell_attempts=attempts)
    second = size_orders(live, ps, marks, held, cash, now=NOW, decided_keys=decided,
                         open_buy_symbols=open_symbols, sell_attempts=attempts)

    assert first == second
    assert dict(marks) == markets_before
    assert dict(held) == held_before
    assert set(decided) == decided_before
    assert set(open_symbols) == open_before
    assert dict(attempts) == attempts_before
    assert live == signals_before
    assert sorted(os.listdir(".")) == before_files == []
    assert not [name for name in os.listdir(tmp_path)]


def test_float_inputs_are_refused():
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    mark = market("OAS/JPY", asks=(("100", "10"),))
    entry = opportunity("OAS/JPY", "buy")
    live = live_signals(entries=(entry,))

    for bad_funds in (
        Funds(D("1000"), D("0"), 1000.0),
        Funds(1000.0, D("0"), D("1000")),
        Funds(D("1000"), 0.0, D("1000")),
    ):
        with pytest.raises(ValueError):
            size_orders(live, ps, {"OAS/JPY": mark}, {}, bad_funds, now=NOW)

    float_close = SymbolMarket(rules=mark.rules, book=mark.book, last_close=100.0)
    with pytest.raises(ValueError):
        size_orders(live, ps, {"OAS/JPY": float_close}, {}, funds(), now=NOW)

    float_held = {"OAS/JPY": HeldPosition("pos", 1.0, D("1"))}
    with pytest.raises(ValueError):
        size_orders(
            live_signals(exits=(opportunity("OAS/JPY", "sell"),)),
            ps, {"OAS/JPY": mark}, float_held, funds(), now=NOW,
        )

    with pytest.raises(ValueError):
        size_orders(
            live_signals(exits=(opportunity("OAS/JPY", "sell"),)), ps, {"OAS/JPY": mark},
            {"OAS/JPY": HeldPosition("pos", D("1"), D("1"))}, funds(), now=NOW,
            sell_attempts={"opp-1": 1.0},
        )


def test_import_is_free_of_network_and_paper_modules(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        # docich/trading/__init__.py imports docich.trading.risk before any LIVE submodule
        # runs, so drop it and observe whether importing sizing pulls it back in.
        import docich.trading
        sys.modules.pop("docich.trading.risk", None)
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.sizing
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests", "socket")
               if m in sys.modules]
        assert not bad, bad
        forbidden = [m for m in ("docich.trading.risk", "docich.trading.worker",
                                 "docich.trading.slot_runtime", "docich.trading.cli")
                     if m in sys.modules]
        assert not forbidden, forbidden
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(SRC)),
    )
    assert out.stdout.strip() == "clean", out.stderr


# ---- (m) the closed skip-code set ------------------------------------------------------


def test_skip_codes_are_the_documented_closed_set():
    assert SKIP_CODES == frozenset({
        "expired", "no_policy", "no_position", "duplicate_exit", "market_unavailable",
        "sell_attempts_exhausted", "no_inventory", "below_min_amount", "exchange_free_short",
        "book_empty", "book_off_tick", "depth_insufficient", "buys_blocked_safety",
        "already_decided", "open_buy_exists", "no_capital_recorded", "fee_rate_unknown",
        "book_far_from_signal", "total_cap_exhausted", "insufficient_funds", "open_sell_exists",
    })
    assert len(SKIP_CODES) == 21
    assert isinstance(SKIP_CODES, frozenset)


# ---- (n) exact boundaries (rounding direction and < / <=) -------------------------------


def test_sell_exactly_at_the_minimum_is_planned():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    exit_ = opportunity("AAA/JPY", "sell", strategy_id="lab:exp-1:exit-1")
    mark = market(
        "AAA/JPY", step="0.01", minamt="0.05", tick="1", quote_unit="1",
        bids=(("100", "10"),),
    )

    at_min = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("0.05"), D("0.05"))}, funds(), now=NOW,
    )
    assert at_min.skipped == ()
    assert len(at_min.sells) == 1
    assert (at_min.sells[0].amount, at_min.sells[0].price) == (D("0.05"), D("100"))
    assert at_min.residual_position_ids == ()

    free_at_min = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("1"), D("0.05"))}, funds(), now=NOW,
    )
    assert free_at_min.skipped == ()
    assert (free_at_min.sells[0].amount, free_at_min.sells[0].price) == (D("0.05"), D("100"))

    free_short = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("1"), D("0.049"))}, funds(), now=NOW,
    )
    assert free_short.sells == ()
    assert codes(free_short) == ["exchange_free_short"]
    assert free_short.residual_position_ids == ()

    ledger_dust = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("0.049"), D("0.049"))}, funds(), now=NOW,
    )
    assert ledger_dust.sells == ()
    assert codes(ledger_dust) == ["below_min_amount"]
    assert ledger_dust.residual_position_ids == ("pos-aaa",)


def test_sell_amount_is_the_floor_of_the_smaller_of_ledger_and_exchange():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    exit_ = opportunity("AAA/JPY", "sell", strategy_id="lab:exp-1:exit-1")
    mark = market(
        "AAA/JPY", step="0.01", minamt="0.05", tick="1", quote_unit="1",
        bids=(("100", "50"),),
    )

    for net, free in (("12.345", "20"), ("20", "12.345"), ("12.345", "12.345")):
        result = size_orders(
            live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
            {"AAA/JPY": HeldPosition("pos-aaa", D(net), D(free))}, funds(), now=NOW,
        )
        assert result.skipped == (), (net, free)
        observed = (result.sells[0].amount, result.sells[0].price)
        assert observed == (D("12.34"), D("100")), (net, free)


def test_sell_walk_stops_exactly_at_the_needed_amount():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    exit_ = opportunity("AAA/JPY", "sell", strategy_id="lab:exp-1:exit-1")
    mark = market(
        "AAA/JPY", step="1", minamt="1", tick="1", quote_unit="1",
        bids=(("100", "5"), ("99", "5")),
    )

    at_first_level = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("5"), D("5"))}, funds(), now=NOW,
    )
    assert at_first_level.skipped == ()
    assert (at_first_level.sells[0].amount, at_first_level.sells[0].price) == (D("5"), D("100"))
    assert at_first_level.sells[0].notes == ()

    at_second_level = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}, funds(), now=NOW,
    )
    assert at_second_level.skipped == ()
    assert (at_second_level.sells[0].amount, at_second_level.sells[0].price) == (D("10"), D("99"))
    assert at_second_level.sells[0].notes == ()

    inside_second_level = size_orders(
        live_signals(exits=(exit_,)), ps, {"AAA/JPY": mark},
        {"AAA/JPY": HeldPosition("pos-aaa", D("6"), D("6"))}, funds(), now=NOW,
    )
    assert inside_second_level.skipped == ()
    assert (inside_second_level.sells[0].amount, inside_second_level.sells[0].price) == (
        D("6"), D("99")
    )
    assert inside_second_level.sells[0].notes == ()


def test_sell_depth_exactly_at_the_minimum():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    exit_ = opportunity("AAA/JPY", "sell", strategy_id="lab:exp-1:exit-1")
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("1"), D("1"))}

    at_min = market(
        "AAA/JPY", step="0.01", minamt="0.05", tick="1", quote_unit="1",
        bids=(("100", "0.05"),),
    )
    planned = size_orders(live_signals(exits=(exit_,)), ps, {"AAA/JPY": at_min}, held,
                          funds(), now=NOW)
    assert planned.skipped == ()
    assert len(planned.sells) == 1
    assert (planned.sells[0].amount, planned.sells[0].price) == (D("0.05"), D("100"))
    assert planned.sells[0].notes == ("depth_insufficient",)

    below_min = market(
        "AAA/JPY", step="0.01", minamt="0.05", tick="1", quote_unit="1",
        bids=(("100", "0.04"),),
    )
    skipped = size_orders(live_signals(exits=(exit_,)), ps, {"AAA/JPY": below_min}, held,
                          funds(), now=NOW)
    assert skipped.sells == ()
    assert codes(skipped) == ["depth_insufficient"]


def test_buy_limited_by_free_quote_with_unit_and_fee():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    entry = opportunity("AAA/JPY", "buy", fraction="0.30")
    mark = market(
        "AAA/JPY", last_close="100", step="0.01", minamt="0.01", tick="1",
        quote_unit="1", fee="0.001", asks=(("100", "1000"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("100000", "0", "500"), now=NOW,
    )

    assert result.skipped == ()
    assert len(result.buys) == 1
    plan = result.buys[0]
    assert (plan.amount, plan.price) == (D("4.98"), D("100"))
    assert plan.required_quote == D("499.498")
    assert plan.notes == ()
    assert result.free_quote_left == D("0.502")
    assert result.headroom_left == D("99502")


def test_buy_depth_shortfall_over_several_levels_uses_the_worst_level():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    entry = opportunity("AAA/JPY", "buy", fraction="0.30")
    mark = market(
        "AAA/JPY", last_close="101", step="1", minamt="1", tick="1",
        quote_unit="1", fee="0.001", asks=(("100", "2"), ("101", "3")),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("10000", "0", "10000"), now=NOW,
    )

    assert result.skipped == ()
    plan = result.buys[0]
    assert (plan.amount, plan.price) == (D("5"), D("101"))
    assert plan.notes == ("depth_insufficient",)
    assert plan.required_quote == D("506.505")
    assert result.headroom_left == D("9495")
    assert result.free_quote_left == D("9493.495")


def test_buy_amount_exactly_the_minimum_is_not_a_round_up():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    entry = opportunity("AAA/JPY", "buy", fraction="0.10")
    mark = market(
        "AAA/JPY", last_close="100", step="1", minamt="1", tick="1",
        quote_unit="1", fee="0.001", asks=(("100", "10"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("1000", "0", "1000"), now=NOW,
    )

    assert result.skipped == ()
    plan = result.buys[0]
    assert (plan.amount, plan.price) == (D("1"), D("100"))
    assert plan.notes == ()
    assert plan.required_quote == D("101.1")


def test_round_up_when_the_visible_depth_equals_the_minimum():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    entry = opportunity("AAA/JPY", "buy", fraction="0.001")
    mark = market(
        "AAA/JPY", last_close="100", step="1", minamt="5", tick="1",
        quote_unit="1", fee="0.001", asks=(("100", "5"),),
    )

    result = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("10000", "0", "10000"), now=NOW,
    )

    assert result.skipped == ()
    plan = result.buys[0]
    assert (plan.amount, plan.price) == (D("5"), D("100"))
    assert plan.notes == ("min_amount_rounded_up",)
    assert plan.required_quote == D("501.5")

    thin = market(
        "AAA/JPY", last_close="100", step="1", minamt="5", tick="1",
        quote_unit="1", fee="0.001", asks=(("100", "4"),),
    )
    short = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": thin}, {},
        funds("10000", "0", "10000"), now=NOW,
    )
    assert short.buys == ()
    assert codes(short) == ["depth_insufficient"]


def test_round_up_boundaries_hard_cap_headroom_and_funds():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    entry = opportunity("AAA/JPY", "buy", fraction="0.01")
    mark = market(
        "AAA/JPY", last_close="100", step="1", minamt="3", tick="1",
        quote_unit="1", fee="0.001", asks=(("100", "100"),),
    )

    hard_cap_at_min = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("1000", "700", "1000"), now=NOW,
    )
    assert hard_cap_at_min.skipped == ()
    plan = hard_cap_at_min.buys[0]
    assert (plan.amount, plan.price) == (D("3"), D("100"))
    assert plan.notes == ("min_amount_rounded_up",)
    assert plan.required_quote == D("301.3")
    assert hard_cap_at_min.headroom_left == D("0")
    assert hard_cap_at_min.free_quote_left == D("698.7")

    exactly_funded = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("1000", "0", D("301.3")), now=NOW,
    )
    assert exactly_funded.skipped == ()
    assert exactly_funded.buys[0].required_quote == D("301.3")
    assert exactly_funded.free_quote_left == D("0")

    one_unit_short = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("1000", "0", D("301.2")), now=NOW,
    )
    assert one_unit_short.buys == ()
    assert codes(one_unit_short) == ["insufficient_funds"]

    headroom_short = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("1000", "701", "1000"), now=NOW,
    )
    assert headroom_short.buys == ()
    assert codes(headroom_short) == ["total_cap_exhausted"]

    hard_cap_short = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": mark}, {},
        funds("999", "0", "1000"), now=NOW,
    )
    assert hard_cap_short.buys == ()
    assert codes(hard_cap_short) == ["below_min_amount"]


# ---- (o) an open sell blocks a second sell for the symbol ------------------------------


def test_open_sell_blocks_a_second_sell_for_the_symbol():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "50"),))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}
    exits = live_signals(exits=(opportunity("AAA/JPY", "sell"),))

    blocked = size_orders(
        exits, ps, {"AAA/JPY": mark}, held, funds(), now=NOW,
        open_sell_symbols={"AAA/JPY"},
    )

    assert blocked.sells == ()
    assert codes(blocked) == ["open_sell_exists"]
    assert blocked.skipped[0].side == "sell"
    assert blocked.residual_position_ids == ()

    allowed = size_orders(exits, ps, {"AAA/JPY": mark}, held, funds(), now=NOW)

    assert allowed.skipped == ()
    assert len(allowed.sells) == 1
    assert (allowed.sells[0].amount, allowed.sells[0].price) == (D("10"), D("100"))


def test_open_sell_of_another_symbol_does_not_block():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-2"), "BBB/JPY"))
    marks = {
        symbol: market(symbol, step="1", minamt="1", tick="1", bids=(("100", "50"),))
        for symbol in ("AAA/JPY", "BBB/JPY")
    }
    held = {
        "AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10")),
        "BBB/JPY": HeldPosition("pos-bbb", D("10"), D("10")),
    }
    exits = (
        opportunity("AAA/JPY", "sell", oid="exit-aaa"),
        opportunity("BBB/JPY", "sell", oid="exit-bbb"),
    )

    result = size_orders(
        live_signals(exits=exits), ps, marks, held, funds(), now=NOW,
        open_sell_symbols={"BBB/JPY"},
    )

    assert [plan.symbol for plan in result.sells] == ["AAA/JPY"]
    assert codes(result) == ["open_sell_exists"]
    assert result.skipped[0].symbol == "BBB/JPY"


def test_open_sell_check_comes_after_expiry_policy_and_position_checks():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "50"),))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}

    expired = size_orders(
        live_signals(exits=(opportunity("AAA/JPY", "sell", expires_at=NOW),)),
        ps, {"AAA/JPY": mark}, held, funds(), now=NOW,
        open_sell_symbols={"AAA/JPY"},
    )
    assert codes(expired) == ["expired"]

    no_policy = size_orders(
        live_signals(exits=(opportunity("BBB/JPY", "sell"),)),
        ps, {"AAA/JPY": mark}, held, funds(), now=NOW,
        open_sell_symbols={"BBB/JPY"},
    )
    assert codes(no_policy) == ["no_policy"]

    no_position = size_orders(
        live_signals(exits=(opportunity("AAA/JPY", "sell"),)),
        ps, {"AAA/JPY": mark}, {}, funds(), now=NOW,
        open_sell_symbols={"AAA/JPY"},
    )
    assert codes(no_position) == ["no_position"]


def test_open_sell_and_open_buy_are_independent():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    buy_market = market(
        "AAA/JPY", last_close="100", step="1", minamt="1", tick="1", asks=(("100", "50"),),
    )
    entry = opportunity("AAA/JPY", "buy")

    bought = size_orders(
        live_signals(entries=(entry,)), ps, {"AAA/JPY": buy_market}, {},
        funds("10000", "0", "10000"), now=NOW,
        open_sell_symbols={"AAA/JPY"},
    )

    assert bought.skipped == ()
    assert len(bought.buys) == 1
    assert (bought.buys[0].amount, bought.buys[0].price) == (D("30"), D("100"))

    sell_market = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "50"),))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}

    sold = size_orders(
        live_signals(exits=(opportunity("AAA/JPY", "sell"),)),
        ps, {"AAA/JPY": sell_market}, held, funds(), now=NOW,
        open_buy_symbols={"AAA/JPY"},
    )

    assert sold.skipped == ()
    assert len(sold.sells) == 1
    assert (sold.sells[0].amount, sold.sells[0].price) == (D("10"), D("100"))


def test_open_sell_does_not_touch_headroom():
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"))
    mark = market("AAA/JPY", step="1", minamt="1", tick="1", bids=(("100", "50"),))
    held = {"AAA/JPY": HeldPosition("pos-aaa", D("10"), D("10"))}

    result = size_orders(
        live_signals(exits=(opportunity("AAA/JPY", "sell"),)),
        ps, {"AAA/JPY": mark}, held, funds("10000", "2000", "10000"), now=NOW,
        open_sell_symbols={"AAA/JPY"},
    )

    assert codes(result) == ["open_sell_exists"]
    assert result.sells == ()
    assert result.headroom_left == D("8000")
    assert result.headroom_left == max(D("0"), D("10000") - D("2000"))
