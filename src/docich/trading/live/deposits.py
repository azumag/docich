"""Deposit-history reconciliation for LIVE (Issue #4587, under the parent #3623).

The ledger's expected balance counts cashflows and fills, so a deposit the owner makes
while the worker is live would otherwise sit in ``balance_warnings`` as an unexplained
difference forever.  ``run`` reads the exchange's own deposit history (read-only) and
records each credited deposit the ledger does not already count, under a deterministic
``<exchange>:deposit:<uuid>`` cashflow id, so re-reading the same history after a restart
is a no-op.

Every row the exchange reports is classified, never guessed:

- ``baseline``        -- at or just after the currency's baseline (the recorded opening
                        balance, else the ledger's own creation): part of the opening
                        snapshot, so counting it again would double count.
- ``pending``         -- the exchange has not credited it yet (bitbank ``FOUND`` /
                        ``CONFIRMED``): nothing is written; it may simply be balance lag.
- ``duplicate``       -- the exact record id already exists with the same content
                        (a repeat read; nothing is written).
- ``conflict``        -- the record id exists with different content; left alone, counted.
- ``already_counted`` -- an owner-recorded deposit of the same currency, amount and time
                        already covers it; ingesting it again would double count.
- ``recorded``        -- a new ledger cashflow with ``source='exchange_history'``.

``DepositPage.complete`` False is kept on the per-currency result and in the
``cashflow_check`` ledger event: the history may be cut off, so a missing row then proves
nothing and any residual difference stays an unexplained balance warning.

Only deposits are reconciled this way: the reference read path exposes no withdrawal
history, so a withdrawal stays an unexplained balance warning for the owner to account
for.  Nothing here infers cashflows from balance deltas, and no balance difference ever
stops the worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import views
from .exchange import DepositRecord, ExchangeError, dec_text, ms_to_seconds
from .ledger import Cashflow, LiveLedgerError
from .lease import LeaseLost
from .opening import ID_PREFIX as OPENING_ID_PREFIX

# A deposit this close to the baseline may already be inside the opening snapshot
# (the snapshot read and the exchange's own clock can disagree), so it is not counted.
BASELINE_MARGIN_S = 60.0
# An owner cashflow this close in time, same currency and amount, covers the same deposit.
MATCH_WINDOW_S = 3 * 86400.0


@dataclass(frozen=True)
class DepositCheck:
    """What one pass found for one currency (all counts are rows, not money)."""
    currency: str
    seen: int = 0
    complete: bool = True
    baseline: int = 0
    pending: int = 0
    duplicate: int = 0
    already_counted: int = 0
    conflict: int = 0
    recorded: int = 0
    error: str | None = None

    def summary(self) -> dict:
        out = {"seen": self.seen, "complete": 1 if self.complete else 0, "baseline": self.baseline,
               "pending": self.pending, "duplicate": self.duplicate, "already_counted": self.already_counted,
               "conflict": self.conflict, "recorded": self.recorded}
        if self.error is not None:
            out["error"] = self.error
        return out


def _baseline_s(rows: list[views.CashflowRow], ledger, currency: str) -> float:
    """The instant the books for ``currency`` started: the recorded opening balance, else
    the ledger's own creation.  Deposits at/before it (within the skew margin) are part of
    that snapshot."""
    opening = next((row for row in rows if row.cashflow_id == f"{OPENING_ID_PREFIX}{currency}"), None)
    return opening.occurred_at if opening is not None else views.ledger_created_at(ledger)


def _same(row: views.CashflowRow, record: DepositRecord, occurred_s: float) -> bool:
    return (row.kind == "deposit" and row.currency == record.currency and row.amount == record.amount
            and abs(row.occurred_at - occurred_s) <= 1.0)


def run(ex) -> dict[str, DepositCheck]:
    """One read-only reconciliation pass: ingest credited deposits the ledger does not count.

    Reads the exchange's deposit history per watched currency and records new ledger
    cashflows for provably new deposits.  Safe to run every few steps and at startup:
    every write is idempotent.  Returns the per-currency classification.
    """
    if not getattr(ex.reader.capabilities, "deposit_history", False):
        return {}
    try:
        currencies = ex.watched_currencies()
    except ExchangeError:           # market rules unreadable: try again next step
        return {}
    rows = views.cashflow_rows(ex.ledger)
    with_history = views.currencies_with_history(ex.ledger)
    checks: dict[str, DepositCheck] = {}
    for currency in sorted(currencies):
        if currency not in with_history:
            continue                # no books yet: the opening-balance path owns this currency
        checks[currency] = _check_currency(ex, currency, rows)
    if checks:
        _record_summary(ex, checks)
    return checks


def _check_currency(ex, currency: str, rows: list[views.CashflowRow]) -> DepositCheck:
    try:
        page = ex.reader.fetch_deposits(currency, since_ms=0)
    except LeaseLost:
        raise
    except Exception as exc:        # any read/parse failure: retry next pass, flag it
        return DepositCheck(currency=currency, complete=False, error=type(exc).__name__)
    baseline_s = _baseline_s(rows, ex.ledger, currency)
    by_id = {row.cashflow_id: row for row in rows}
    manual = [row for row in rows if row.kind == "deposit" and row.currency == currency
              and row.source == "owner_manual"]
    counts = {"baseline": 0, "pending": 0, "duplicate": 0, "already_counted": 0,
              "conflict": 0, "recorded": 0}
    error = None
    try:
        for record in sorted(page.deposits, key=lambda r: (r.occurred_at_ms, r.record_id)):
            if record.currency != currency:
                continue            # defensive: the reader already filters by currency
            if not record.credited:
                counts["pending"] += 1
                continue
            occurred_s = ms_to_seconds(record.occurred_at_ms)
            if occurred_s <= baseline_s + BASELINE_MARGIN_S:
                counts["baseline"] += 1
                continue
            existing = by_id.get(record.record_id)
            if existing is not None:
                counts["duplicate" if _same(existing, record, occurred_s) else "conflict"] += 1
                continue
            covered = any(row.amount == record.amount and abs(row.occurred_at - occurred_s) <= MATCH_WINDOW_S
                          for row in manual)
            if covered:
                counts["already_counted"] += 1
                continue
            try:
                ex.ledger.record_cashflow(
                    ex.lease, Cashflow(cashflow_id=record.record_id, kind="deposit", currency=currency,
                                       amount=dec_text(record.amount), source="exchange_history",
                                       occurred_at=occurred_s, note="from exchange deposit history"),
                    now=ex.clock())
            except LiveLedgerError:
                counts["conflict"] += 1  # the id appeared between read and write: never overwrite
                continue
            counts["recorded"] += 1
            row = views.CashflowRow(record.record_id, "deposit", currency, record.amount, occurred_s,
                                    "exchange_history")
            rows.append(row)
            by_id[record.record_id] = row
    except LeaseLost:
        raise
    except Exception as exc:        # a malformed record: keep what was written, flag the rest
        error = type(exc).__name__
    return DepositCheck(currency=currency, seen=len(page.deposits), complete=page.complete,
                        error=error, **counts)


def _record_summary(ex, checks: dict[str, DepositCheck]) -> None:
    try:
        ex.ledger.record_live_event(ex.lease, "cashflow_check",
                                    {"currencies": {c: check.summary() for c, check in checks.items()}},
                                    now=ex.clock())
    except LeaseLost:
        raise
    except LiveLedgerError:
        pass                        # the audit row is best-effort; the cashflows are the fact
