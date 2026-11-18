"""Shadow-mode simulation over the hash-chained LIVE event log (Issue #3623, L4c).

Shadow mode never reaches an exchange, an intent, an order or a real fill.  It projects a
small ``ShadowBook`` from the immutable ``shadow_fill`` events, injects that book's
simulated positions and quote cash into the normal pure scan/size path, and executes each
resulting limit plan against the exact book ``DecisionSet.markets`` already validated by
``decide.build_decisions``.  Execution is a price-limited VWAP over the visible depth.

The durable state is the ordered ``shadow_fill`` stream.  A process starts with an empty
book, replays new events in bounded pages and then reads only events after the last seen
event id.  The book does not keep past fill objects: it keeps the fill ids (with a content
digest for idempotence), decision keys, per-opportunity sell attempts and the identities
of partial sell books.  Reporting iterates the stream separately (``shadow_report``).

A fill is appended before it is applied to memory.  A crash between the two is recovered
by the next replay; a failed append changes neither the log nor the book.  ``LeaseLost``
always propagates.  Payloads contain canonical strings and integers only.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping

from . import views
from .canonical import canonical_json, sha256_hex
from .decide import DecisionSet, build_decisions
from .exchange import MarketRules, dec, dec_text
from .inputs import CycleInputs
from .lease import LeaseLost
from .sizing import (Funds, HeldPosition, OrderPlan, SymbolMarket, _BookUnusable,
                     _rules_values, _usable_levels, floor_step)

if TYPE_CHECKING:  # annotations only: keeps import time and dependency direction simple
    from .cycle import CycleContext, CycleDeps, CycleState

ZERO = Decimal(0)
ONE = Decimal(1)
SHADOW_EVENT_KIND = "shadow_fill"
SHADOW_RESIDUAL_KIND = "shadow_residual"
SHADOW_EVENT_KINDS = (SHADOW_EVENT_KIND, SHADOW_RESIDUAL_KIND)
SHADOW_RESIDUAL_ACTION = "residual"
RESIDUAL_BELOW_MIN = "below_min_amount"
SHADOW_MODE = "shadow"
SHADOW_DECISION_PREFIX = "shadow1"
NO_LIQUIDITY = "no_liquidity"
NO_POSITION = "no_position"
POLICY_MISMATCH = "policy_mismatch"
ALREADY_FILLED = "already_filled"
BOOK_EXHAUSTED = "book_exhausted"
RECORD_FAILED = "record_failed"
SHADOW_PAGE_SIZE = 250
MAX_PAGE_SIZE = 10_000


class ShadowBookError(RuntimeError):
    """The shadow event stream is malformed or contradicts the projected book."""


def _decimal(value: object, name: str) -> Decimal:
    try:
        number = dec(value)
    except (TypeError, ValueError) as exc:
        raise ShadowBookError(f"{name} is not an exact decimal") from exc
    if not number.is_finite():
        raise ShadowBookError(f"{name} is not finite")
    return number


def _positive(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number <= 0:
        raise ShadowBookError(f"{name} must be positive")
    return number


def _nonnegative(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number < 0:
        raise ShadowBookError(f"{name} must not be negative")
    return number


def _side(value: object) -> str:
    side = str(value or "").strip().lower()
    if side not in ("buy", "sell"):
        raise ShadowBookError("side must be buy or sell")
    return side


def _symbol_parts(symbol: str) -> tuple[str, str]:
    parts = str(symbol).split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ShadowBookError("symbol must be a BASE/QUOTE pair")
    return parts[0], parts[1]


def _position_id(decision_key: str) -> str:
    """Hash entropy starts at character 0: sell sizing keys on ``position_id[:8]``."""
    digest = sha256_hex(decision_key.encode("utf-8"))
    return f"{digest[:8]}:shadow:{digest[8:32]}"


def _fill_id(decision_key: str) -> str:
    return "shadow:" + sha256_hex(("fill:" + decision_key).encode("utf-8"))[:32]


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ShadowBookError(f"{name} must be an int")
    return value


def _required(payload: Mapping, name: str) -> object:
    if name not in payload:
        raise ShadowBookError(f"shadow fill is missing {name}")
    return payload[name]


@dataclass(frozen=True)
class ShadowFill:
    """One simulated execution, also the exact shape stored in a ``shadow_fill`` event."""

    fill_id: str
    decision_key: str
    opportunity_id: str
    position_id: str
    symbol: str
    side: str
    amount: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal
    fee_currency: str
    fee_rate: Decimal
    planned_amount: Decimal
    planned_price: Decimal
    status: str
    remaining: Decimal
    policy_digest: str
    experiment_digest: str
    executed_at_ms: int
    levels_used: int
    book_ts_ms: int
    bar_ts_ms: int
    timeframe_s: int
    rule_id: str = ""
    event_id: int = 0

    def __post_init__(self) -> None:
        for name in ("amount", "price", "notional", "fee", "fee_rate", "planned_amount",
                     "planned_price", "remaining"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                object.__setattr__(self, name, _decimal(value, name))
        for name in ("fill_id", "decision_key", "position_id", "symbol", "policy_digest"):
            if not str(getattr(self, name) or ""):
                raise ShadowBookError(f"{name} is required")
        _symbol_parts(self.symbol)
        object.__setattr__(self, "side", _side(self.side))
        if self.amount <= 0 or self.price <= 0 or self.notional <= 0:
            raise ShadowBookError("a fill needs positive amount, price and notional")
        if self.fee < 0 or self.fee_rate < 0 or self.remaining < 0:
            raise ShadowBookError("fee, fee_rate and remaining must not be negative")
        if self.amount > self.planned_amount:
            raise ShadowBookError("a fill cannot exceed its planned amount")
        status = str(self.status or "").strip().lower()
        if status not in ("filled", "partial"):
            raise ShadowBookError("status must be filled or partial")
        if status == "filled" and self.remaining != 0:
            raise ShadowBookError("a filled fill cannot have a remainder")
        if status == "partial" and self.remaining <= 0:
            raise ShadowBookError("a partial fill needs a positive remainder")
        object.__setattr__(self, "status", status)
        for name in ("executed_at_ms", "levels_used", "book_ts_ms", "bar_ts_ms",
                     "timeframe_s", "event_id"):
            value = _int(getattr(self, name), name)
            if value < 0:
                raise ShadowBookError(f"{name} must not be negative")
            object.__setattr__(self, name, value)
        if not self.fee_currency:
            object.__setattr__(self, "fee_currency", _symbol_parts(self.symbol)[1])

    def payload(self) -> dict[str, object]:
        """Canonical strings/ints only; the ledger envelope carries the float time."""
        return {
            "schema_version": 1,
            "mode": SHADOW_MODE,
            "order_type": "limit",
            "fill_id": self.fill_id,
            "decision_key": self.decision_key,
            "opportunity_id": self.opportunity_id,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "policy_digest": self.policy_digest,
            "experiment_digest": self.experiment_digest,
            "rule_id": self.rule_id,
            "planned_amount": dec_text(self.planned_amount),
            "planned_price": dec_text(self.planned_price),
            "amount": dec_text(self.amount),
            "price": dec_text(self.price),
            "notional": dec_text(self.notional),
            "fee": dec_text(self.fee),
            "fee_currency": self.fee_currency or _symbol_parts(self.symbol)[1],
            "fee_rate": dec_text(self.fee_rate),
            "status": self.status,
            "remaining": dec_text(self.remaining),
            "levels_used": int(self.levels_used),
            "book_ts_ms": int(self.book_ts_ms),
            "bar_ts_ms": int(self.bar_ts_ms),
            "timeframe_s": int(self.timeframe_s),
            "executed_at_ms": int(self.executed_at_ms),
        }

    @classmethod
    def from_payload(cls, payload: Mapping, *, event_id: int = 0) -> "ShadowFill":
        if not isinstance(payload, Mapping):
            raise ShadowBookError("shadow payload must be an object")
        if payload.get("mode") != SHADOW_MODE:
            raise ShadowBookError("a shadow fill must say mode=shadow")
        if payload.get("schema_version") != 1:
            raise ShadowBookError("unsupported shadow fill schema")
        if payload.get("order_type") != "limit":
            raise ShadowBookError("shadow fills are limit executions")
        symbol = str(_required(payload, "symbol") or "")
        base, quote = _symbol_parts(symbol)
        return cls(
            fill_id=str(_required(payload, "fill_id") or ""),
            decision_key=str(_required(payload, "decision_key") or ""),
            opportunity_id=str(_required(payload, "opportunity_id") or ""),
            position_id=str(_required(payload, "position_id") or ""),
            symbol=symbol,
            side=str(_required(payload, "side") or ""),
            amount=_positive(_required(payload, "amount"), "amount"),
            price=_positive(_required(payload, "price"), "price"),
            notional=_positive(_required(payload, "notional"), "notional"),
            fee=_nonnegative(_required(payload, "fee"), "fee"),
            fee_currency=str(payload.get("fee_currency") or quote),
            fee_rate=_nonnegative(_required(payload, "fee_rate"), "fee_rate"),
            planned_amount=_positive(_required(payload, "planned_amount"), "planned_amount"),
            planned_price=_positive(_required(payload, "planned_price"), "planned_price"),
            status=str(_required(payload, "status") or ""),
            remaining=_nonnegative(_required(payload, "remaining"), "remaining"),
            policy_digest=str(_required(payload, "policy_digest") or ""),
            experiment_digest=str(payload.get("experiment_digest") or ""),
            executed_at_ms=_int(_required(payload, "executed_at_ms"), "executed_at_ms"),
            levels_used=_int(_required(payload, "levels_used"), "levels_used"),
            book_ts_ms=_int(_required(payload, "book_ts_ms"), "book_ts_ms"),
            bar_ts_ms=_int(_required(payload, "bar_ts_ms"), "bar_ts_ms"),
            timeframe_s=_int(_required(payload, "timeframe_s"), "timeframe_s"),
            rule_id=str(payload.get("rule_id") or ""),
            event_id=int(event_id),
        )

    @classmethod
    def from_event(cls, event) -> "ShadowFill":
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            raise ShadowBookError("shadow event has no payload object")
        return cls.from_payload(payload, event_id=int(getattr(event, "event_id", 0) or 0))


@dataclass(frozen=True)
class ShadowResidual:
    """The durable close of simulated dust that the real path would mark residual."""

    position_id: str
    symbol: str
    amount: Decimal
    reason: str = RESIDUAL_BELOW_MIN
    event_id: int = 0

    def __post_init__(self) -> None:
        if not str(self.position_id or ""):
            raise ShadowBookError("position_id is required")
        _symbol_parts(self.symbol)
        object.__setattr__(self, "amount", _positive(self.amount, "amount"))
        if not str(self.reason or ""):
            raise ShadowBookError("reason is required")
        object.__setattr__(self, "event_id", _int(self.event_id, "event_id"))
        if self.event_id < 0:
            raise ShadowBookError("event_id must not be negative")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mode": SHADOW_MODE,
            "action": SHADOW_RESIDUAL_ACTION,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "amount": dec_text(self.amount),
            "reason": self.reason,
        }

    @classmethod
    def from_payload(cls, payload: Mapping, *, event_id: int = 0) -> "ShadowResidual":
        if not isinstance(payload, Mapping):
            raise ShadowBookError("shadow residual payload must be an object")
        if payload.get("mode") != SHADOW_MODE:
            raise ShadowBookError("a shadow residual must say mode=shadow")
        if payload.get("schema_version") != 1:
            raise ShadowBookError("unsupported shadow residual schema")
        if payload.get("action") != SHADOW_RESIDUAL_ACTION:
            raise ShadowBookError("not a shadow residual row")
        return cls(
            position_id=str(_required(payload, "position_id") or ""),
            symbol=str(_required(payload, "symbol") or ""),
            amount=_positive(_required(payload, "amount"), "amount"),
            reason=str(payload.get("reason") or RESIDUAL_BELOW_MIN),
            event_id=int(event_id),
        )

    @classmethod
    def from_event(cls, event) -> "ShadowResidual":
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            raise ShadowBookError("shadow event has no payload object")
        return cls.from_payload(payload, event_id=int(getattr(event, "event_id", 0) or 0))


@dataclass(frozen=True)
class ShadowPosition:
    """A simulated open position, keyed by symbol because LIVE allows one per symbol."""

    position_id: str
    symbol: str
    amount: Decimal
    cost: Decimal
    opened_at: float
    entry_policy_digest: str
    entry_experiment_digest: str = ""

    @property
    def avg_cost(self) -> Decimal:
        return self.cost / self.amount if self.amount > 0 else ZERO

    @property
    def cost_basis(self) -> views.CostBasis:
        return views.CostBasis(self.amount, self.cost, self.avg_cost, self.opened_at)

    @property
    def held(self) -> HeldPosition:
        return HeldPosition(position_id=self.position_id, net_base=self.amount,
                            free_base=self.amount,
                            entry_policy_digest=self.entry_policy_digest)


@dataclass(frozen=True)
class _FillTransition:
    """The uncommitted effect of one fill; computing it changes no book state."""

    content: str
    duplicate: bool
    position: ShadowPosition | None
    remove: bool
    cash_delta: Decimal
    realized_pnl: Decimal
    sell_attempt: bool
    sell_book: tuple[str, str, int, int] | None


@dataclass(frozen=True)
class _ResidualTransition:
    content: str
    duplicate: bool


@dataclass
class ShadowBook:
    """Incremental simulated holdings/cash projected from shadow fill/residual events."""

    positions: dict[str, ShadowPosition] = field(default_factory=dict)
    fill_ids: dict[str, str] = field(default_factory=dict)
    residual_ids: dict[str, str] = field(default_factory=dict)
    decision_keys: set[str] = field(default_factory=set)
    sell_attempts: dict[str, int] = field(default_factory=dict)
    sell_books: set[tuple[str, str, int, int]] = field(default_factory=set)
    cash_delta: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    last_event_id: int = 0
    ledger_path: str | None = None

    @property
    def total_cost(self) -> Decimal:
        return sum((position.cost for position in self.positions.values()), ZERO)

    def free_quote(self, exchange_free: Decimal) -> Decimal:
        value = _decimal(exchange_free, "exchange_free") + self.cash_delta
        return value if value > 0 else ZERO

    def book_used(self, symbol: str, opportunity_id: str, book_ts_ms: int,
                  bar_ts_ms: int) -> bool:
        return (symbol, opportunity_id, int(book_ts_ms), int(bar_ts_ms)) in self.sell_books

    def _fill_transition(self, fill: ShadowFill) -> _FillTransition:
        """Validate and compute a fill's effect without touching the book."""
        content = sha256_hex(canonical_json(fill.payload()))
        existing = self.fill_ids.get(fill.fill_id)
        if existing is not None:
            if existing != content:
                raise ShadowBookError("a fill id was reused with different content")
            return _FillTransition(content, True, None, False, ZERO, ZERO, False, None)

        base, quote = _symbol_parts(fill.symbol)
        quote_fee = fill.fee if fill.fee_currency == quote else ZERO
        base_fee = fill.fee if fill.fee_currency == base else ZERO
        if fill.fee and fill.fee_currency not in (base, quote):
            raise ShadowBookError("a fee must be in the base or quote currency")
        if fill.side == "buy":
            position = self.positions.get(fill.symbol)
            if position is not None and position.position_id != fill.position_id:
                raise ShadowBookError("a second open shadow position for one symbol")
            net_amount = fill.amount - base_fee
            if net_amount <= 0:
                raise ShadowBookError("a base fee consumed the whole buy")
            cost_add = fill.notional + quote_fee
            if position is None:
                position = ShadowPosition(
                    position_id=fill.position_id, symbol=fill.symbol, amount=net_amount,
                    cost=cost_add, opened_at=fill.executed_at_ms / 1000.0,
                    entry_policy_digest=fill.policy_digest,
                    entry_experiment_digest=fill.experiment_digest)
            else:
                if (position.entry_policy_digest != fill.policy_digest
                        or position.entry_experiment_digest != fill.experiment_digest):
                    raise ShadowBookError("a shadow buy changed the entry policy")
                position = dataclasses.replace(
                    position, amount=position.amount + net_amount,
                    cost=position.cost + cost_add)
            return _FillTransition(content, False, position, False,
                                   -(fill.notional + quote_fee), ZERO, False, None)

        position = self.positions.get(fill.symbol)
        if position is None:
            raise ShadowBookError("a shadow sell has no open position")
        if fill.position_id and position.position_id != fill.position_id:
            raise ShadowBookError("a shadow sell has the wrong position id")
        if (position.entry_policy_digest != fill.policy_digest
                or position.entry_experiment_digest != fill.experiment_digest):
            raise ShadowBookError("a shadow sell changed the entry policy")
        sold_base = fill.amount + base_fee
        if sold_base > position.amount:
            raise ShadowBookError("a shadow sell exceeds the simulated inventory")
        average = position.avg_cost
        remaining = position.amount - sold_base
        if remaining > 0:
            position = dataclasses.replace(position, amount=remaining,
                                           cost=average * remaining)
            remove = False
        else:
            position = None
            remove = True
        cash_delta = fill.notional - quote_fee
        sell_book = None
        if fill.status == "partial" and (fill.book_ts_ms or fill.bar_ts_ms):
            sell_book = (fill.symbol, fill.opportunity_id,
                         fill.book_ts_ms, fill.bar_ts_ms)
        return _FillTransition(content, False, position, remove, cash_delta,
                               cash_delta - average * sold_base, True, sell_book)

    def _commit_fill(self, fill: ShadowFill, transition: _FillTransition, *,
                     event_id: int = 0) -> bool:
        if transition.duplicate:
            self.last_event_id = max(self.last_event_id, int(event_id))
            return False
        if transition.remove:
            self.positions.pop(fill.symbol, None)
        else:
            self.positions[fill.symbol] = transition.position
        self.cash_delta += transition.cash_delta
        self.realized_pnl += transition.realized_pnl
        self.fill_ids[fill.fill_id] = transition.content
        self.decision_keys.add(fill.decision_key)
        if transition.sell_attempt:
            self.sell_attempts[fill.opportunity_id] = (
                self.sell_attempts.get(fill.opportunity_id, 0) + 1)
        if transition.sell_book is not None:
            self.sell_books.add(transition.sell_book)
        self.last_event_id = max(self.last_event_id, int(event_id))
        return True

    def apply_fill(self, fill: ShadowFill, *, event_id: int = 0) -> bool:
        """Apply one projected fill; an identical duplicate is a no-op, a conflict raises."""
        return self._commit_fill(fill, self._fill_transition(fill), event_id=event_id)

    def _residual_transition(self, residual: ShadowResidual) -> _ResidualTransition:
        content = sha256_hex(canonical_json(residual.payload()))
        existing = self.residual_ids.get(residual.position_id)
        if existing is not None:
            if existing != content:
                raise ShadowBookError("a residual id was reused with different content")
            return _ResidualTransition(content, True)
        position = self.positions.get(residual.symbol)
        if position is None:
            raise ShadowBookError("a shadow residual has no open position")
        if position.position_id != residual.position_id:
            raise ShadowBookError("a shadow residual has the wrong position id")
        if position.amount != residual.amount:
            raise ShadowBookError("a shadow residual amount does not match the position")
        return _ResidualTransition(content, False)

    def _commit_residual(self, residual: ShadowResidual,
                         transition: _ResidualTransition, *, event_id: int = 0) -> bool:
        if transition.duplicate:
            self.last_event_id = max(self.last_event_id, int(event_id))
            return False
        self.positions.pop(residual.symbol, None)
        self.residual_ids[residual.position_id] = transition.content
        self.last_event_id = max(self.last_event_id, int(event_id))
        return True

    def apply_residual(self, residual: ShadowResidual, *, event_id: int = 0) -> bool:
        """Close one simulated dust position; an identical duplicate is a no-op."""
        return self._commit_residual(residual, self._residual_transition(residual),
                                     event_id=event_id)


