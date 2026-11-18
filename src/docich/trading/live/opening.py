"""Opening balances recorded automatically by the LIVE worker (Issue #3623, PR-B).

The owner never runs ``cashflow add`` for an opening balance.  For each watched currency
with no ledger history of any kind and a positive exchange total, this module writes ONE
deposit cashflow with source ``exchange_history`` (it is derived from the exchange) and an
id prefixed ``auto_opening:``.  The total is the exchange's ``total`` (free + locked): dust
is recorded exactly like any other positive balance.

Two consecutive ``reader.fetch_balance()`` reads must agree on a currency's total; a
currency whose reads differ is skipped and retried by the caller next time.  A currency
with ANY history -- a cashflow, a fill, a fee currency, or an intent that may have reached
the exchange -- is never touched: ``views.currencies_with_history`` already counts those,
so the ledger's own record wins over a guess from the exchange.

Nothing here trips a safety stop or blocks the worker.  A duplicate id (already recorded)
is swallowed.  Any failure other than ``LeaseLost`` returns what was recorded so far;
``LeaseLost`` propagates.  The caller retries later.
"""
from __future__ import annotations

from decimal import Decimal

from . import views
from .lease import LeaseLost
from .ledger import Cashflow

ZERO = Decimal(0)
SOURCE = "exchange_history"          # the ledger's CHECK allows owner_manual / exchange_history
ID_PREFIX = "auto_opening:"
NOTE = "opening balance recorded automatically"


def _total(balance, currency: str) -> Decimal:
    asset = balance.assets.get(currency)
    return ZERO if asset is None else asset.total


def record_auto_opening(ledger, lease, reader, currencies, *, now: float) -> tuple[str, ...]:
    """Record one ``auto_opening:<CCY>`` deposit per new, positive, stable currency.

    The cashflow source is ``exchange_history`` because the amount comes from the exchange;
    the id prefix and the note carry the ``auto_opening`` marker.  Returns the currencies
    written in this call (empty when there was nothing to do or an exception was swallowed).
    The reader is called exactly twice for the whole set.
    """
    recorded: list[str] = []
    try:
        history = views.currencies_with_history(ledger)
        wanted = sorted({str(currency).upper() for currency in currencies}
                        - {str(currency).upper() for currency in history})
        if not wanted:
            return ()
        first = reader.fetch_balance()
        second = reader.fetch_balance()
    except LeaseLost:
        raise
    except Exception:
        return ()

    for currency in wanted:
        try:
            first_total = _total(first, currency)
            second_total = _total(second, currency)
        except Exception:
            continue
        if first_total != second_total or first_total <= 0:
            continue
        try:
            ledger.record_cashflow(lease, Cashflow(
                cashflow_id=f"{ID_PREFIX}{currency}",
                kind="deposit",
                currency=currency,
                amount=str(first_total),
                source=SOURCE,
                occurred_at=now,
                note=NOTE,
            ), now=now)
        except LeaseLost:
            raise
        except Exception:
            # An already-recorded id is the normal no-op; any other write failure is
            # retried by the caller on a later attempt.
            continue
        recorded.append(currency)
    return tuple(recorded)
