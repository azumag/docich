"""The LIVE worker runner: startup order, the cycle loop and the status file (L4b-6a).

``run_worker`` is the process-level skeleton around ``cycle.run_cycle``.  Every dependency is
injected -- the reader factory, the public market-data factory, the clock, the stop flag and
``sleep`` -- so the whole runner is testable against the mock exchange with no network, no
threads and no signals.  This module never imports an exchange adapter and never builds a
sender: ``OrderExecutor(..., sender=None, ...)`` is the production wiring here.  The owner's
on/off switch is read from ``live_control.json`` on every cycle.

Exit codes (module constants)::

    EXIT_OK = 0           stop requested, or no active policy set
    EXIT_CONFIG = 2       layout/ledger/reader-config/policy failure
    EXIT_AUTH = 3         the exchange reader reports an authentication failure
    EXIT_CLOCK = 4        the exchange reader reports a clock-skew failure
    EXIT_LEASE_HELD = 5   another worker holds the writer lease (no status is written)
    6                     unused (was: code revision changed)
    EXIT_LEASE_LOST = 7   the writer lease was lost during startup or a cycle (no status is written)
    EXIT_RETRYABLE = 8    startup could not complete for a transient or unclassified reason,
                          or public market data stayed unusable for EVERY symbol longer than
                          ``CycleConfig.market_gap_exit_s`` (Issue #4586); safe to restart
                          (systemd does not restart 2, 3 or 5)

Startup order is fixed: ledger -> lease -> reader -> active policy set -> executor ->
``reconcile.on_startup`` -> ``opening.record_auto_opening`` -> the cycle loop.  An INERT
start (no active policy set) writes the status, releases the lease and returns 0 without a
safety row, an intent or an order.  Opening balances are recorded by the worker itself (one
``auto_opening:<CCY>`` cashflow with source ``exchange_history`` per watched currency with no
history and a positive total); a balance condition never makes the worker inert and never
stops it.  Every exit path releases the lease (best effort) and closes the ledger.
"""
from __future__ import annotations

import math
import os
from decimal import Decimal
from functools import partial
from pathlib import Path

from ..live import act, reconcile, views
from ..live.control import read_control
from ..live.cycle import CycleConfig, CycleDeps, CycleOutcome, CycleState, next_wait, run_cycle
from ..live.executor import OrderExecutor
from ..live.ingest import ExecutorConfig
from ..live.lease import (LeaseHeld, LeaseLost, acquire_writer_lease, release_writer_lease)
from ..live.ledger import LiveLedger, LiveLedgerError
from ..live.opening import record_auto_opening
from ..live.paths import LiveLayoutError, live_layout
from .status import write_status

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_CLOCK = 4
EXIT_LEASE_HELD = 5
EXIT_LEASE_LOST = 7
EXIT_RETRYABLE = 8

SLEEP_SLICE_S = 1.0
READER_KINDS = frozenset({"auth", "clock", "config"})
NO_POLICY_SET = "no_active_policy_set"
ZERO = Decimal(0)


def _safe_now(clock, default: float = 0.0) -> float:
    try:
        return float(clock())
    except Exception:
        return float(default)


def _gap_seconds(fields: dict, now: float) -> None:
    """Fill the ``*_seconds`` companions of the ``*_since`` epochs the report carried.

    A status helper must never raise: a ``*_since`` value that cannot be subtracted
    leaves the companion ``None`` instead of breaking the status write.
    """
    for since_key, seconds_key in (("market_gap_since", "market_gap_seconds"),
                                   ("market_all_stale_since", "market_all_stale_seconds")):
        if since_key not in fields:
            continue
        since = fields[since_key]
        seconds: int | None = None
        if since is not None:
            try:
                seconds = max(0, _as_int(now - since))
            except Exception:
                seconds = None
        fields[seconds_key] = seconds


