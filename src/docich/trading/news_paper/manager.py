"""Decision manager for the news PAPER account: receipts, T1/T2 and fills.

The manager is the single decision boundary between composition (N03) and
execution (N06). It owns what parent #769 B2 assigns to N05: accept/reject/
hold decisions, cash/inventory reservations, event-use consumption, the
two-phase durable commit of the receipt, and the transaction that applies an
``ExecutionOutcome`` (fills, fees, cancel, reservation release, balances).

Policy values here are FIXTURES (``fixture-manager-v1`` / ``fixture-risk-v1``):
every operational number (capital, exposure caps, loss/DD limits, lot, fee,
latency, TTL) stays unapproved per N00 v2 §5 and is only ever supplied as an
explicit fixture by tests. Contract sources: N00 v2 §2.6/§2.7 (receipt/order
schemas), §3.4 (stage×code actions), §4.1-4.2 (T1/T2 and crash recovery),
v3 §1 (fill fence), parent #769 D6/D7/D9.

No network module is imported; nothing here touches an existing account, the
crypto PAPER stack, ``free_strategy`` or the active policy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Sequence

from .execution import (
    ClockQuality,
    ExecutionOutcome,
    InstrumentConstraints,
    LiquidityTracker,
    PendingOrder,
    ReceiptState,
    execute_pending_order,
)
from .ledger import (
    REASON_CODES,
    EventUseDraft,
    LedgerError,
    NewsLedger,
    OrderDraft,
    ReceiptDraft,
    ReservationDraft,
    _parse_ts,
)
from .risk import FIXTURE_RISK_POLICY_V1, RiskPolicy, evaluate, headroom_snapshot

D = Decimal
ZERO = D("0")
UTC = timezone.utc

MANAGER_POLICY_VERSION = "fixture-manager-v1"
ACCEPT_REASON_CODES = frozenset({"NEWS_PRICE_AGREE", "NEWS_ONLY_ELIGIBLE", "RISK_EXIT"})


@dataclass(frozen=True)
class ManagerPolicy:
    """Fixture manager policy; ``execution_delay_ms`` is the unapproved extra
    execution latency that T2 adds to ``not_before_at`` exactly once."""

    version: str = MANAGER_POLICY_VERSION
    execution_delay_ms: Decimal = ZERO

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        delay = self.execution_delay_ms
        if not isinstance(delay, Decimal) or not delay.is_finite() or delay < 0:
            raise LedgerError("VALUE_OUT_OF_RANGE")


FIXTURE_MANAGER_POLICY_V1 = ManagerPolicy()


@dataclass(frozen=True)
class DecisionRequest:
    """One decision candidate handed to the manager by the host.

    ``decision_quote`` is the identity of the market observation the decision
    was made on; a fill must come from a different identity (N00 v2 §4.3).
    """

    receipt_id: str
    run_id: str
    scenario_id: str
    account_id: str
    decision_at: datetime
    manager_seen_at: datetime
    input_cutoff_sequence: int
    signal_refs: Sequence[str]
    market_refs: Sequence[str]
    decision_quote: tuple[str, str, datetime, str] | None
    asset: str
    side: str
    reference_price: Decimal
    expires_at: datetime
    constraints: InstrumentConstraints
    event_group_id: str = ""
    quantity_cap: Decimal | None = None
    accept_reason_code: str = "NEWS_PRICE_AGREE"
    supersedes_receipt_id: str | None = None
    daily_pnl: Decimal | None = None
    peak_equity: Decimal | None = None
    current_equity: Decimal | None = None


@dataclass(frozen=True)
class ReceiptView:
    """Read-only projection of a stored receipt (parsed, no raw payloads)."""

    receipt_id: str
    run_id: str
    scenario_id: str
    account_id: str
    status: str
    reason_codes: tuple[str, ...]
    decision_at: datetime
    receipt_committed_at: datetime | None
    order_ref: str | None
    supersedes_receipt_id: str | None
    abandoned: bool
    input_cutoff_sequence: int
    signal_refs: tuple[str, ...]
    market_refs: tuple[str, ...]


class PaperManager:
    """Single decision path: candidate -> risk gate -> T1 -> T2 -> N06 fills."""

    def __init__(self, ledger: NewsLedger, *, clock: Callable[[], datetime] | None = None,
                 clock_quality: ClockQuality | None = None,
                 risk_policy: RiskPolicy = FIXTURE_RISK_POLICY_V1,
                 policy: ManagerPolicy = FIXTURE_MANAGER_POLICY_V1) -> None:
        self._ledger = ledger
        # One clock source per ledger: the manager reuses the ledger's injected
        # clock unless a separate one is supplied.
        self._clock = clock or ledger.clock
        self._clock_quality = clock_quality or ClockQuality(
            synced=False, source="unknown", max_error_ms=1000
        )
        self._risk_policy = risk_policy
        self._policy = policy

    # -- decision path -----------------------------------------------------

    def submit_candidate(self, request: DecisionRequest, *, intent: str = "accept",
                         reason_codes: Sequence[str] = ()) -> ReceiptView:
        """Evaluate one candidate and persist its decision.

        ``intent="accept"`` runs the risk gate and, on success, the T1/T2
        bundle. ``intent="held"``/``"rejected"`` record a receipt only (no
        order, reservation or event-use). A repeated receipt id returns the
        stored receipt without any state change (idempotent re-send).
        """
        if intent not in {"accept", "held", "rejected"}:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        if not self._clock_quality.trusted:
            # N00 v2 §3.4, DECIDE x CLOCK_UNTRUSTED -> stop_new.
            raise LedgerError("CLOCK_UNTRUSTED")
        existing = self._view(request.receipt_id)
        if existing is not None:
            return existing
        if intent == "accept":
            return self._accept(request)
        codes = tuple(reason_codes)
        if not codes:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        for code in codes:
            if code not in REASON_CODES:
                raise LedgerError("VALUE_OUT_OF_RANGE")
        self._ledger.record_decision_only(
            self._receipt_draft(request, status=intent, reason_codes=codes, order_id=None)
        )
        view = self._view(request.receipt_id)
        assert view is not None
        return view

    def _accept(self, request: DecisionRequest) -> ReceiptView:
        account = self._ledger.account_snapshot(request.account_id)
        event_used = self._ledger.event_use_exists(
            run_id=request.run_id,
            scenario_id=request.scenario_id,
            event_group_id=request.event_group_id,
            asset=request.asset,
        )
        daily_pnl, peak, current = self._loss_state(request)
        decision = evaluate(
            account=account,
            side=request.side,
            asset=request.asset,
            ref_price=request.reference_price,
            constraints=request.constraints,
            policy=self._risk_policy,
            # PIT: the expiry/loss checks run at the decision time, not at an
            # arbitrary later clock read (parent #769 §C).
            now=request.decision_at,
            expires_at=request.expires_at,
            quantity_cap=request.quantity_cap,
            daily_pnl=request.daily_pnl if request.daily_pnl is not None else daily_pnl,
            peak_equity=request.peak_equity if request.peak_equity is not None else peak,
            current_equity=(
                request.current_equity if request.current_equity is not None else current
            ),
            event_already_used=event_used,
        )
        if not decision.accepted:
            self._ledger.record_decision_only(
                self._receipt_draft(
                    request, status="held", reason_codes=decision.reason_codes, order_id=None
                )
            )
            view = self._view(request.receipt_id)
            assert view is not None
            return view

        receipt_id = request.receipt_id
        order_id = f"paper_order:{receipt_id}"
        reservation = decision.reservation
        assert reservation is not None
        reservation_id = f"res:{receipt_id}"
        event_use_id = (
            f"event_use:{request.run_id}:{request.scenario_id}:"
            f"{request.event_group_id}:{request.asset}"
        )
        reservation_draft = ReservationDraft(
            reservation_id=reservation_id,
            kind=reservation.kind,
            asset=reservation.asset,
            quantity=reservation.quantity,
            amount=reservation.amount,
        )
        try:
            self._ledger.begin_receipt(
                receipt=self._receipt_draft(
                    request, status="accepted",
                    reason_codes=(request.accept_reason_code,), order_id=order_id,
                ),
                order=OrderDraft(
                    order_id=order_id,
                    asset=request.asset,
                    side=request.side,
                    requested_quantity=decision.requested_quantity,
                    expires_at=request.expires_at,
                    reservation_id=reservation_id,
                    event_use_id=event_use_id,
                ),
                reservation=reservation_draft,
                event_use=EventUseDraft(
                    event_use_id=event_use_id,
                    event_group_id=request.event_group_id,
                    asset=request.asset,
                ),
            )
        except LedgerError as exc:
            if exc.code == "ORDER_ALREADY_FINAL":
                view = self._view(receipt_id)
                if view is not None:
                    return view
            if exc.code == "EVENT_ALREADY_USED":
                # Lost a T1 race for the same event: record the hold instead.
                self._ledger.record_decision_only(
                    self._receipt_draft(
                        request, status="held", reason_codes=("EVENT_ALREADY_USED",),
                        order_id=None,
                    )
                )
                view = self._view(receipt_id)
                assert view is not None
                return view
            raise
        return self._commit_after_t1(receipt_id)

    def _commit_after_t1(self, receipt_id: str) -> ReceiptView:
        """T2 with the crash rule of N00 v2 §4.2 (never fill, never backfill)."""
        result = self._ledger.commit_receipt(
            receipt_id, execution_delay_ms=self._policy.execution_delay_ms
        )
        if result == "expired_before_commit":
            self._ledger.abandon_receipt(receipt_id)
        view = self._view(receipt_id)
        assert view is not None
        return view

    def reevaluate_held(self, original_receipt_id: str, request: DecisionRequest) -> ReceiptView:
        """Held re-evaluation creates a new receipt with ``supersedes_receipt_id``."""
        original = self._ledger.receipt_row(original_receipt_id)
        if original["status"] != "held":
            raise LedgerError("VALUE_OUT_OF_RANGE")
        superseding = DecisionRequest(
            **{**request.__dict__, "supersedes_receipt_id": original_receipt_id}
        )
        return self.submit_candidate(superseding, intent="accept")

    # -- execution boundary (N06) -------------------------------------------

    def process_observations(self, order_id: str, observations, constraints,
                             *, now: datetime | None = None,
                             evidence_valid: bool = True) -> ExecutionOutcome:
        """Run the N06 execution model and commit its outcome atomically.

        The durable fill journal, the liquidity already consumed from each
        level and the fence all come from the ledger, so a restart or a
        re-delivered quote can neither double-fill nor resurrect an old quote.
        """
        pending, receipt_state, reservation = self._load_order(order_id, evidence_valid)
        fence = self._ledger.establish_fence(pending.receipt_id, self._clock_quality)
        account = self._ledger.account_snapshot(pending.account_id)
        ref_prices = {
            observation.asset: (observation.bid + observation.ask) / 2
            for observation in observations
        }
        snapshot = headroom_snapshot(
            account=account,
            policy=self._risk_policy,
            ref_prices=ref_prices,
            exclude_reservation=reservation,
        )
        liquidity = LiquidityTracker()
        for observation in observations:
            for level_index, quantity in self._ledger.liquidity_used(
                pending.account_id, observation.observation_id
            ).items():
                liquidity.consume(
                    pending.account_id, observation.observation_id, level_index, quantity
                )
        outcome = execute_pending_order(
            pending,
            receipt=receipt_state,
            fence=fence,
            observations=tuple(observations),
            constraints=constraints,
            risk=snapshot,
            clock=self._clock_quality,
            now=now if now is not None else self._clock(),
            liquidity=liquidity,
            journal=self._ledger.fill_journal(),
        )
        if outcome.fills or outcome.terminal:
            self._ledger.apply_execution(outcome)
        return outcome

    def cancel_pending(self, receipt_id: str, *, reason_code: str):
        """Cancel a not-yet-final order (corrections); past fills stay immutable."""
        row = self._ledger.receipt_row(receipt_id)
        order_ref = row["order_ref"]
        if order_ref is None:
            raise LedgerError("ORDER_ALREADY_FINAL")
        return self._ledger.cancel_pending_order(str(order_ref), reason_code=reason_code)

    def recover(self) -> dict[str, list[str]]:
        """Re-run T2 for interrupted receipts or abandon expired ones."""
        from .ledger import recover as ledger_recover

        return ledger_recover(
            self._ledger, execution_delay_ms=self._policy.execution_delay_ms
        )

    # -- helpers -------------------------------------------------------------

    def _load_order(self, order_id: str, evidence_valid: bool):
        row = self._ledger.order_row(order_id)
        receipt_row = self._ledger.receipt_row(str(row["receipt_id"]))
        pending = PendingOrder(
            order_id=str(row["order_id"]),
            receipt_id=str(row["receipt_id"]),
            run_id=str(row["run_id"]),
            scenario_id=str(row["scenario_id"]),
            account_id=str(row["account_id"]),
            asset=str(row["asset"]),
            side=str(row["side"]),
            requested_quantity=D(str(row["requested_quantity"])),
            remaining_quantity=D(str(row["remaining_quantity"])),
            filled_quantity=D(str(row["filled_quantity"])),
            status=str(row["status"]),
            expires_at=_parse_ts(str(row["expires_at"])),
            accepted_at=(
                _parse_ts(str(row["accepted_at"])) if row["accepted_at"] is not None else None
            ),
            not_before_at=(
                _parse_ts(str(row["not_before_at"])) if row["not_before_at"] is not None else None
            ),
            reservation_id=str(row["reservation_id"]),
            event_use_id=str(row["event_use_id"]),
        )
        decision_quote = None
        raw_quote = receipt_row["decision_quote"]
        if raw_quote is not None:
            source, asset, moment, digest = json.loads(str(raw_quote))
            decision_quote = (source, asset, _parse_ts(moment), digest)
        receipt_state = ReceiptState(
            receipt_id=str(receipt_row["receipt_id"]),
            status=str(receipt_row["status"]),
            input_cutoff_sequence=int(receipt_row["input_cutoff_sequence"]),
            receipt_committed_at=(
                _parse_ts(str(receipt_row["receipt_committed_at"]))
                if receipt_row["receipt_committed_at"] is not None else None
            ),
            decision_quote=decision_quote,
            evidence_valid=evidence_valid,
        )
        reservation = self._ledger.reservation_for_order(order_id)
        return pending, receipt_state, reservation

    def _receipt_draft(self, request: DecisionRequest, *, status: str,
                       reason_codes: Sequence[str], order_id: str | None) -> ReceiptDraft:
        if status == "accepted":
            if request.accept_reason_code not in ACCEPT_REASON_CODES:
                raise LedgerError("VALUE_OUT_OF_RANGE")
        return ReceiptDraft(
            receipt_id=request.receipt_id,
            run_id=request.run_id,
            scenario_id=request.scenario_id,
            account_id=request.account_id,
            decision_at=request.decision_at,
            manager_seen_at=request.manager_seen_at,
            input_cutoff_sequence=request.input_cutoff_sequence,
            signal_refs=tuple(request.signal_refs),
            market_refs=tuple(request.market_refs),
            decision_quote=request.decision_quote,
            policy_version=self._policy.version,
            risk_policy_version=self._risk_policy.version,
            status=status,
            reason_codes=tuple(reason_codes),
            supersedes_receipt_id=request.supersedes_receipt_id,
        )

    def _loss_state(self, request: DecisionRequest):
        history = self._ledger.equity_history(request.account_id)
        if not history:
            return ZERO, None, None
        current = history[-1][1]
        day = request.decision_at.astimezone(UTC).date()
        daily_pnl = ZERO
        for as_of, equity in history:
            if _parse_ts(as_of).astimezone(UTC).date() == day:
                daily_pnl = current - equity
                break
        peak = max((equity for _, equity in history), default=current)
        return daily_pnl, peak, current

    def _view(self, receipt_id: str) -> ReceiptView | None:
        try:
            row = self._ledger.receipt_row(receipt_id)
        except LedgerError:
            return None
        return ReceiptView(
            receipt_id=str(row["receipt_id"]),
            run_id=str(row["run_id"]),
            scenario_id=str(row["scenario_id"]),
            account_id=str(row["account_id"]),
            status=str(row["status"]),
            reason_codes=tuple(json.loads(str(row["reason_codes"]))),
            decision_at=_parse_ts(str(row["decision_at"])),
            receipt_committed_at=(
                _parse_ts(str(row["receipt_committed_at"]))
                if row["receipt_committed_at"] is not None else None
            ),
            order_ref=str(row["order_ref"]) if row["order_ref"] is not None else None,
            supersedes_receipt_id=(
                str(row["supersedes_receipt_id"])
                if row["supersedes_receipt_id"] is not None else None
            ),
            abandoned=row["abandoned_at"] is not None,
            input_cutoff_sequence=int(row["input_cutoff_sequence"]),
            signal_refs=tuple(json.loads(str(row["signal_refs"]))),
            market_refs=tuple(json.loads(str(row["market_refs"]))),
        )
