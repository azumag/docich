"""One LIVE worker cycle: the skeleton around the executor (Issue #3623, L4b-5b-1).

``run_cycle`` performs the non-decision steps of one cycle in a fixed order: read the
owner's on/off control switch, check the writer lease, look at the exchange reader's health, reload
the market rules on their cadence, run ``OrderExecutor.step`` (poll/resolve/sweep/close
flat positions/check the balance), fetch one balance snapshot, fetch one market frame per
symbol and keep only the fresh ones, read the persisted safety state, then hand the
per-cycle facts to the ``decide`` hook.

What is *not* here yet (task 5b-2): order planning and sending.  ``decide`` is a
documented no-op placeholder in this task; the hook receives the same executor the cycle
runs (so the eventual implementation can plan/send through it), the cycle state and the
per-cycle context.

There are no exits here: ``run_cycle`` returns a ``CycleOutcome``; the runner maps
outcomes to exit codes (auth -> 3, clock -> 4, lease lost -> 7) in a later task.  Nothing
in this module ends the process, imports an exchange adapter, or reaches the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Protocol

from ..freshness import QUALITY_FRESH, assess_market_freshness
from ..market_data import MarketFrame
from . import fence
from .enable import EnableReason, EnableResult
from .lease import LeaseLost
from .safety import read_safety_state
from .ttl import cancel_expired

if TYPE_CHECKING:  # annotations only: the cycle itself does not need these types to run
    from .exchange import BalanceSnapshot
    from .policy import LivePolicySet
    from .safety import SafetyState

RULES_UNAVAILABLE = "rules_unavailable"
BALANCE_UNAVAILABLE = "balance_unavailable"
READER_UNHEALTHY = "reader_unhealthy"
LEASE_LOST_REASON = "lease_lost"


class CycleOutcome(str, Enum):
    CONTINUE = "continue"
    CYCLE_FAILED = "cycle_failed"
    STOP_REQUESTED = "stop_requested"
    EXIT_AUTH = "exit_auth"
    EXIT_CLOCK = "exit_clock"
    EXIT_LEASE_LOST = "exit_lease_lost"


class CycleReader(Protocol):
    """What a cycle needs from the reader object (the SAME instance the executor wraps)."""

    def health(self):  # -> object with .last_error_kind: str|None, .consecutive_failures: int
        ...

    def reload_rules(self) -> None:
        ...

    def fetch_balance(self):  # -> BalanceSnapshot
        ...


class MarketData(Protocol):
    def fetch_frame(self, symbol: str, now: float) -> MarketFrame:
        ...  # raises on ANY failure


@dataclass(frozen=True)
class CycleConfig:
    rules_reload_every: int = 10          # cycles; must be >= 1
    max_consecutive_reader_failures: int = 3

    def __post_init__(self) -> None:
        if self.rules_reload_every < 1:
            raise ValueError("rules_reload_every must be at least 1")


def _decide_and_act(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext") -> None:
    """Placeholder for the decision step (task 5b-2): no orders are planned or sent here.

    The real hook will receive the executor the cycle already ran, the runner-owned state
    and this cycle's context, so it can plan/send through the same gate/lease/reader.
    """
    return None


@dataclass(frozen=True)
class CycleDeps:
    ledger: object
    lease: object
    executor: object
    reader: CycleReader
    market_data: MarketData
    gate: Callable[[], "EnableResult"]     # the very callable passed to OrderExecutor(enable_check=...)
    policy_set: "LivePolicySet"
    symbols: tuple[str, ...]
    clock: Callable[[], float]
    stop_requested: Callable[[], bool]
    decide: Callable[["CycleDeps", "CycleState", "CycleContext"], None] = _decide_and_act
    config: CycleConfig = CycleConfig()

    def __post_init__(self) -> None:
        if getattr(self.executor, "enable_check", None) is not self.gate:
            raise ValueError("gate must be the very callable the executor uses for enable_check")
        # OrderExecutor wraps its reader in Guarded; the cycle must read health/balance from that
        # same inner reader, otherwise the gate and health could disagree about the exchange.
        if getattr(getattr(self.executor, "reader", None), "_inner", None) is not self.reader:
            raise ValueError("reader must be the same object the executor wraps")


@dataclass
class CycleState:
    """Mutable state owned by the runner; survives across cycles."""

    cycle_number: int = 0
    consecutive_failures: int = 0
    last_worker_state: tuple | None = None
    dedupe: dict = field(default_factory=dict)   # reserved for task 5b-2; lost on restart on purpose
    ttl_deferred: int = 0                        # per-cycle TTL facts: reset at the top of every cycle
    ttl_failed: int = 0
    record_failures: int = 0
    balance_warnings: int = 0                    # per-cycle: confirmed mismatch currencies
    shadow_dedupe: dict = field(default_factory=dict)  # shadow-only decision dedupe
    shadow_book: object | None = None                  # in-process projection cache (replayed on restart)


@dataclass
class CycleContext:
    """Per-cycle facts handed to the decide hook."""

    now: float
    mode: str | None
    enable: "EnableResult"
    safety: "SafetyState"
    balance: "BalanceSnapshot | None"
    frames: dict[str, MarketFrame]            # FRESH frames only
    stale_symbols: tuple[str, ...]
    block_reasons: set[str]                   # cycle-level BUY blocks (e.g. "rules_unavailable")
    can_decide: bool                          # False if the balance could not be read


@dataclass(frozen=True)
class CycleReport:
    outcome: CycleOutcome
    cycle_number: int
    mode: str | None
    reason: str | None
    block_reasons: tuple[str, ...]
    fresh_symbols: tuple[str, ...]
    stale_symbols: tuple[str, ...]
    control_state: str
    control_mode: str | None
    balance_warnings: int = 0


def _control_state(enable: "EnableResult | None") -> str:
    """The status view of one gate reading ("unknown" when there was no reading)."""
    if enable is None:
        return "unknown"
    if enable.enabled:
        return "on"
    return "off" if EnableReason.SWITCHED_OFF in enable.reasons else "unknown"


def _report(outcome: CycleOutcome, state: CycleState, mode: str | None, reason: str | None,
            block_reasons: object = (), fresh: object = (), stale: object = (),
            enable: "EnableResult | None" = None) -> CycleReport:
    return CycleReport(outcome=outcome, cycle_number=state.cycle_number, mode=mode, reason=reason,
                       block_reasons=tuple(sorted(block_reasons)),
                       fresh_symbols=tuple(sorted(fresh)), stale_symbols=tuple(sorted(stale)),
                       control_state=_control_state(enable),
                       control_mode=None if enable is None else enable.mode,
                       balance_warnings=max(0, int(state.balance_warnings)))


def _lease_lost(deps: CycleDeps, state: CycleState, mode: str | None, now: float, detail: str,
                block_reasons: object = (), fresh: object = (), stale: object = (),
                enable: "EnableResult | None" = None) -> CycleReport:
    """Best-effort trip (stopping needs no lease), then the exit outcome."""
    fence.on_lease_lost(deps.ledger, detail=detail, now=now)
    return _report(CycleOutcome.EXIT_LEASE_LOST, state, mode, LEASE_LOST_REASON,
                   block_reasons, fresh, stale, enable=enable)


def _finish(deps: CycleDeps, state: CycleState, outcome: CycleOutcome, mode: str | None,
            reason: str | None, block_reasons: object, fresh: object, stale: object,
            now: float, enable: "EnableResult | None" = None) -> CycleReport:
    """Record one worker_state event when its key changes, then build the report.

    Used by every return of ``_run_cycle`` except STOP_REQUESTED (which writes nothing) and
    EXIT_LEASE_LOST (the lease is gone, so no write is attempted).  A LeaseLost from the write
    itself is the same exit; any other write failure keeps the computed outcome.
    """
    blocks = ",".join(sorted(block_reasons))
    key = (mode or "none", outcome.value, blocks, reason or "")
    if key != state.last_worker_state:
        payload = {"mode": mode or "none", "outcome": outcome.value, "blocks": blocks,
                   "reason": reason or "", "cycle": str(state.cycle_number)}
        try:
            deps.ledger.record_live_event(deps.lease, "worker_state", payload, now=now)
        except LeaseLost as exc:
            return _lease_lost(deps, state, mode, now, str(exc), block_reasons, fresh, stale,
                               enable=enable)
        except Exception:
            pass  # keep the computed outcome; do not record, do not raise
        else:
            state.last_worker_state = key
    return _report(outcome, state, mode, reason, block_reasons, fresh, stale, enable=enable)


def _health(reader: CycleReader):
    """Read health; a reader that cannot answer is unknown, not a reason to exit."""
    try:
        return reader.health()
    except Exception:
        return None


def _health_kind(health: object) -> str | None:
    return getattr(health, "last_error_kind", None)


def _health_failures(health: object) -> int:
    try:
        return int(getattr(health, "consecutive_failures", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _balance_warnings(executor) -> int:
    """How many currencies the last confirmed balance check warned about (never negative)."""
    try:
        currencies = getattr(getattr(executor, "monitor", None), "warning_currencies", ())
        return max(0, len(currencies))
    except Exception:
        return 0


def _run_cycle(deps: CycleDeps, state: CycleState) -> CycleReport:
    """Run steps 0-9 and 14 of one cycle; ``run_cycle`` contains any exception escaping here."""
    state.cycle_number += 1
    # These are per-cycle facts, never stale: clear them before any step can fill them.
    state.ttl_deferred = 0
    state.ttl_failed = 0
    state.record_failures = 0
    state.balance_warnings = 0
    cycle_number = state.cycle_number
    now = float(deps.clock())

    # 0. the stop flag wins over everything: nothing is read and nothing is written
    if deps.stop_requested():
        return _report(CycleOutcome.STOP_REQUESTED, state, None, "stop_requested")

    # 1. the owner's on/off switch; a gate that raises is a closed gate, not a failed cycle
    mode: str | None = None
    try:
        enable = deps.gate()
        mode = enable.mode if enable.enabled else None
    except Exception:
        enable = EnableResult(False, (EnableReason.CHECK_ERROR,))

    # 2. the writer lease, before anything touches the exchange
    try:
        fence.check_lease(deps.ledger, deps.lease)
    except LeaseLost as exc:
        return _lease_lost(deps, state, mode, now, str(exc), enable=enable)

    # 3. reader health: auth/clock are terminal, and write no trip (nothing was being sent)
    health = _health(deps.reader)
    kind = _health_kind(health)
    if kind == "auth":
        return _finish(deps, state, CycleOutcome.EXIT_AUTH, mode, "auth", (), (), (), now, enable=enable)
    if kind == "clock":
        return _finish(deps, state, CycleOutcome.EXIT_CLOCK, mode, "clock", (), (), (), now, enable=enable)

    block_reasons: set[str] = set()

    # 4. market rules reload on a cadence, never on the first cycle; a failure keeps the old rules
    if (cycle_number - 1) % deps.config.rules_reload_every == 0 and cycle_number > 1:
        try:
            deps.reader.reload_rules()
        except Exception:
            block_reasons.add(RULES_UNAVAILABLE)
        else:
            deps.executor.reset_rules()

    # 5. withdraw open orders older than the TTL (only when the executor has a sender)
    try:
        report = cancel_expired(deps, state, now)
        state.ttl_deferred = report.deferred
        state.ttl_failed = len(report.failed)
    except LeaseLost as exc:
        return _lease_lost(deps, state, mode, now, str(exc), enable=enable)

    # 6. poll/resolve/sweep/close-flat/check-balance
    try:
        deps.executor.step()
    except LeaseLost:
        # the executor's own _guard already tripped the latch; do not trip twice
        return _report(CycleOutcome.EXIT_LEASE_LOST, state, mode, LEASE_LOST_REASON, block_reasons,
                       enable=enable)
    except Exception as exc:
        state.consecutive_failures += 1
        return _finish(deps, state, CycleOutcome.CYCLE_FAILED, mode,
                       f"step_failed:{type(exc).__name__}", block_reasons, (), (), now,
                       enable=enable)

    # The balance monitor only warns: its confirmed mismatch currencies are a per-cycle
    # fact for the status file, never a block and never a safety trip.
    state.balance_warnings = _balance_warnings(deps.executor)

    # 7. one fresh balance snapshot; without it no decision can be made safely
    balance = None
    can_decide = True
    try:
        balance = deps.reader.fetch_balance()
    except Exception:
        balance = None
        can_decide = False
        block_reasons.add(BALANCE_UNAVAILABLE)

    # 8. one frame per symbol; only FRESH frames may reach a decision
    frames: dict[str, MarketFrame] = {}
    stale: list[str] = []
    for symbol in sorted(deps.symbols):
        try:
            frame = deps.market_data.fetch_frame(symbol, now)
            if not isinstance(frame, MarketFrame) or frame.symbol != symbol:
                raise ValueError("public frame missing or mismatched")
            fetched_at = float(deps.clock())
            assessment = assess_market_freshness(
                symbol, fetched_at=fetched_at, data_as_of=frame.as_of,
                timeframe_s=frame.timeframe_seconds,
                last_bar_start=frame.timestamps[-1] if frame.timestamps else None,
                now=now,
            )
        except Exception:
            stale.append(symbol)
            continue
        if assessment.quality == QUALITY_FRESH:
            frames[symbol] = frame
        else:
            stale.append(symbol)

    # 9. persisted safety latch, then the decision hook (monitor mode never decides)
    safety = read_safety_state(deps.ledger)
    if mode in ("shadow", "trade"):
        ctx = CycleContext(now=now, mode=mode, enable=enable, safety=safety, balance=balance,
                           frames=frames, stale_symbols=tuple(sorted(stale)),
                           block_reasons=block_reasons, can_decide=can_decide)
        if ctx.can_decide:
            # a stop requested during steps 6-8 wins over the decision: nothing is decided or sent
            if deps.stop_requested():
                return _report(CycleOutcome.STOP_REQUESTED, state, mode, "stop_requested",
                               block_reasons, frames, stale, enable=enable)
            try:
                deps.decide(deps, state, ctx)
            except LeaseLost as exc:
                return _lease_lost(deps, state, mode, now, str(exc), block_reasons, frames, stale,
                                   enable=enable)
            except Exception as exc:
                state.consecutive_failures += 1
                return _finish(deps, state, CycleOutcome.CYCLE_FAILED, mode,
                               f"decide_failed:{type(exc).__name__}", block_reasons, frames,
                               stale, now, enable=enable)

    # 10. the reader after the cycle: auth/clock exit, too many consecutive failures fail the cycle
    health = _health(deps.reader)
    kind = _health_kind(health)
    if kind == "auth":
        return _finish(deps, state, CycleOutcome.EXIT_AUTH, mode, "auth",
                       block_reasons, frames, stale, now, enable=enable)
    if kind == "clock":
        return _finish(deps, state, CycleOutcome.EXIT_CLOCK, mode, "clock",
                       block_reasons, frames, stale, now, enable=enable)
    if _health_failures(health) >= deps.config.max_consecutive_reader_failures:
        outcome = CycleOutcome.CYCLE_FAILED
        reason = READER_UNHEALTHY
        state.consecutive_failures += 1
    else:
        outcome = CycleOutcome.CONTINUE
        reason = None
        state.consecutive_failures = 0

    # 14. one worker_state event per *change* (the per-cycle heartbeat is a later status file)
    return _finish(deps, state, outcome, mode, reason, block_reasons, frames, stale, now,
                   enable=enable)


def run_cycle(deps: CycleDeps, state: CycleState) -> CycleReport:
    """Run one cycle; an exception anywhere is contained here and never escapes.

    Reader health at steps 3 and 10 is judged from the reader's own counters, not from this cycle's
    reads: the real ``BitbankReader`` resets them on every successful public read, and step 7's
    ``fetch_balance`` runs every cycle, so ``reader_unhealthy`` at step 10 means reads at or after
    that balance read kept failing, while a clock/auth error raised earlier in step 6 (and absorbed
    by the executor there) is cleared by the later, successful balance read.  No production code
    calls ``run_cycle`` yet (the runner is a later task), and ``decide`` is a no-op placeholder:
    this module has no order path.
    """
    try:
        return _run_cycle(deps, state)
    except LeaseLost as exc:
        # the lease may already be gone: the trip is best effort and the log is not written
        try:
            now = float(deps.clock())
        except Exception:
            now = 0.0
        fence.on_lease_lost(deps.ledger, detail=str(exc), now=now)
        return _report(CycleOutcome.EXIT_LEASE_LOST, state, None, LEASE_LOST_REASON)
    except Exception as exc:
        state.consecutive_failures += 1
        return _report(CycleOutcome.CYCLE_FAILED, state, None,
                       f"cycle_error:{type(exc).__name__}")


def next_wait(interval: float, consecutive_failures: int) -> float:
    """Seconds to sleep before the next cycle: exponential backoff, capped, never below ``interval``."""
    if interval < 30:
        raise ValueError("interval must be at least 30 seconds")
    if consecutive_failures <= 0:
        return float(interval)
    backoff = interval * 2 ** min(consecutive_failures - 1, 16)
    return float(max(interval, min(backoff, 300.0)))