def _status(state_dir, clock, fields: dict) -> None:
    """Write one status; a clock failure or a status failure never escapes."""
    try:
        now = float(clock())
    except Exception:
        now = 0.0
    _gap_seconds(fields, now)
    try:
        write_status(state_dir, fields, now=now)
    except Exception:
        pass


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _interval_ms(interval_s) -> int | None:
    """The actual interval in whole milliseconds (ceil), or None when it cannot be read.

    Millisecond integers keep a fractional interval exact without allowing a float into the
    status allow-list.  The writer drops an out-of-range value; the UI then falls back to
    the legacy 60 s / 180 s bound.
    """
    try:
        value = float(interval_s)
        if not math.isfinite(value):
            return None
        return int(math.ceil(value * 1000.0))
    except Exception:
        return None


def _fields(*, phase: str, started_at: float, interval_ms: int | None = None,
            exit_code: int | None = None, report=None, state: CycleState | None = None,
            digest: str | None = None, reason: str | None = None,
            inert_reason: str | None = None, cycle: int | None = None,
            mode: str | None = None, outcome: str | None = None) -> dict:
    """Build one status field dict; only allow-listed keys are produced."""
    fields: dict = {"phase": phase, "pid": os.getpid(), "started_at": _as_int(started_at)}
    if interval_ms is not None:
        fields["interval_ms"] = interval_ms
    if exit_code is not None:
        fields["exit_code"] = _as_int(exit_code)
    if inert_reason is not None:
        fields["inert_reason"] = inert_reason
    if report is not None:
        fields.update({
            "cycle": _as_int(report.cycle_number),
            "mode": report.mode,
            "outcome": report.outcome.value,
            "reason": report.reason,
            "blocks": list(report.block_reasons),
            "fresh_symbols": list(report.fresh_symbols),
            "stale_symbols": list(report.stale_symbols),
            "control_state": report.control_state,
            "control_mode": report.control_mode,
            "balance_warnings": max(0, _as_int(report.balance_warnings)),
        })
        gap = getattr(report, "market_gap", None)
        if gap is not None:
            fields.update({
                "market_gap_scope": gap.scope,
                "market_stale_count": _as_int(gap.stale_count),
                "market_gap_since": (None if gap.gap_since is None
                                     else _as_int(gap.gap_since)),
                "market_all_stale_since": (None if gap.all_stale_since is None
                                           else _as_int(gap.all_stale_since)),
                "market_all_stale_cycles": _as_int(gap.all_stale_cycles),
            })
        if reason is not None:
            fields["reason"] = reason
    else:
        if cycle is not None:
            fields["cycle"] = _as_int(cycle)
        if mode is not None:
            fields["mode"] = mode
        if reason is not None:
            fields["reason"] = reason
    if outcome is not None:
        fields["outcome"] = outcome
    if state is not None:
        fields.update({
            "consecutive_failures": _as_int(state.consecutive_failures),
            "record_failures": _as_int(state.record_failures),
            "ttl_deferred": _as_int(state.ttl_deferred),
            "ttl_failed": _as_int(state.ttl_failed),
        })
    if isinstance(digest, str):
        fields["policy_set_digest"] = digest[:12]
    return fields


def _reader_kind(exc: BaseException) -> str | None:
    """The known reader-failure kind (``config``/``auth``/``clock``), else ``None``."""
    try:
        kind = getattr(exc, "kind", None)
        return kind if kind in READER_KINDS else None
    except Exception:
        return None


def _opening_pending(reader, ledger, currencies) -> bool:
    """True while a watched currency still has no history and a positive exchange total."""
    try:
        history = views.currencies_with_history(ledger)
        balance = reader.fetch_balance()
    except LeaseLost:
        raise
    except Exception:
        return True
    for currency in currencies:
        if str(currency).upper() in history:
            continue
        asset = balance.assets.get(currency)
        total = ZERO if asset is None else asset.total
        if total > 0:
            return True
    return False


