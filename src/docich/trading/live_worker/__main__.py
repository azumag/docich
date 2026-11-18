"""``python -m docich.trading.live_worker run``: the process entry point (Issue #3623, L4b-6b).

The runner in ``runner.py`` is fully dependency-injected.  This module is the only place that
supplies the real exchange pieces: a read-only ``BitbankReader`` (the owner's key file, the
read-only ccxt transport and the market rules) and public OHLCV market data from
``BitbankPublicGateway``.  The runner wires no order-placing component, so even a fully
wired, enabled worker has no order path here.

Wiring is a plain parameter (``main(..., wiring=real_wiring)``) so tests can pass a synthetic
exchange without an environment variable or a CLI flag selecting a mock.

``--interval`` accepts 30..3600 seconds (default 60); ``live.cycle.next_wait`` rejects
anything below 30.

``SIGTERM``/``SIGINT`` only set a plain flag; the runner polls it between <= 1 s sleep slices
and exits 0 with ``reason: stop_requested``.  Every exit path restores the previous handlers.

Exit codes are the runner's: 0 ok/inert, 2 config (including ``ImportError`` while wiring),
3 auth, 4 clock, 5 lease held, 7 lease lost, 8 retryable.  An exception that escapes the
wiring itself is reported to stderr as its class name only (no message, no traceback): a
message may contain key material, and this process may run under a journal.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from ..exchanges.bitbank_ccxt import BitbankPublicGateway
from ..exchanges.bitbank_live.factory import open_bitbank_reader
from ..live.policy import INITIAL_SYMBOLS
from .runner import EXIT_CONFIG, EXIT_RETRYABLE, run_worker

DEFAULT_INTERVAL_S = 60.0
MIN_INTERVAL_S = 30.0
MAX_INTERVAL_S = 3600.0
SLEEP_SLICE_S = 1.0

# The PAPER worker into the same strategy-lab scan uses 5-minute frames
# (worker.TIMEFRAME = "5m") with worker.HISTORY_LIMIT = strategy_lab.MAX_LOOKBACK + 1 = 25
# closes: a 24-period return/RSI (the largest lookback an experiment may set) needs 25
# closes.  live.signals uses exactly the same pair (TIMEFRAME = "5m", HISTORY_LIMIT = 25),
# so 5m/25 here gives scan_live every bar its activated policy can reference, and
# frame_from_ohlcv's min_bars=limit refuses a short batch instead of handing the scan a
# truncated history.
TIMEFRAME = "5m"
FRAME_BARS = 25


class _PublicMarketData:
    """``MarketData`` over public OHLCV only: one lazy gateway, one frame per call.

    Any exception propagates to the cycle, which records that symbol as stale for the current
    cycle and continues with the other symbols.  A market-data failure is not counted in
    ``consecutive_failures`` and never causes exit 8; only a failure while the runner
    constructs the market-data object at startup is retryable.  The gateway has no private or
    order-placing method.
    """

    def __init__(self) -> None:
        self._gateway = None

    def fetch_frame(self, symbol: str, now: float):
        if self._gateway is None:
            self._gateway = BitbankPublicGateway()
        frames = self._gateway.fetch_market_frames(
            [symbol], timeframe=TIMEFRAME, limit=FRAME_BARS, now=now)
        return frames[symbol]


def real_wiring(state_dir) -> dict:
    """The ``run_worker`` keyword arguments that need the real (read-only) exchange layer."""

    def open_reader(_state_dir):
        return open_bitbank_reader(state_dir, symbols=INITIAL_SYMBOLS)

    def make_market_data(_reader):
        return _PublicMarketData()

    return {"open_reader": open_reader, "make_market_data": make_market_data}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m docich.trading.live_worker",
        description="the LIVE worker: read-only exchange access, no order placement")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run until SIGTERM/SIGINT (or an exit code above)")
    run.add_argument("--state-dir", required=True,
                     help="absolute state directory (the LIVE files live in <state-dir>/trading-live)")
    run.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S,
                     help=f"seconds between cycles, {MIN_INTERVAL_S:g}..{MAX_INTERVAL_S:g} "
                          f"(default {DEFAULT_INTERVAL_S:g})")
    return parser


def _state_dir_ok(value: str) -> bool:
    """An absolute path whose parent exists (the path itself may be created by the ledger)."""
    if not os.path.isabs(value):
        return False
    parent = os.path.dirname(value)
    if not parent or not os.path.isdir(parent):
        return False
    return os.path.isdir(value) if os.path.exists(value) else True


def _fail(message: str) -> None:
    print(f"live_worker: {message}", file=sys.stderr)


def main(argv=None, *, wiring=real_wiring) -> int:
    """Parse ``run``, install the stop handlers and return the runner's exit code."""
    args = _parser().parse_args(argv)
    state_dir = args.state_dir
    interval = float(args.interval)

    # Validate before anything touches the filesystem or installs a handler.
    if not _state_dir_ok(state_dir):
        _fail("--state-dir must be an absolute path whose parent exists")
        return EXIT_CONFIG
    if not MIN_INTERVAL_S <= interval <= MAX_INTERVAL_S:
        _fail(f"--interval must be {MIN_INTERVAL_S:g}..{MAX_INTERVAL_S:g} seconds")
        return EXIT_CONFIG

    stopped = False

    def request_stop(_signum, _frame):
        # A signal handler does one thing only: set this plain flag.  No I/O, no lock.
        nonlocal stopped
        stopped = True

    def stop_requested() -> bool:
        return stopped

    def sleep(seconds: float) -> None:
        """Sleep in <= 1 s slices; return as soon as the stop flag is set."""
        remaining = float(seconds)
        while remaining > 0 and not stopped:
            step = SLEEP_SLICE_S if remaining > SLEEP_SLICE_S else remaining
            time.sleep(step)
            remaining -= step

    previous = {}
    try:
        previous[signal.SIGTERM] = signal.signal(signal.SIGTERM, request_stop)
        previous[signal.SIGINT] = signal.signal(signal.SIGINT, request_stop)
        kwargs = wiring(state_dir)
        return run_worker(state_dir=state_dir, clock=time.time,
                          stop_requested=stop_requested, sleep=sleep,
                          interval_s=interval, **kwargs)
    except ImportError as exc:
        print(type(exc).__name__, file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return EXIT_RETRYABLE
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    sys.exit(main())
