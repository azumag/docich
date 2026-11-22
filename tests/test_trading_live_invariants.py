"""Seeded random scenarios against the LIVE executor: the invariants that must hold whatever happens.

Operations, faults and crashes are drawn from ``random.Random(seed)``; nothing is time- or
network-dependent, so a failing seed reproduces exactly.
"""
import random
import subprocess
import sys
import textwrap
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.live import balance as bal
from docich.trading.live import executor as live_executor
from docich.trading.live import ingest as live_ingest
from docich.trading.live import ledger as live_ledger
from docich.trading.live import reconcile, views
from docich.trading.live import safety as sf
from docich.trading.live.exchange import Capabilities, OrderRequest
from docich.trading.live.ingest import ExecutorConfig
from test_trading_live_support import Harness, SimulatedCrash, Workspace, block_network

SRC = Path(live_ledger.__file__).parents[3]
CRASH_POINTS = ("before_mark_submitting", "after_submitting_commit", "after_send_before_record",
                "after_order_recorded", "after_ack_transition", "fill_recorded:1", "before_terminal_transition")
FAULTS = [("submit", "LOST_REQUEST"), ("submit", "LOST_RESPONSE"), ("submit", "REJECT"),
          ("submit", "VISIBILITY_LAG"), ("fill", "FILL_DELAY"), ("fill", "FEE_MISSING"),
          ("fetch_order", "READ_TIMEOUT"), ("fetch_open_orders", "READ_TIMEOUT"), ("fetch_fills", "READ_TIMEOUT"),
          ("fetch_balance", "READ_TIMEOUT"), ("fetch_fills", "DUPLICATE_FILL"), ("fetch_fills", "TRUNCATED_PAGE"),
          ("fetch_open_orders", "TRUNCATED_PAGE"), ("cancel", "LOST_RESPONSE"), ("cancel", "CANCEL_RACE"),
          ("fetch_order", "MALFORMED"), ("fetch_fills", "MALFORMED"), ("fetch_balance", "MALFORMED"),
          ("fetch_open_orders", "MALFORMED"), ("fetch_fills", "CONFLICTING_FILL")]
CAPS = {"client_id": None, "list_match": Capabilities(False, False, True)}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


def _hooks(point):
    def hook(p):
        if p == point:
            raise SimulatedCrash(point)
    live_executor.FAULT_HOOK = live_ingest.FAULT_HOOK = live_ledger.FAULT_HOOK = hook if point else None


def run_scenario(ws, seed, caps_name, ops=40):
    rng = random.Random(seed)
    h = Harness(ws, caps=CAPS[caps_name], jpy="20000", symbols=("OAS/JPY", "XYM/JPY"),
                config=ExecutorConfig(settle_s=120.0, sweep_every=1, fill_lag_polls=6))
    for symbol in h.symbols:
        h.mock.set_book(symbol, asks=[(D("100"), D("6")), (D("101"), D("40"))], bids=[(D("99"), D("40"))])
    sends = []
    real_submit = h.mock.submit_order

    def recording_submit(req):
        state = sf.read_safety_state(h.ledger)
        sends.append((req.side, state.block_new_buys, req.client_order_id))
        return real_submit(req)

    h.mock.submit_order = recording_submit
    keys = iter(range(10_000))

    def attempt(fn):
        try:
            return fn()
        except SimulatedCrash:
            _hooks(None)
            h.crash()
            reconcile.on_startup(h.ex)
        except (live_ledger.LiveLedgerError, ValueError):
            return None

    for _ in range(ops):
        choice = rng.random()
        symbol = rng.choice(h.symbols)
        if choice < 0.27:
            side = "buy"
            amount, price = str(rng.randint(1, 8)), rng.choice(["90", "100", "101"])
        elif choice < 0.38:
            held = int(h.mock._free(symbol.split("/")[0]))   # test-only peek, no fault applies
            side, amount, price = "sell", str(max(1, min(held, rng.randint(1, 8)))), rng.choice(["98", "99", "100"])
            if held < 1:
                continue
        elif choice < 0.48:
            if rng.random() < 0.8:
                _hooks(rng.choice(CRASH_POINTS))
                attempt(lambda: h.send(symbol=symbol, side="buy", key=f"k{next(keys)}", amount="5", price="101"))
                _hooks(None)
            continue
        elif choice < 0.70:
            op, kind = rng.choice(FAULTS)
            params = {"n": rng.randint(1, 3)} if kind in ("VISIBILITY_LAG", "FILL_DELAY") else {}
            if kind == "FEE_MISSING" and rng.random() < 0.5:
                params["backfill_after"] = rng.randint(0, 3)
            if kind == "REJECT":
                params["code"] = "busy"
            h.mock.faults.push(op, kind, nth=rng.randint(1, 2), **params)
            continue
        elif choice < 0.74:
            if rng.random() < 0.5:
                h.mock.place_external_order(OrderRequest(symbol, "buy", "limit", D(rng.randint(1, 5)), D("80"), None))
            continue
        elif choice < 0.82:
            h.mock.trade_against(symbol, taker_side=rng.choice(["sell", "buy"]), price=rng.choice(["90", "100", "101"]),
                                 amount=str(rng.randint(1, 10)))
            continue
        elif choice < 0.85:
            open_intents = h.ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED"))
            if open_intents:
                attempt(lambda: h.ex.cancel(rng.choice(open_intents).intent_id))
            continue
        else:
            attempt(h.ex.step)
            h.advance(rng.choice([1, 30, 121]))
            continue
        attempt(lambda: h.send(symbol=symbol, side=side, key=f"k{next(keys)}", amount=amount, price=price))

    # heal: no more faults, restart once, give the executor plenty of rounds
    h.mock.faults._items.clear()
    _hooks(None)
    h.crash()
    reconcile.on_startup(h.ex)
    for _ in range(14):
        attempt(h.ex.step)
        h.advance(121)
    return h, sends