def _opening_attempt(ledger, lease, executor, clock) -> bool:
    """One best-effort auto-opening pass.  True while another pass is still needed.

    ``LeaseLost`` propagates (the caller returns 7 without writing a status).  Every other
    failure is swallowed and leaves the flag set, so a balance can never stop the worker.
    """
    try:
        currencies = executor.watched_currencies()
        record_auto_opening(ledger, lease, executor.reader, currencies, now=clock())
    except LeaseLost:
        raise
    except Exception:
        return True
    try:
        return _opening_pending(executor.reader, ledger, currencies)
    except LeaseLost:
        raise
    except Exception:
        return True


def run_worker(*, state_dir, open_reader, make_market_data, clock, stop_requested,
               sleep, interval_s=60.0, config=CycleConfig()) -> int:
    """Run the LIVE worker until a stop, a terminal outcome or a configuration failure."""
    started_at = _safe_now(clock)
    ledger = None
    lease = None
    state = CycleState()
    ps = None

    # Record the actual cycle interval in every status so the WebUI can judge
    # freshness from 3 x interval (milliseconds keep fractional intervals exact).
    make_fields = partial(_fields, interval_ms=_interval_ms(interval_s))

    try:
        # 1. layout + ledger -------------------------------------------------------------
        try:
            layout = live_layout(state_dir)
            ledger = LiveLedger.open(layout, paper_dir=Path(state_dir) / "trading",
                                     now=started_at)
        except (LiveLayoutError, LiveLedgerError, OSError) as exc:
            _status(state_dir, clock, make_fields(
                phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
                reason=f"ledger_open_failed:{type(exc).__name__}"))
            return EXIT_CONFIG

        # 2. the writer lease: another worker owns the file -> no status at all -----------
        try:
            lease = acquire_writer_lease(ledger, layout, now=_safe_now(clock))
        except LeaseHeld:
            return EXIT_LEASE_HELD

        # 2b. a previous run's "exited" status must not look current any longer.
        _status(state_dir, clock, make_fields(phase="starting", started_at=started_at))

        # 3. the exchange reader ----------------------------------------------------------
        try:
            reader = open_reader(state_dir)
        except Exception as exc:
            kind = _reader_kind(exc)
            code = (EXIT_CONFIG if kind == "config" else
                    EXIT_AUTH if kind == "auth" else
                    EXIT_CLOCK if kind == "clock" else EXIT_RETRYABLE)
            _status(state_dir, clock, make_fields(
                phase="exited", started_at=started_at, exit_code=code,
                reason=kind or f"reader_open_failed:{type(exc).__name__}"))
            return code

        # 4. the active policy set --------------------------------------------------------
        digest = ledger.active_policy_set_digest()
        if digest is None:
            _status(state_dir, clock, make_fields(
                phase="inert", started_at=started_at, exit_code=EXIT_OK,
                inert_reason=NO_POLICY_SET))
            return EXIT_OK
        try:
            ps = ledger.load_policy_set(digest)
        except Exception as exc:
            _status(state_dir, clock, make_fields(
                phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
                reason=f"policy_load_failed:{type(exc).__name__}", digest=digest))
            return EXIT_CONFIG

        # 5. the executor: production wiring is a read-only sender ------------------------
        gate = lambda: read_control(state_dir, now=clock())
        executor = OrderExecutor(ledger, lease, reader=reader, sender=None,
                                 enable_check=gate, clock=clock,
                                 symbols=tuple(sorted(ps.policies)),
                                 config=ExecutorConfig())

        # 6. restart reconciliation: unrecorded fills and sent intents are ingested FIRST ---
        try:
            reconcile.on_startup(executor)
        except LeaseLost:
            # The lease may already be gone: write no status at all (the new owner owns the
            # file); release is best effort in the finally.
            return EXIT_LEASE_LOST
        except Exception as exc:
            _status(state_dir, clock, make_fields(
                phase="exited", started_at=started_at, exit_code=EXIT_RETRYABLE,
                reason=f"on_startup_failed:{type(exc).__name__}",
                digest=ps.digest))
            return EXIT_RETRYABLE

        # 7. opening balances: the worker records them itself, best effort.  A reader or
        # write failure only means the cycle loop retries; it never makes the worker inert.
        try:
            opening_pending = _opening_attempt(ledger, lease, executor, clock)
        except LeaseLost:
            # The lease may already be gone: write no status at all.
            return EXIT_LEASE_LOST

        # 8. the cycle loop ---------------------------------------------------------------
        try:
            market_data = make_market_data(reader)
        except Exception as exc:
            _status(state_dir, clock, make_fields(
                phase="exited", started_at=started_at, exit_code=EXIT_RETRYABLE,
                reason=f"market_data_failed:{type(exc).__name__}", digest=ps.digest))
            return EXIT_RETRYABLE
        deps = CycleDeps(ledger=ledger, lease=lease, executor=executor, reader=reader,
                         market_data=market_data, gate=gate, policy_set=ps,
                         symbols=executor.symbols, clock=clock,
                         stop_requested=stop_requested, decide=act.decide_and_act,
                         config=config)
        last_report = None
        while True:
            # At most one retry per cycle while some watched currency still needs an
            # opening balance.  It runs before the cycle so the cycle sees the cashflow.
            if opening_pending:
                try:
                    opening_pending = _opening_attempt(ledger, lease, executor, clock)
                except LeaseLost:
                    # The lease may already be gone: write nothing at all.
                    return EXIT_LEASE_LOST
            report = run_cycle(deps, state)
            last_report = report
            if report.outcome is CycleOutcome.EXIT_LEASE_LOST:
                # The lease may already be gone: write nothing at all (neither "running" nor
                # "exited"): the status file now belongs to the worker that took over.
                # Release is best effort in the finally.
                return EXIT_LEASE_LOST
            _status(state_dir, clock, make_fields(
                phase="running", started_at=started_at, report=report, state=state,
                digest=ps.digest))

            if report.outcome is CycleOutcome.STOP_REQUESTED:
                _status(state_dir, clock, make_fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_OK,
                    report=report, state=state, digest=ps.digest))
                return EXIT_OK
            if report.outcome is CycleOutcome.EXIT_AUTH:
                _status(state_dir, clock, make_fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_AUTH,
                    report=report, state=state, digest=ps.digest))
                return EXIT_AUTH
            if report.outcome is CycleOutcome.EXIT_CLOCK:
                _status(state_dir, clock, make_fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_CLOCK,
                    report=report, state=state, digest=ps.digest))
                return EXIT_CLOCK
            if report.outcome is CycleOutcome.EXIT_MARKET_DATA:
                # Issue #4586: the whole public book stayed unusable past the configured
                # threshold.  The status still carries the gap fields so the exit shows
                # the scope and duration, then the process asks for a retryable restart.
                _status(state_dir, clock, _fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_RETRYABLE,
                    report=report, state=state, digest=ps.digest))
                return EXIT_RETRYABLE

            # CONTINUE / CYCLE_FAILED: wait in at most SLEEP_SLICE_S slices.
            wait = next_wait(float(interval_s), state.consecutive_failures)
            slept = 0.0
            stopped = False
            while slept < wait:
                if stop_requested():
                    stopped = True
                    break
                step = min(SLEEP_SLICE_S, wait - slept)
                if step <= 0:
                    break
                sleep(step)
                slept += step
            if stopped:
                _status(state_dir, clock, make_fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_OK,
                    report=last_report, state=state, digest=ps.digest,
                    reason="stop_requested", outcome="stop_requested"))
                return EXIT_OK

    except Exception as exc:  # contract: no exception escapes run_worker
        _status(state_dir, clock, make_fields(
            phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
            reason=f"runner_error:{type(exc).__name__}",
            digest=None if ps is None else ps.digest))
        return EXIT_CONFIG
    finally:
        if lease is not None:
            try:
                release_writer_lease(ledger, lease, now=_safe_now(clock))
            except Exception:
                pass
        if ledger is not None:
            try:
                ledger.close()
            except Exception:
                pass
