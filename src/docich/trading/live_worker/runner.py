"""The LIVE worker runner: startup order, the cycle loop and the status file (L4b-6a).

``run_worker`` is the process-level skeleton around ``cycle.run_cycle``.  Every dependency is
injected -- the reader factory, the public market-data factory, the git revision, the clock,
the stop flag and ``sleep`` -- so the whole runner is testable against the mock exchange with
no network, no threads and no signals.  This module never imports an exchange adapter and never
builds a sender: ``OrderExecutor(..., sender=None, ...)`` is the production wiring here.

Exit codes (module constants)::

    EXIT_OK = 0           stop requested, or no active policy set / opening balance to record
    EXIT_CONFIG = 2       layout/ledger/reader-config/revision/policy failure
    EXIT_AUTH = 3         the exchange reader reports an authentication failure
    EXIT_CLOCK = 4        the exchange reader reports a clock-skew failure
    EXIT_LEASE_HELD = 5   another worker holds the writer lease (no status is written)
    EXIT_CODE_CHANGED = 6 the code revision changed under the running worker
    EXIT_LEASE_LOST = 7   the writer lease was lost during startup or a cycle (no status is written)
    EXIT_RETRYABLE = 8    startup could not complete for a transient or unclassified reason;
                          safe to restart (systemd does not restart 2, 3 or 5)

Startup order is fixed: ledger -> lease -> reader -> active policy set -> revision -> executor
-> opening-balance precheck -> ``reconcile.on_startup`` -> the cycle loop.  An INERT start
(no active policy set, or a non-zero opening balance without any ledger history) writes the
status, releases the lease and returns 0 without a safety row, an intent, an order or a startup
reconcile.  Every exit path releases the lease (best effort) and closes the ledger.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..live import act, reconcile, views
from ..live.cycle import CycleConfig, CycleDeps, CycleOutcome, CycleState, next_wait, run_cycle
from ..live.enable import live_enabled
from ..live.executor import OrderExecutor
from ..live.ingest import ExecutorConfig
from ..live.lease import (LeaseHeld, LeaseLost, acquire_writer_lease, release_writer_lease)
from ..live.ledger import LiveLedger, LiveLedgerError
from ..live.paths import LiveLayoutError, live_layout
from .status import write_status

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_CLOCK = 4
EXIT_LEASE_HELD = 5
EXIT_CODE_CHANGED = 6
EXIT_LEASE_LOST = 7
EXIT_RETRYABLE = 8

SLEEP_SLICE_S = 1.0
READER_KINDS = frozenset({"auth", "clock", "config"})
NO_POLICY_SET = "no_active_policy_set"
OPENING_BALANCE_MISSING = "opening_balance_missing:"
BALANCE_UNAVAILABLE_AT_START = "balance_unavailable_at_start"


def _safe_now(clock, default: float = 0.0) -> float:
    try:
        return float(clock())
    except Exception:
        return float(default)


def _status(state_dir, clock, fields: dict) -> None:
    """Write one status; a clock failure or a status failure never escapes."""
    try:
        now = float(clock())
    except Exception:
        now = 0.0
    try:
        write_status(state_dir, fields, now=now)
    except Exception:
        pass


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _fields(*, phase: str, started_at: float, exit_code: int | None = None,
            report=None, state: CycleState | None = None, rev: str | None = None,
            digest: str | None = None, reason: str | None = None,
            inert_reason: str | None = None, cycle: int | None = None,
            mode: str | None = None, outcome: str | None = None) -> dict:
    """Build one status field dict; only allow-listed keys are produced."""
    fields: dict = {"phase": phase, "pid": os.getpid(), "started_at": _as_int(started_at)}
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
    if isinstance(rev, str):
        fields["code_revision"] = rev[:12]
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


def _opening_balance_missing(balance, currencies, ledger) -> tuple[str, ...]:
    """Watched currencies with a non-zero total and no ledger history of any kind."""
    seen = views.currencies_with_history(ledger)
    missing: list[str] = []
    for currency in currencies:
        asset = balance.assets.get(currency)
        if asset is None:
            continue
        if asset.free + asset.locked == 0:
            continue
        if str(currency).upper() not in seen:
            missing.append(currency)
    return tuple(sorted(missing))


def run_worker(*, state_dir, open_reader, make_market_data, revision, clock, stop_requested,
               sleep, interval_s=60.0, config=CycleConfig(),
               revision_check_every_s=60.0) -> int:
    """Run the LIVE worker until a stop, a terminal outcome or a configuration failure."""
    started_at = _safe_now(clock)
    ledger = None
    lease = None
    state = CycleState()
    ps = None
    rev = None

    try:
        # 1. layout + ledger -------------------------------------------------------------
        try:
            layout = live_layout(state_dir)
            ledger = LiveLedger.open(layout, paper_dir=Path(state_dir) / "trading",
                                     now=started_at)
        except (LiveLayoutError, LiveLedgerError, OSError) as exc:
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
                reason=f"ledger_open_failed:{type(exc).__name__}"))
            return EXIT_CONFIG

        # 2. the writer lease: another worker owns the file -> no status at all -----------
        try:
            lease = acquire_writer_lease(ledger, layout, now=_safe_now(clock))
        except LeaseHeld:
            return EXIT_LEASE_HELD

        # 2b. a previous run's "exited" status must not look current any longer.
        _status(state_dir, clock, _fields(phase="starting", started_at=started_at))

        # 3. the exchange reader ----------------------------------------------------------
        try:
            reader = open_reader(state_dir)
        except Exception as exc:
            kind = _reader_kind(exc)
            code = (EXIT_CONFIG if kind == "config" else
                    EXIT_AUTH if kind == "auth" else
                    EXIT_CLOCK if kind == "clock" else EXIT_RETRYABLE)
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=code,
                reason=kind or f"reader_open_failed:{type(exc).__name__}"))
            return code

        # 4. the active policy set --------------------------------------------------------
        digest = ledger.active_policy_set_digest()
        if digest is None:
            _status(state_dir, clock, _fields(
                phase="inert", started_at=started_at, exit_code=EXIT_OK,
                inert_reason=NO_POLICY_SET))
            return EXIT_OK
        try:
            ps = ledger.load_policy_set(digest)
        except Exception as exc:
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
                reason=f"policy_load_failed:{type(exc).__name__}", digest=digest))
            return EXIT_CONFIG

        # 5. the code revision the gate will be bound to ----------------------------------
        try:
            rev = revision()
        except Exception as exc:
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
                reason=f"revision_error:{type(exc).__name__}", digest=ps.digest))
            return EXIT_CONFIG

        # 6. the executor: production wiring is a read-only sender ------------------------
        gate = lambda: live_enabled(state_dir, policy_set_digest=ps.digest,
                                    code_revision=rev, now=clock())
        executor = OrderExecutor(ledger, lease, reader=reader, sender=None,
                                 enable_check=gate, clock=clock,
                                 symbols=tuple(sorted(ps.policies)),
                                 config=ExecutorConfig())

        # 7. opening-balance precheck (read-only, before on_startup) ----------------------
        try:
            balance = reader.fetch_balance()
            currencies = executor.watched_currencies()
        except Exception as exc:
            kind = _reader_kind(exc)
            if kind is None:
                try:
                    kind = getattr(reader.health(), "last_error_kind", None)
                except Exception:
                    kind = None
            code = (EXIT_AUTH if kind == "auth" else
                    EXIT_CLOCK if kind == "clock" else EXIT_RETRYABLE)
            reason = kind if kind in ("auth", "clock") else BALANCE_UNAVAILABLE_AT_START
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=code, reason=reason,
                rev=rev, digest=ps.digest))
            return code

        missing = _opening_balance_missing(balance, currencies, ledger)
        if missing:
            _status(state_dir, clock, _fields(
                phase="inert", started_at=started_at, exit_code=EXIT_OK,
                inert_reason=OPENING_BALANCE_MISSING + ",".join(missing),
                rev=rev, digest=ps.digest))
            return EXIT_OK

        # 8. restart reconciliation -------------------------------------------------------
        try:
            reconcile.on_startup(executor)
        except LeaseLost:
            # The lease may already be gone: write no status at all (the new owner owns the
            # file); release is best effort in the finally.
            return EXIT_LEASE_LOST
        except Exception as exc:
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=EXIT_RETRYABLE,
                reason=f"on_startup_failed:{type(exc).__name__}", rev=rev,
                digest=ps.digest))
            return EXIT_RETRYABLE

        # 9. the cycle loop ---------------------------------------------------------------
        try:
            market_data = make_market_data(reader)
        except Exception as exc:
            _status(state_dir, clock, _fields(
                phase="exited", started_at=started_at, exit_code=EXIT_RETRYABLE,
                reason=f"market_data_failed:{type(exc).__name__}", rev=rev, digest=ps.digest))
            return EXIT_RETRYABLE
        deps = CycleDeps(ledger=ledger, lease=lease, executor=executor, reader=reader,
                         market_data=market_data, gate=gate, policy_set=ps,
                         symbols=executor.symbols, clock=clock,
                         stop_requested=stop_requested, decide=act.decide_and_act,
                         config=config)
        next_revision_at = _safe_now(clock) + float(revision_check_every_s)
        last_report = None
        while True:
            now = _safe_now(clock)
            if now >= next_revision_at:
                next_revision_at = now + float(revision_check_every_s)
                try:
                    current_rev = revision()
                except Exception as exc:
                    _status(state_dir, clock, _fields(
                        phase="exited", started_at=started_at,
                        exit_code=EXIT_CODE_CHANGED,
                        reason=f"revision_unreadable:{type(exc).__name__}",
                        cycle=state.cycle_number, state=state, rev=rev, digest=ps.digest))
                    return EXIT_CODE_CHANGED
                if current_rev != rev:
                    _status(state_dir, clock, _fields(
                        phase="exited", started_at=started_at,
                        exit_code=EXIT_CODE_CHANGED, reason="code_revision_changed",
                        cycle=state.cycle_number, state=state, rev=rev, digest=ps.digest))
                    return EXIT_CODE_CHANGED

            report = run_cycle(deps, state)
            last_report = report
            if report.outcome is CycleOutcome.EXIT_LEASE_LOST:
                # The lease may already be gone: write nothing at all (neither "running" nor
                # "exited"): the status file now belongs to the worker that took over.
                # Release is best effort in the finally.
                return EXIT_LEASE_LOST
            _status(state_dir, clock, _fields(
                phase="running", started_at=started_at, report=report, state=state,
                rev=rev, digest=ps.digest))

            if report.outcome is CycleOutcome.STOP_REQUESTED:
                _status(state_dir, clock, _fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_OK,
                    report=report, state=state, rev=rev, digest=ps.digest))
                return EXIT_OK
            if report.outcome is CycleOutcome.EXIT_AUTH:
                _status(state_dir, clock, _fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_AUTH,
                    report=report, state=state, rev=rev, digest=ps.digest))
                return EXIT_AUTH
            if report.outcome is CycleOutcome.EXIT_CLOCK:
                _status(state_dir, clock, _fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_CLOCK,
                    report=report, state=state, rev=rev, digest=ps.digest))
                return EXIT_CLOCK

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
                _status(state_dir, clock, _fields(
                    phase="exited", started_at=started_at, exit_code=EXIT_OK,
                    report=last_report, state=state, rev=rev, digest=ps.digest,
                    reason="stop_requested", outcome="stop_requested"))
                return EXIT_OK

    except Exception as exc:  # contract: no exception escapes run_worker
        _status(state_dir, clock, _fields(
            phase="exited", started_at=started_at, exit_code=EXIT_CONFIG,
            reason=f"runner_error:{type(exc).__name__}", rev=rev,
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
