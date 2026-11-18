"""Cycle inputs built from a real LIVE ledger (Issue #3623, L4b-5).

Every number is pinned against the hand calculation in the task; money is ``Decimal``
and compared with ``==``.  The ledger is a real SQLite ``LiveLedger`` created through the
same harness as the views tests; the policies use the builders of the sizing tests.
Nothing here touches the network.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import inputs, views
from docich.trading.live.exchange import AssetBalance, BalanceSnapshot
from docich.trading.live.inputs import build_cycle_inputs, sell_attempts_for
from docich.trading.live.ledger import FillRecord, IntentRequest, OrderObservation
from docich.trading.live.policy import build_policy, policy_set_from_policies
from docich.trading.live.sizing import Funds, HeldPosition
from docich.trading.models import Opportunity
from docich.trading.strategy_lab import Condition, Rule, StrategyExperiment

from test_trading_live_support import Harness, Workspace, block_network, market_rules

SRC = Path(inputs.__file__).parents[3]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


@pytest.fixture
def harnesses(ws):
    made = []

    def make(**kw):
        harness = Harness(ws, **kw)
        made.append(harness)
        return harness

    yield make
    for harness in made:
        harness.close()


@pytest.fixture
def h(harnesses):
    return harnesses()


# ---- builders: same policies as tests/test_trading_live_sizing.py ---------------------------
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


def rules_for(*symbols, fee: str = "0.001"):
    """The task's rules: max_fee_rate 0.001, quote_unit 1, amount_step/base_unit as neighbours."""
    return {
        symbol: market_rules(
            symbol,
            max_fee_rate=None if fee is None else D(fee),
            quote_unit=D("1"),
        )
        for symbol in symbols
    }


def snapshot(*, jpy_free: str | None = None, jpy_total: str | None = None, free=None):
    """A freshly fetched balance snapshot (JPY absent when ``jpy_free`` is None)."""
    assets = {}
    if jpy_free is not None:
        total = jpy_free if jpy_total is None else jpy_total
        assets["JPY"] = AssetBalance(D(total), D(jpy_free), D(total) - D(jpy_free))
    for currency, amount in (free or {}).items():
        assets[currency] = AssetBalance(D(amount), D(amount), D("0"))
    return BalanceSnapshot(assets, 1_700_000_000_000, 1000.0)


def jpy_balance(total: str, free: str) -> BalanceSnapshot:
    """A JPY-only snapshot with a consistent locked = total - free."""
    return BalanceSnapshot({"JPY": AssetBalance(D(total), D(free), D(total) - D(free))},
                           1_700_000_000_000, 1000.0)


def opportunity(symbol: str, side: str, *, oid: str = "opp-1") -> Opportunity:
    return Opportunity(
        opportunity_id=oid,
        strategy_id="lab:exp-1:exit-1",
        symbol=symbol,
        side=side,
        score=D("0.5"),
        expected_edge_bps=D("10"),
        max_notional_fraction=D("0.10"),
        expires_at=1_800_000_060.0,
        reason_code="exit_signal",
    )


# ---- ledger helpers: every row goes through the ledger's public methods ---------------------
def _store(h, ps):
    h.ledger.store_policy_set(h.lease, ps, now=1000.0)


def _intent(h, ps, *, key, side="buy", symbol="OAS/JPY", amount="10", price="100", now=1000.0):
    return h.ledger.create_intent(
        h.lease,
        IntentRequest(
            decision_key=key,
            symbol=symbol,
            side=side,
            order_type="limit",
            amount=amount,
            price=price,
            policy_digest=ps.policy_for(symbol).digest,
            rule_id="entry-1" if side == "buy" else "exit-1",
        ),
        now=now,
    )


def _observe(h, intent, *, order_id, amount="10", price="100", status="FULLY_FILLED", executed="0"):
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(
            exchange_order_id=order_id,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type="limit",
            amount=amount,
            status=status,
            executed_amount=executed,
            remaining_amount=str(D(amount) - D(executed)),
            price=price,
        ),
        now=1000.0,
    )


def _fill(h, *, fill_id, order_id, symbol, side, amount, price, executed_at,
          fee_status="known", fee_amount=None, fee_currency=None):
    if fee_status == "known":
        fee_amount = "0" if fee_amount is None else fee_amount
        fee_currency = symbol.split("/")[1] if fee_currency is None else fee_currency
    else:
        fee_amount, fee_currency = None, None
    h.ledger.record_fill(
        h.lease,
        FillRecord(
            fill_id=fill_id,
            exchange_order_id=order_id,
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            quote_notional=str(D(amount) * D(price)),
            executed_at=executed_at,
            fee_status=fee_status,
            fee_amount=fee_amount,
            fee_currency=fee_currency,
        ),
        now=executed_at,
    )


def _to_acked(h, intent, *, order_id=None, now=1001.0):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=now)
    return h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                                      to_state="ACKED", now=now, exchange_order_id=order_id)


def _finish(h, intent, *, state="FILLED", now=1001.0):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state=state, now=now)


