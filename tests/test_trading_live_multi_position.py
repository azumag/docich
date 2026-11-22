"""Same-symbol multiple positions (Issue #4588, parent #3623): permitted vs allowed.

The open-position uniqueness rule is ``(symbol, entry_policy_digest)``, so the
ledger can legitimately hold two positions on one symbol once the strategy
changed underneath (a buy under digest B while a position entered under digest
A is still open).  This file pins, with synthetic data only, what is permitted
where:

Permitted at the LEDGER layer (existing ledgers need no migration; these rows
are valid and stay valid under schema v1):
* a second open position on a symbol exists only under a different entry digest;
* a sell always binds to the position entered under the request's policy digest
  (the ``sell_uses_entry_policy`` trigger refuses any other binding);
* quantities, cost basis and residual checks are per ``position_id``;
* at most one open intent per (symbol, side) across *all* of that symbol's
  positions — a second sell is rejected while the first is open.

Allowed at the WORKER layer (the safety net above the ledger):
* one open position under a non-current digest is "legacy": its sells are still
  planned under the entry digest, but a new buy is skipped — so the worker never
  creates the second position itself;
* two or more open positions on a symbol are "ambiguous": the worker plans
  nothing at all for it (no buy, no sell) until only one open position remains;
* the executor refuses a sell larger than its own position's net, even when the
  symbol's total holdings would cover it.

Compatibility record: a ledger created before this issue may already contain
multiple same-symbol open positions.  Nothing here migrates or merges them —
``open`` reads them unchanged, the event chain still verifies, and the worker
keeps treating the symbol as ambiguous (fail-closed).
"""
from decimal import Decimal as D

import pytest

from docich.trading.live import views
from docich.trading.live.exchange import AssetBalance, BalanceSnapshot, OrderBook
from docich.trading.live.executor import SubmitOutcome
from docich.trading.live.inputs import build_cycle_inputs
from docich.trading.live.ledger import (
    LIVE_SCHEMA_VERSION,
    FillRecord,
    IntentRequest,
    LiveLedgerError,
    OrderObservation,
)
from docich.trading.live.policy import build_policy, policy_set_from_policies
from docich.trading.live.signals import LiveSignals
from docich.trading.live.sizing import HeldPosition, SymbolMarket, size_orders
from docich.trading.models import Opportunity
from docich.trading.strategy_lab import Condition, Rule, StrategyExperiment
from test_trading_live_support import Harness, Workspace, block_network, market_rules

NOW = 1000.0


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


# ---- builders: a different experiment id is a different policy digest --------------
def spec(experiment_id: str) -> StrategyExperiment:
    return StrategyExperiment(
        experiment_id=experiment_id,
        name=f"{experiment_id} name",
        thesis=f"{experiment_id} thesis",
        entry_rules=(
            Rule("entry-1", "all", (Condition("return_bps", ">=", D("100"), 3),), D("0.30")),
        ),
        exit_rules=(
            Rule("exit-1", "all", (Condition("hold_minutes", ">=", D("100000")),), D("0.10")),
        ),
        max_pair_correlation=D("0.85"),
    )


def policy_set_for(*pairs):
    return policy_set_from_policies(
        {symbol: build_policy(experiment, symbol) for experiment, symbol in pairs}
    )


def snapshot(*, jpy_free="0", free=None):
    assets = {"JPY": AssetBalance(D(jpy_free), D(jpy_free), D("0"))}
    for currency, amount in (free or {}).items():
        assets[currency] = AssetBalance(D(amount), D(amount), D("0"))
    return BalanceSnapshot(assets, 1_700_000_000_000, 1000.0)


