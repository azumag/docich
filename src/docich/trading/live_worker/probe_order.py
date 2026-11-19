"""One explicitly approved Phase E spot buy, isolated from the continuous LIVE runner.

This module is a separate operator command, not a subcommand of ``live_worker`` and not
imported by any application path::

    python -m docich.trading.live_worker.probe_order --state-dir ... --symbol OAS/JPY \
        --amount 10 --price 100 --max-quote 1010 --fee 2 --reserve 8 [--confirm TOKEN]

Without ``--confirm`` it only reads and prints a preview plus a short token.  With the exact
token it acquires the existing LIVE writer lease, re-checks the same static plan and the
dynamic exchange state, records the one durable intent, and lets ``OrderExecutor`` send it.
The owner's global control file must say ``off``; this command never writes it and never uses
the normal on+trade gate.  A local gate and sender wrapper allow exactly the previewed order
once and check ``off`` again immediately before the POST.

The trial has one fixed ``decision_key``.  Any existing intent under that key (in any state)
prevents a second POST; a rerun with different parameters is refused rather than creating a
new order.  The monitor window is bounded.  An open remainder is canceled through the
existing executor and the final state, order rows and fills are checked.  A full confirmed
fill is the only successful actual-fill outcome; canceled-unfilled, partial, ACK-only,
ambiguous and inconsistent reads are reported as not successful.  Inventory from a fill is
left on the normal LIVE position under the active policy.  No sell is created here.

Exit codes: 0 preview/confirmed full fill, 1 refused/no order, 2 usage, 5 lease held,
7 lease lost, 8 unresolved/partial/unfilled (the normal OFF worker can continue reconciliation
and TTL cancellation).

All exchange access in the default wiring goes through ``open_bitbank_spot_session`` so the
GET-only reader and the order sender use the same fixed order-capable key file.  Tests inject
a synthetic session; nothing here prints keys, balances, account ids or exchange order ids.
"""
from __future__ import annotations

import argparse
import hmac
import os
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from ..live import balance, control, decide, reconcile, safety, views
from ..live.canonical import confirmation_token
from ..live.enable import EnableReason
from ..live.exchange import ExchangeRejected, OrderRequest, dec, dec_text
from ..live.executor import OrderExecutor, SubmitOutcome
from ..live.ingest import ExecutorConfig
from ..live.lease import LeaseHeld, LeaseLost, acquire_writer_lease, release_writer_lease
from ..live.ledger import Intent, IntentRequest, LiveLedger, LiveLedgerError
from ..live.paths import LiveLayoutError, live_layout
from ..live.policy import INITIAL_SYMBOLS, LivePolicy, LivePolicySet

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_LEASE_HELD = 5
EXIT_LEASE_LOST = 7
EXIT_UNRESOLVED = 8

DECISION_KEY = "probe1:phase-e-single-buy"
RULE_ID = "phase_e_single_buy"
SIDE = "buy"
ORDER_TYPE = "limit"
TOKEN_LEN = 10
TOKEN_CHARS = frozenset("0123456789abcdef")
DEFAULT_MONITOR_S = 30
MIN_MONITOR_S = 1
MAX_MONITOR_S = 120
POLL_S = 2.0
CANCEL_CONFIRM_S = 10.0
RESERVE_FLOOR_JPY = Decimal("1")
OPEN_INTENT_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED")
TERMINAL_INTENT_STATES = frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED", "ABANDONED", "NOT_CREATED"})
_PLAIN_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")


class ProbeRefused(ValueError):
    """A fixed, sanitized refusal code with a CLI exit code."""

    def __init__(self, code: str, *, exit_code: int = EXIT_REFUSED) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class ProbePlan:
    symbol: str
    amount: Decimal
    price: Decimal
    max_quote: Decimal
    fee: Decimal
    reserve: Decimal
    monitor_s: int = DEFAULT_MONITOR_S

    def notional(self) -> Decimal:
        return self.amount * self.price

    def gross(self) -> Decimal:
        return self.notional() + self.fee + self.reserve


@dataclass(frozen=True)
class _GateDecision:
    allows_orders: bool


