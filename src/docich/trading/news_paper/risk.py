"""Accept-time risk gate, sizing and reservations for the news PAPER account.

N05 owns quantities, reservations and accept/reject/hold decisions (parent
#769 B2/D7). Composition output carries no order fields, so this module turns
a decision candidate into a sized, reserved order proposal or a held decision
with fixed reason codes.

Risk policy values below are FIXTURES (``fixture-risk-v1``): N00 v2 §5 leaves
every risk number unapproved, so nothing here inherits the crypto defaults
(``max_total_deployed_fraction=1.0``, the stocks/fx 30%/3-position limits) and
nothing may become an operational setting without the owner's numeric
approval. Cost/lot/tick assumptions live in N06's ``InstrumentConstraints``
fixtures, which are equally explicit.

Accounting rules enforced here (parent #769 D8, issue #782):

* Cash and inventory are reserved separately; an unsettled sell proceeds
  never funds a buy (the fill-time snapshot excludes it).
* Risk-reducing orders (sells) are never blocked by loss limits: a news
  outage or a drawdown stops new risk only, never protective exits.
* Exposure caps are recorded with ``CURRENCY_EXPOSURE_LIMIT`` (gross,
  per-asset and currency exposure share one fixed code in D10); the number of
  open positions uses ``POSITION_LIMIT``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from typing import Mapping

from .execution import (
    InstrumentConstraints,
    RiskSnapshot,
    execution_price,
    round_lot_down,
)
from .ledger import AccountState, LedgerError, ReservationDraft

D = Decimal
ZERO = D("0")
TEN_THOUSAND = D("10000")


@dataclass(frozen=True)
class RiskPolicy:
    """Fixture risk limits; every field is mandatory (no inherited defaults)."""

    version: str
    max_positions: int
    max_gross_exposure: Decimal
    per_asset_exposure_cap: Decimal
    daily_loss_limit: Decimal
    drawdown_limit: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        if not isinstance(self.max_positions, int) or isinstance(self.max_positions, bool):
            raise LedgerError("TYPE_INVALID")
        if self.max_positions < 1:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        for name in ("max_gross_exposure", "per_asset_exposure_cap", "daily_loss_limit",
                     "drawdown_limit"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise LedgerError("VALUE_OUT_OF_RANGE")


FIXTURE_RISK_POLICY_V1 = RiskPolicy(
    version="fixture-risk-v1",
    max_positions=3,
    max_gross_exposure=D("3000000"),
    per_asset_exposure_cap=D("1000000"),
    daily_loss_limit=D("20000"),
    drawdown_limit=D("50000"),
)


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of the accept-time gate: accept with a reservation, or hold."""

    accepted: bool
    reason_codes: tuple[str, ...] = ()
    requested_quantity: Decimal = ZERO
    reservation: ReservationDraft | None = None
    estimated_notional: Decimal = ZERO


def gross_exposure(account: AccountState, ref_prices: Mapping[str, Decimal]) -> Decimal:
    """Absolute position value plus cash already reserved for pending buys.

    Positions are long-only in v1, so ``abs`` is defensive. Unknown market
    prices fall back to the position's cost basis (conservative for gains,
    optimistic for losses - valuation staleness is reported separately).
    """
    total = ZERO
    for asset, (quantity, avg_cost) in account.positions.items():
        price = ref_prices.get(asset, avg_cost)
        total += abs(quantity * price)
    return total + account.cash_reserved


def headroom_snapshot(*, account: AccountState, policy: RiskPolicy,
                      ref_prices: Mapping[str, Decimal],
                      exclude_reservation: ReservationDraft | None = None,
                      new_entries_allowed: bool = True) -> RiskSnapshot:
    """``RiskSnapshot`` for N06 with the order's own reservation added back.

    The fill of an order may consume exactly what its acceptance reserved, so
    the reservation of *this* order is excluded from the occupied amounts;
    every other active reservation still holds its cash/inventory.
    """
    own_cash = ZERO
    own_inventory = ZERO
    asset = ""
    if exclude_reservation is not None:
        asset = exclude_reservation.asset
        if exclude_reservation.kind == "cash":
            own_cash = Decimal(str(exclude_reservation.amount))
        else:
            own_inventory = Decimal(str(exclude_reservation.quantity))
    exposure = gross_exposure(account, ref_prices)
    if exclude_reservation is not None and exclude_reservation.kind == "cash":
        exposure -= Decimal(str(exclude_reservation.amount))
    cash_available = account.cash - (account.cash_reserved - own_cash)
    inventory_reserved = account.inventory_reserved.get(asset, ZERO)
    inventory_available = (
        account.positions.get(asset, (ZERO, ZERO))[0] - (inventory_reserved - own_inventory)
    )
    return RiskSnapshot(
        cash_available=max(ZERO, cash_available),
        inventory_available=max(ZERO, inventory_available),
        exposure_headroom=max(ZERO, policy.max_gross_exposure - exposure),
        new_entries_allowed=new_entries_allowed,
    )