def _page_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be an int in 1..{MAX_PAGE_SIZE}")
    return value


def apply_shadow_event(book: ShadowBook, row) -> ShadowFill | None:
    """Apply one replayed event; return the fill only when a new fill was committed."""
    payload = getattr(row, "payload", None)
    event_id = int(getattr(row, "event_id", 0) or 0)
    if isinstance(payload, Mapping) and payload.get("action") == SHADOW_RESIDUAL_ACTION:
        book.apply_residual(ShadowResidual.from_event(row), event_id=event_id)
        return None
    fill = ShadowFill.from_event(row)
    return fill if book.apply_fill(fill, event_id=event_id) else None


def project_shadow_book(ledger, *, book: ShadowBook | None = None,
                        page_size: int = SHADOW_PAGE_SIZE) -> ShadowBook:
    """Replay ``shadow_fill`` and residual events in bounded pages, resuming the cache."""
    size = _page_size(int(page_size))
    path = str(getattr(ledger, "path", "") or "")
    if not isinstance(book, ShadowBook) or book.ledger_path not in (None, path):
        book = ShadowBook()
    book.ledger_path = path or book.ledger_path
    after = int(book.last_event_id)
    while True:
        rows = views.events_of_kinds(ledger, SHADOW_EVENT_KINDS,
                                     after_event_id=after, limit=size)
        if not rows:
            break
        for row in rows:
            apply_shadow_event(book, row)
        advanced = int(book.last_event_id)
        if advanced <= after:
            raise ShadowBookError("a shadow event page did not advance")
        after = advanced
        if len(rows) < size:
            break
    return book