class _ProbeGate:
    """The locally scoped gate: allow only while the owner's global control is explicitly off."""

    def __init__(self, state_dir, *, clock: Callable[[], float]) -> None:
        self._state_dir = state_dir
        self._clock = clock

    def __call__(self) -> _GateDecision:
        try:
            _require_control_off(self._state_dir, self._clock)
        except ProbeRefused:
            return _GateDecision(False)
        return _GateDecision(True)


class _ProbeSender:
    """One-shot sender wrapper: exact previewed request, and cancel only the acknowledged id."""

    def __init__(self, inner, plan: ProbePlan, *, state_dir, clock: Callable[[], float],
                 allowed_order_id: str | None = None) -> None:
        self._inner = inner
        self._plan = plan
        self._state_dir = state_dir
        self._clock = clock
        self._posted = False
        self._order_id = allowed_order_id

    @property
    def kind(self):
        return getattr(self._inner, "kind", None)

    def submit_order(self, req: OrderRequest):
        if self._posted:
            # An explicit rejection is the only safe answer: the executor must not retry a POST.
            raise ExchangeRejected("probe_sender_exhausted")
        if not _request_matches(req, self._plan):
            raise ExchangeRejected("probe_request_mismatch")
        try:
            _require_control_off(self._state_dir, self._clock)
        except ProbeRefused:
            raise ExchangeRejected("probe_control_not_off") from None
        self._posted = True
        snapshot = self._inner.submit_order(req)
        self._order_id = str(snapshot.exchange_order_id)
        return snapshot

    def allow_order(self, exchange_order_id: str) -> None:
        """Bind the order id discovered by reconciliation for this fixed probe intent."""
        if not self._order_id and exchange_order_id:
            self._order_id = str(exchange_order_id)

    def cancel_order(self, symbol: str, exchange_order_id: str):
        if not self._order_id or str(exchange_order_id) != self._order_id or symbol != self._plan.symbol:
            raise ValueError("only the acknowledged probe order can be canceled")
        return self._inner.cancel_order(symbol, exchange_order_id)


def _request_matches(req: object, plan: ProbePlan) -> bool:
    return (isinstance(req, OrderRequest)
            and req.symbol == plan.symbol and req.side == SIDE and req.order_type == ORDER_TYPE
            and req.amount == plan.amount and req.price == plan.price)


def _parse_decimal(text: object, name: str, *, positive: bool = False,
                   nonnegative: bool = False) -> Decimal:
    if not isinstance(text, str):
        raise ProbeRefused(f"invalid_{name}")
    raw = text.strip()
    if _PLAIN_DECIMAL.fullmatch(raw) is None:
        raise ProbeRefused(f"invalid_{name}")
    try:
        value = dec(raw)
    except ValueError:
        raise ProbeRefused(f"invalid_{name}") from None
    if not value.is_finite():
        raise ProbeRefused(f"invalid_{name}")
    if positive and value <= 0:
        raise ProbeRefused(f"invalid_{name}")
    if nonnegative and value < 0:
        raise ProbeRefused(f"invalid_{name}")
    return value


def _state_dir(value: object) -> Path:
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ProbeRefused("state_dir_must_be_absolute")
    path = Path(value)
    if not path.is_dir():
        raise ProbeRefused("state_dir_missing")
    return path


def _plan_from_args(args) -> ProbePlan:
    symbol = args.symbol
    if not isinstance(symbol, str) or symbol not in INITIAL_SYMBOLS:
        raise ProbeRefused("symbol_not_allowed")
    amount = _parse_decimal(args.amount, "amount", positive=True)
    price = _parse_decimal(args.price, "price", positive=True)
    max_quote = _parse_decimal(args.max_quote, "max_quote", positive=True)
    fee = _parse_decimal(args.fee, "fee", nonnegative=True)
    reserve = _parse_decimal(args.reserve, "reserve", nonnegative=True)
    monitor_s = int(args.monitor_seconds)
    if not MIN_MONITOR_S <= monitor_s <= MAX_MONITOR_S:
        raise ProbeRefused("monitor_seconds_out_of_range")
    return ProbePlan(symbol=symbol, amount=amount, price=price, max_quote=max_quote,
                     fee=fee, reserve=reserve, monitor_s=monitor_s)