@pytest.mark.parametrize("caps_name", ["client_id", "list_match"])
@pytest.mark.parametrize("seed", range(45))
def test_invariants_hold_for_random_scenarios(seed, caps_name):
    ws = Workspace()
    h = None
    try:
        h, sends = run_scenario(ws, seed, caps_name)
        mock, ledger = h.mock, h.ledger
        trips = set(h.trips())
        bot_orders = [s for s in mock.submissions if s["outcome"] == "created"]

        # 1. no buy ever left while buys were stopped
        assert not [s for s in sends if s[0] == "buy" and s[1]], (seed, sends)

        # 2. one intent never produces two exchange orders
        if caps_name == "client_id":
            cids = [s["client_order_id"] for s in bot_orders]
            assert len(cids) == len(set(cids)), (seed, cids)
        attempted = ledger.list_intents(states=("SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED",
                                                "FILLED", "CANCELED", "EXPIRED", "NOT_CREATED", "REJECTED"))
        assert len(bot_orders) <= len(attempted), (seed, len(bot_orders), len(attempted))

        # 3. nothing is left half-sent; NOT_CREATED never hides an order unless the sweep flagged it
        assert not ledger.list_intents(states=("SUBMITTING", "PLANNED")), seed
        if caps_name == "client_id":
            made = {s["client_order_id"] for s in bot_orders}
            for intent in ledger.list_intents(states=("NOT_CREATED",)):
                assert intent.client_order_id not in made or "unknown_order" in trips, (seed, intent)

        # 4. every recorded fill is a real fill, once, with the same size and price
        real = {(f.symbol + ":" + f.trade_id): f for f in mock.all_fills()}
        recorded = views.all_fills(ledger)
        assert len({f.fill_id for f in recorded}) == len(recorded)
        for fill in recorded:
            truth = real[fill.fill_id]
            assert (fill.amount, fill.price) == (truth.amount, truth.price), (seed, fill)

        # 5. when everything is recorded and nothing is suspicious, ledger and exchange balances agree
        bot_order_ids = {views_row for views_row in views.known_order_ids(ledger)}
        mine = [f for f in mock.all_fills() if f.exchange_order_id in bot_order_ids]
        clean = not (trips & {"unknown_order", "data_anomaly"}) and len(mine) == len(recorded)
        if clean and not [f for f in recorded if f.fee_status == "unknown"]:
            result = bal.check_balances(ledger, mock.fetch_balance(), h.ex.rules(), currencies=h.ex.watched_currencies())
            assert not result.mismatches(), (seed, result)
    finally:
        if h is not None:
            h.close()
        ws.cleanup()


def test_new_modules_import_without_network_or_side_effects(tmp_path):
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        import docich.trading.live.executor, docich.trading.live.reconcile, docich.trading.live.balance
        import docich.trading.live.ingest, docich.trading.live.views, docich.trading.live.fence
        bad = [m for m in ("ssl", "ccxt", "http.client", "urllib.request", "requests", "socket") if m in sys.modules]
        assert not bad, bad
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    import os
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert out.stdout.strip() == "clean", out.stderr