def _quote_and_free(balance) -> tuple[Decimal, Decimal]:
    if balance is None:
        return ZERO, ZERO
    asset = getattr(balance, "assets", {}).get("JPY")
    if asset is None:
        return ZERO, ZERO
    return asset.total, asset.free


def build_shadow_inputs(ledger, *, balance, rules: Mapping[str, MarketRules],
                        policy_set, book: ShadowBook) -> CycleInputs:
    """Cycle inputs from the exchange baseline plus the simulated book."""
    block_buys: set[str] = set()
    fee_rates = {symbol: rule.max_fee_rate for symbol, rule in rules.items()}
    try:
        real_deployed = views.deployed_cost(ledger, fee_rates)
    except ValueError:
        real_deployed = ZERO
        block_buys.add("fee_rate_unknown")

    jpy_total, jpy_free = _quote_and_free(balance)
    simulated_cost = book.total_cost
    capital = jpy_total + real_deployed + book.cash_delta + simulated_cost
    deployed = real_deployed + simulated_cost
    free_quote = jpy_free + book.cash_delta
    positions: dict[str, views.CostBasis] = {}
    held: dict[str, HeldPosition] = {}
    legacy: set[str] = set()
    for symbol in sorted(book.positions):
        position = book.positions[symbol]
        if position.amount <= 0:
            continue
        policy = policy_set.policies.get(symbol)
        if policy is not None and position.entry_policy_digest != policy.digest:
            legacy.add(symbol)
        positions[symbol] = position.cost_basis
        held[symbol] = position.held

    return CycleInputs(
        funds=Funds(capital=capital if capital > 0 else ZERO, deployed=deployed,
                    free_quote=free_quote if free_quote > 0 else ZERO),
        positions=positions,
        held=held,
        legacy_position_symbols=frozenset(legacy),
        ambiguous_position_symbols=frozenset(),
        open_buy_symbols=frozenset(),
        open_sell_symbols=frozenset(),
        decided_keys=book.decision_keys,
        block_buys=tuple(sorted(block_buys)),
        problems=(),
    )