def _confirm_token(value: object) -> str:
    if not isinstance(value, str):
        raise ProbeRefused("confirmation_invalid")
    token = value.strip()
    if len(token) != TOKEN_LEN or any(ch not in TOKEN_CHARS for ch in token):
        raise ProbeRefused("confirmation_invalid")
    return token


def _require_existing_ledger(layout) -> None:
    if layout.root.is_symlink() or layout.ledger_path.is_symlink():
        raise ProbeRefused("ledger_unreadable")
    if not layout.ledger_path.is_file():
        raise ProbeRefused("ledger_missing")


def _ledger_id(ledger: LiveLedger) -> str:
    try:
        with ledger.read_cursor() as cur:
            row = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
    except Exception:
        raise ProbeRefused("ledger_unreadable") from None
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise ProbeRefused("ledger_unreadable")
    return row[0]


def _active_policy(ledger: LiveLedger, symbol: str) -> tuple[LivePolicySet, LivePolicy]:
    digest = ledger.active_policy_set_digest()
    if digest is None:
        raise ProbeRefused("policy_missing")
    try:
        policy_set = ledger.load_policy_set(digest)
        policy = policy_set.policy_for(symbol)
    except Exception:
        raise ProbeRefused("policy_unavailable") from None
    return policy_set, policy


def _require_control_off(state_dir, clock: Callable[[], float]) -> None:
    result = control.read_control(state_dir, now=clock())
    if result.enabled:
        raise ProbeRefused("control_on")
    if EnableReason.SWITCHED_OFF not in result.reasons:
        raise ProbeRefused("control_not_off")


def _safety(ledger: LiveLedger):
    state = safety.read_safety_state(ledger)
    if not state.ledger_readable:
        raise ProbeRefused("safety_unreadable")
    if state.trips:
        raise ProbeRefused("safety_stops_active")
    return state


def _check_existing(intent: Intent | None, plan: ProbePlan, policy: LivePolicy) -> None:
    if intent is None:
        return
    price = None if intent.price is None else Decimal(intent.price)
    if (intent.symbol, intent.side, intent.order_type, Decimal(intent.amount), price,
            intent.policy_digest, intent.rule_id) != (
            plan.symbol, SIDE, ORDER_TYPE, plan.amount, plan.price, policy.digest, RULE_ID):
        raise ProbeRefused("existing_intent_mismatch")


def _check_other_intents(ledger: LiveLedger, existing: Intent | None) -> None:
    # The fixed decision row is this trial itself: a rerun after a crash must monitor or
    # cancel it.  Every other open intent is foreign and blocks the trial.
    for intent in ledger.list_intents(states=OPEN_INTENT_STATES):
        if existing is None or intent.intent_id != existing.intent_id:
            raise ProbeRefused("active_intents")


def _check_positions(ledger: LiveLedger, plan: ProbePlan, policy: LivePolicy) -> None:
    rows = [row for row in views.open_position_rows(ledger) if row.symbol == plan.symbol]
    if len(rows) > 1:
        raise ProbeRefused("ambiguous_position")
    if rows and rows[0].entry_policy_digest != policy.digest:
        raise ProbeRefused("ambiguous_position")


def _token_content(plan: ProbePlan, policy: LivePolicy, policy_set: LivePolicySet) -> dict[str, str]:
    return {
        "action": "probe_single_buy",
        "symbol": plan.symbol,
        "side": SIDE,
        "order_type": ORDER_TYPE,
        "amount": dec_text(plan.amount),
        "price": dec_text(plan.price),
        "max_quote": dec_text(plan.max_quote),
        "fee": dec_text(plan.fee),
        "reserve": dec_text(plan.reserve),
        "decision_key": DECISION_KEY,
        "rule_id": RULE_ID,
        "policy_digest": policy.digest,
        "policy_set_digest": policy_set.digest,
    }


def _default_session_opener(state_dir, *, symbols: Sequence[str], clock: Callable[[], float]):
    """The production pair: one fixed key, GET-only reader and spot sender, same as the worker."""
    from ..exchanges.bitbank_live.transport import CHECK_ONLY_ENDPOINTS, ENDPOINTS, CcxtReadTransport
    from ..exchanges.bitbank_spot_session import open_bitbank_spot_session

    def transport_factory(credentials):
        # spot_status is a read-only check endpoint; the default worker transport deliberately
        # does not carry it, so this explicit probe command opts into it for this GET read.
        return CcxtReadTransport(credentials, endpoints={**ENDPOINTS, **CHECK_ONLY_ENDPOINTS})

    return open_bitbank_spot_session(state_dir, symbols=symbols, clock=clock,
                                     transport_factory=transport_factory)