def opportunity(symbol: str, side: str, *, oid: str = "opp-1") -> Opportunity:
    return Opportunity(
        opportunity_id=oid,
        strategy_id="lab:exp-1:exit-1" if side == "sell" else "lab:exp-1:entry-1",
        symbol=symbol,
        side=side,
        score=D("0.5"),
        expected_edge_bps=D("10"),
        max_notional_fraction=D("0.10"),
        expires_at=1_800_000_060.0,
        reason_code="exit_signal" if side == "sell" else "entry_signal",
    )


def sell_market() -> SymbolMarket:
    return SymbolMarket(
        rules=market_rules("OAS/JPY", amount_step=D("0.01"), min_amount=D("0.01"),
                           price_tick=D("1")),
        book=OrderBook(symbol="OAS/JPY", bids=((D("100"), D("50")),), asks=(),
                       exchange_ts_ms=1_700_000_000_000),
        last_close=D("100"),
    )


# ---- ledger helpers: every row goes through the ledger's public methods -----------
def _store(h, ps):
    h.ledger.store_policy_set(h.lease, ps, now=NOW)


def _intent(h, ps, *, key, side="buy", symbol="OAS/JPY", amount="10", price="100", now=NOW):
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


def _observe(h, intent, *, order_id, amount="10", price="100"):
    h.ledger.record_order_observation(
        h.lease,
        OrderObservation(
            exchange_order_id=order_id,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type="limit",
            amount=amount,
            status="FULLY_FILLED",
            executed_amount=amount,
            remaining_amount="0",
            price=price,
        ),
        now=NOW,
    )


def _fill(h, *, fill_id, order_id, symbol, side, amount, price, executed_at=NOW,
          fee_amount="0", fee_currency="JPY"):
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
            fee_status="known",
            fee_amount=fee_amount,
            fee_currency=fee_currency,
        ),
        now=executed_at,
    )


def _finish(h, intent, *, state="FILLED", now=NOW + 1):
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="PLANNED",
                               to_state="SUBMITTING", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="SUBMITTING",
                               to_state="ACKED", now=now)
    h.ledger.transition_intent(h.lease, intent.intent_id, expected_state="ACKED",
                               to_state=state, now=now)


def _filled_position(h, ps, *, key, amount="10", price="100", now=NOW):
    """A bought-and-filled position (buy intent terminal) through public writers."""
    buy = _intent(h, ps, key=f"{key}-buy", amount=amount, price=price, now=now)
    _observe(h, buy, order_id=f"o-{key}-buy", amount=amount, price=price)
    _fill(h, fill_id=f"f-{key}-buy", order_id=f"o-{key}-buy", symbol=buy.symbol,
          side="buy", amount=amount, price=price)
    _finish(h, buy, now=now + 1)
    return buy


def _two_positions(h, amounts=("10", "20")):
    """Two open OAS/JPY positions: A under exp-a, B under exp-b."""
    ps_a = policy_set_for((spec("exp-a"), "OAS/JPY"))
    ps_b = policy_set_for((spec("exp-b"), "OAS/JPY"))
    _store(h, ps_a)
    _store(h, ps_b)
    pos_a = _filled_position(h, ps_a, key="pos-a", amount=amounts[0], now=NOW)
    pos_b = _filled_position(h, ps_b, key="pos-b", amount=amounts[1], now=NOW + 100)
    return ps_a, ps_b, pos_a, pos_b


# ---- a. the ledger layer -----------------------------------------------------------
def test_same_digest_buys_share_one_position_but_a_new_digest_opens_another(h):
    ps_a = policy_set_for((spec("exp-a"), "OAS/JPY"))
    ps_b = policy_set_for((spec("exp-b"), "OAS/JPY"))
    _store(h, ps_a)
    _store(h, ps_b)

    first = _intent(h, ps_a, key="b1")
    _finish(h, first)
    second = _intent(h, ps_a, key="b2", now=NOW + 100)
    _finish(h, second, now=NOW + 101)
    third = _intent(h, ps_b, key="b3", now=NOW + 200)

    assert second.position_id == first.position_id          # same digest: same position
    assert third.position_id != first.position_id           # a new digest: a new position
    rows = [row for row in views.open_position_rows(h.ledger) if row.symbol == "OAS/JPY"]
    assert len(rows) == 2
    assert {row.entry_policy_digest for row in rows} == {
        ps_a.policy_for("OAS/JPY").digest, ps_b.policy_for("OAS/JPY").digest}