# ---- a. funds with an open buy: notional deployed, reserved quote --------------------------
def test_funds_with_an_open_buy_reserve_and_outstanding_notional(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    _to_acked(h, _intent(h, ps, key="live1:acked"))

    rules = rules_for("OAS/JPY")
    result = build_cycle_inputs(h.ledger, rules=rules,
                                balance=jpy_balance("10000", "8998"), policy_set=ps)

    # C = jpy_total 10000 + no position; D = 0 deployed cost + 1000 outstanding notional;
    # F = the exchange's free JPY (the locked 1002 is not spendable, but C includes it).
    assert result.funds == Funds(D("10000"), D("1000"), D("8998"))
    assert result.funds.capital - result.funds.deployed == D("9000")   # 10000 - 1000
    assert result.positions == {}
    assert result.held == {}
    assert result.open_buy_symbols == frozenset({"OAS/JPY"})
    assert result.block_buys == ()
    assert result.problems == ()

    larger = build_cycle_inputs(h.ledger, rules=rules,
                                balance=jpy_balance("10000", "9500"), policy_set=ps)
    assert larger.funds == Funds(D("10000"), D("1000"), D("9500"))

    smaller = build_cycle_inputs(h.ledger, rules=rules,
                                 balance=jpy_balance("10000", "8000"), policy_set=ps)
    assert smaller.funds.free_quote == D("8000")

    absent = build_cycle_inputs(h.ledger, rules=rules, balance=snapshot(), policy_set=ps)
    assert absent.funds == Funds(D("0"), D("1000"), D("0"))


# ---- b. funds and the position of a filled buy --------------------------------------------
def test_funds_with_a_filled_position(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    intent = _intent(h, ps, key="live1:entry")
    _observe(h, intent, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="1", fee_currency="JPY")
    _finish(h, intent)
    digest = ps.policy_for("OAS/JPY").digest

    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=snapshot(jpy_free="8999", free={"OAS": "10"}),
                                policy_set=ps)

    # 1000 notional + 1 quote fee = 1001; expected JPY 10000 - 1000 - 1 = 8999.
    assert result.funds == Funds(D("10000"), D("1001"), D("8999"))
    assert result.positions["OAS/JPY"] == views.CostBasis(D("10"), D("1001"), D("100.1"), 1000.0)
    assert result.held["OAS/JPY"] == HeldPosition(intent.position_id, D("10"), D("10"), digest)
    assert result.open_buy_symbols == frozenset()
    assert result.open_sell_symbols == frozenset()
    assert result.legacy_position_symbols == frozenset()
    assert result.ambiguous_position_symbols == frozenset()
    assert result.block_buys == ()
    assert result.problems == ()


# ---- c. free_base comes from the balance, net_base from the ledger -------------------------
def test_held_free_base_comes_from_the_balance(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    intent = _intent(h, ps, key="live1:entry")
    _observe(h, intent, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, intent)

    partial = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                 balance=snapshot(jpy_free="8999", free={"OAS": "4"}),
                                 policy_set=ps)
    assert partial.held["OAS/JPY"].net_base == D("10")
    assert partial.held["OAS/JPY"].free_base == D("4")

    absent = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=snapshot(jpy_free="8999"), policy_set=ps)
    assert absent.held["OAS/JPY"].net_base == D("10")
    assert absent.held["OAS/JPY"].free_base == D("0")


# ---- d. a position with no fills is not held ----------------------------------------------
def test_a_position_without_holdings_is_not_held(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    _to_acked(h, _intent(h, ps, key="live1:no-fills"))

    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=snapshot(jpy_free="8998"), policy_set=ps)

    assert result.positions == {}
    assert result.held == {}
    assert result.open_buy_symbols == frozenset({"OAS/JPY"})
    assert result.legacy_position_symbols == frozenset()
    assert result.ambiguous_position_symbols == frozenset()


# ---- e. legacy position when the policy digest changed ------------------------------------
def test_legacy_position_when_the_policy_digest_changed(h):
    entry = policy_set_for((spec("exp-entry"), "OAS/JPY"))
    current = policy_set_for((spec("exp-current"), "OAS/JPY"))
    _store(h, entry)
    _store(h, current)
    intent = _intent(h, entry, key="live1:entry")
    _observe(h, intent, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, intent)
    entry_digest = entry.policy_for("OAS/JPY").digest
    assert current.policy_for("OAS/JPY").digest != entry_digest

    changed = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                 balance=snapshot(jpy_free="9000", free={"OAS": "10"}),
                                 policy_set=current)
    assert changed.legacy_position_symbols == frozenset({"OAS/JPY"})
    assert changed.positions["OAS/JPY"] == views.CostBasis(D("10"), D("1000"), D("100"), 1000.0)
    assert changed.held["OAS/JPY"] == HeldPosition(intent.position_id, D("10"), D("10"), entry_digest)

    same = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                              balance=snapshot(jpy_free="9000", free={"OAS": "10"}),
                              policy_set=entry)
    assert same.legacy_position_symbols == frozenset()
    assert same.held["OAS/JPY"].entry_policy_digest == entry_digest

    without = policy_set_for((spec("exp-entry"), "XYM/JPY"))
    missing = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                 balance=snapshot(jpy_free="9000", free={"OAS": "10"}),
                                 policy_set=without)
    assert missing.legacy_position_symbols == frozenset()
    assert "OAS/JPY" in missing.positions
    assert "OAS/JPY" in missing.held