def _open_session(state_dir, plan: ProbePlan, *, clock, session_opener):
    opener = session_opener or _default_session_opener
    try:
        reader, sender = opener(state_dir, symbols=INITIAL_SYMBOLS, clock=clock)
    except Exception:
        raise ProbeRefused("session_unavailable") from None
    if reader is None or sender is None:
        raise ProbeRefused("session_unavailable")
    return reader, sender


def _rule_set(reader, policy_set: LivePolicySet) -> dict:
    try:
        return {symbol: reader.market_rules(symbol) for symbol in sorted(policy_set.policies)}
    except Exception:
        raise ProbeRefused("market_rules_unavailable") from None


def _spot_status(reader, plan: ProbePlan):
    try:
        status = reader.fetch_spot_status(plan.symbol)
    except Exception:
        raise ProbeRefused("spot_status_unavailable") from None
    if getattr(status, "symbol", plan.symbol) != plan.symbol:
        raise ProbeRefused("spot_status_unavailable")
    text = getattr(status, "status", None)
    if text != "NORMAL":
        raise ProbeRefused("pair_stopped")
    try:
        min_amount = dec(getattr(status, "min_amount", None))
    except ValueError:
        raise ProbeRefused("spot_status_unavailable") from None
    if not min_amount.is_finite() or min_amount <= 0:
        raise ProbeRefused("spot_status_unavailable")
    return min_amount


def _check_dynamic(ledger: LiveLedger, reader, plan: ProbePlan, policy_set: LivePolicySet,
                   *, clock: Callable[[], float]) -> None:
    rules = _rule_set(reader, policy_set)
    rule = rules.get(plan.symbol)
    if rule is None:
        raise ProbeRefused("market_rules_unavailable")
    if plan.amount % rule.amount_step != 0:
        raise ProbeRefused("amount_step")
    if plan.price % rule.price_tick != 0:
        raise ProbeRefused("price_tick")
    dynamic_min = _spot_status(reader, plan)
    if plan.amount < max(rule.min_amount, dynamic_min):
        raise ProbeRefused("amount_below_min")
    if rule.max_fee_rate is None:
        raise ProbeRefused("fee_rate_unknown")
    if plan.fee < plan.notional() * rule.max_fee_rate:
        raise ProbeRefused("fee_below_max")
    reserve_floor = max(rule.quote_unit * 2, RESERVE_FLOOR_JPY)
    if plan.reserve < reserve_floor:
        raise ProbeRefused("reserve_too_small")
    if plan.gross() > plan.max_quote:
        raise ProbeRefused("max_quote_exceeded")
    try:
        snapshot = reader.fetch_balance()
    except Exception:
        raise ProbeRefused("balance_unavailable") from None
    quote = snapshot.assets.get(rule.quote)
    if quote is None or quote.free < plan.gross():
        raise ProbeRefused("insufficient_jpy")
    try:
        check = balance.check_balances(ledger, snapshot, rules, currencies=(rule.base, rule.quote))
    except Exception:
        raise ProbeRefused("balance_inconsistent") from None
    if not check.checks or any(item.status != "ok" for item in check.checks):
        raise ProbeRefused("balance_inconsistent")
    try:
        book = reader.fetch_order_book(plan.symbol, depth=20)
    except Exception:
        raise ProbeRefused("book_unavailable") from None
    problem = decide.book_age_problem(clock(), book.exchange_ts_ms)
    if problem is not None:
        raise ProbeRefused(problem)
    if not book.asks or book.asks[0][0] > plan.price:
        raise ProbeRefused("limit_not_executable")
    available = sum((amount for price, amount in book.asks if price <= plan.price), Decimal(0))
    if available < plan.amount:
        raise ProbeRefused("liquidity_insufficient")


def _check_status_for_resume(reader, plan: ProbePlan) -> None:
    """A non-terminal existing trial may still be canceled: require the pair to be NORMAL."""
    _spot_status(reader, plan)


