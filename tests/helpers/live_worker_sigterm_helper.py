"""Run the LIVE worker entry point with a synthetic exchange until a signal stops it.

``tests/test_trading_live_worker_main.py`` starts this file as a separate process group,
waits for ``live_status.json`` to say ``running``, sends SIGTERM or SIGINT and asserts a
clean exit.  The state dir, the mock exchange and the market frames are synthetic; there is
no network, no key file and no sender.

The state dir is prepared here (the minimum of the runner tests' ``prepare``): a ledger, the
writer lease, a stored policy set, an owner activation and the on/trade control file.  The
helper then calls ``main`` with a fake wiring, exactly as a test would; ``main`` itself still
installs the real signal handlers and calls the real ``run_worker``.
"""
from __future__ import annotations

import sys
import time
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for _entry in (ROOT / "src", ROOT / "tests"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from docich.trading.live import control as live_control  # noqa: E402
from docich.trading.live.lease import acquire_writer_lease, release_writer_lease  # noqa: E402
from docich.trading.live.ledger import LiveLedger  # noqa: E402
from docich.trading.live.paths import live_layout  # noqa: E402
from docich.trading.live_worker.__main__ import main  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from test_trading_live_support import Clock, make_mock, policy_set  # noqa: E402

SYMBOLS = ("OAS/JPY",)


def prepare(state_dir: Path):
    """A ledger + active policy set + on/trade control file for ``state_dir``."""
    clock = Clock(start_ms=int(time.time() * 1000))
    mock, _ = make_mock(clock, symbols=SYMBOLS, jpy="10000")
    ps = policy_set(SYMBOLS)
    layout = live_layout(state_dir)
    paper = state_dir / "trading"
    paper.mkdir(exist_ok=True)
    ledger = LiveLedger.open(layout, paper_dir=paper, now=clock.seconds())
    try:
        lease = acquire_writer_lease(ledger, layout, now=clock.seconds())
        try:
            ledger.store_policy_set(lease, ps, now=clock.seconds())
            live_control.write_control(state_dir, state="on", mode="trade",
                                       updated_by="owner", now=clock.seconds())
            ledger.record_owner_activation(lease, ps, operator="owner", note="sigterm helper",
                                           now=clock.seconds())
        finally:
            release_writer_lease(ledger, lease, now=clock.seconds())
    finally:
        ledger.close()
    return mock


class _Reader:
    """The executor's inner reader: the mock plus a healthy ``health`` hook."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health(self):
        return SimpleNamespace(last_error_kind=None, consecutive_failures=0, last_ok_at=time.time())

    def fetch_balance(self):
        return self._inner.fetch_balance()


class _MarketData:
    def fetch_frame(self, symbol, now):
        return MarketFrame(symbol=str(symbol), timeframe_seconds=300,
                           timestamps=(float(now),), closes=(D("100"),), volumes=(D("1"),))


def main_with_fake_wiring(state_dir: Path, *, interval: float) -> int:
    reader = _Reader(prepare(state_dir))
    market_data = _MarketData()

    def wiring(_state_dir):
        return {"open_reader": lambda _given: reader,
                "make_market_data": lambda _reader: market_data}

    return main(["run", "--state-dir", str(state_dir), "--interval", str(interval)],
                wiring=wiring)


if __name__ == "__main__":
    _state = Path(sys.argv[1])
    _interval = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    sys.exit(main_with_fake_wiring(_state, interval=_interval))
