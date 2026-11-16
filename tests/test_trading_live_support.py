"""Shared fixtures for the LIVE L1 tests (synthetic, no network)."""
import json
import os
import socket
import tempfile
from pathlib import Path

from docich.trading.live import enable as live_enable
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

    def write_enable(self, ps, *, mode="trade", revision="a" * 40, created="2026-01-01T00:00:00+09:00",
                     expires="2099-01-01T00:00:00+09:00", perm=0o600, extra=None):
        self.layout.owner_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.layout.root, 0o700)
        os.chmod(self.layout.owner_dir, 0o700)
        data = {"schema_version": 1, "kind": "docich-live-enable", "mode": mode,
                "policy_set_digest": ps.digest, "code_revision": revision,
                "created_at": created, "expires_at": expires}
        data.update(extra or {})
        path = self.layout.enable_path
        path.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(path, perm)
        return path

    def enable_result(self, ps, *, revision="a" * 40, now=1_800_000_000.0):
        return live_enable.live_enabled(self.state, policy_set_digest=ps.digest,
                                        code_revision=revision, now=now)


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