def preview(state_dir, plan: ProbePlan, *, clock: Callable[[], float] = time.time,
            session_opener=None) -> dict:
    """Read-only preview; no ledger, lease, control or exchange write happens here."""
    state = _state_dir(str(state_dir))
    layout = live_layout(state)
    _require_existing_ledger(layout)
    try:
        ledger = LiveLedger.open_readonly(layout)
    except Exception:
        raise ProbeRefused("ledger_unreadable") from None
    try:
        ledger_id = _ledger_id(ledger)
        policy_set, policy = _active_policy(ledger, plan.symbol)
        _safety(ledger)
        existing = views.intent_by_decision_key(ledger, DECISION_KEY)
        _check_existing(existing, plan, policy)
        _check_other_intents(ledger, existing)
        _check_positions(ledger, plan, policy)
        _require_control_off(state, clock)
        if existing is None:
            reader, _sender = _open_session(state, plan, clock=clock, session_opener=session_opener)
            _check_dynamic(ledger, reader, plan, policy_set, clock=clock)
        token = confirmation_token(_token_content(plan, policy, policy_set), ledger_id)
        return {
            "kind": "preview",
            "symbol": plan.symbol,
            "amount": dec_text(plan.amount),
            "price": dec_text(plan.price),
            "max_quote": dec_text(plan.max_quote),
            "policy_digest": policy.digest[:12],
            "existing_state": None if existing is None else existing.state,
            "token": token,
        }
    finally:
        ledger.close()


def _prepare_existing(ledger: LiveLedger, lease, intent: Intent, *, now: float) -> Intent:
    if intent.state == "PLANNED":
        # Provably never sent.  Keep the evidence and make sure it can never be submitted later.
        return ledger.transition_intent(lease, intent.intent_id, expected_state="PLANNED",
                                        to_state="ABANDONED", now=now,
                                        reason_code="probe_resume_stale_plan")
    if intent.state == "SUBMITTING":
        return ledger.transition_intent(lease, intent.intent_id, expected_state="SUBMITTING",
                                        to_state="UNKNOWN_SUBMISSION", now=now,
                                        reason_code="probe_resume_in_flight")
    return intent


def _new_executor(ledger, lease, reader, sender, gate, plan: ProbePlan, state_dir,
                  clock: Callable[[], float], *, allowed_order_id: str | None = None):
    wrapped = _ProbeSender(sender, plan, state_dir=state_dir, clock=clock,
                           allowed_order_id=allowed_order_id)
    executor = OrderExecutor(ledger, lease, reader=reader, sender=wrapped, enable_check=gate,
                             clock=clock, symbols=(plan.symbol,), config=ExecutorConfig())
    return executor, wrapped


def _poll(executor: OrderExecutor, ledger: LiveLedger, intent_id: str) -> Intent | None:
    intent = ledger.get_intent(intent_id)
    if intent is None:
        return None
    if intent.state == "UNKNOWN_SUBMISSION":
        reconcile.try_resolve(executor, intent)
    elif intent.state in ("ACKED", "PARTIALLY_FILLED"):
        executor.ingest.refresh(intent)
    return ledger.get_intent(intent_id)


def _sleep_until(clock, sleep, moment: float) -> None:
    remaining = moment - clock()
    if remaining > 0:
        sleep(min(POLL_S, remaining))


def _monitor(executor: OrderExecutor, ledger: LiveLedger, intent_id: str, plan: ProbePlan,
             *, clock, sleep, sender_wrapper: _ProbeSender | None = None) -> Intent | None:
    deadline = clock() + plan.monitor_s
    intent = None
    while clock() < deadline:
        try:
            intent = _poll(executor, ledger, intent_id)
        except LeaseLost:
            raise
        except Exception:
            break
        if intent is None or intent.state in TERMINAL_INTENT_STATES:
            return intent
        _sleep_until(clock, sleep, deadline)
    try:
        intent = _poll(executor, ledger, intent_id)
    except LeaseLost:
        raise
    except Exception:
        intent = ledger.get_intent(intent_id)
    if intent is None or intent.state not in ("ACKED", "PARTIALLY_FILLED"):
        return intent
    if not intent.exchange_order_id:
        return intent
    if sender_wrapper is not None:
        sender_wrapper.allow_order(intent.exchange_order_id)
    try:
        executor.cancel(intent.intent_id)
    except LeaseLost:
        raise
    except Exception:
        return ledger.get_intent(intent_id)
    cancel_end = clock() + CANCEL_CONFIRM_S
    while clock() < cancel_end:
        try:
            intent = _poll(executor, ledger, intent_id)
        except LeaseLost:
            raise
        except Exception:
            break
        if intent is None or intent.state in TERMINAL_INTENT_STATES:
            return intent
        _sleep_until(clock, sleep, cancel_end)
    try:
        return _poll(executor, ledger, intent_id)
    except LeaseLost:
        raise
    except Exception:
        return ledger.get_intent(intent_id)