# ---- f. two open positions make the symbol ambiguous --------------------------------------
def test_two_open_positions_make_the_symbol_ambiguous(h):
    first_ps = policy_set_for((spec("exp-a"), "OAS/JPY"))
    second_ps = policy_set_for((spec("exp-b"), "OAS/JPY"))
    _store(h, first_ps)
    _store(h, second_ps)

    first = _intent(h, first_ps, key="live1:first")
    _observe(h, first, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, first)
    second = _intent(h, second_ps, key="live1:second", now=1100.0)
    _observe(h, second, order_id="o2")
    _fill(h, fill_id="f2", order_id="o2", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1100.0)
    _finish(h, second, now=1101.0)

    assert first.position_id != second.position_id
    rows = [row for row in views.open_position_rows(h.ledger) if row.symbol == "OAS/JPY"]
    assert len(rows) == 2

    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=snapshot(jpy_free="8000", free={"OAS": "20"}),
                                policy_set=first_ps)

    assert result.ambiguous_position_symbols == frozenset({"OAS/JPY"})
    assert result.positions == {}
    assert result.held == {}
    assert result.legacy_position_symbols == frozenset()
    assert result.problems == ()


# ---- g. unknown fee blocks buys but not the rest ------------------------------------------
def test_unknown_fee_blocks_buys_but_not_the_rest(harnesses):
    h = harnesses(symbols=("OAS/JPY", "XYM/JPY"))
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"), (spec("exp-1"), "XYM/JPY"))
    _store(h, ps)

    filled = _intent(h, ps, key="live1:oas", symbol="OAS/JPY")
    _observe(h, filled, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_status="unknown")
    _finish(h, filled)
    _to_acked(h, _intent(h, ps, key="live1:xym", symbol="XYM/JPY", amount="5", price="200"))

    rules = {"OAS/JPY": market_rules("OAS/JPY", max_fee_rate=None, quote_unit=D("1"))}
    result = build_cycle_inputs(h.ledger, rules=rules,
                                balance=jpy_balance("9000", "9000"), policy_set=ps)

    # Both the deployed cost and the open buy exposure need a fee rate; one code suffices.
    assert result.block_buys == ("fee_rate_unknown",)
    assert result.problems == (("OAS/JPY", "cost_basis_unavailable"),)
    assert result.funds == Funds(D("9000"), D("0"), D("9000"))
    assert result.positions == {}
    assert result.held == {}
    assert result.open_buy_symbols == frozenset({"XYM/JPY"})
    assert result.open_sell_symbols == frozenset()
    assert result.decided_keys == frozenset({"live1:oas", "live1:xym"})
    assert result.legacy_position_symbols == frozenset()
    assert result.ambiguous_position_symbols == frozenset()


