"""Shared fixtures for the LIVE L1 tests (synthetic, no network)."""
import os
import socket
import tempfile
from pathlib import Path

from docich.trading.live import control as live_control
from docich.trading.live.ledger import LiveLedger
from docich.trading.live.paths import live_layout
from docich.trading.live.policy import build_policy_set, load_experiment_snapshot, experiment_digest
from docich.trading.strategy_lab import experiment_from_mapping


def synthetic_mapping():
    return {
        "schema_version": 1, "experiment_id": "synthetic_v1", "name": "synthetic rebound",
        "thesis": "synthetic fixture for LIVE tests",
        "entry_rules": [
            {"combine": "all", "conditions": [
                {"feature": "drawdown_bps", "lookback": 12, "op": "<=", "threshold": "-120"},
                {"feature": "rsi", "lookback": 12, "op": "<=", "threshold": "45"}],
             "max_notional_fraction": "0.07", "rule_id": "entry-1"},
            {"combine": "all", "conditions": [
                {"feature": "zscore", "lookback": 12, "op": "<=", "threshold": "-2.0"}],
             "max_notional_fraction": "0.05", "rule_id": "entry-2"}],
        "exit_rules": [
            {"combine": "all", "conditions": [{"feature": "pnl_bps", "op": ">=", "threshold": "60"}],
             "max_notional_fraction": "0.10", "rule_id": "exit-1"},
            {"combine": "all", "conditions": [{"feature": "pnl_bps", "op": "<=", "threshold": "-150"}],
             "max_notional_fraction": "0.10", "rule_id": "exit-2"}],
        "max_pair_correlation": "0.35", "activated_at": 1.0,
    }


def synthetic_spec():
    return experiment_from_mapping(synthetic_mapping())


def policy_set(symbols=("OAS/JPY", "XYM/JPY")):
    return build_policy_set(synthetic_spec(), symbols)


class Workspace:
    """A temp state dir with a PAPER dir next to the LIVE root."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)
        self.paper = self.state / "trading"
        self.paper.mkdir()
        self.layout = live_layout(self.state)

    def cleanup(self):
        self._tmp.cleanup()

    def open_ledger(self, now=1000.0):
        return LiveLedger.open(self.layout, paper_dir=self.paper, now=now)

    def write_control(self, *, state="on", mode="trade", updated_by="owner",
                      now=1_800_000_000.0, perm=None):
        """Write the owner's control file (on/trade by default); ``perm`` for a bad mode."""
        live_control.write_control(self.state, state=state, mode=mode,
                                   updated_by=updated_by, now=now)
        path = self.layout.control_path
        if perm is not None:
            os.chmod(path, perm)
        return path

    def control_result(self, *, now=1_800_000_000.0):
        return live_control.read_control(self.state, now=now)


