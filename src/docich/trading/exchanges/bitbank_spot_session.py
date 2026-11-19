"""The paired bitbank spot session: one protected key -> one reader and one order sender.

This is the only production-shaped place that builds ``BitbankSpotSender``.  The operator
chooses the session once, at process start (``live_worker run --spot-order-sender``), not at
runtime.  The existing ``live_control`` on+trade gate still decides whether a cycle may place
an order; this module only supplies the two exchange halves from the *same* key file.

The key is the fixed ``bitbank_spot_order_key.json`` in the owner-only LIVE directory, read by
``load_spot_order_credentials`` (no environment fallback, no caller-controlled path).  Both
halves are built from the one ``Credentials`` object: the reader is built through the normal
GET-only factory, and the sender receives that same object's revealed pair.  Two separately
configured accounts are never combined.

The paired clock check keeps the reader's existing ahead bound (0.8 s, tighter than bitbank's
1 s limit).  The sender signs with a 5 s window; budgeting 2 s for the request to reach bitbank
leaves a maximum of 3 s behind, so a freshly signed request is not already outside the window.
That is a bound on the clock, not a guarantee of delivery.  A local setup failure is
``ReaderUnavailable(kind="config")`` with a short reason only.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from .bitbank_live.credentials import CredentialsUnavailable, load_spot_order_credentials
from .bitbank_live.factory import open_bitbank_reader_from_credentials
from .bitbank_live.reader import BitbankReader, ReaderUnavailable
from .bitbank_spot_sender import TIME_WINDOW_MS, BitbankSpotSender

SIGNING_WINDOW_S = TIME_WINDOW_MS / 1000.0          # bitbank's 5 s window for a signed request
TRANSIT_BUDGET_S = 2.0                              # allowed for the request to reach bitbank
SPOT_SESSION_MAX_BEHIND_S = SIGNING_WINDOW_S - TRANSIT_BUDGET_S


def open_bitbank_spot_session(
        state_dir, *, symbols: Sequence[str], clock: Callable[[], float] = time.time,
        transport_factory: Callable[[object], object] | None = None,
) -> tuple[BitbankReader, BitbankSpotSender]:
    """Build the GET-only reader and the spot order sender from one account's fixed key file.

    A missing or invalid key, an invalid local sender configuration, or any reader startup
    failure raises ``ReaderUnavailable``; the runner maps its ``kind`` exactly like the
    read-only reader's.  There is deliberately no return value with only one half.
    """
    try:
        credentials = load_spot_order_credentials(state_dir)
    except CredentialsUnavailable as exc:
        raise ReaderUnavailable(f"credentials: {exc.reason.value}", kind="config") from None

    key, secret = credentials.reveal()
    sender_failure = None
    try:
        sender = BitbankSpotSender(key, secret, symbols, wall_clock=clock)
    except Exception as exc:
        sender_failure = f"sender: {type(exc).__name__}"
    finally:
        del key, secret
    if sender_failure is not None:
        # Raised outside the handler: the raw exception (and any value or path in it) is not
        # retained as __context__.
        raise ReaderUnavailable(sender_failure, kind="config")

    reader_failure = None
    try:
        reader = open_bitbank_reader_from_credentials(
            credentials, symbols=symbols, clock=clock, transport_factory=transport_factory,
            max_behind_s=SPOT_SESSION_MAX_BEHIND_S)
    except ReaderUnavailable:
        raise                                   # auth/clock stay typed for the runner's exit code
    except Exception as exc:
        reader_failure = f"reader: {type(exc).__name__}"
    if reader_failure is not None:
        raise ReaderUnavailable(reader_failure, kind="config")
    return reader, sender


__all__ = ["open_bitbank_spot_session", "SIGNING_WINDOW_S", "TRANSIT_BUDGET_S",
           "SPOT_SESSION_MAX_BEHIND_S"]
