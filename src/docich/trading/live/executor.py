"""The order executor (Issue #3623, L2): intent -> send -> record -> poll, with every crash point named.

Rules this module keeps (each is tested with crash injection):

* The intent is in the ledger *before* anything is sent (``create_intent``), and the commit of
  ``mark_submitting`` is the barrier: no send without it.
* Only an explicit rejection from the exchange means "not created".  Any other failure of a send
  (timeout, disconnect, an exception nobody classified) leaves the intent UNKNOWN_SUBMISSION, and
  the same intent is never sent twice.  A new attempt is a *new* intent (new decision_key) after the
  old one is proven NOT_CREATED.
* Only a sender whose ``kind`` is on the allow-list can be used.  In L2/L3 that is the mock.  A
  ``sender=None`` executor is read-only: it polls, but every submit is abandoned and cancel refuses.
* The writer lease is checked right before every send and cancel.  Losing it stops everything.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable

from . import deposits, fence, reconcile, views
from .balance import BalanceMonitor, stable_check
from .exchange import (Guarded, ExchangeAmbiguous, ExchangeDataError, ExchangeError, ExchangeReader, ExchangeRejected,
                       MarketRules, OrderNotFound, OrderRequest, OrderSender, make_client_order_id)
from .ingest import ExecutorConfig, Ingestor
from .lease import LeaseLost, WriterLease
from .ledger import Intent, IntentRequest, SafetyBlocked

FAULT_HOOK: Callable[[str], None] | None = None   # tests only
SENDER_KINDS_ALLOWED = frozenset({"mock"})        # phase E changes this one line, with the owner's review


def _fault(point: str) -> None:
    if FAULT_HOOK is not None:
        FAULT_HOOK(point)


class SubmitOutcome(str, Enum):
    ACKED = "acked"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    LOCAL_INVALID = "local_invalid"


@dataclass
class StepReport:
    resolved: list[str] = field(default_factory=list)
    refreshed: int = 0
    swept: bool = False
    balance_checked: bool = False
    deposits: dict = field(default_factory=dict)   # per-currency DepositCheck from deposits.run (#4587)


class OrderExecutor:
    def __init__(self, ledger, lease: WriterLease, *, reader: ExchangeReader, sender: OrderSender | None,
                 enable_check: Callable[[], object], clock: Callable[[], float], symbols: tuple[str, ...],
                 config: ExecutorConfig = ExecutorConfig()) -> None:
        if sender is not None and getattr(sender, "kind", None) not in SENDER_KINDS_ALLOWED:
            raise ValueError(f"sender kind {getattr(sender, 'kind', None)!r} is not allowed")
        self.ledger, self.lease = ledger, lease
        self.reader = Guarded(reader)
        self.sender = None if sender is None else Guarded(sender)
        self.enable_check, self.clock, self.symbols, self.config = enable_check, clock, tuple(symbols), config
        self.ingest = Ingestor(ledger, lease, self.reader, config, clock)
        self.monitor = BalanceMonitor()
        self._rules: dict[str, MarketRules] = {}
        self._steps = 0
        self.sweep_since_ms: dict[str, int] = {}
        self.none_seen: dict[str, float] = {}

    # ------------------------------------------------------------------ helpers
    def rules(self) -> dict[str, MarketRules]:
        for symbol in self.symbols:
            if symbol not in self._rules:
                self._rules[symbol] = self.reader.market_rules(symbol)
        return dict(self._rules)

    def reset_rules(self) -> None:
        """Forget the cached market rules so the next ``rules()`` reads them again."""
        self._rules.clear()

    def _abandon(self, intent_id: str, reason: str) -> None:
        self.ledger.transition_intent(self.lease, intent_id, expected_state="PLANNED", to_state="ABANDONED",
                                      now=self.clock(), reason_code=reason)

    def _guard(self, fn):
        try:
            return fn()
        except LeaseLost as exc:
            fence.on_lease_lost(self.ledger, detail=str(exc), now=self.clock())
            raise

    # ------------------------------------------------------------------ planning and sending
    def plan(self, req: IntentRequest) -> Intent:
        if req.client_order_id is None:
            cid = make_client_order_id(req.decision_key, self.reader.capabilities)
            req = dataclasses.replace(req, client_order_id=cid)
        return self._guard(lambda: self.ledger.create_intent(self.lease, req, now=self.clock()))

    def submit(self, intent_id: str) -> SubmitOutcome:
        return self._guard(lambda: self._submit(intent_id))

    def _submit(self, intent_id: str) -> SubmitOutcome:
        intent = self.ledger.get_intent(intent_id)
        if intent is None or intent.state != "PLANNED":
            raise ValueError("only a PLANNED intent can be submitted")
        fence.check_lease(self.ledger, self.lease)
        gate = self.enable_check()
        if not gate.allows_orders:
            self._abandon(intent_id, "gate_closed")
            return SubmitOutcome.BLOCKED
        if self.sender is None:                       # read-only executor: nothing can leave
            self._abandon(intent_id, "no_sender")
            return SubmitOutcome.BLOCKED
        try:
            rules = self.reader.market_rules(intent.symbol)
        except ExchangeError:
            self._abandon(intent_id, "no_market_rules")
            return SubmitOutcome.BLOCKED
        amount = Decimal(intent.amount)
        price = None if intent.price is None else Decimal(intent.price)
        if (amount < rules.min_amount or amount % rules.amount_step != 0
                or (price is not None and price % rules.price_tick != 0)):
            self._abandon(intent_id, "local_invalid")
            return SubmitOutcome.LOCAL_INVALID
        if intent.side == "sell" and amount > views.position_net_base(self.ledger, intent.position_id):
            self._abandon(intent_id, "exceeds_holding")
            return SubmitOutcome.LOCAL_INVALID
        _fault("before_mark_submitting")
        try:
            self.ledger.mark_submitting(self.lease, intent_id, now=self.clock())
        except SafetyBlocked:
            self._abandon(intent_id, "safety_blocked")
            return SubmitOutcome.BLOCKED
        fence.check_lease(self.ledger, self.lease)  # the last look before the request leaves
        request = OrderRequest(symbol=intent.symbol, side=intent.side, order_type=intent.order_type, amount=amount,
                               price=price, client_order_id=intent.client_order_id)
        try:
            snap = self.sender.submit_order(request)
        except ExchangeRejected as exc:
            self.ledger.transition_intent(self.lease, intent_id, expected_state="SUBMITTING", to_state="REJECTED",
                                          now=self.clock(), reason_code=exc.code)
            return SubmitOutcome.REJECTED
        except Exception as exc:  # anything else: we do not know whether the exchange has the order
            self.ledger.transition_intent(self.lease, intent_id, expected_state="SUBMITTING",
                                          to_state="UNKNOWN_SUBMISSION", now=self.clock(),
                                          reason_code=f"send_unknown:{type(exc).__name__}")
            return SubmitOutcome.UNKNOWN
        _fault("after_send_before_record")
        recorded = self.ingest.sync_order(intent, snap)
        if not recorded:
            self.ledger.transition_intent(self.lease, intent_id, expected_state="SUBMITTING",
                                          to_state="UNKNOWN_SUBMISSION", now=self.clock(),
                                          reason_code="ack_not_recorded")
            return SubmitOutcome.UNKNOWN
        _fault("after_order_recorded")
        acked = self.ledger.transition_intent(self.lease, intent_id, expected_state="SUBMITTING", to_state="ACKED",
                                              now=self.clock(), exchange_order_id=snap.exchange_order_id)
        _fault("after_ack_transition")
        self.ingest.refresh(acked)
        return SubmitOutcome.ACKED

    def cancel(self, intent_id: str) -> None:
        self._guard(lambda: self._cancel(intent_id))

    def _cancel(self, intent_id: str) -> None:
        if self.sender is None:
            raise ValueError("this executor has no sender (read-only)")
        intent = self.ledger.get_intent(intent_id)
        if intent is None or intent.state not in ("ACKED", "PARTIALLY_FILLED") or not intent.exchange_order_id:
            raise ValueError("only an acknowledged order can be canceled")
        fence.check_lease(self.ledger, self.lease)
        _fault("before_cancel")
        try:
            self.sender.cancel_order(intent.symbol, intent.exchange_order_id)
        except OrderNotFound:
            self.ingest.anomaly(f"cancel: order {intent.exchange_order_id} of intent {intent_id} not found")
        except ExchangeDataError as exc:
            self.ingest.anomaly(f"cancel: {exc}")
        except ExchangeAmbiguous:
            pass  # unknown outcome (timeout, unclassified): the next poll tells; canceling again is harmless
        self.ingest.refresh(intent)

    # ------------------------------------------------------------------ polling
    def step(self) -> StepReport:
        return self._guard(self._step)

    def _step(self) -> StepReport:
        fence.check_lease(self.ledger, self.lease)
        report = StepReport()
        for intent in self.ledger.list_intents(states=("UNKNOWN_SUBMISSION",)):
            outcome = reconcile.try_resolve(self, intent)
            if outcome in ("acked", "not_created"):
                report.resolved.append(f"{intent.intent_id}:{outcome}")
        for intent in self.ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED")):
            self.ingest.refresh(intent)
            report.refreshed += 1
        self._steps += 1
        if self._steps == 1 or self._steps % max(1, self.config.sweep_every) == 0:
            report.swept = reconcile.sweep(self)
        if self._steps == 1 or self._steps % max(1, self.config.cashflow_every) == 0:
            report.deposits = deposits.run(self)
        self.ingest.close_flat_positions()
        report.balance_checked = self.check_balance()
        return report

    def quiet(self) -> bool:
        if self.ledger.list_intents(states=("SUBMITTING", "UNKNOWN_SUBMISSION")):
            return False
        for intent in self.ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED")):
            row = views.order_row(self.ledger, intent.exchange_order_id or "")
            fills = views.fills_for_order(self.ledger, intent.exchange_order_id or "")
            if row is None or sum((f.amount for f in fills), Decimal(0)) != row.executed_amount:
                return False
        return True

    def watched_currencies(self) -> tuple[str, ...]:
        rules = self.rules()
        return tuple(sorted({r.quote for r in rules.values()} | {r.base for r in rules.values()}))

    def check_balance(self) -> bool:
        """Compare ledger and exchange balances when nothing is in flight.  True if a stable check ran."""
        if not self.quiet():
            return False

        behind: list[str] = []

        def between():
            # the ledger must be up to date *inside* the two balance reads: refresh every open order, and
            # judge nothing if any refresh failed or found something it cannot account for
            for intent in self.ledger.list_intents(states=("ACKED", "PARTIALLY_FILLED")):
                outcome = self.ingest.refresh(intent, tick=False)
                if outcome in ("unavailable", "missing", "data_error", "anomaly", "no_order"):
                    behind.append(outcome)
            for symbol in self.symbols:
                self.reader.fetch_open_orders(symbol)

        try:
            result, stable = stable_check(self.ledger, self.reader, self.rules(), currencies=self.watched_currencies(),
                                          between=between)
        except ExchangeAmbiguous:
            return False
        except ExchangeDataError as exc:
            self.ingest.anomaly(f"fetch_balance: {exc}")
            return False
        if not stable or behind or not self.quiet():
            return False
        self.monitor.observe(self.ledger, result, now=self.clock())
        return True