def _consume(levels, target: Decimal) -> tuple[Decimal, Decimal, int]:
    """Exact notional/remainder/levels for ``target`` over sorted visible levels."""
    left = target
    notional = ZERO
    used = 0
    for price, level_amount in levels:
        if left <= 0:
            break
        take = min(level_amount, left)
        if take <= 0:
            continue
        notional += take * price
        left -= take
        used += 1
    return notional, left, used


def simulate_plan(plan: OrderPlan, market: SymbolMarket, *,
                  available_quote: Decimal | None = None,
                  available_base: Decimal | None = None) -> ShadowFill | None:
    """Execute one limit plan as a price-limited VWAP over the validated book.

    A buy consumes ask levels at or below ``plan.price``; a sell consumes bid levels at or
    above it.  The executed amount is floored to the real amount step, so ``levels_used``
    counts only the levels that the rounded quantity actually touches.  Missing depth,
    an unusable book or an unknown fee rate returns None; no fill is fabricated.
    """
    if not isinstance(plan, OrderPlan):
        raise TypeError("plan must be an OrderPlan")
    if not isinstance(market, SymbolMarket):
        raise TypeError("market must be a SymbolMarket")
    side = _side(plan.side)
    amount = _positive(plan.amount, "plan.amount")
    limit = _positive(plan.price, "plan.price")
    try:
        values = _rules_values(market.rules)
    except ValueError:
        return None
    if values.fee_rate is None:
        return None
    fee_rate = abs(values.fee_rate)

    try:
        if side == "buy":
            levels = tuple((price, size) for price, size in _usable_levels(
                market.book.asks, tick=values.price_tick, descending=False)
                if price <= limit)
        else:
            levels = tuple((price, size) for price, size in _usable_levels(
                market.book.bids, tick=values.price_tick, descending=True)
                if price >= limit)
    except (_BookUnusable, TypeError, ValueError):
        return None
    if not levels:
        return None

    if side == "buy":
        cash = (plan.required_quote if available_quote is None
                else _nonnegative(available_quote, "available_quote"))
        if cash <= 0:
            return None
        raw = ZERO
        cash_left = cash
        for price, level_amount in levels:
            if raw >= amount:
                break
            per_unit = price * (ONE + fee_rate)
            by_cash = cash_left / per_unit if per_unit > 0 else ZERO
            take = min(level_amount, amount - raw, by_cash)
            if take <= 0:
                break
            raw += take
            cash_left -= take * price * (ONE + fee_rate)
        filled = floor_step(raw, values.amount_step)
    else:
        inventory = (amount if available_base is None
                     else _nonnegative(available_base, "available_base"))
        visible = sum((level_amount for _price, level_amount in levels), ZERO)
        filled = floor_step(min(amount, inventory, visible), values.amount_step)
    if filled <= 0:
        return None

    notional, left, used = _consume(levels, filled)
    if left > 0 or notional <= 0:
        return None
    fee = notional * fee_rate
    if side == "buy" and notional + fee > cash:
        return None
    remaining = amount - filled
    return ShadowFill(
        fill_id=_fill_id(plan.decision_key),
        decision_key=plan.decision_key,
        opportunity_id=plan.opportunity_id,
        position_id=plan.position_id or _position_id(plan.decision_key),
        symbol=plan.symbol,
        side=side,
        amount=filled,
        price=notional / filled,
        notional=notional,
        fee=fee,
        fee_currency=market.rules.quote,
        fee_rate=fee_rate,
        planned_amount=amount,
        planned_price=limit,
        status="filled" if remaining <= 0 else "partial",
        remaining=remaining if remaining > 0 else ZERO,
        policy_digest=plan.policy_digest,
        experiment_digest="",
        executed_at_ms=0,
        levels_used=used,
        book_ts_ms=int(market.book.exchange_ts_ms),
        bar_ts_ms=0,
        timeframe_s=0,
        rule_id=plan.rule_id,
    )