def test_a_sell_binds_to_the_position_entered_under_its_digest(h):
    ps_a, ps_b, pos_a, pos_b = _two_positions(h)
    ps_c = policy_set_for((spec("exp-c"), "OAS/JPY"))
    _store(h, ps_c)                                          # stored, but never entered

    sell_a = _intent(h, ps_a, key="s-a", side="sell", amount="10", price="110", now=NOW + 300)
    assert sell_a.position_id == pos_a.position_id
    _finish(h, sell_a, now=NOW + 301)

    sell_b = _intent(h, ps_b, key="s-b", side="sell", amount="20", price="110", now=NOW + 400)
    assert sell_b.position_id == pos_b.position_id

    # A sell under a digest with no open position on the symbol is refused outright.
    with pytest.raises(LiveLedgerError, match="^no open position under that policy$"):
        _intent(h, ps_c, key="s-c", side="sell", amount="1", price="110", now=NOW + 500)


def test_one_open_sell_per_symbol_covers_every_position(h):
    ps_a, ps_b, pos_a, pos_b = _two_positions(h)

    sell_a = _intent(h, ps_a, key="s-a", side="sell", amount="10", price="110", now=NOW + 300)
    with pytest.raises(LiveLedgerError, match="intent rejected"):
        _intent(h, ps_b, key="s-b", side="sell", amount="20", price="110", now=NOW + 301)

    # Once the first sell reaches a terminal state the (symbol, side) slot frees.
    h.ledger.transition_intent(h.lease, sell_a.intent_id, expected_state="PLANNED",
                               to_state="ABANDONED", now=NOW + 302)
    sell_b = _intent(h, ps_b, key="s-b", side="sell", amount="20", price="110", now=NOW + 303)
    assert sell_b.position_id == pos_b.position_id
    assert views.open_intent_symbols(h.ledger, "sell") == {"OAS/JPY"}


def test_quantities_are_accounted_per_position(h):
    ps_a, ps_b, pos_a, pos_b = _two_positions(h, amounts=("10", "20"))
    assert views.position_net_base(h.ledger, pos_a.position_id) == D("10")
    assert views.position_net_base(h.ledger, pos_b.position_id) == D("20")

    sell_a = _intent(h, ps_a, key="s-a", side="sell", amount="4", price="110", now=NOW + 300)
    _observe(h, sell_a, order_id="o-sell-a", amount="4", price="110")
    _fill(h, fill_id="f-sell-a", order_id="o-sell-a", symbol="OAS/JPY", side="sell",
          amount="4", price="110")
    _finish(h, sell_a, now=NOW + 301)

    # The sell fill landed on position A only; position B is untouched.
    assert views.position_net_base(h.ledger, pos_a.position_id) == D("6")
    assert views.position_net_base(h.ledger, pos_b.position_id) == D("20")
    cost_a = views.position_cost(h.ledger, pos_a.position_id, D("0.001"))
    cost_b = views.position_cost(h.ledger, pos_b.position_id, D("0.001"))
    assert cost_a.net_base == D("6") and cost_b.net_base == D("20")


