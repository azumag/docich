"""The one place the owner CLI reaches the real exchange layer (imported only when needed).

``status``, ``cashflow`` and ``unknown list`` never touch the exchange, so importing the CLI must not
import ``docich.trading.exchanges`` (and with it ccxt).  The factory is imported inside
``open_reader``; the reader it builds is read-only and may fail closed with a short reason.
"""
from __future__ import annotations


class ReaderUnavailableError(RuntimeError):
    """The read-only reader could not be built (a short reason, never key material)."""


def open_reader(state_dir):
    """A read-only ``ExchangeReader`` for ``state_dir``; the exchange layer is imported here, lazily."""
    from docich.trading.exchanges.bitbank_live.factory import ReaderUnavailable, open_bitbank_reader
    from docich.trading.live.policy import INITIAL_SYMBOLS

    try:
        return open_bitbank_reader(state_dir, symbols=INITIAL_SYMBOLS)
    except ReaderUnavailable as exc:
        raise ReaderUnavailableError(str(exc)) from exc