def _bar_identity(ctx: "CycleContext", symbol: str) -> tuple[int, int]:
    frame = ctx.frames.get(symbol)
    if frame is None or not frame.timestamps:
        return 0, 0
    return int(frame.timestamps[-1] * 1000), int(frame.timeframe_seconds)


def _shadow_dedupe(state: "CycleState") -> dict:
    dedupe = getattr(state, "shadow_dedupe", None)
    if not isinstance(dedupe, dict):
        dedupe = {}
        state.shadow_dedupe = dedupe
    return dedupe


def _record_skip(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext",
                 dedupe: dict, plan: OrderPlan, reason: str,
                 decisions: DecisionSet) -> bool:
    key = ("skip", plan.decision_key, reason)
    if key in dedupe:
        return True
    payload = {
        "action": "skip", "mode": SHADOW_MODE, "side": plan.side,
        "symbol": plan.symbol, "opportunity_id": plan.opportunity_id,
        "reason": reason, "decision_key": plan.decision_key,
        "position_id": plan.position_id or "", "cycle": str(state.cycle_number),
        "blocks": ",".join(decisions.block_causes),
    }
    try:
        deps.ledger.record_live_event(deps.lease, "live_decision", payload, now=ctx.now)
    except LeaseLost:
        raise
    except Exception:
        return False
    dedupe[key] = state.cycle_number
    return True


