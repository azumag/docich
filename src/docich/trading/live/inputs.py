"""One cycle's inputs for the LIVE worker (Issue #3623, L4b-5).

``build_cycle_inputs`` reads the LIVE ledger and one balance snapshot and returns
everything ``signals.scan_live`` and ``sizing.size_orders`` need: the funds (capital,
deployed cost, free quote), the positions that can be sold, the per-symbol skip sets
(legacy / ambiguous / open buy / open sell), the decision keys already handled, and the
reason codes that block every buy this cycle.

It only READS the ledger: no network, no clock, no file writes, no module-level mutable
state.  ``balance`` must be a snapshot the caller just fetched from the exchange, not one
remembered from an earlier poll.  ``ledger`` is a ``LiveLedger`` (or anything with the
same ``read_cursor``).

An outstanding buy counts against the investment limit (its notional is added to the
deployed cost), otherwise several symbols could be bought past the capital in total; the
quote it reserves is subtracted from the free quote for the same reason.  When a fee rate
or a cost basis cannot be determined the buy side is blocked with ``fee_rate_unknown``
and the affected symbol is reported in ``problems``; the rest of the inputs are still
filled, so other symbols' sells can still be planned (a symbol whose cost basis is
unavailable cannot be sold, only reported).

``sell_attempts_for`` counts, per exit opportunity, the decision keys of the attempts
already recorded for that position.  The key carries the digest the position was entered
under (falls back to the current policy digest only when the entry digest is unknown),
exactly as ``sizing`` builds it.

Money is ``Decimal`` (never ``float``).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Sequence

from . import views
from .balance import expected_balances
from .exchange import BalanceSnapshot, MarketRules
from .sizing import Funds, HeldPosition

if TYPE_CHECKING:  # imported for annotations only: keeps this module light at import time
    from ..models import Opportunity
    from .ledger import LiveLedger
    from .policy import LivePolicySet

ZERO = Decimal(0)
FEE_RATE_UNKNOWN = "fee_rate_unknown"
COST_BASIS_UNAVAILABLE = "cost_basis_unavailable"


@dataclass(frozen=True)
class CycleInputs:
    funds: Funds
    positions: Mapping[str, views.CostBasis]
    held: Mapping[str, HeldPosition]
    legacy_position_symbols: frozenset[str]
    ambiguous_position_symbols: frozenset[str]
    open_buy_symbols: frozenset[str]
    open_sell_symbols: frozenset[str]
    decided_keys: frozenset[str]
    block_buys: tuple[str, ...]
    problems: tuple[tuple[str, str], ...]


def _asset_free(balance: BalanceSnapshot, currency: str) -> Decimal:
    asset = balance.assets.get(currency)
    return ZERO if asset is None else asset.free


def build_cycle_inputs(ledger: LiveLedger, *, rules: Mapping[str, MarketRules],
                       balance: BalanceSnapshot, policy_set: LivePolicySet) -> CycleInputs:
    """Everything one LIVE cycle needs, read from ``ledger`` and ``balance`` (read-only)."""
    block_buys: set[str] = set()
    problems: set[tuple[str, str]] = set()

    capital = views.capital_quote(ledger, "JPY")
    fee_rates = {symbol: rule.max_fee_rate for symbol, rule in rules.items()}

    try:
        deployed = views.deployed_cost(ledger, fee_rates)
    except ValueError:
        deployed = ZERO
        block_buys.add(FEE_RATE_UNKNOWN)
    try:
        exposure = views.open_buy_exposure(ledger, rules)
    except ValueError:
        exposure = views.OpenBuyExposure(ZERO, ZERO)
        block_buys.add(FEE_RATE_UNKNOWN)

    expected = expected_balances(ledger, rules)
    jpy_expected = expected.get("JPY")
    expected_jpy_total = ZERO if jpy_expected is None else jpy_expected.total
    jpy_free = _asset_free(balance, "JPY")
    free_quote = max(ZERO, min(jpy_free, expected_jpy_total - exposure.reserved_quote))
    funds = Funds(capital=capital, deployed=deployed + exposure.notional, free_quote=free_quote)

    rows_by_symbol: dict[str, list[views.PositionRow]] = {}
    for row in views.open_position_rows(ledger):
        rows_by_symbol.setdefault(row.symbol, []).append(row)

    positions: dict[str, views.CostBasis] = {}
    held: dict[str, HeldPosition] = {}
    legacy: set[str] = set()
    ambiguous: set[str] = set()
    for symbol in sorted(rows_by_symbol):
        rows = rows_by_symbol[symbol]
        if len(rows) > 1:
            # Nothing is planned for a symbol with two positions; a sell would be ambiguous.
            ambiguous.add(symbol)
            continue
        row = rows[0]
        policy = policy_set.policies.get(symbol)
        if policy is not None and row.entry_policy_digest != policy.digest:
            # Entered under another digest: a buy here would open a second position.
            legacy.add(symbol)
        try:
            cost = views.position_cost(ledger, row.position_id, fee_rates.get(symbol))
        except ValueError:
            problems.add((symbol, COST_BASIS_UNAVAILABLE))
            continue
        if cost.net_base > 0:
            base = symbol.split("/")[0]
            positions[symbol] = cost
            held[symbol] = HeldPosition(
                position_id=row.position_id,
                net_base=cost.net_base,
                free_base=_asset_free(balance, base),
                entry_policy_digest=row.entry_policy_digest,
            )

    return CycleInputs(
        funds=funds,
        positions=positions,
        held=held,
        legacy_position_symbols=frozenset(legacy),
        ambiguous_position_symbols=frozenset(ambiguous),
        open_buy_symbols=views.open_intent_symbols(ledger, "buy"),
        open_sell_symbols=views.open_intent_symbols(ledger, "sell"),
        decided_keys=frozenset(views.decision_keys(ledger, "live1:")),
        block_buys=tuple(sorted(block_buys)),
        problems=tuple(sorted(problems)),
    )


def sell_attempts_for(ledger: LiveLedger, exits: Sequence[Opportunity],
                      held: Mapping[str, HeldPosition],
                      policy_set: LivePolicySet) -> dict[str, int]:
    """Attempts already recorded per exit opportunity, keyed by ``opportunity_id``.

    Only exits whose symbol is in ``held`` are counted; an exit with no recorded attempt
    maps to 0.  The key uses the digest the position was entered under, because the
    ledger refuses a sell under any other one.
    """
    attempts: dict[str, int] = {}
    for opportunity in exits:
        position = held.get(opportunity.symbol)
        if position is None:
            continue
        digest_used = position.entry_policy_digest
        if digest_used is None:
            policy = policy_set.policies.get(opportunity.symbol)
            if policy is None:
                continue
            digest_used = policy.digest
        prefix = (f"live1:{digest_used[:16]}:{opportunity.opportunity_id}:"
                  f"{position.position_id[:8]}:a")
        count = 0
        for key in views.decision_keys(ledger, prefix):
            tail = key[len(prefix):]
            if tail.isascii() and tail.isdigit():
                count += 1
        attempts[opportunity.opportunity_id] = count
    return attempts
