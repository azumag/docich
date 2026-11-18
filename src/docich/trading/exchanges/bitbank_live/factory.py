"""Build a ``BitbankReader`` from the owner's key file: key -> transport -> rules -> clock check.

Any failure ends in ``ReaderUnavailable`` with a short reason (never a value), so a caller can
only ever hold a reader that has loaded its market rules and found the local clock acceptable.

The clock: bitbank rejects a signed request whose time is 1 s or more *ahead* of its own, or older than the
time window (10 s here).  bitbank has no time endpoint, and ``depth.timestamp`` is when the book was published
(not "now": measured 0.7-2.4 s behind the local clock on a synced machine).  The usable server clock is the HTTP
``Date`` header of any response (whole-second resolution): each call gives an interval for the offset, and the
intervals of several calls spaced across a second intersect into a tight one.  The signed
requests made while building the reader are the real test (the server answers with a time-window error code, which is
classified as a clock failure); this check is the early, cheap one.
"""
from __future__ import annotations

import time
from typing import Callable, Sequence

from ...live.exchange import ExchangeError
from . import parse
from .credentials import CredentialsUnavailable, load_credentials
from .reader import BitbankReader, ReaderUnavailable
from .transport import CcxtReadTransport

MAX_LOCAL_AHEAD_S = 0.8      # bitbank's own limit is 1.0
MAX_LOCAL_BEHIND_S = 7.0     # the time window is 10 s
CLOCK_PROBES = 8             # at the transport's 0.2 s pacing this spans 1.6 s: every phase of the whole second
INCONSISTENCY_S = 0.05       # intervals that miss each other by more than this mean the clock moved: do not trust


def clock_bounds(transport, symbol: str, *, probes: int = CLOCK_PROBES) -> tuple[float, float]:
    """(low, high) for local minus bitbank, in seconds, from the response ``Date`` of a few public calls."""
    for _ in range(probes):
        transport.get("depth", {"pair": parse.pair_id(symbol)})
    bounds = transport.clock_bounds(probes)
    if bounds is None:
        raise ValueError("no response carried a Date header")
    return bounds


def judge_clock(low: float, high: float, *, max_ahead_s: float = MAX_LOCAL_AHEAD_S,
                max_behind_s: float = MAX_LOCAL_BEHIND_S) -> str | None:
    """None if the clock is acceptable, else the reason.  Fail-closed: the offset is only known to lie in
    [low, high], so "ahead" is judged by the highest it could be and "behind" by the lowest it could be."""
    if low > high + INCONSISTENCY_S:
        return "the clock readings are inconsistent (the clock moved while measuring?)"
    if high > max_ahead_s:
        return "the local clock may be ahead of bitbank's by too much"
    if low < -max_behind_s:
        return "the local clock may be behind bitbank's by too much"
    return None


def open_bitbank_reader(state_dir, *, symbols: Sequence[str], clock: Callable[[], float] = time.time,
                        source: str = "file", transport_factory: Callable[[object], object] | None = None,
                        max_ahead_s: float = MAX_LOCAL_AHEAD_S, max_behind_s: float = MAX_LOCAL_BEHIND_S,
                        probes: int = CLOCK_PROBES) -> BitbankReader:
    if not symbols:
        raise ReaderUnavailable("no symbols", kind="config")
    try:
        credentials = load_credentials(state_dir, source=source)
    except CredentialsUnavailable as exc:
        raise ReaderUnavailable(f"credentials: {exc.reason.value}", kind="config") from None
    try:
        transport = (transport_factory or CcxtReadTransport)(credentials)
    except ReaderUnavailable:
        raise
    except Exception as exc:
        raise ReaderUnavailable(f"transport: {type(exc).__name__}", kind="config") from None
    del credentials
    reader = BitbankReader(transport, symbols=symbols, clock=clock)       # ReaderUnavailable passes through
    try:
        low, high = clock_bounds(transport, symbols[0], probes=probes)
    except ExchangeError as exc:
        health_kind = getattr(exc, "health_kind", None)
        kind = health_kind if health_kind in ("auth", "clock") else None
        raise ReaderUnavailable(f"clock check failed ({type(exc).__name__}/{health_kind})", kind=kind) from None
    except ValueError as exc:
        raise ReaderUnavailable(f"clock check failed ({type(exc).__name__})", kind="clock") from None
    problem = judge_clock(low, high, max_ahead_s=max_ahead_s, max_behind_s=max_behind_s)
    if problem:
        raise ReaderUnavailable(f"clock: {problem}", kind="clock")
    return reader