def block_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("LIVE L1 tests must not use the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# ---- L2: mock exchange fixtures --------------------------------------------------------
from decimal import Decimal as _D

from docich.trading.live.exchange import Capabilities, MarketRules
from docich.trading.live.mock_exchange import MockExchange, MockFees


class Clock:
    """A settable millisecond clock shared by the mock and the executor tests."""

    def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
        self.ms = start_ms

    def __call__(self) -> int:
        return self.ms

    def seconds(self) -> float:
        return self.ms / 1000

    def advance(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


def market_rules(symbol="OAS/JPY", **kw):
    base, quote = symbol.split("/")
    values = dict(symbol=symbol, base=base, quote=quote, amount_step=_D("1"), min_amount=_D("1"),
                  price_tick=_D("0.001"), base_unit=_D("0.0001"), quote_unit=_D("0.0001"),
                  max_fee_rate=_D("0.002"))
    values.update(kw)
    return MarketRules(**values)


def make_mock(clock=None, *, symbols=("OAS/JPY",), jpy="10000", fees=None, caps=None, state_path=None, **kw):
    clock = clock or Clock()
    markets = {s: market_rules(s) for s in symbols}
    balances = {"JPY": _D(jpy), **{s.split("/")[0]: _D(0) for s in symbols}}
    caps = caps or Capabilities(client_order_id=True, fetch_by_client_id=True, fills_by_order=True,
                                client_order_id_max_len=32)
    mock = MockExchange(clock_ms=clock, markets=markets, balances=balances,
                        fees=fees or MockFees(maker_rate=_D("0"), taker_rate=_D("0.001")),
                        capabilities=caps, state_path=state_path, **kw)
    return mock, clock


# ---- L2b: executor harness ---------------------------------------------------------------
from docich.trading.live import executor as live_executor
from docich.trading.live import ingest as live_ingest
from docich.trading.live import ledger as live_ledger
from docich.trading.live.ingest import ExecutorConfig
from docich.trading.live.lease import acquire_writer_lease
from docich.trading.live.ledger import Cashflow, IntentRequest


class SimulatedCrash(BaseException):
    """Raised from a FAULT_HOOK: unwinds like a process death (every open transaction rolls back)."""


class Harness:
    """Ledger + lease + mock exchange + executor on one fake clock.  ``crash()`` is a process restart."""

    def __init__(self, ws, *, symbols=("OAS/JPY",), jpy="10000", caps=None, fees=None, config=None,
                 gate_open=True, opening=True, state_path=None):
        self.ws, self.symbols = ws, tuple(symbols)
        self.clock = Clock()
        self.mock, _ = make_mock(self.clock, symbols=self.symbols, jpy=jpy, caps=caps, fees=fees,
                                 state_path=state_path)
        self.ps = policy_set(self.symbols)
        self.config = config or ExecutorConfig(settle_s=120.0, sweep_every=1)
        self.gate_open = gate_open
        self.seq = 0
        self.boot()
        if opening:
            self.deposit("JPY", jpy)
        if gate_open:
            ws.write_control()

    # -- life cycle
    def boot(self):
        self.ledger = self.ws.open_ledger(now=self.clock.seconds())
        self.lease = acquire_writer_lease(self.ledger, self.ws.layout, now=self.clock.seconds())
        self.ledger.store_policy_set(self.lease, self.ps, now=self.clock.seconds())
        self.ex = live_executor.OrderExecutor(
            self.ledger, self.lease, reader=self.mock, sender=self.mock, enable_check=self._gate,
            clock=self.clock.seconds, symbols=self.symbols, config=self.config)

    def _gate(self):
        return self.ws.control_result()

    def crash(self):
        """The process dies: the lock is released by the OS, nothing is closed politely."""
        os.close(self.lease.fd)
        self.ledger.close()
        self.boot()

    def close(self):
        live_executor.FAULT_HOOK = None
        live_ingest.FAULT_HOOK = None
        live_ledger.FAULT_HOOK = None
        try:
            self.ledger.close()
        except Exception:
            pass

    # -- helpers
    def deposit(self, currency, amount):
        self.seq += 1
        self.ledger.record_cashflow(self.lease, Cashflow(f"cf{self.seq}", "deposit", currency, str(amount),
                                                         "owner_manual", self.clock.seconds()),
                                    now=self.clock.seconds())

    def req(self, symbol="OAS/JPY", side="buy", key="k1", amount="10", price="100", order_type="limit",
            rule_id=None):
        return IntentRequest(decision_key=key, symbol=symbol, side=side, order_type=order_type, amount=amount,
                             price=None if order_type == "market" else price,
                             policy_digest=self.ps.policy_for(symbol).digest,
                             rule_id=rule_id or ("entry-1" if side == "buy" else "exit-1"))

    def send(self, **kw):
        intent = self.ex.plan(self.req(**kw))
        return intent, self.ex.submit(intent.intent_id)

    def state(self, intent_id):
        return self.ledger.get_intent(intent_id).state

    def trips(self):
        return sorted(t.reason for t in live_safety_read(self.ledger).trips)

    def advance(self, seconds):
        self.clock.advance(seconds)


def live_safety_read(ledger):
    from docich.trading.live import safety
    return safety.read_safety_state(ledger)