# ---- h. open symbols and decided keys -----------------------------------------------------
def test_open_symbols_and_decided_keys(harnesses):
    h = harnesses(symbols=("AAA/JPY", "BBB/JPY", "CCC/JPY"))
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"),
                        (spec("exp-1"), "CCC/JPY"))
    _store(h, ps)

    _to_acked(h, _intent(h, ps, key="live1:aaa", symbol="AAA/JPY"))

    b_buy = _intent(h, ps, key="live1:bbb-buy", symbol="BBB/JPY")
    _observe(h, b_buy, order_id="o-bbb")
    _fill(h, fill_id="f-bbb", order_id="o-bbb", symbol="BBB/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0)
    _finish(h, b_buy)
    _to_acked(h, _intent(h, ps, key="live1:bbb", side="sell", symbol="BBB/JPY", amount="10",
                         price="110", now=1100.0), now=1101.0)

    c_buy = _intent(h, ps, key="snapback:zzz", symbol="CCC/JPY")
    _observe(h, c_buy, order_id="o-ccc")
    _fill(h, fill_id="f-ccc", order_id="o-ccc", symbol="CCC/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0)
    _finish(h, c_buy)

    result = build_cycle_inputs(
        h.ledger, rules=rules_for("AAA/JPY", "BBB/JPY", "CCC/JPY"),
        balance=snapshot(jpy_free="5998"), policy_set=ps)

    assert result.open_buy_symbols == frozenset({"AAA/JPY"})
    assert result.open_sell_symbols == frozenset({"BBB/JPY"})
    # every "live1:" key, terminal intents (bbb-buy, aaa's ACKED buy included) too
    assert result.decided_keys == frozenset({"live1:aaa", "live1:bbb-buy", "live1:bbb"})
    assert "snapback:zzz" not in result.decided_keys


# ---- i. sell attempts count exact-prefix keys --------------------------------------------
def test_sell_attempts_count_exact_prefix_keys(h):
    entry = policy_set_for((spec("exp-entry"), "OAS/JPY"))
    current = policy_set_for((spec("exp-current"), "OAS/JPY"))
    _store(h, entry)
    _store(h, current)

    bought = _intent(h, entry, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, bought)
    pid = bought.position_id
    entry_digest = entry.policy_for("OAS/JPY").digest
    current_digest = current.policy_for("OAS/JPY").digest

    stem = f"live1:{entry_digest[:16]}:opp-1:{pid[:8]}"
    for key in (f"{stem}:a0", f"{stem}:a1", f"{stem}:a1x"):
        abandoned = _intent(h, entry, key=key, side="sell", symbol="OAS/JPY", amount="10",
                            price="110", now=1100.0)
        h.ledger.transition_intent(h.lease, abandoned.intent_id, expected_state="PLANNED",
                                   to_state="ABANDONED", now=1101.0)
    other = _intent(h, entry, key=f"live1:{entry_digest[:16]}:opp-10:{pid[:8]}:a1",
                    side="sell", symbol="OAS/JPY", amount="10", price="110", now=1102.0)
    h.ledger.transition_intent(h.lease, other.intent_id, expected_state="PLANNED",
                               to_state="ABANDONED", now=1103.0)
    # A key under the current policy digest must not be mistaken for an entry-digest attempt.
    _intent(h, current, key=f"live1:{current_digest[:16]}:opp-1:{pid[:8]}:a9", now=1200.0)

    exits = (opportunity("OAS/JPY", "sell", oid="opp-1"),
             opportunity("OAS/JPY", "sell", oid="opp-2"),
             opportunity("XYM/JPY", "sell", oid="opp-3"))
    held = {"OAS/JPY": HeldPosition(pid, D("10"), D("10"), entry_digest)}

    counted = sell_attempts_for(h.ledger, exits, held, current)
    assert counted == {"opp-1": 2, "opp-2": 0}          # a1x, opp-10 and the current digest do not count
    assert "opp-3" not in counted                       # not held: skipped

    without_entry_digest = {"OAS/JPY": HeldPosition(pid, D("10"), D("10"))}
    fallback = sell_attempts_for(h.ledger, exits, without_entry_digest, current)
    assert fallback == {"opp-1": 1, "opp-2": 0}         # only the current-policy key

    no_policy = {"XYM/JPY": HeldPosition("x" * 32, D("10"), D("10"))}
    assert sell_attempts_for(h.ledger, exits, no_policy, current) == {}


# ---- j. the inputs do not write ----------------------------------------------------------
def test_inputs_do_not_write(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="1", fee_currency="JPY")
    _finish(h, bought)

    events_before = h.ledger.events(limit=10**6)
    fills_before = views.fill_count(h.ledger)
    rows_before = views.open_position_rows(h.ledger)
    rule_map = rules_for("OAS/JPY")
    balance = snapshot(jpy_free="8999", free={"OAS": "10"})
    exits = (opportunity("OAS/JPY", "sell", oid="opp-1"),)
    held = {"OAS/JPY": HeldPosition(bought.position_id, D("10"), D("10"))}

    first = build_cycle_inputs(h.ledger, rules=rule_map, balance=balance, policy_set=ps)
    first_attempts = sell_attempts_for(h.ledger, exits, held, ps)
    second = build_cycle_inputs(h.ledger, rules=rule_map, balance=balance, policy_set=ps)
    second_attempts = sell_attempts_for(h.ledger, exits, held, ps)

    assert first == second
    assert first_attempts == second_attempts == {"opp-1": 0}
    assert h.ledger.events(limit=10**6) == events_before
    assert len(h.ledger.events(limit=10**6)) == len(events_before)
    assert views.fill_count(h.ledger) == fills_before
    assert views.open_position_rows(h.ledger) == rows_before


# ---- k. a fresh interpreter imports the module without network or side effects -------------
def test_inputs_import_without_network_or_side_effects(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.inputs
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests", "socket")
               if m in sys.modules]
        assert not bad, bad
        for name in ("docich.trading.worker", "docich.trading.slot_runtime", "docich.trading.cli"):
            assert name not in sys.modules, name
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert out.stdout.strip() == "clean", out.stderr


# ---- l. only deployed_cost lacks a fee rate: nothing is recorded as deployed -----------------
def test_unknown_fee_in_deployed_cost_alone_blocks_buys(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_status="unknown")
    _finish(h, bought)

    # No open buy intent: only deployed_cost has to consult the (missing) max fee rate.
    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY", fee=None),
                                balance=jpy_balance("9000", "9000"), policy_set=ps)

    assert result.block_buys == ("fee_rate_unknown",)
    # The whole deployed cost falls back to 0; the free quote is the live JPY.
    assert result.funds == Funds(D("9000"), D("0"), D("9000"))
    assert result.funds.capital == D("9000")
    assert result.funds.deployed == D("0")
    assert result.funds.free_quote == D("9000")
    assert result.problems == (("OAS/JPY", "cost_basis_unavailable"),)
    assert result.positions == {}
    assert result.held == {}
    assert result.open_buy_symbols == frozenset()


# ---- m. only open_buy_exposure lacks a fee rate: deployed stays 0, quote is untouched -------
def test_unknown_fee_in_open_buy_exposure_alone_blocks_buys(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    _to_acked(h, _intent(h, ps, key="live1:acked"))

    # No filled position: only open_buy_exposure needs the (missing) max fee rate.
    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY", fee=None),
                                balance=snapshot(jpy_free="10000"), policy_set=ps)

    assert result.block_buys == ("fee_rate_unknown",)
    # No exposure is known, so no quote is reserved and nothing is deployed.
    assert result.funds == Funds(D("10000"), D("0"), D("10000"))
    assert result.funds.deployed == D("0")
    assert result.funds.free_quote == D("10000")
    assert result.problems == ()
    assert result.positions == {}
    assert result.held == {}
    assert result.open_buy_symbols == frozenset({"OAS/JPY"})


# ---- n. an unknown fee with a known rate costs notional * (1 + max_fee_rate) ----------------
def test_unknown_fee_fill_uses_the_rules_max_fee_rate(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_status="unknown")
    _finish(h, bought)
    digest = ps.policy_for("OAS/JPY").digest

    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=snapshot(jpy_total="9000", jpy_free="8999",
                                                 free={"OAS": "10"}),
                                policy_set=ps)

    # 1000 notional + 0.001 * 1000 estimated fee = 1001; 1001 / 10 = 100.1.
    assert result.block_buys == ()
    assert result.problems == ()
    # C = 9000 live JPY + 1001 position cost; F is the exchange's free JPY (8999).
    assert result.funds == Funds(D("10001"), D("1001"), D("8999"))
    assert result.funds.deployed == D("1001")
    assert result.positions["OAS/JPY"] == views.CostBasis(D("10"), D("1001"), D("100.1"), 1000.0)
    assert result.held["OAS/JPY"] == HeldPosition(bought.position_id, D("10"), D("10"), digest)


