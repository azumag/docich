"""The LIVE sell retry window (docich#4589, parent #3623): burn, cap and candle reset.

Production wiring being verified (the pins are the tests below):

* The candle is 5m (300 s): ``live_worker/__main__.py`` fetches ``TIMEFRAME = "5m"`` with
  ``FRAME_BARS = 25`` -- the same pair ``live.signals`` declares (``TIMEFRAME`` /
  ``HISTORY_LIMIT``) and the PAPER worker scans with.
* An exit ``opportunity_id`` is ``lab-exit:{experiment_id}:{rule_id}:{symbol}:{int(as_of)}``
  (``strategy_lab.scan_experiment_exits``; ``as_of`` is the last bar's timestamp).  The id
  changes when the candle rolls, when a different exit rule fires, or when the experiment id
  changes.
* A sell ``decision_key`` is ``live1:{entry_digest[:16]}:{opportunity_id}:{position_id[:8]}:a{n}``
  (``sizing.size_orders``).  ``n`` is the number of intents already recorded under that stem
  (``inputs.sell_attempts_for`` reads the durable ``intents`` table): the counter survives a
  restart and counts terminal/failed attempts (CANCELED, ABANDONED, ...) alike.  An attempt
  is burned at INTENT CREATION, not at fill or cancel; shadow/monitor modes create no
  intents and therefore never burn attempts.
* ``n >= SizingSettings().max_sell_attempts`` (5) -> ``sell_attempts_exhausted``: the
  opportunity is dead for that id.  A TTL cancel (``ttl.ORDER_TTL_S``, the same 45 s the
  sizer stamps) frees the (symbol, sell) slot, and the SAME cycle plans the next attempt
  while the signal still fires (ttl test t10 shows one hop).

Retry becomes possible again only when the decision-key stem changes:

  1. the next candle arrives -- ``int(as_of)`` moves -- AND an exit rule still matches;
  2. a DIFFERENT exit rule matches (``rule_id`` is inside the id), same or later candle;
  3. a new ``position_id`` (the position was closed and re-opened);
  4. a new ``entry_policy_digest`` -- only for positions opened under it: the ledger's
     ``sell_uses_entry_policy`` trigger refuses a sell under any other digest, so a policy
     swap can never reset the window of an existing position.
  A restart resets nothing (the intents table is durable; only the per-process dedupe of the
  decision LOG is lost, so a decision row may be written once more).  A stale frame produces
  no signal at all.  And when the exit signal fades on the new candle, no window opens for
  it: the position simply keeps sitting.

Everything runs on synthetic frames and the mock exchange through the real
``run_cycle``/``scan_live``/``decide_and_act``/``cancel_expired`` path: no network, no real
orders, the default worker still wires no sender.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from docich.trading.live import signals as signals_module
from docich.trading.live import ttl as ttl_module
from docich.trading.live import views
from docich.trading.live.cycle import CycleOutcome, CycleState, run_cycle
from docich.trading.live.executor import SubmitOutcome
from docich.trading.live.inputs import build_cycle_inputs, sell_attempts_for
from docich.trading.live.signals import scan_live
from docich.trading.live.sizing import SizingSettings
from docich.trading.live.views import CostBasis
from docich.trading.live_worker import __main__ as worker_main
from docich.trading.market_data import MarketFrame, timeframe_seconds

from test_trading_live_support import Harness, Workspace, block_network, policy_set
import test_trading_live_act as act_tests
import test_trading_live_decide as decide_tests

NOW = decide_tests.NOW
SYMBOL = "OAS/JPY"
EXP = "synthetic_v1"                     # the shared harness's experiment id
ALL_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED",
              "FILLED", "CANCELED", "EXPIRED", "REJECTED", "ABANDONED", "NOT_CREATED")


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


# ---- helpers ---------------------------------------------------------------------------
def _candle(symbol: str = SYMBOL, *, at: float, price: str, bars: int = 25) -> MarketFrame:
    """A flat synthetic 5m frame whose last bar is stamped ``at`` (the candle's id tail)."""
    stamps = tuple(float(at) - i * 300 for i in reversed(range(bars)))
    return MarketFrame(symbol=symbol, timeframe_seconds=300, timestamps=stamps,
                       closes=tuple(D(price) for _ in range(bars)),
                       volumes=tuple(D("1") for _ in range(bars)))


def _exit_oid(rule_id: str, at: float) -> str:
    return f"lab-exit:{EXP}:{rule_id}:{SYMBOL}:{int(at)}"


def _rig(ws, h):
    """A trade-mode rig on the harness's real ledger/lease/mock (sender is the mock)."""
    rig = act_tests.make_rig(ws, h, ps=h.ps, rules={SYMBOL: decide_tests.rule()})
    rig.state = CycleState()
    return rig


def _filled_position(h: Harness):
    """A real filled buy through the mock: 10 OAS at 100, position open at h.ps's digest."""
    h.mock.set_book(SYMBOL, asks=[(D("100"), D("1000"))])
    intent, outcome = h.send(symbol=SYMBOL, side="buy", key="entry-1",
                             amount="10", price="100")
    assert outcome is SubmitOutcome.ACKED
    intent = h.ledger.get_intent(intent.intent_id)
    assert intent.state == "FILLED"
    assert views.position_net_base(h.ledger, intent.position_id) == D("10")
    return intent


def _cycle(rig, *, advance: float = 0.0):
    """Advance the shared clock past the 45 s TTL and run one cycle with a fresh book."""
    if advance:
        rig.h.clock.advance(advance)
    rig.reader.books[SYMBOL] = decide_tests.book(SYMBOL, ts=int(rig.clock() * 1000))
    return run_cycle(rig.deps, rig.state)


def _sell_intents(h: Harness) -> list:
    return [item for item in h.ledger.list_intents(states=ALL_STATES)
            if item.side == "sell" and item.symbol == SYMBOL]


def _rows(h: Harness, *, action: str | None = None, reason: str | None = None) -> list[dict]:
    rows = [row.payload for row in decide_tests.decision_rows(h.ledger)]
    if action is not None:
        rows = [row for row in rows if row.get("action") == action]
    if reason is not None:
        rows = [row for row in rows if row.get("reason") == reason]
    return rows


def _mock_status(h: Harness, intent) -> str:
    found = [order for order in h.mock.all_orders()
             if order.exchange_order_id == intent.exchange_order_id]
    assert len(found) == 1
    return found[0].status.value


# ---- the production wiring pins ---------------------------------------------------------
def test_the_production_wiring_uses_5m_candles_and_a_five_attempt_window():
    assert worker_main.TIMEFRAME == signals_module.TIMEFRAME == "5m"
    assert timeframe_seconds(worker_main.TIMEFRAME) == 300
    assert worker_main.FRAME_BARS == signals_module.HISTORY_LIMIT == 25
    settings = SizingSettings()
    assert settings.max_sell_attempts == 5
    assert ttl_module.ORDER_TTL_S == settings.order_ttl_s == 45


def test_the_exit_opportunity_id_carries_the_rule_and_the_candle_timestamp():
    ps = policy_set((SYMBOL,))
    basis = {SYMBOL: CostBasis(net_base=D("10"), cost=D("1000"), avg_cost=D("100"),
                               opened_at=NOW - 60)}

    # ~+9.8% above cost after sell costs: exit-1 (pnl_bps >= 60) fires on candle NOW.
    first = scan_live({SYMBOL: _candle(at=NOW, price="110")}, basis, ps, now=NOW)
    assert [item.opportunity_id for item in first.exits] == [_exit_oid("exit-1", NOW)]
    assert first.exits[0].expires_at == NOW + 600          # two timeframes from the scan

    # The SAME candle scanned with a different rule firing: a different id entirely.
    other = scan_live({SYMBOL: _candle(at=NOW, price="80")}, basis, ps, now=NOW)
    assert [item.opportunity_id for item in other.exits] == [_exit_oid("exit-2", NOW)]

    # The next candle moves only the id's timestamp tail.
    nxt = scan_live({SYMBOL: _candle(at=NOW + 300, price="110")}, basis, ps,
                    now=NOW + 300)
    assert [item.opportunity_id for item in nxt.exits] == [_exit_oid("exit-1", NOW + 300)]

    # Back at cost (~-17 bps): neither rule fires -- no opportunity, no window at all.
    flat = scan_live({SYMBOL: _candle(at=NOW + 600, price="100")}, basis, ps,
                     now=NOW + 600)
    assert flat.exits == ()


# ---- the same candle burns at most five attempts ---------------------------------------
def test_consecutive_ttl_cancels_burn_the_five_attempt_window(ws, harnesses):
    h = harnesses()
    rig = _rig(ws, h)
    position = _filled_position(h)
    # Candle A never rolls in this test: the exchange keeps serving the same as_of.
    rig.market.overrides[SYMBOL] = _candle(at=NOW, price="110")
    oid_a = _exit_oid("exit-1", NOW)

    assert _cycle(rig).outcome is CycleOutcome.CONTINUE
    sells = _sell_intents(h)
    assert len(sells) == 1
    assert sells[0].decision_key.endswith(":a0") and oid_a in sells[0].decision_key
    assert sells[0].state == "ACKED"

    # Every later cycle cancels the resting attempt (> 45 s old) and re-plans it under the
    # SAME opportunity id: the intents table is what makes the counter climb.
    for expected in range(1, 5):
        assert _cycle(rig, advance=46).outcome is CycleOutcome.CONTINUE
        sells = _sell_intents(h)
        assert len(sells) == expected + 1
        assert sells[-1].decision_key.endswith(f":a{expected}")
        assert sells[-1].state == "ACKED"
        assert all(item.state == "CANCELED" for item in sells[:-1])
        assert all(oid_a in item.decision_key for item in sells)

    # Attempt 6 is refused: a4 is TTL-canceled like the rest but nothing replaces it.
    assert _cycle(rig, advance=46).outcome is CycleOutcome.CONTINUE
    sells = _sell_intents(h)
    assert len(sells) == 5
    assert all(item.state == "CANCELED" for item in sells)
    exhausted = _rows(h, action="skip", reason="sell_attempts_exhausted")
    assert len(exhausted) == 1
    assert exhausted[0]["opportunity_id"] == oid_a
    assert exhausted[0]["side"] == "sell"
    assert views.position_net_base(h.ledger, position.position_id) == D("10")

    # Another cycle on the SAME candle stays exhausted and writes no second row
    # (the dedupe holds), and the TTL cancel log shows exactly five burned attempts.
    assert _cycle(rig, advance=46).outcome is CycleOutcome.CONTINUE
    assert len(_sell_intents(h)) == 5
    assert len(_rows(h, action="skip", reason="sell_attempts_exhausted")) == 1
    assert len(_rows(h, action="cancel", reason="ttl_expired")) == 5


# ---- a restart keeps the count for the same candle -------------------------------------
def test_a_restart_keeps_the_attempt_count_of_the_same_candle(ws, harnesses):
    h = harnesses()
    rig = _rig(ws, h)
    _filled_position(h)
    frame = _candle(at=NOW, price="110")
    rig.market.overrides[SYMBOL] = frame
    oid_a = _exit_oid("exit-1", NOW)

    _cycle(rig)                                          # a0
    _cycle(rig, advance=46)                              # a0 canceled, a1 placed
    assert [item.decision_key[-2:] for item in _sell_intents(h)] == ["a0", "a1"]

    h.crash()                                            # a real restart: new ledger, lease
    rig = _rig(ws, h)
    rig.market.overrides[SYMBOL] = frame                 # the same candle is still served

    # The durable intents table is the counter: the new process already sees two attempts.
    inputs = build_cycle_inputs(h.ledger, rules={SYMBOL: decide_tests.rule()},
                              balance=h.mock.fetch_balance(), policy_set=h.ps)
    exits = scan_live({SYMBOL: frame}, inputs.positions, h.ps,
                      now=h.clock.seconds()).exits
    assert [item.opportunity_id for item in exits] == [oid_a]
    assert sell_attempts_for(h.ledger, exits, inputs.held, h.ps) == {oid_a: 2}

    # The next cycle cancels the pre-crash resting order and places attempt a2, not a0.
    assert _cycle(rig, advance=46).outcome is CycleOutcome.CONTINUE
    sells = _sell_intents(h)
    assert [item.decision_key[-2:] for item in sells] == ["a0", "a1", "a2"]
    assert all(oid_a in item.decision_key for item in sells)
    assert sells[2].state == "ACKED"
    assert _mock_status(h, sells[1]) == "CANCELED_UNFILLED"
    assert _mock_status(h, sells[2]) == "UNFILLED"


# ---- exhaustion survives a restart; the next candle reopens the window ------------------
def test_exhaustion_survives_restart_and_the_next_candle_reopens(ws, harnesses):
    h = harnesses()
    rig = _rig(ws, h)
    _filled_position(h)
    frame_a = _candle(at=NOW, price="110")
    rig.market.overrides[SYMBOL] = frame_a
    oid_a = _exit_oid("exit-1", NOW)

    _cycle(rig)
    for _ in range(5):
        _cycle(rig, advance=46)                          # a1..a4, then the exhausted skip
    assert len(_sell_intents(h)) == 5
    assert len(_rows(h, action="skip", reason="sell_attempts_exhausted")) == 1

    h.crash()
    rig = _rig(ws, h)
    rig.market.overrides[SYMBOL] = frame_a               # the same candle is still served
    _cycle(rig, advance=46)
    assert len(_sell_intents(h)) == 5                    # the restart reopened nothing
    # The decision LOG's dedupe is process-local: the skip is recorded once more.
    assert len(_rows(h, action="skip", reason="sell_attempts_exhausted")) == 2

    # The next candle rolls in with the exit condition intact: a fresh window, a0 again.
    rig.market.overrides[SYMBOL] = _candle(at=NOW + 300, price="110")
    assert _cycle(rig, advance=74).outcome is CycleOutcome.CONTINUE     # @ NOW+350
    sells = _sell_intents(h)
    assert len(sells) == 6
    retry = sells[-1]
    assert retry.decision_key.endswith(":a0")
    assert oid_a not in retry.decision_key
    assert _exit_oid("exit-1", NOW + 300) in retry.decision_key
    assert retry.state == "ACKED"
    assert _mock_status(h, retry) == "UNFILLED"


# ---- a candle where the exit signal fades opens no window -------------------------------
def test_a_candle_without_the_signal_opens_no_window(ws, harnesses):
    h = harnesses()
    rig = _rig(ws, h)
    _filled_position(h)
    rig.market.overrides[SYMBOL] = _candle(at=NOW, price="110")
    _cycle(rig)                                          # a0 under candle A
    assert len(_sell_intents(h)) == 1

    # Candle B closes back at cost (~-17 bps after sell costs): no exit rule matches, so
    # the resting a0 ages out and is canceled with NOTHING replacing it.
    rig.market.overrides[SYMBOL] = _candle(at=NOW + 300, price="100")
    assert _cycle(rig, advance=346).outcome is CycleOutcome.CONTINUE    # @ NOW+346
    sells = _sell_intents(h)
    assert len(sells) == 1 and sells[0].state == "CANCELED"
    assert len(_rows(h, action="plan")) == 1             # the only sell plan ever was a0

    # Candle C brings the exit condition back: a fresh window opens under the new id.
    rig.market.overrides[SYMBOL] = _candle(at=NOW + 600, price="110")
    assert _cycle(rig, advance=300).outcome is CycleOutcome.CONTINUE    # @ NOW+646
    sells = _sell_intents(h)
    assert len(sells) == 2
    assert sells[-1].decision_key.endswith(":a0")
    assert _exit_oid("exit-1", NOW + 600) in sells[-1].decision_key
    assert sells[-1].state == "ACKED"