def test_the_executor_refuses_a_sell_larger_than_its_own_position(h):
    """15 <= 30 total OAS, but position A only holds 10: the sell is abandoned."""
    ps_a, ps_b, pos_a, pos_b = _two_positions(h, amounts=("10", "20"))

    sell = h.ledger.create_intent(
        h.lease,
        IntentRequest(decision_key="over-sell", symbol="OAS/JPY", side="sell",
                      order_type="limit", amount="15", price="100",
                      policy_digest=ps_a.policy_for("OAS/JPY").digest, rule_id="exit-1"),
        now=NOW + 300)
    assert sell.position_id == pos_a.position_id

    outcome = h.ex.submit(sell.intent_id)

    assert outcome is SubmitOutcome.LOCAL_INVALID
    stored = h.ledger.get_intent(sell.intent_id)
    assert stored.state == "ABANDONED" and stored.reason_code == "exceeds_holding"
    assert h.mock.submissions == []                          # nothing was ever sent


# ---- b. the worker layer -------------------------------------------------------------
def test_two_open_positions_make_the_symbol_ambiguous_end_to_end(h):
    ps_a, ps_b, pos_a, pos_b = _two_positions(h)

    cycle = build_cycle_inputs(h.ledger, rules={"OAS/JPY": market_rules("OAS/JPY")},
                             balance=snapshot(jpy_free="8000", free={"OAS": "30"}),
                             policy_set=ps_b)

    assert cycle.ambiguous_position_symbols == frozenset({"OAS/JPY"})
    assert cycle.legacy_position_symbols == frozenset()
    assert cycle.held == {} and cycle.positions == {}        # nothing sellable is offered

    exits = (opportunity("OAS/JPY", "sell", oid="opp-exit"),)
    entries = (opportunity("OAS/JPY", "buy", oid="opp-entry"),)
    result = size_orders(
        LiveSignals(entries, exits, (), {}),
        ps_b,
        {"OAS/JPY": sell_market()},
        cycle.held,
        cycle.funds,
        now=1_800_000_000.0,
        decided_keys=cycle.decided_keys,
        open_buy_symbols=cycle.open_buy_symbols,
        open_sell_symbols=cycle.open_sell_symbols,
        legacy_position_symbols=cycle.legacy_position_symbols,
        ambiguous_position_symbols=cycle.ambiguous_position_symbols,
        sell_attempts={},
        buys_blocked=bool(cycle.block_buys),
    )

    assert result.sells == () and result.buys == ()
    assert {(s.symbol, s.side, s.reason_code) for s in result.skipped} == {
        ("OAS/JPY", "sell", "ambiguous_position"),
        ("OAS/JPY", "buy", "ambiguous_position"),
    }


def test_a_legacy_position_sells_under_its_entry_digest_and_never_multiplies(h):
    """One old-digest position: its sell is planned under A; the second buy is refused
    at the worker layer — but the ledger itself would still accept the write."""
    ps_a, ps_b = policy_set_for((spec("exp-a"), "OAS/JPY")), policy_set_for((spec("exp-b"), "OAS/JPY"))
    _store(h, ps_a)
    _store(h, ps_b)
    pos_a = _filled_position(h, ps_a, key="pos-a", amount="10")
    digest_a = ps_a.policy_for("OAS/JPY").digest
    digest_b = ps_b.policy_for("OAS/JPY").digest

    cycle = build_cycle_inputs(h.ledger, rules={"OAS/JPY": market_rules("OAS/JPY")},
                             balance=snapshot(jpy_free="9000", free={"OAS": "10"}),
                             policy_set=ps_b)
    assert cycle.legacy_position_symbols == frozenset({"OAS/JPY"})
    assert cycle.ambiguous_position_symbols == frozenset()
    assert cycle.held["OAS/JPY"] == HeldPosition(pos_a.position_id, D("10"), D("10"), digest_a)

    result = size_orders(
        LiveSignals((opportunity("OAS/JPY", "buy", oid="opp-entry"),),
                    (opportunity("OAS/JPY", "sell", oid="opp-exit"),), (), {}),
        ps_b,
        {"OAS/JPY": sell_market()},
        cycle.held,
        cycle.funds,
        now=1_800_000_000.0,
        decided_keys=cycle.decided_keys,
        open_buy_symbols=cycle.open_buy_symbols,
        open_sell_symbols=cycle.open_sell_symbols,
        legacy_position_symbols=cycle.legacy_position_symbols,
        ambiguous_position_symbols=cycle.ambiguous_position_symbols,
        sell_attempts={},
        buys_blocked=False,
    )
    (sell_plan,) = result.sells
    assert sell_plan.policy_digest == digest_a               # the owning strategy, not B
    assert sell_plan.position_id == pos_a.position_id
    assert result.buys == ()
    assert [(s.symbol, s.reason_code) for s in result.skipped] == [
        ("OAS/JPY", "legacy_position_open")]

    # The ledger layer itself still accepts the second-digest buy the planner refused;
    # the worker's ambiguous check is what then keeps it untraded.
    second = h.ledger.create_intent(
        h.lease,
        IntentRequest(decision_key="forced-second", symbol="OAS/JPY", side="buy",
                      order_type="limit", amount="5", price="100",
                      policy_digest=digest_b, rule_id="entry-1"),
        now=NOW + 300)
    assert second.position_id != pos_a.position_id
    again = build_cycle_inputs(h.ledger, rules={"OAS/JPY": market_rules("OAS/JPY")},
                             balance=snapshot(jpy_free="9000", free={"OAS": "10"}),
                             policy_set=ps_b)
    assert again.ambiguous_position_symbols == frozenset({"OAS/JPY"})


