"""Build a ``BitbankReader`` from the owner's key file: key -> transport -> rules -> clock check.

Any failure ends in ``ReaderUnavailable`` with a short reason (never a value), so a caller can
only ever hold a reader that has loaded its market rules and found the local clock acceptable.

The clock: bitbank rejects a signed request whose time is 1 s or more *ahead* of its own, or older than the
time window (10 s here).  bitbank has no time endpoint, and ``depth.timestamp`` is when the book was published
(not "now": measured 0.7-2.4 s behind the local clock on a synced machine).  The usable server clock is the HTTP
``Date`` header of any response, which has whole-second resolution; ``local - Date`` is therefore at most one second
above the true offset, and the *minimum* over several calls spaced across a second is a good estimate.  The signed
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


def estimate_skew_s(transport, symbol: str, *, probes: int = CLOCK_PROBES) -> float:
    """Local minus bitbank, in seconds, from the response ``Date`` headers of a few public calls."""
    for _ in range(probes):
        transport.get("depth", {"pair": parse.pair_id(symbol)})
    samples = list(transport.clock_samples())[-probes:]
    if not samples:
        raise ValueError("no response carried a Date header")
    return min(samples)


def open_bitbank_reader(state_dir, *, symbols: Sequence[str], clock: Callable[[], float] = time.time,
                        source: str = "file", transport_factory: Callable[[object], object] | None = None,
                        max_ahead_s: float = MAX_LOCAL_AHEAD_S, max_behind_s: float = MAX_LOCAL_BEHIND_S,
                        probes: int = CLOCK_PROBES) -> BitbankReader:
    try:
        credentials = load_credentials(state_dir, source=source)
    except CredentialsUnavailable as exc:
        raise ReaderUnavailable(f"credentials: {exc.reason.value}") from None
    try:
        transport = (transport_factory or CcxtReadTransport)(credentials)
    except ReaderUnavailable:
        raise
    except Exception as exc:
        raise ReaderUnavailable(f"transport: {type(exc).__name__}") from None
    del credentials
    reader = BitbankReader(transport, symbols=symbols, clock=clock)       # ReaderUnavailable passes through
    try:
        skew = estimate_skew_s(transport, symbols[0], probes=probes)
    except (ExchangeError, ValueError) as exc:
        raise ReaderUnavailable(f"clock check failed ({type(exc).__name__})") from None
    if skew > max_ahead_s:
        raise ReaderUnavailable("clock: the local clock is ahead of bitbank's by too much")
    if skew < -max_behind_s:
        raise ReaderUnavailable("clock: the local clock is behind bitbank's by too much")
    return reader
