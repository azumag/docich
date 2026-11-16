"""Build a ``BitbankReader`` from the owner's key file: key -> transport -> rules -> clock check.

Any failure ends in ``ReaderUnavailable`` with a short reason (never a value), so a caller can
only ever hold a reader that has loaded its market rules and found the local clock close to bitbank's.
bitbank refuses requests whose time is more than a second ahead of its own (and older than the
time window), so a drifting clock must stop the LIVE side before it starts, not at the first read.
"""
from __future__ import annotations

import time
from typing import Callable, Sequence

from ...live.exchange import ExchangeError
from . import parse
from .credentials import CredentialsUnavailable, load_credentials
from .reader import BitbankReader, ReaderUnavailable
from .transport import CcxtReadTransport

MAX_CLOCK_SKEW_MS = 800


def measure_skew_ms(transport, symbol: str, *, wall_clock: Callable[[], float] = time.time) -> int:
    """Local minus bitbank, in milliseconds, from the public depth timestamp (midpoint of the request)."""
    before = wall_clock()
    data = transport.get("depth", {"pair": parse.pair_id(symbol)})
    after = wall_clock()
    server_ms = parse.ms_value(parse._field(data, "timestamp", "timestamp"), "timestamp")
    return int(round(((before + after) / 2) * 1000)) - server_ms


def open_bitbank_reader(state_dir, *, symbols: Sequence[str], clock: Callable[[], float] = time.time,
                        source: str = "file", wall_clock: Callable[[], float] = time.time,
                        transport_factory: Callable[[object], object] | None = None,
                        max_skew_ms: int = MAX_CLOCK_SKEW_MS) -> BitbankReader:
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
        skew = measure_skew_ms(transport, symbols[0], wall_clock=wall_clock)
    except (ExchangeError, ValueError) as exc:
        raise ReaderUnavailable(f"clock check failed ({type(exc).__name__})") from None
    if abs(skew) > max_skew_ms:
        raise ReaderUnavailable("clock: local time is too far from bitbank's")
    return reader