# ---- o. decided keys are those with the literal "live1:" prefix ----------------------------
def test_decided_keys_only_take_the_live1_colon_prefix(harnesses):
    h = harnesses(symbols=("AAA/JPY", "BBB/JPY", "CCC/JPY"))
    ps = policy_set_for((spec("exp-1"), "AAA/JPY"), (spec("exp-1"), "BBB/JPY"),
                        (spec("exp-1"), "CCC/JPY"))
    _store(h, ps)
    for symbol, key in (("AAA/JPY", "live1:abc:1"), ("BBB/JPY", "live10:abc:2"),
                        ("CCC/JPY", "live1abc:3")):
        planned = _intent(h, ps, key=key, symbol=symbol)
        h.ledger.transition_intent(h.lease, planned.intent_id, expected_state="PLANNED",
                                   to_state="ABANDONED", now=1001.0)

    result = build_cycle_inputs(h.ledger, rules=rules_for("AAA/JPY", "BBB/JPY", "CCC/JPY"),
                                balance=snapshot(jpy_free="10000"), policy_set=ps)

    # "live1:" is a literal prefix, not a prefix of a longer namespace ("live10", "live1abc").
    assert result.decided_keys == frozenset({"live1:abc:1"})


# ---- p. an attempt tail must be ASCII digits, not any Unicode digit ------------------------
def test_sell_attempts_ignore_non_ascii_digit_tails(h):
    entry = policy_set_for((spec("exp-entry"), "OAS/JPY"))
    _store(h, entry)
    bought = _intent(h, entry, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0)
    _finish(h, bought)

    stem = f"live1:{entry.policy_for('OAS/JPY').digest[:16]}:opp-1:{bought.position_id[:8]}:a"
    # "٢" is str.isdigit() but not ASCII; only the two ASCII tails are attempts.
    for key in (f"{stem}0", f"{stem}1", f"{stem}٢"):
        attempted = _intent(h, entry, key=key, side="sell", symbol="OAS/JPY", amount="10",
                            price="110", now=1100.0)
        h.ledger.transition_intent(h.lease, attempted.intent_id, expected_state="PLANNED",
                                   to_state="ABANDONED", now=1101.0)

    exits = (opportunity("OAS/JPY", "sell", oid="opp-1"),)
    held = {"OAS/JPY": HeldPosition(bought.position_id, D("10"), D("10"),
                                    entry.policy_for("OAS/JPY").digest)}

    assert sell_attempts_for(h.ledger, exits, held, entry) == {"opp-1": 2}


# ---- q/r. a sell must carry the digest its position was entered under ----------------------
NOW = 1_800_000_000.0


def _sell_market():
    from docich.trading.live.exchange import OrderBook
    from docich.trading.live.sizing import SymbolMarket

    return SymbolMarket(
        rules=market_rules("OAS/JPY", amount_step=D("0.01"), min_amount=D("0.01"),
                           price_tick=D("1")),
        book=OrderBook(symbol="OAS/JPY", bids=((D("100"), D("50")),), asks=(),
                       exchange_ts_ms=1_700_000_000_000),
        last_close=D("100"),
    )


def _legacy_sell_setup(h):
    """A filled OAS/JPY position entered under A, with B as the current policy set."""
    entry = policy_set_for((spec("exp-1"), "OAS/JPY"))
    current = policy_set_for((spec("exp-1", entry_fraction="0.20"), "OAS/JPY"))
    _store(h, entry)
    _store(h, current)
    bought = _intent(h, entry, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10", price="100",
          executed_at=1000.0, fee_amount="1", fee_currency="JPY")
    _finish(h, bought)
    digest_a = entry.policy_for("OAS/JPY").digest
    digest_b = current.policy_for("OAS/JPY").digest
    assert digest_a != digest_b            # both digests are registered in the ledger's policies
    cycle = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                               balance=snapshot(jpy_free="8999", free={"OAS": "10"}),
                               policy_set=current)
    return entry, current, bought, cycle, digest_a, digest_b