def evaluate(*, account: AccountState, side: str, asset: str, ref_price: Decimal,
             constraints: InstrumentConstraints, policy: RiskPolicy, now: datetime,
             expires_at: datetime, quantity_cap: Decimal | None = None,
             daily_pnl: Decimal = ZERO, peak_equity: Decimal | None = None,
             current_equity: Decimal | None = None,
             event_already_used: bool = False) -> RiskDecision:
    """Gate one decision candidate and size its order (hold on any failure).

    Checks run in a fixed order so the same inputs always produce the same
    single reason code (N00 v2 §3.3: no ambiguity between codes).
    """
    ref_price = Decimal(str(ref_price))
    if ref_price <= 0:
        raise LedgerError("VALUE_OUT_OF_RANGE")
    if side not in {"buy", "sell"}:
        raise LedgerError("VALUE_OUT_OF_RANGE")
    codes: list[str] = []
    if event_already_used:
        codes.append("EVENT_ALREADY_USED")
    if now >= expires_at:
        codes.append("SIGNAL_EXPIRED")
    if codes:
        return RiskDecision(accepted=False, reason_codes=tuple(codes))

    risk_increasing = side == "buy"
    if risk_increasing:
        if daily_pnl <= -policy.daily_loss_limit:
            codes.append("DAILY_LOSS_LIMIT")
        if peak_equity is not None and current_equity is not None:
            drawdown = peak_equity - current_equity
            if drawdown >= policy.drawdown_limit:
                codes.append("DRAWDOWN_LIMIT")

    if codes:
        return RiskDecision(accepted=False, reason_codes=tuple(codes))

    # Sizing: minimum of headroom, exposure caps, optional participation cap,
    # floored to the lot. A sell sizes down to available inventory only.
    slip = constraints.slippage_bps / TEN_THOUSAND
    worst_unit_cost = ref_price * (1 + slip)
    if side == "buy":
        spendable = max(ZERO, account.cash_available - constraints.fee_min)
        by_cash = spendable / (worst_unit_cost * (1 + constraints.fee_bps / TEN_THOUSAND))
        exposure_room = policy.per_asset_exposure_cap / ref_price
        gross_room = (
            (policy.max_gross_exposure - gross_exposure(account, {asset: ref_price}))
            / ref_price
        )
        quantity = min(by_cash, exposure_room, gross_room)
    else:
        quantity = account.inventory_available(asset)
    if quantity_cap is not None:
        quantity = min(quantity, Decimal(str(quantity_cap)))
    quantity = round_lot_down(quantity, constraints.lot)
    if quantity <= 0 or quantity < constraints.min_quantity \
            or quantity * ref_price < constraints.min_notional:
        return RiskDecision(accepted=False, reason_codes=("MIN_ORDER_NOT_MET",))

    exposure_after = gross_exposure(account, {asset: ref_price})
    if side == "buy":
        exposure_after += quantity * ref_price
    if exposure_after > policy.max_gross_exposure:
        return RiskDecision(accepted=False, reason_codes=("CURRENCY_EXPOSURE_LIMIT",))
    per_asset_after = abs(account.positions.get(asset, (ZERO, ZERO))[0]) * ref_price
    if side == "buy":
        per_asset_after += quantity * ref_price
    if per_asset_after > policy.per_asset_exposure_cap:
        return RiskDecision(accepted=False, reason_codes=("CURRENCY_EXPOSURE_LIMIT",))
    if side == "buy" and asset not in account.positions \
            and len(account.positions) >= policy.max_positions:
        return RiskDecision(accepted=False, reason_codes=("POSITION_LIMIT",))

    estimated_notional = quantity * ref_price
    if side == "buy":
        worst_price = execution_price("buy", ref_price, constraints)
        fee = constraints.fee_for(quantity * worst_price)
        amount = D(_ceil_money(quantity * worst_price + fee))
        reservation = ReservationDraft(
            reservation_id="",  # assigned by the manager
            kind="cash",
            asset=asset,
            quantity=ZERO,
            amount=format(amount, "f"),
        )
    else:
        reservation = ReservationDraft(
            reservation_id="",
            kind="inventory",
            asset=asset,
            quantity=format(quantity, "f"),
            amount=ZERO,
        )
    return RiskDecision(
        accepted=True,
        reason_codes=(),
        requested_quantity=quantity,
        reservation=reservation,
        estimated_notional=estimated_notional,
    )


def _ceil_money(value: Decimal) -> Decimal:
    return value.quantize(D("0.00000001"), rounding=ROUND_CEILING)