def _accounting(ledger: LiveLedger, intent: Intent) -> tuple[str | None, Decimal]:
    """(problem, filled base amount) for the intent's order and recorded fills."""
    order = views.order_for_intent(ledger, intent.intent_id)
    if order is None:
        return "order_missing", Decimal(0)
    if order.intent_id != intent.intent_id or order.symbol != intent.symbol or order.side != SIDE:
        return "order_identity", Decimal(0)
    if (intent.exchange_order_id is not None
            and order.exchange_order_id != intent.exchange_order_id):
        return "order_identity", Decimal(0)
    if order.order_type != ORDER_TYPE or order.amount != Decimal(intent.amount) or (
            intent.price is not None and order.price != Decimal(intent.price)):
        return "order_identity", Decimal(0)
    fills = views.fills_for_order(ledger, order.exchange_order_id)
    executed = sum((fill.amount for fill in fills), Decimal(0))
    if executed != order.executed_amount:
        return "fill_total", executed
    if any(fill.symbol != order.symbol or fill.side != SIDE for fill in fills):
        return "fill_identity", executed
    if any(fill.fee_status != "known" for fill in fills):
        return "fee_unknown", executed
    expected_net = Decimal(0)
    for fill in fills:
        expected_net += fill.amount
        if fill.fee_currency == order.symbol.split("/")[0]:
            expected_net -= fill.fee_amount or Decimal(0)
    try:
        net = views.position_net_base(ledger, intent.position_id)
    except Exception:
        return "position_unreadable", executed
    if net != expected_net:
        return "position_mismatch", executed
    if executed > 0 and net <= 0:
        return "position_empty", executed
    return None, executed


@dataclass(frozen=True)
class _Outcome:
    outcome: str
    state: str
    filled: Decimal
    exit_code: int
    next: str | None = None
    problem: str | None = None


def _outcome(ledger: LiveLedger, intent: Intent | None) -> _Outcome:
    if intent is None:
        return _Outcome("unresolved", "missing", Decimal(0), EXIT_UNRESOLVED,
                        next="restore the OFF worker", problem="intent_missing")
    state = intent.state
    live_safety = safety.read_safety_state(ledger)
    if not live_safety.ledger_readable or live_safety.trips:
        return _Outcome("unresolved", state, Decimal(0), EXIT_UNRESOLVED,
                        next="restore the OFF worker", problem="safety_tripped")
    if state in ("REJECTED", "ABANDONED", "NOT_CREATED", "EXPIRED"):
        return _Outcome("no_order", state, Decimal(0), EXIT_REFUSED)
    if state == "FILLED":
        problem, filled = _accounting(ledger, intent)
        order = views.order_for_intent(ledger, intent.intent_id)
        if problem is None and (order is None or order.status != "FULLY_FILLED"
                                or filled != Decimal(intent.amount)):
            problem = "fill_incomplete"
        if problem is not None:
            return _Outcome("unresolved", state, filled, EXIT_UNRESOLVED,
                            next="restore the OFF worker", problem=problem)
        return _Outcome("filled", state, filled, EXIT_OK)
    if state == "CANCELED":
        problem, filled = _accounting(ledger, intent)
        order = views.order_for_intent(ledger, intent.intent_id)
        expected_status = "CANCELED_PARTIALLY_FILLED" if filled > 0 else "CANCELED_UNFILLED"
        if problem is None and (order is None or order.status != expected_status):
            problem = "order_status"
        if problem is not None:
            return _Outcome("unresolved", state, filled, EXIT_UNRESOLVED,
                            next="restore the OFF worker", problem=problem)
        if filled > 0:
            return _Outcome("partial", state, filled, EXIT_UNRESOLVED,
                            next="restore the OFF worker")
        return _Outcome("canceled_unfilled", state, Decimal(0), EXIT_UNRESOLVED)
    return _Outcome("unresolved", state, Decimal(0), EXIT_UNRESOLVED,
                    next="restore the OFF worker", problem="not_terminal")