def _sell_orders(h, current, exits, held, cycle):
    from docich.trading.live.sizing import size_orders
    from docich.trading.live.signals import LiveSignals

    return size_orders(
        LiveSignals((), exits, (), {}),
        current,
        {"OAS/JPY": _sell_market()},
        held,
        cycle.funds,
        now=NOW,
        decided_keys=cycle.decided_keys,
        open_buy_symbols=cycle.open_buy_symbols,
        open_sell_symbols=cycle.open_sell_symbols,
        legacy_position_symbols=cycle.legacy_position_symbols,
        ambiguous_position_symbols=cycle.ambiguous_position_symbols,
        sell_attempts=sell_attempts_for(h.ledger, exits, held, current),
        buys_blocked=bool(cycle.block_buys),
    )


def test_a_sell_under_the_entry_digest_is_accepted_by_the_real_ledger(h):
    entry, current, bought, cycle, digest_a, digest_b = _legacy_sell_setup(h)

    assert "OAS/JPY" in cycle.legacy_position_symbols
    assert cycle.held["OAS/JPY"].entry_policy_digest == digest_a

    exits = (opportunity("OAS/JPY", "sell", oid="opp-1"),)
    result = _sell_orders(h, current, exits, cycle.held, cycle)

    assert result.buys == ()
    assert len(result.sells) == 1
    plan = result.sells[0]
    assert plan.policy_digest == digest_a
    assert plan.position_id == bought.position_id
    assert plan.decision_key.startswith("live1:" + digest_a[:16] + ":opp-1:")
    assert plan.decision_key.endswith(":a0")

    created = h.ledger.create_intent(h.lease, plan.intent_request(), now=NOW)
    assert created.side == "sell"
    assert created.policy_digest == digest_a
    assert created.position_id == bought.position_id
    stored = views.intent_by_decision_key(h.ledger, plan.decision_key)
    assert stored is not None
    assert stored.intent_id == created.intent_id
    assert stored.policy_digest == digest_a
    assert views.open_intent_symbols(h.ledger, "sell") == {"OAS/JPY"}

    # The key ends ":a0", so exactly one attempt is now recorded for this exit.
    assert sell_attempts_for(h.ledger, exits, cycle.held, current) == {"opp-1": 1}


def test_a_sell_under_the_current_digest_of_a_legacy_position_is_refused(h):
    from dataclasses import replace

    from docich.trading.live.ledger import LiveLedgerError

    entry, current, bought, cycle, digest_a, digest_b = _legacy_sell_setup(h)

    assert "OAS/JPY" in cycle.legacy_position_symbols
    assert cycle.held["OAS/JPY"].entry_policy_digest == digest_a

    # Without the entry digest the planner falls back to the current one (B).
    held = {"OAS/JPY": replace(cycle.held["OAS/JPY"], entry_policy_digest=None)}
    exits = (opportunity("OAS/JPY", "sell", oid="opp-1"),)
    result = _sell_orders(h, current, exits, held, cycle)

    assert len(result.sells) == 1
    plan = result.sells[0]
    assert plan.policy_digest == digest_b
    assert plan.policy_digest != digest_a
    assert plan.position_id == bought.position_id
    assert plan.decision_key.startswith("live1:" + digest_b[:16] + ":opp-1:")
    assert plan.decision_key.endswith(":a0")

    # ``create_intent`` resolves the position by (symbol, policy_digest) before it inserts,
    # so a sell under B finds no open position and is refused there; the
    # ``sell_uses_entry_policy`` trigger is the backstop behind that lookup.
    with pytest.raises(LiveLedgerError) as refused:
        h.ledger.create_intent(h.lease, plan.intent_request(), now=NOW)
    assert "policy" in str(refused.value)
    assert str(refused.value) == "no open position under that policy"
    assert views.intent_by_decision_key(h.ledger, plan.decision_key) is None
    assert views.open_intent_symbols(h.ledger, "sell") == frozenset()