def _last_shadow_event_id(ledger) -> int:
    marks = ",".join("?" for _ in SHADOW_EVENT_KINDS)
    with ledger.read_cursor() as cur:
        row = cur.execute(f"SELECT MAX(event_id) FROM events WHERE kind IN ({marks})",
                          SHADOW_EVENT_KINDS).fetchone()
    return int(row[0] or 0)


def _canonical(payload: Mapping, parse):
    """Parse a payload with the replay parser and require the exact same canonical form."""
    normalized = parse(payload)
    if canonical_json(normalized.payload()) != canonical_json(payload):
        raise ShadowBookError("a shadow event payload is not canonical")
    return normalized


def _append_fill(deps: "CycleDeps", ctx: "CycleContext", book: ShadowBook,
                 fill: ShadowFill) -> ShadowFill | None:
    """Validate first, append durably, then commit the previewed transition."""
    normalized = _canonical(fill.payload(), ShadowFill.from_payload)
    transition = book._fill_transition(normalized)
    try:
        deps.ledger.record_live_event(deps.lease, SHADOW_EVENT_KIND, normalized.payload(),
                                      now=ctx.now)
    except LeaseLost:
        raise
    except Exception:
        return None
    book._commit_fill(normalized, transition, event_id=_last_shadow_event_id(deps.ledger))
    return normalized