def run(state_dir, plan: ProbePlan, confirm: object, *, clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep, session_opener=None) -> dict:
    """Execute exactly the previewed plan once, under the LIVE writer lease."""
    token = _confirm_token(confirm)
    state = _state_dir(str(state_dir))
    _require_control_off(state, clock)
    layout = live_layout(state)
    _require_existing_ledger(layout)
    try:
        ledger = LiveLedger.open(layout, paper_dir=Path(state) / "trading", now=clock())
    except (LiveLayoutError, LiveLedgerError, OSError):
        raise ProbeRefused("ledger_unreadable") from None
    writer = None
    try:
        try:
            writer = acquire_writer_lease(ledger, layout, now=clock())
        except LeaseHeld:
            raise ProbeRefused("lease_held", exit_code=EXIT_LEASE_HELD) from None
        ledger_id = _ledger_id(ledger)
        policy_set, policy = _active_policy(ledger, plan.symbol)
        expected = confirmation_token(_token_content(plan, policy, policy_set), ledger_id)
        if len(token) != TOKEN_LEN or not hmac.compare_digest(token, expected):
            raise ProbeRefused("stale_preview")
        _require_control_off(state, clock)
        _safety(ledger)
        existing = views.intent_by_decision_key(ledger, DECISION_KEY)
        _check_existing(existing, plan, policy)
        _check_other_intents(ledger, existing)
        _check_positions(ledger, plan, policy)

        if existing is not None and existing.state in TERMINAL_INTENT_STATES:
            return _view(plan, _outcome(ledger, existing))

        gate = _ProbeGate(state, clock=clock)
        if existing is not None:
            # A stale PLANNED row is provably unsent; an in-flight row becomes UNKNOWN.  Do
            # this before any exchange read so recovery does not depend on the pair's status.
            intent = _prepare_existing(ledger, writer, existing, now=clock())
            if intent.state in TERMINAL_INTENT_STATES:
                return _view(plan, _outcome(ledger, intent))
            reader, sender = _open_session(state, plan, clock=clock, session_opener=session_opener)
            _check_status_for_resume(reader, plan)
            allowed = intent.exchange_order_id
            if intent.state in ("ACKED", "PARTIALLY_FILLED") and not allowed:
                raise ProbeRefused("order_id_missing")
            executor, wrapper = _new_executor(ledger, writer, reader, sender, gate, plan, state, clock,
                                              allowed_order_id=allowed)
        else:
            reader, sender = _open_session(state, plan, clock=clock, session_opener=session_opener)
            _check_dynamic(ledger, reader, plan, policy_set, clock=clock)
            executor, wrapper = _new_executor(ledger, writer, reader, sender, gate, plan, state, clock)
            request = IntentRequest(decision_key=DECISION_KEY, symbol=plan.symbol, side=SIDE,
                                    order_type=ORDER_TYPE, amount=dec_text(plan.amount),
                                    price=dec_text(plan.price), policy_digest=policy.digest,
                                    rule_id=RULE_ID)
            try:
                intent = executor.plan(request)
            except (LiveLedgerError, ValueError):
                raise ProbeRefused("intent_refused") from None
            if intent.state != "PLANNED":
                raise ProbeRefused("intent_not_planned")
            try:
                _require_control_off(state, clock)
            except ProbeRefused:
                # The intent is durable but provably unsent.  Close it locally so a later
                # rerun cannot mistake it for a sendable plan.
                try:
                    ledger.transition_intent(writer, intent.intent_id, expected_state="PLANNED",
                                             to_state="ABANDONED", now=clock(),
                                             reason_code="probe_control_closed")
                    executor.ingest.close_flat_positions()
                except Exception:
                    pass
                raise
            outcome = executor.submit(intent.intent_id)
            if outcome is not SubmitOutcome.ACKED:
                intent = ledger.get_intent(intent.intent_id)
                if outcome is SubmitOutcome.UNKNOWN:
                    intent = _monitor(executor, ledger, intent.intent_id, plan, clock=clock, sleep=sleep,
                                      sender_wrapper=wrapper)
                return _view(plan, _outcome(ledger, intent))
        intent = _monitor(executor, ledger, intent.intent_id, plan, clock=clock, sleep=sleep,
                          sender_wrapper=wrapper)
        return _view(plan, _outcome(ledger, intent))
    finally:
        if writer is not None:
            try:
                release_writer_lease(ledger, writer, now=clock())
            except Exception:
                pass
        try:
            ledger.close()
        except Exception:
            pass


