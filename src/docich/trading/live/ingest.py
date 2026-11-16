"""Turn what the exchange says into ledger facts, idempotently (Issue #3623, L2).

Everything is validated first.  Where the data is impossible (a fill count going backwards, a
terminal order coming back to life, two orders for one intent, ...) a DATA_ANOMALY is tripped and
nothing is recorded; where it is merely late (a fill not in the history yet) we wait.

A fee that arrives late for an already recorded fill is *not* written (the L1 ledger treats a
changed fill as a conflict); the balance check absorbs the uncertainty instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from . import views
from .exchange import (TERMINAL_STATUSES, ExchangeAmbiguous, ExchangeDataError, ExchangeReader, Fill, FeeStatus,
                       OrderNotFound, OrderSnapshot, OrderStatus, dec_text, ms_to_seconds, raw_json_text)
from .ledger import FillRecord, Intent, LiveLedgerError, OrderObservation
from .safety import SafetyReason

FAULT_HOOK: Callable[[str], None] | None = None   # tests only (crash injection)


def _fault(point: str) -> None:
    if FAULT_HOOK is not None:
        FAULT_HOOK(point)


_ZERO = Decimal(0)
_TERMINAL = {s.value for s in TERMINAL_STATUSES}


@dataclass(frozen=True)
class ExecutorConfig:
    settle_s: float = 120.0        # a lost submission is never "not created" before this (> 2 x send timeout)
    skew_margin_s: float = 300.0   # how far back fill history is read (exchange clock skew)
    fill_lag_polls: int = 10       # polls an executed order may wait for its fills before DATA_ANOMALY
    fee_grace_polls: int = 3       # polls a fill with an unknown fee is held back, hoping the fee shows up
    sweep_every: int = 5           # steps between scans for orders the ledger does not know


def observation_from(snap: OrderSnapshot, intent_id: str | None) -> OrderObservation:
    return OrderObservation(
        exchange_order_id=snap.exchange_order_id, symbol=snap.symbol, side=snap.side, order_type=snap.order_type,
        amount=dec_text(snap.amount), status=snap.status.value, executed_amount=dec_text(snap.executed_amount),
        remaining_amount=dec_text(snap.remaining_amount), intent_id=intent_id,
        price=None if snap.price is None else dec_text(snap.price),
        average_price=None if snap.average_price is None else dec_text(snap.average_price),
        raw_json=raw_json_text(snap.raw))


def fill_record_from(fill: Fill) -> FillRecord:
    known = fill.fee.status is FeeStatus.KNOWN
    return FillRecord(
        fill_id=f"{fill.symbol}:{fill.trade_id}", exchange_order_id=fill.exchange_order_id, symbol=fill.symbol,
        side=fill.side, amount=dec_text(fill.amount), price=dec_text(fill.price),
        quote_notional=dec_text(fill.notional()), executed_at=ms_to_seconds(fill.executed_at_ms),
        fee_status="known" if known else "unknown",
        fee_amount=dec_text(fill.fee.amount) if known else None,
        fee_currency=fill.fee.currency if known else None, maker_taker=fill.maker_taker,
        raw_json=raw_json_text(fill.raw))


def intent_mismatch(intent: Intent, snap: OrderSnapshot) -> list[str]:
    problems = []
    if snap.symbol != intent.symbol or snap.side != intent.side or snap.order_type != intent.order_type:
        problems.append("symbol/side/type")
    if snap.amount != Decimal(intent.amount):
        problems.append("amount")
    if intent.order_type == "limit" and (snap.price is None or snap.price != Decimal(intent.price)):
        problems.append("price")
    return problems


def order_problems(prev: views.OrderRow | None, snap: OrderSnapshot) -> list[str]:
    problems = []
    if snap.executed_amount < 0 or snap.remaining_amount < 0 or snap.executed_amount > snap.amount:
        problems.append("executed out of range")
    if snap.order_type == "limit" and snap.executed_amount + snap.remaining_amount != snap.amount:
        problems.append("executed + remaining != amount")
    if prev is not None:
        if (prev.symbol, prev.side, prev.order_type, prev.amount) != (snap.symbol, snap.side, snap.order_type,
                                                                      snap.amount):
            problems.append("order identity changed")
        if snap.executed_amount < prev.executed_amount:
            problems.append("executed amount went down")
        if prev.status in _TERMINAL and snap.status.value != prev.status:
            problems.append("a finished order changed")
    return problems


class Ingestor:
    def __init__(self, ledger, lease, reader: ExchangeReader, config: ExecutorConfig,
                 clock: Callable[[], float]) -> None:
        self.ledger, self.lease, self.reader, self.config, self.clock = ledger, lease, reader, config, clock
        self._fee_wait: dict[str, int] = {}
        self._lag: dict[str, int] = {}
        self._missing: dict[str, int] = {}
        self._anomalies_noted: set[str] = set()
        self._sent_at: dict[str, float] = {}

    # ---------------------------------------------------------------- helpers
    def anomaly(self, detail: str) -> None:
        self.ledger.trip_safety(SafetyReason.DATA_ANOMALY, detail=detail, now=self.clock())

    def since_ms(self, intent: Intent) -> int:
        """A fixed lower bound for reading fill history: when this intent was sent (or planned), minus the skew
        margin.  It never moves with the clock, so fills missed during an outage are still reachable."""
        sent = self._sent_at.get(intent.intent_id)
        if sent is None:
            sent = self._sent_at[intent.intent_id] = (views.submitting_time(self.ledger, intent.intent_id)
                                                      or intent.created_at)
        return max(0, int(sent * 1000) - int(self.config.skew_margin_s * 1000))

    # ---------------------------------------------------------------- orders
    def sync_order(self, intent: Intent, snap: OrderSnapshot) -> bool:
        """Record the order against the intent.  False: nothing was recorded (an anomaly was tripped)."""
        prev = views.order_row(self.ledger, snap.exchange_order_id)
        problems = order_problems(prev, snap)
        linked = views.order_for_intent(self.ledger, intent.intent_id)
        if linked is not None and linked.exchange_order_id != snap.exchange_order_id:
            problems.append("second order for one intent")
        if prev is not None and prev.intent_id not in (None, intent.intent_id):
            problems.append("order belongs to another intent")
        if problems:
            self.anomaly(f"order {snap.exchange_order_id} for intent {intent.intent_id}: {'; '.join(problems)}")
            return False
        mismatch = intent_mismatch(intent, snap)
        if mismatch:  # the order exists, so it is recorded; the disagreement is what stops buying
            self.anomaly(f"order {snap.exchange_order_id} differs from intent {intent.intent_id}: {', '.join(mismatch)}")
        self.ledger.record_order_observation(self.lease, observation_from(snap, intent.intent_id), now=self.clock())
        return True

    # ---------------------------------------------------------------- fills
    def record_fill_once(self, fill: Fill) -> str:
        rec = fill_record_from(fill)
        existing = views.fill_row(self.ledger, rec.fill_id)
        if existing is not None:
            same = (existing.exchange_order_id, existing.symbol, existing.side, existing.amount, existing.price,
                    existing.quote_notional, existing.executed_at) == (
                rec.exchange_order_id, rec.symbol, rec.side, Decimal(rec.amount), Decimal(rec.price),
                Decimal(rec.quote_notional), float(rec.executed_at))
            if same and (existing.fee_status, existing.fee_amount, existing.fee_currency) == (
                    rec.fee_status, None if rec.fee_amount is None else Decimal(rec.fee_amount), rec.fee_currency):
                return "duplicate"
            if same and existing.fee_status == "unknown" and rec.fee_status == "known":
                return "fee_late_ignored"
        try:
            return "recorded" if self.ledger.record_fill(self.lease, rec, now=self.clock()) else "duplicate"
        except LiveLedgerError as exc:
            return "no_order" if "unknown order" in str(exc) else "conflict"

    def pull_fills(self, intent: Intent, snap: OrderSnapshot, *, tick: bool = True) -> None:
        page = self.reader.fetch_fills(intent.symbol, since_ms=self.since_ms(intent),
                                       exchange_order_id=snap.exchange_order_id)
        recorded = 0
        ticked: set[str] = set()   # one grace tick per fill per poll, however often it is delivered
        for fill in page.fills:
            if fill.exchange_order_id != snap.exchange_order_id:
                continue
            if fill.symbol != snap.symbol or fill.side != snap.side:
                self.anomaly(f"fill {fill.trade_id} does not match its order {snap.exchange_order_id}")
                continue
            key = f"{fill.symbol}:{fill.trade_id}"
            if fill.fee.status is FeeStatus.UNKNOWN and views.fill_row(self.ledger, key) is None:
                if key not in ticked and tick:
                    ticked.add(key)
                    self._fee_wait[key] = self._fee_wait.get(key, 0) + 1
                if not tick or self._fee_wait.get(key, 0) <= self.config.fee_grace_polls:
                    continue
            if self.record_fill_once(fill) == "recorded":
                recorded += 1
                _fault(f"fill_recorded:{recorded}")

    # ---------------------------------------------------------------- intents
    def advance(self, intent: Intent, snap: OrderSnapshot, *, tick: bool = True) -> str:
        """Move the intent along from the order status.  Returns the intent state afterwards."""
        current = self.ledger.get_intent(intent.intent_id)
        if current is None or current.state not in ("ACKED", "PARTIALLY_FILLED"):
            return current.state if current else "MISSING"
        fills = views.fills_for_order(self.ledger, snap.exchange_order_id)
        got = sum((f.amount for f in fills), _ZERO)
        status = snap.status
        target = None
        if status is OrderStatus.PARTIALLY_FILLED and current.state == "ACKED":
            target = "PARTIALLY_FILLED"
        elif status is OrderStatus.FULLY_FILLED:
            if got == snap.executed_amount == snap.amount:
                target = "FILLED"
        elif status is OrderStatus.CANCELED_UNFILLED:
            if snap.executed_amount == 0 and got == 0:
                target = "CANCELED"
        elif status is OrderStatus.CANCELED_PARTIALLY_FILLED:
            if got == snap.executed_amount:
                target = "CANCELED"
        elif status is OrderStatus.REJECTED:
            self.anomaly(f"order {snap.exchange_order_id} of intent {intent.intent_id} was rejected after ack")
        waiting = (status in (OrderStatus.FULLY_FILLED, OrderStatus.CANCELED_PARTIALLY_FILLED,
                              OrderStatus.CANCELED_UNFILLED) and target is None)
        if waiting and tick:
            self._lag[intent.intent_id] = self._lag.get(intent.intent_id, 0) + 1
            if self._lag[intent.intent_id] > self.config.fill_lag_polls:
                self.anomaly(f"order {snap.exchange_order_id}: executed {snap.executed_amount} but fills {got}")
        if target is not None and target != current.state:
            _fault("before_terminal_transition")
            current = self.ledger.transition_intent(
                self.lease, intent.intent_id, expected_state=current.state, to_state=target, now=self.clock())
        if current.state in ("FILLED", "CANCELED"):
            self._close_if_flat(current)
        return current.state

    def _close_if_flat(self, intent: Intent) -> None:
        if views.has_open_intent(self.ledger, intent.position_id):
            return
        if views.position_net_base(self.ledger, intent.position_id) == 0:
            _fault("before_close_position")
            self.ledger.close_position(self.lease, intent.position_id, now=self.clock())

    def close_flat_positions(self) -> int:
        """Close open positions that hold nothing and have no open intent (idempotent; also heals a crash
        between an intent's terminal state and the close of its position)."""
        closed = 0
        for position_id in views.open_position_ids(self.ledger):
            if views.has_open_intent(self.ledger, position_id):
                continue
            if views.position_net_base(self.ledger, position_id) == 0:
                self.ledger.close_position(self.lease, position_id, now=self.clock())
                closed += 1
        return closed

    # ---------------------------------------------------------------- one round for one intent
    def refresh(self, intent: Intent, *, tick: bool = True) -> str:
        """fetch order -> record -> fetch fills -> record -> advance.  Returns the resulting state or a word.

        ``tick=False`` is a look that must not count towards the "waiting too long" anomalies (the balance
        check refreshes inside its bracket on top of the step's own poll).
        """
        eid = intent.exchange_order_id
        if eid is None:
            row = views.order_for_intent(self.ledger, intent.intent_id)
            eid = row.exchange_order_id if row else None
        if eid is None:
            return "no_order"
        try:
            snap = self.reader.fetch_order(intent.symbol, eid)
        except OrderNotFound:
            if not tick:
                return "missing"
            self._missing[intent.intent_id] = self._missing.get(intent.intent_id, 0) + 1
            if self._missing[intent.intent_id] > self.config.fill_lag_polls:
                self.anomaly(f"acknowledged order {eid} of intent {intent.intent_id} is gone from the exchange")
            return "missing"
        except ExchangeAmbiguous:
            return "unavailable"
        except ExchangeDataError as exc:
            self.anomaly(f"fetch_order: {exc}")
            return "data_error"
        self._missing.pop(intent.intent_id, None)
        if not self.sync_order(intent, snap):
            return "anomaly"
        try:
            self.pull_fills(intent, snap, tick=tick)
        except ExchangeAmbiguous:
            return "unavailable"
        except ExchangeDataError as exc:
            self.anomaly(f"fetch_fills: {exc}")
            return "data_error"
        return self.advance(intent, snap, tick=tick)