def _append_residual(deps: "CycleDeps", ctx: "CycleContext", book: ShadowBook,
                     residual: ShadowResidual) -> bool:
    normalized = _canonical(residual.payload(), ShadowResidual.from_payload)
    transition = book._residual_transition(normalized)
    try:
        deps.ledger.record_live_event(deps.lease, SHADOW_RESIDUAL_KIND,
                                      normalized.payload(), now=ctx.now)
    except LeaseLost:
        raise
    except Exception:
        return False
    book._commit_residual(normalized, transition,
                          event_id=_last_shadow_event_id(deps.ledger))
    return True


def _close_residuals(deps: "CycleDeps", ctx: "CycleContext", book: ShadowBook,
                     result) -> int:
    """Durably close simulated dust named by the sizing result; never touch real positions."""
    if result is None:
        return 0
    failures = 0
    for position_id in result.residual_position_ids:
        position = next((item for item in book.positions.values()
                         if item.position_id == position_id), None)
        if position is None:
            continue
        residual = ShadowResidual(position_id=position.position_id,
                                  symbol=position.symbol, amount=position.amount)
        if not _append_residual(deps, ctx, book, residual):
            failures += 1
    return failures


@dataclass(frozen=True)
class ShadowRun:
    decisions: DecisionSet
    book: ShadowBook
    fills: tuple[ShadowFill, ...]
    skipped: tuple[tuple[str, str], ...]
    record_failures: int