def _view(plan: ProbePlan, outcome: _Outcome) -> dict:
    return {
        "kind": "run",
        "symbol": plan.symbol,
        "amount": dec_text(plan.amount),
        "price": dec_text(plan.price),
        "max_quote": dec_text(plan.max_quote),
        "outcome": outcome.outcome,
        "state": outcome.state,
        "filled_amount": dec_text(outcome.filled) if outcome.filled > 0 else "0",
        "problem": outcome.problem,
        "next": outcome.next,
        "exit_code": outcome.exit_code,
    }


def _print_preview(view: Mapping, out: TextIO) -> None:
    print("probe: preview", file=out)
    print(f"symbol: {view['symbol']}", file=out)
    print(f"side: {SIDE}", file=out)
    print(f"type: {ORDER_TYPE}", file=out)
    print(f"amount: {view['amount']}", file=out)
    print(f"limit: {view['price']}", file=out)
    print(f"max_quote: {view['max_quote']}", file=out)
    print(f"policy: {view['policy_digest']}", file=out)
    print(f"intent: {view['existing_state'] or 'none'}", file=out)
    print(f"token: {view['token']}", file=out)


def _print_run(view: Mapping, out: TextIO) -> None:
    print("probe: run", file=out)
    print(f"symbol: {view['symbol']}", file=out)
    print(f"amount: {view['amount']}", file=out)
    print(f"limit: {view['price']}", file=out)
    print(f"max_quote: {view['max_quote']}", file=out)
    print(f"outcome: {view['outcome']}", file=out)
    print(f"state: {view['state']}", file=out)
    print(f"filled_amount: {view['filled_amount']}", file=out)
    if view.get("problem"):
        print(f"reason: {view['problem']}", file=out)
    if view.get("next"):
        print(f"next: {view['next']}", file=out)


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, stream: TextIO, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stream = stream

    def _print_message(self, message: str, file=None) -> None:
        if message:
            self._stream.write(message)


def _parser(out: TextIO) -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m docich.trading.live_worker.probe_order",
        description="preview/run one explicitly approved Phase E spot buy while global control is off",
        stream=out)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--amount", required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--max-quote", required=True)
    parser.add_argument("--fee", required=True)
    parser.add_argument("--reserve", required=True)
    parser.add_argument("--monitor-seconds", type=int, default=DEFAULT_MONITOR_S)
    parser.add_argument("--confirm", default=None,
                        help="the short lower-hex token printed by the preview")
    return parser


def main(argv=None, *, out: TextIO | None = None, err: TextIO | None = None,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         session_opener=None) -> int:
    stream = out or sys.stdout
    parser = _parser(stream)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        plan = _plan_from_args(args)
        if args.confirm is None:
            view = preview(args.state_dir, plan, clock=clock, session_opener=session_opener)
            _print_preview(view, stream)
            return EXIT_OK
        view = run(args.state_dir, plan, args.confirm, clock=clock, sleep=sleep,
                   session_opener=session_opener)
        _print_run(view, stream)
        return int(view["exit_code"])
    except ProbeRefused as exc:
        print(f"refused: {exc.code}", file=stream)
        return exc.exit_code
    except LeaseLost:
        print("refused: lease_lost", file=stream)
        return EXIT_LEASE_LOST
    except Exception as exc:
        # Never print an exception message: adapter messages can contain account or key data.
        print(f"refused: internal_error:{type(exc).__name__}", file=stream)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