def test_marking_one_position_residual_lifts_the_ambiguity(h):
    """A residual/closed position is not open: one remaining open position is normal."""
    ps_a, ps_b, pos_a, pos_b = _two_positions(h, amounts=("0.00005", "20"))

    cycle = build_cycle_inputs(h.ledger, rules={"OAS/JPY": market_rules("OAS/JPY")},
                             balance=snapshot(jpy_free="8000", free={"OAS": "20"}),
                             policy_set=ps_b)
    assert cycle.ambiguous_position_symbols == frozenset({"OAS/JPY"})

    h.ledger.mark_residual_inventory(h.lease, pos_a.position_id, now=NOW + 300,
                                     min_amount=D("0.0001"), amount_step=D("0.0001"))

    after = build_cycle_inputs(h.ledger, rules={"OAS/JPY": market_rules("OAS/JPY")},
                             balance=snapshot(jpy_free="8000", free={"OAS": "20"}),
                             policy_set=ps_b)
    assert after.ambiguous_position_symbols == frozenset()
    assert after.held["OAS/JPY"].position_id == pos_b.position_id


# ---- c. compatibility with an existing ledger ----------------------------------------
def test_an_existing_ledger_with_two_positions_reopens_unchanged(h):
    """Pre-#4588 ledgers may already hold two same-symbol positions: they keep
    reading, the chain verifies, no migration happens (schema stays v1)."""
    ps_a, ps_b, pos_a, pos_b = _two_positions(h)
    events_before = len(h.ledger.events(limit=10 ** 6))
    h.ledger.close()

    h.ledger = h.ws.open_ledger()                            # reopen the same file
    with h.ledger.read_cursor() as cur:
        assert cur.execute("PRAGMA user_version").fetchone()[0] == LIVE_SCHEMA_VERSION
    rows = [row for row in views.open_position_rows(h.ledger) if row.symbol == "OAS/JPY"]
    assert len(rows) == 2
    assert {row.position_id for row in rows} == {pos_a.position_id, pos_b.position_id}
    assert h.ledger.verify_event_chain()
    cycle = build_cycle_inputs(h.ledger, rules={"OAS/JPY": market_rules("OAS/JPY")},
                             balance=snapshot(jpy_free="8000", free={"OAS": "30"}),
                             policy_set=ps_b)
    assert cycle.ambiguous_position_symbols == frozenset({"OAS/JPY"})
    # Reopening is read-only: the event log did not grow.
    assert len(h.ledger.events(limit=10 ** 6)) == events_before