def _execute_plans(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext",
                   decisions: DecisionSet, book: ShadowBook, *,
                   now: float) -> tuple[tuple[ShadowFill, ...], tuple[tuple[str, str], ...],
                                        int]:
    result = decisions.result
    if result is None:
        return (), (), 0
    jpy_free = _quote_and_free(ctx.balance)[1]
    dedupe = _shadow_dedupe(state)
    fills: list[ShadowFill] = []
    skipped: list[tuple[str, str]] = []
    failures = 0

    for plan in result.sells + result.buys:
        if plan.decision_key in book.decision_keys:
            skipped.append((plan.decision_key, ALREADY_FILLED))
            continue
        market = decisions.markets.get(plan.symbol)
        if market is None:
            skipped.append((plan.decision_key, "market_unavailable"))
            if not _record_skip(deps, state, ctx, dedupe, plan, "market_unavailable",
                                decisions):
                failures += 1
            continue

        position = book.positions.get(plan.symbol)
        bar_ts_ms, timeframe_s = _bar_identity(ctx, plan.symbol)
        if plan.side == "sell":
            if position is None or position.amount <= 0:
                reason = NO_POSITION
            elif plan.position_id and position.position_id != plan.position_id:
                reason = NO_POSITION
            elif position.entry_policy_digest != plan.policy_digest:
                reason = POLICY_MISMATCH
            elif book.book_used(plan.symbol, plan.opportunity_id,
                                market.book.exchange_ts_ms, bar_ts_ms):
                reason = BOOK_EXHAUSTED
            else:
                reason = ""
            if reason:
                skipped.append((plan.decision_key, reason))
                if not _record_skip(deps, state, ctx, dedupe, plan, reason, decisions):
                    failures += 1
                continue
            position_id = position.position_id
            policy_digest = position.entry_policy_digest
            experiment_digest = position.entry_experiment_digest
            available_base = position.amount
            available_quote = None
        else:
            if position is not None and position.entry_policy_digest != plan.policy_digest:
                skipped.append((plan.decision_key, POLICY_MISMATCH))
                if not _record_skip(deps, state, ctx, dedupe, plan, POLICY_MISMATCH,
                                    decisions):
                    failures += 1
                continue
            policy = deps.policy_set.policies.get(plan.symbol)
            position_id = (position.position_id if position is not None
                           else _position_id(plan.decision_key))
            policy_digest = plan.policy_digest
            experiment_digest = policy.experiment_digest if policy is not None else ""
            available_base = None
            available_quote = book.free_quote(jpy_free)

        execution = simulate_plan(plan, market, available_quote=available_quote,
                                  available_base=available_base)
        if execution is None:
            skipped.append((plan.decision_key, NO_LIQUIDITY))
            if not _record_skip(deps, state, ctx, dedupe, plan, NO_LIQUIDITY, decisions):
                failures += 1
            continue

        fill = dataclasses.replace(
            execution, position_id=position_id, policy_digest=policy_digest,
            experiment_digest=experiment_digest, executed_at_ms=int(now * 1000),
            bar_ts_ms=bar_ts_ms, timeframe_s=timeframe_s)
        if fill.fill_id in book.fill_ids:
            skipped.append((plan.decision_key, ALREADY_FILLED))
            continue
        recorded = _append_fill(deps, ctx, book, fill)
        if recorded is None:
            failures += 1
            skipped.append((plan.decision_key, RECORD_FAILED))
            continue
        fills.append(recorded)
    return tuple(fills), tuple(skipped), failures


def _cached_book(state: "CycleState", ledger) -> ShadowBook:
    book = getattr(state, "shadow_book", None)
    if not isinstance(book, ShadowBook):
        book = ShadowBook()
    book = project_shadow_book(ledger, book=book)
    state.shadow_book = book
    return book


def run_shadow(deps: "CycleDeps", state: "CycleState",
               ctx: "CycleContext", *, book: ShadowBook | None = None) -> ShadowRun | None:
    """Simulate one shadow cycle; the normal hook calls this and ignores the result."""
    if ctx.mode != SHADOW_MODE:
        return None
    book = _cached_book(state, deps.ledger) if book is None else project_shadow_book(
        deps.ledger, book=book)
    inputs = build_shadow_inputs(deps.ledger, balance=ctx.balance,
                                 rules=deps.executor.rules(),
                                 policy_set=deps.policy_set, book=book)
    decisions = build_decisions(
        deps, state, ctx, inputs=inputs, dedupe=_shadow_dedupe(state),
        decision_prefix=SHADOW_DECISION_PREFIX, mark_residual=False,
        sell_attempts=book.sell_attempts)
    state.record_failures = decisions.record_failures
    residual_failures = _close_residuals(deps, ctx, book, decisions.result)
    fills, skipped, failures = _execute_plans(deps, state, ctx, decisions, book,
                                              now=float(ctx.now))
    state.record_failures += residual_failures + failures
    state.shadow_book = book
    return ShadowRun(decisions=decisions, book=book, fills=fills, skipped=skipped,
                     record_failures=state.record_failures)


def decide_and_act(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext") -> None:
    """The ACT-path shadow branch: simulate, append events, never place an order."""
    run_shadow(deps, state, ctx)
    return None
