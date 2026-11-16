"""Balance reconciliation (Issue #3623, L2): ledger-expected balance vs the exchange's balance.

Expected(c) = sum of cashflows + effect of every recorded fill (+ fee when known).  The opening
balance is whatever the owner recorded as cashflows; nothing is derived from the exchange, because
that would swallow a mismatch instead of exposing it.

Tolerance is never a percentage of the balance.  It is the rounding unit times (1 + number of
fills touching the currency), plus, for every fill whose fee is *unknown*, ``max_fee_rate`` times
its size in both currencies of the pair (a hidden fee may be in base or quote, and may even be a
rebate).  A mismatch larger than that is real.  When ``max_fee_rate`` is not known the currency is
``indeterminate`` and proves nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from . import views
from .exchange import BalanceSnapshot, MarketRules
from .safety import SafetyReason

_ZERO = Decimal(0)


@dataclass(frozen=True)
class CurrencyCheck:
    currency: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal
    status: str          # "ok" | "mismatch" | "indeterminate"

    @property
    def difference(self) -> Decimal:
        return self.actual - self.expected


@dataclass(frozen=True)
class BalanceCheckResult:
    checks: tuple[CurrencyCheck, ...]

    def mismatches(self) -> tuple[CurrencyCheck, ...]:
        return tuple(c for c in self.checks if c.status == "mismatch")

    def proven(self, currencies) -> bool:
        """True only when every named currency was checked and is ok (indeterminate is not proof)."""
        wanted = set(currencies)
        return wanted <= {c.currency for c in self.checks if c.status == "ok"}


@dataclass
class _Expected:
    total: Decimal = _ZERO
    touches: int = 0
    slack: Decimal = _ZERO
    indeterminate: bool = False


def _unit(currency: str, rules: Mapping[str, MarketRules]) -> Decimal:
    units = [r.quote_unit for r in rules.values() if r.quote == currency]
    units += [r.base_unit for r in rules.values() if r.base == currency]
    return max(units) if units else _ZERO


def expected_balances(ledger, rules: Mapping[str, MarketRules]) -> dict[str, _Expected]:
    out: dict[str, _Expected] = {}

    def cell(currency: str) -> _Expected:
        return out.setdefault(currency, _Expected())

    for kind, currency, amount in views.cashflows(ledger):
        cell(currency).total += -amount if kind == "withdrawal" else amount
    unruled = False
    for fill in views.all_fills(ledger):
        market = rules.get(fill.symbol)
        if market is None:
            unruled = True
            continue
        base, quote = cell(market.base), cell(market.quote)
        sign = 1 if fill.side == "buy" else -1
        base.total += sign * fill.amount
        quote.total -= sign * fill.quote_notional
        base.touches += 1
        quote.touches += 1
        if fill.fee_status == "known":
            fee = cell(fill.fee_currency)
            fee.total -= fill.fee_amount
            fee.touches += 1
        elif market.max_fee_rate is None:
            base.indeterminate = quote.indeterminate = True
        else:
            base.slack += fill.amount * market.max_fee_rate
            quote.slack += fill.quote_notional * market.max_fee_rate
    if unruled:
        for item in out.values():
            item.indeterminate = True
    return out


def check_balances(ledger, snapshot: BalanceSnapshot, rules: Mapping[str, MarketRules], *,
                   currencies) -> BalanceCheckResult:
    expected = expected_balances(ledger, rules)
    checks = []
    for currency in sorted(set(currencies)):
        cell = expected.get(currency, _Expected())
        asset = snapshot.assets.get(currency)
        actual = asset.total if asset is not None else _ZERO
        tolerance = _unit(currency, rules) * (1 + cell.touches) + cell.slack
        if cell.indeterminate:
            status = "indeterminate"
        else:
            status = "ok" if abs(actual - cell.total) <= tolerance else "mismatch"
        checks.append(CurrencyCheck(currency, cell.total, actual, tolerance, status))
    return BalanceCheckResult(tuple(checks))


def stable_check(ledger, reader, rules: Mapping[str, MarketRules], *, currencies,
                 between=None) -> tuple[BalanceCheckResult | None, bool]:
    """Read the balance, run ``between`` (the order/fill reads), read it again.

    Returns (result, stable).  Not stable (the totals or the ledger's fills changed in between)
    means a race with exchange activity: the result is None and the caller tries again later.
    """
    before_fills = views.fill_count(ledger)
    first = reader.fetch_balance()
    if between is not None:
        between()
    second = reader.fetch_balance()
    wanted = set(currencies)
    same = all((first.assets.get(c).total if first.assets.get(c) else _ZERO) ==
               (second.assets.get(c).total if second.assets.get(c) else _ZERO) for c in wanted)
    if not same or views.fill_count(ledger) != before_fills:
        return None, False
    return check_balances(ledger, second, rules, currencies=currencies), True


@dataclass
class BalanceMonitor:
    """Two stable checks with a mismatch, at least ``min_gap_s`` apart and no new fills in between, trip.

    The gap keeps a balance that merely lags behind (it shows up on the next poll) from stopping buys.
    """
    min_gap_s: float = 30.0
    streak: int = 0
    last_fill_count: int | None = None
    first_at: float | None = None
    tripped: bool = field(default=False)

    def observe(self, ledger, result: BalanceCheckResult | None, *, now: float) -> bool:
        if result is None:
            return False
        mismatches = result.mismatches()
        if not mismatches:
            self.streak, self.last_fill_count, self.first_at = 0, None, None
            return False
        count = views.fill_count(ledger)
        if self.streak == 0 or self.last_fill_count != count:
            self.streak, self.last_fill_count, self.first_at = 1, count, now
            return False
        if now - (self.first_at or now) < self.min_gap_s:
            return False
        self.streak += 1
        detail = "; ".join(f"{c.currency} expected {c.expected} actual {c.actual} tol {c.tolerance}"
                           for c in mismatches)
        ledger.trip_safety(SafetyReason.BALANCE_MISMATCH, detail=detail, now=now)
        self.tripped = True
        return True