# ---- an unruled fill no longer proves the balance unreliable -------------------------------
def test_missing_rules_for_a_traded_symbol_no_longer_blocks_buys(h):
    ps = policy_set_for((spec("exp-1"), "OLD/JPY"), (spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    old = _intent(h, ps, key="live1:old", symbol="OLD/JPY")
    _observe(h, old, order_id="o-old")
    _fill(h, fill_id="f-old", order_id="o-old", symbol="OLD/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0, fee_amount="1", fee_currency="JPY")
    _finish(h, old)
    _to_acked(h, _intent(h, ps, key="live1:acked"))

    rules = rules_for("OAS/JPY")      # OLD/JPY deliberately has no rules in this cycle
    result = build_cycle_inputs(h.ledger, rules=rules,
                                balance=jpy_balance("9500", "9500"), policy_set=ps)

    # The ledger expectation is not consulted any more: the unruled fill does not block a
    # buy, and capital is the live JPY plus the cost of the bot's own position.
    assert result.block_buys == ()
    assert result.funds.capital == D("10501")        # 9500 + 1001
    assert result.funds.deployed == D("2001")        # 1001 position + 1000 open buy
    assert result.funds.free_quote == D("9500")
    # The rest of the inputs are still returned.
    assert result.positions["OLD/JPY"] == views.CostBasis(D("10"), D("1001"), D("100.1"), 1000.0)
    assert result.held["OLD/JPY"] == HeldPosition(old.position_id, D("10"), D("0"),
                                                  ps.policy_for("OLD/JPY").digest)
    assert result.open_buy_symbols == frozenset({"OAS/JPY"})


def test_free_quote_is_the_exchange_free_jpy_not_the_ledger_expectation(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0, fee_status="unknown")
    _finish(h, bought)

    rules = rules_for("OAS/JPY")                 # max_fee_rate 0.001, quote_unit 1
    rich = build_cycle_inputs(h.ledger, rules=rules,
                              balance=jpy_balance("9000", "9000"), policy_set=ps)
    assert rich.block_buys == ()
    assert rich.funds == Funds(D("10001"), D("1001"), D("9000"))

    thin = build_cycle_inputs(h.ledger, rules=rules,
                              balance=jpy_balance("8990", "8990"), policy_set=ps)
    assert thin.funds == Funds(D("9991"), D("1001"), D("8990"))


def test_complete_rules_do_not_trigger_the_indeterminate_block(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:entry")
    _observe(h, bought, order_id="o1")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0, fee_amount="1", fee_currency="JPY")
    _finish(h, bought)

    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=snapshot(jpy_free="8999", free={"OAS": "10"}),
                                policy_set=ps)

    assert result.block_buys == ()
    assert "balance_expectation_indeterminate" not in result.block_buys
    assert result.funds == Funds(D("10000"), D("1001"), D("8999"))


# ---- no JPY on the exchange: no buys, sells still planned ---------------------------------
def test_no_jpy_on_the_exchange_stops_buys_but_not_sells(h):
    from docich.trading.live.signals import LiveSignals
    from docich.trading.live.sizing import size_orders

    ps = policy_set_for((spec("exp-1"), "OAS/JPY"), (spec("exp-1"), "XYM/JPY"))
    _store(h, ps)
    # A zero-cost position (an airdrop: 10 OAS at price 0) keeps a sell possible while the
    # ledger holds no JPY at all.
    bought = _intent(h, ps, key="live1:entry")
    _observe(h, bought, order_id="o1", amount="10", price="0")
    _fill(h, fill_id="f1", order_id="o1", symbol="OAS/JPY", side="buy", amount="10",
          price="0", executed_at=1000.0)
    _finish(h, bought)

    exits = (opportunity("OAS/JPY", "sell", oid="opp-sell"),)
    cycle = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY", "XYM/JPY"),
                               balance=snapshot(free={"OAS": "10"}), policy_set=ps)

    assert cycle.funds == Funds(D("0"), D("0"), D("0"))    # no JPY asset at all
    assert cycle.block_buys == ()
    result = size_orders(
        LiveSignals((opportunity("XYM/JPY", "buy", oid="opp-buy"),), exits, (), {}),
        ps,
        {"OAS/JPY": _sell_market()},
        cycle.held,
        cycle.funds,
        now=NOW,
        decided_keys=cycle.decided_keys,
        open_buy_symbols=cycle.open_buy_symbols,
        open_sell_symbols=cycle.open_sell_symbols,
        legacy_position_symbols=cycle.legacy_position_symbols,
        ambiguous_position_symbols=cycle.ambiguous_position_symbols,
        sell_attempts=sell_attempts_for(h.ledger, exits, cycle.held, ps),
        buys_blocked=bool(cycle.block_buys),
    )

    assert len(result.sells) == 1
    assert result.sells[0].position_id == bought.position_id
    assert (result.sells[0].amount, result.sells[0].price) == (D("10"), D("100"))
    assert result.buys == ()
    assert [(skip.symbol, skip.side, skip.reason_code) for skip in result.skipped] == [
        ("XYM/JPY", "buy", "no_capital_recorded")]


# ---- capital C is the live JPY balance, not cashflows -----------------------------------
def test_capital_is_live_jpy_plus_position_cost_and_headroom_never_double_counts(h):
    from docich.trading.live.signals import LiveSignals
    from docich.trading.live.sizing import SymbolMarket, size_orders
    from docich.trading.live.exchange import OrderBook

    ps = policy_set_for((spec("exp-1"), "OAS/JPY"), (spec("exp-1"), "XYM/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:position", amount="20", price="100")
    _observe(h, bought, order_id="o-pos", amount="20", price="100")
    _fill(h, fill_id="f-pos", order_id="o-pos", symbol="OAS/JPY", side="buy", amount="20",
          price="100", executed_at=1000.0, fee_amount="0", fee_currency="JPY")
    _finish(h, bought)
    rules = rules_for("OAS/JPY", "XYM/JPY")

    # No open buy: 8000 JPY + 2000 position cost = 10000 capital, 2000 deployed, 8000 left.
    cash_only = build_cycle_inputs(h.ledger, rules=rules,
                                   balance=jpy_balance("8000", "8000"), policy_set=ps)
    assert cash_only.funds == Funds(D("10000"), D("2000"), D("8000"))
    assert cash_only.funds.capital - cash_only.funds.deployed == D("8000")

    # An open buy for 15 XYM at 100 locks 1500 of the JPY: total stays 8000, free is 6500.
    _to_acked(h, _intent(h, ps, key="live1:open", symbol="XYM/JPY", amount="15", price="100"))
    with_open_buy = build_cycle_inputs(h.ledger, rules=rules,
                                       balance=jpy_balance("8000", "6500"), policy_set=ps)
    assert with_open_buy.funds == Funds(D("10000"), D("3500"), D("6500"))
    assert with_open_buy.funds.capital - with_open_buy.funds.deployed == D("6500")
    # headroom = jpy_total (8000) - the open buy's notional (1500), not 8000 - 1500 - 1500.
    assert (with_open_buy.funds.capital - with_open_buy.funds.deployed
            == D("8000") - D("1500"))

    market = SymbolMarket(
        rules=market_rules("OAS/JPY", amount_step=D("1"), min_amount=D("1"),
                           price_tick=D("1"), quote_unit=D("1"), max_fee_rate=D("0.001")),
        book=OrderBook(symbol="OAS/JPY", bids=((D("99"), D("100")),),
                       asks=((D("100"), D("100")),), exchange_ts_ms=1_700_000_000_000),
        last_close=D("100"),
    )
    sized = size_orders(
        LiveSignals((opportunity("OAS/JPY", "buy", oid="buy-1"),), (), (), {}), ps,
        {"OAS/JPY": market}, with_open_buy.held, with_open_buy.funds, now=NOW,
        decided_keys=with_open_buy.decided_keys,
        open_buy_symbols=with_open_buy.open_buy_symbols,
        buys_blocked=bool(with_open_buy.block_buys),
    )
    # The 0.10 opportunity cap on C=10000 is 1000; the headroom (6500) leaves it standing.
    assert len(sized.buys) == 1
    assert sized.buys[0].amount == D("10")
    # required_quote = 10 * 100 * (1 + 0.001) + quote_unit 1 = 1002.000.
    assert sized.buys[0].required_quote == D("1002.000")


def test_a_deposit_raises_capital_next_cycle_without_any_ledger_write(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    bought = _intent(h, ps, key="live1:position", amount="20", price="100")
    _observe(h, bought, order_id="o-pos", amount="20", price="100")
    _fill(h, fill_id="f-pos", order_id="o-pos", symbol="OAS/JPY", side="buy", amount="20",
          price="100", executed_at=1000.0, fee_amount="0", fee_currency="JPY")
    _finish(h, bought)
    rules = rules_for("OAS/JPY")
    events_before = h.ledger.events(limit=10**6)

    before = build_cycle_inputs(h.ledger, rules=rules,
                                balance=jpy_balance("8000", "8000"), policy_set=ps)
    after = build_cycle_inputs(h.ledger, rules=rules,
                               balance=jpy_balance("9000", "9000"), policy_set=ps)

    assert before.funds.capital == D("10000")        # 8000 + 2000
    assert after.funds.capital == D("11000")         # the 1000 deposit, no new cashflow row
    assert after.funds.deployed == before.funds.deployed == D("2000")
    assert h.ledger.events(limit=10**6) == events_before


def test_an_unruled_fill_no_longer_blocks_buys(h):
    from docich.trading.live.signals import LiveSignals
    from docich.trading.live.sizing import SymbolMarket, size_orders
    from docich.trading.live.exchange import OrderBook

    ps = policy_set_for((spec("exp-1"), "OLD/JPY"), (spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    old = _intent(h, ps, key="live1:old", symbol="OLD/JPY")
    _observe(h, old, order_id="o-old")
    _fill(h, fill_id="f-old", order_id="o-old", symbol="OLD/JPY", side="buy", amount="10",
          price="100", executed_at=1000.0, fee_amount="1", fee_currency="JPY")
    _finish(h, old)

    cycle = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                               balance=jpy_balance("9000", "9000"), policy_set=ps)
    assert cycle.block_buys == ()
    assert cycle.funds == Funds(D("10001"), D("1001"), D("9000"))

    market = SymbolMarket(
        rules=market_rules("OAS/JPY", amount_step=D("1"), min_amount=D("1"),
                           price_tick=D("1"), quote_unit=D("1"), max_fee_rate=D("0.001")),
        book=OrderBook(symbol="OAS/JPY", bids=((D("99"), D("100")),),
                       asks=((D("100"), D("100")),), exchange_ts_ms=1_700_000_000_000),
        last_close=D("100"),
    )
    result = size_orders(LiveSignals((opportunity("OAS/JPY", "buy", oid="buy-1"),), (), (), {}),
                         ps, {"OAS/JPY": market}, cycle.held, cycle.funds, now=NOW,
                         decided_keys=cycle.decided_keys,
                         open_buy_symbols=cycle.open_buy_symbols,
                         buys_blocked=bool(cycle.block_buys))
    assert len(result.buys) == 1
    assert result.buys[0].symbol == "OAS/JPY"


def test_a_negative_free_jpy_is_floored_to_zero(h):
    ps = policy_set_for((spec("exp-1"), "OAS/JPY"))
    _store(h, ps)
    odd = BalanceSnapshot({"JPY": AssetBalance(D("100"), D("-5"), D("105"))},
                          1_700_000_000_000, 1000.0)
    result = build_cycle_inputs(h.ledger, rules=rules_for("OAS/JPY"),
                                balance=odd, policy_set=ps)
    assert result.funds.capital == D("100")
    assert result.funds.free_quote == D("0")
