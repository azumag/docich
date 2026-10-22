"""News PAPER worker (N07): default-off, network-free risk loop.

Contract (issue #784, implemented under the 2026-10-22 delegated approval):

* **Default off.** A missing or disabled config creates no database, opens no
  socket, starts no child process and leaves every existing setting untouched.
* **No HTTP/LLM wait in the risk loop.** Market observations arrive through a
  bounded, deadline-aware, deduplicated and prioritised queue. In production
  the queue is filled by an ingestion thread around the N02 collector contract
  (:class:`ObservationSource`); the risk loop itself only drains the queue and
  processes durable work. Decision candidates arrive pre-composed through the
  N03 seam (:class:`CandidateSource`) and are never computed here.
* **Risk/valuation continue without news.** ``entries_disabled`` stops new
  news decisions and cancels risk-increasing pending buys, while valuation,
  protective (sell) exits, accounting and the heartbeat keep running.
* **Single writer.** A per-run worker lock plus the ledger's own exclusive
  lock exclude a second worker; a conflict fails closed.
* **PAPER only.** ``mode`` is fixed to ``paper`` and ``live_eligible`` is
  always false. Nothing here pushes, merges, deploys or touches VM units.

Unapproved operational numbers (cycle length, queue limits, risk caps, lot,
fee, latency) are *not* defaulted: an enabled config must supply every value
explicitly, otherwise the worker refuses to start (fail-closed, N00 v2 §5).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .execution import ClockQuality, ExecutionError, InstrumentConstraints, MarketObservation
from .ledger import LedgerError, NewsLedger
from .manager import DecisionRequest, ManagerPolicy, PaperManager, ReceiptView
from .risk import RiskPolicy

MODE = "paper"
LIVE_ELIGIBLE = False
WORKER_SCHEMA_VERSION = 1
HEALTH_FILE_NAME = "health.json"
WORKER_LOCK_NAME = "worker.lock"

#: Health ``status`` vocabulary (fixed; see docs/operations/news-paper-worker.md).
STATUS_IDLE = "idle"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_STOPPING = "stopping"
STATUS_FAILED = "failed"

#: Worker-local failures that end the loop (D10-compatible codes).
FATAL_CODES = frozenset(
    {"STORE_UNAVAILABLE", "SCHEMA_VERSION_UNSUPPORTED", "STATE_PATH_FORBIDDEN", "CLOCK_UNTRUSTED"}
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class WorkerConfigError(ValueError):
    """Invalid or incomplete configuration; ``code`` is a fixed error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or not _ID_RE.match(value):  # type: ignore[union-attr]
        raise WorkerConfigError("TYPE_INVALID")
    return value


def _dec(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise WorkerConfigError("TYPE_INVALID")
    try:
        parsed = Decimal(value)
    except Exception as exc:  # noqa: BLE001 - any parse failure is TYPE_INVALID
        raise WorkerConfigError("TYPE_INVALID") from exc
    if not parsed.is_finite():
        raise WorkerConfigError("NONFINITE_VALUE")
    if parsed < 0 or (positive and parsed <= 0):
        raise WorkerConfigError("VALUE_OUT_OF_RANGE")
    return parsed


def _int(value: object, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkerConfigError("TYPE_INVALID")
    if value < minimum:
        raise WorkerConfigError("VALUE_OUT_OF_RANGE")
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise WorkerConfigError("TYPE_INVALID")
    return value


def _text(value: object, name: str, *, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise WorkerConfigError("TYPE_INVALID")
    return value


def _table(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise WorkerConfigError("TYPE_INVALID")
    return value


def _require(block: Mapping[str, object], key: str) -> object:
    if key not in block:
        # Missing operational values are never defaulted (N00 v2 §5).
        raise WorkerConfigError("VALUE_OUT_OF_RANGE")
    return block[key]


def _reject_unknown(block: Mapping[str, object], allowed: frozenset[str], where: str) -> None:
    extra = set(block) - allowed
    if extra:
        raise WorkerConfigError("UNKNOWN_KEY")


@dataclass(frozen=True)
class WorkerConfig:
    """Validated worker configuration. Every value is explicit."""

    state_dir: Path
    run_id: str
    scenario_id: str
    account_id: str
    cycle_seconds: int
    queue_size: int
    queue_deadline_seconds: int
    initial_cash_jpy: Decimal
    entries_disabled: bool
    execution_delay_ms: int
    worker_lock_timeout_seconds: int
    clock_quality: ClockQuality
    risk_policy: RiskPolicy
    constraints: Mapping[str, InstrumentConstraints]
    worker_id: str = "n07"

    @property
    def ledger_path(self) -> Path:
        return (
            self.state_dir
            / "news-paper"
            / "runs"
            / self.run_id
            / self.scenario_id
            / "paper.sqlite3"
        )


def _load_instruments(data: object) -> dict[str, InstrumentConstraints]:
    block = _table(data, "instruments")
    instruments: dict[str, InstrumentConstraints] = {}
    for asset, raw in block.items():
        if not isinstance(asset, str) or not asset:
            raise WorkerConfigError("TYPE_INVALID")
        table = _table(raw, f"instruments.{asset}")
        _reject_unknown(
            table,
            frozenset(
                {
                    "version", "market", "quote_currency", "lot", "min_quantity",
                    "min_notional", "price_tick", "fee_bps", "fee_min", "slippage_bps",
                    "spread_limit_bps", "participation_rate", "max_quote_age_seconds",
                    "volatility_limit_bps", "max_order_notional",
                }
            ),
            "instruments",
        )
        try:
            instruments[asset] = InstrumentConstraints(
                version=_text(_require(table, "version"), "version", limit=64),
                market=_text(_require(table, "market"), "market", limit=16),
                quote_currency=_text(_require(table, "quote_currency"), "quote_currency", limit=16),
                lot=_dec(_require(table, "lot"), "lot", positive=True),
                min_quantity=_dec(_require(table, "min_quantity"), "min_quantity", positive=True),
                min_notional=_dec(_require(table, "min_notional"), "min_notional"),
                price_tick=_dec(_require(table, "price_tick"), "price_tick", positive=True),
                fee_bps=_dec(_require(table, "fee_bps"), "fee_bps"),
                fee_min=_dec(_require(table, "fee_min"), "fee_min"),
                slippage_bps=_dec(_require(table, "slippage_bps"), "slippage_bps"),
                spread_limit_bps=_dec(_require(table, "spread_limit_bps"), "spread_limit_bps"),
                participation_rate=_dec(
                    _require(table, "participation_rate"), "participation_rate", positive=True
                ),
                max_quote_age_seconds=_dec(
                    _require(table, "max_quote_age_seconds"), "max_quote_age_seconds"
                ),
                volatility_limit_bps=(
                    _dec(table["volatility_limit_bps"], "volatility_limit_bps")
                    if table.get("volatility_limit_bps") is not None
                    else None
                ),
                max_order_notional=(
                    _dec(table["max_order_notional"], "max_order_notional")
                    if table.get("max_order_notional") is not None
                    else None
                ),
            )
        except WorkerConfigError:
            raise
        except ValueError as exc:  # execution.ExecutionError carries fixed codes
            code = getattr(exc, "code", None)
            if isinstance(code, str):
                raise WorkerConfigError(code) from exc
            raise WorkerConfigError("TYPE_INVALID") from exc
    return instruments


def load_config(path: str | Path) -> WorkerConfig | None:
    """Parse ``config/news-paper.toml``; ``None`` means disabled.

    ``enabled`` absent or false is the only disabled form. When enabled, every
    required key must be present and valid; unknown keys are rejected.
    """
    import tomllib

    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise WorkerConfigError("VALUE_OUT_OF_RANGE") from exc
    allowed_sections = frozenset({"worker", "risk", "instruments", "dashboard"})
    if set(raw) - allowed_sections:
        raise WorkerConfigError("UNKNOWN_KEY")
    worker = raw.get("worker")
    if worker is None:
        return None
    block = _table(worker, "worker")
    _reject_unknown(
        block,
        frozenset(
            {
                "enabled", "mode", "run_id", "scenario_id", "account_id", "state_dir",
                "cycle_seconds", "queue_size", "queue_deadline_seconds",
                "initial_cash_jpy", "entries_disabled", "execution_delay_ms",
                "worker_lock_timeout_seconds", "worker_id", "clock_quality",
            }
        ),
        "worker",
    )
    enabled = block.get("enabled")
    if enabled is None or enabled is False:
        return None
    if enabled is not True:
        raise WorkerConfigError("TYPE_INVALID")
    if _text(_require(block, "mode"), "mode", limit=16) != MODE:
        # LIVE_ELIGIBILITY_FORBIDDEN: no other mode may ever be activated.
        raise WorkerConfigError("LIVE_ELIGIBILITY_FORBIDDEN")
    state_dir = Path(_text(_require(block, "state_dir"), "state_dir", limit=4096))
    if not state_dir.is_absolute() or ".." in state_dir.parts:
        raise WorkerConfigError("STATE_PATH_FORBIDDEN")
    clock = _table(_require(block, "clock_quality"), "clock_quality")
    _reject_unknown(clock, frozenset({"synced", "source", "max_error_ms"}), "clock_quality")
    try:
        quality = ClockQuality(
            synced=_bool(_require(clock, "synced"), "synced"),
            source=_text(_require(clock, "source"), "source", limit=16),
            max_error_ms=_int(_require(clock, "max_error_ms"), "max_error_ms", minimum=0),
        )
    except WorkerConfigError:
        raise
    except ValueError as exc:
        raise WorkerConfigError(getattr(exc, "code", "VALUE_OUT_OF_RANGE")) from exc
    if not quality.trusted:
        # CLOCK_UNTRUSTED: new decisions must not start on an unproven clock.
        raise WorkerConfigError("CLOCK_UNTRUSTED")
    risk = _table(_require(raw, "risk"), "risk")
    _reject_unknown(
        risk,
        frozenset(
            {
                "version", "max_positions", "max_gross_exposure",
                "per_asset_exposure_cap", "daily_loss_limit", "drawdown_limit",
            }
        ),
        "risk",
    )
    try:
        policy = RiskPolicy(
            version=_text(_require(risk, "version"), "version", limit=64),
            max_positions=_int(_require(risk, "max_positions"), "max_positions", minimum=1),
            max_gross_exposure=_dec(_require(risk, "max_gross_exposure"), "max_gross_exposure"),
            per_asset_exposure_cap=_dec(
                _require(risk, "per_asset_exposure_cap"), "per_asset_exposure_cap"
            ),
            daily_loss_limit=_dec(_require(risk, "daily_loss_limit"), "daily_loss_limit"),
            drawdown_limit=_dec(_require(risk, "drawdown_limit"), "drawdown_limit"),
        )
    except WorkerConfigError:
        raise
    except ValueError as exc:
        raise WorkerConfigError(getattr(exc, "code", "VALUE_OUT_OF_RANGE")) from exc
    instruments = _load_instruments(raw.get("instruments", {}))
    cash = _dec(_require(block, "initial_cash_jpy"), "initial_cash_jpy", positive=True)
    if cash.adjusted() >= 18:
        # The ledger stores decimals as bounded text; fail closed up front.
        raise WorkerConfigError("VALUE_OUT_OF_RANGE")
    return WorkerConfig(
        state_dir=state_dir,
        run_id=_id(_text(_require(block, "run_id"), "run_id"), "run_id"),
        scenario_id=_id(_text(_require(block, "scenario_id"), "scenario_id"), "scenario_id"),
        account_id=_id(_text(_require(block, "account_id"), "account_id"), "account_id"),
        cycle_seconds=_int(_require(block, "cycle_seconds"), "cycle_seconds", minimum=1),
        queue_size=_int(_require(block, "queue_size"), "queue_size", minimum=1),
        queue_deadline_seconds=_int(
            _require(block, "queue_deadline_seconds"), "queue_deadline_seconds", minimum=1
        ),
        initial_cash_jpy=cash,
        entries_disabled=_bool(_require(block, "entries_disabled"), "entries_disabled"),
        execution_delay_ms=_int(_require(block, "execution_delay_ms"), "execution_delay_ms"),
        worker_lock_timeout_seconds=_int(
            _require(block, "worker_lock_timeout_seconds"), "worker_lock_timeout_seconds", minimum=1
        ),
        clock_quality=quality,
        risk_policy=policy,
        constraints=instruments,
        worker_id=_id(_text(block.get("worker_id", "n07"), "worker_id", limit=64), "worker_id"),
    )


# ---------------------------------------------------------------------------
# seams (N02 / N03) — unconnected by default
# ---------------------------------------------------------------------------


class ObservationSource(Protocol):
    """N02 collector seam. Production wiring arrives with the N02 unit."""

    def poll(self) -> Sequence[MarketObservation]:
        """Return newly available observations; must be bounded in time."""


class CandidateSource(Protocol):
    """N03 composition seam. Candidates are pre-composed decision requests."""

    def candidates(self, now: datetime) -> Sequence[DecisionRequest]:
        """Return decision candidates for this cycle (any order)."""


# ---------------------------------------------------------------------------
# bounded, deadline-aware, deduplicated, prioritised queue
# ---------------------------------------------------------------------------


@dataclass
class _QueueEntry:
    observation: MarketObservation
    priority: int
    deadline: datetime


class ObservationQueue:
    """In-process observation queue with fixed capacity and expiry rule.

    * duplicates (same quote identity) are dropped and counted;
    * items past their deadline are discarded on drain (late responses never
      reach the risk loop);
    * the highest priority drains first, FIFO within one priority.
    """

    def __init__(self, max_size: int, *, dedupe_limit: int | None = None) -> None:
        if max_size < 1:
            raise WorkerConfigError("VALUE_OUT_OF_RANGE")
        self._max_size = max_size
        self._items: list[_QueueEntry] = []
        self._seen: dict[tuple[str, str, datetime, str], None] = {}
        self._dedupe_limit = dedupe_limit if dedupe_limit is not None else max_size * 4
        self.dropped_duplicate = 0
        self.dropped_expired = 0
        self.dropped_full = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def dropped(self) -> int:
        return self.dropped_duplicate + self.dropped_expired + self.dropped_full

    def push(
        self,
        observation: MarketObservation,
        *,
        priority: int = 0,
        deadline: datetime,
    ) -> bool:
        key = observation.identity
        if key in self._seen:
            self.dropped_duplicate += 1
            return False
        if len(self._items) >= self._max_size:
            self.dropped_full += 1
            return False
        self._items.append(
            _QueueEntry(observation=observation, priority=int(priority), deadline=deadline)
        )
        self._seen[key] = None
        if len(self._seen) > self._dedupe_limit:
            oldest = next(iter(self._seen))
            del self._seen[oldest]
        return True

    def drain(self, now: datetime) -> list[MarketObservation]:
        items, self._items = self._items, []
        ordered = sorted(items, key=lambda entry: (-entry.priority, entry.deadline))
        drained: list[MarketObservation] = []
        for entry in ordered:
            if entry.deadline <= now:
                self.dropped_expired += 1
                continue
            drained.append(entry.observation)
        return drained


# ---------------------------------------------------------------------------
# single-instance lock
# ---------------------------------------------------------------------------


class WorkerLock:
    """Exclusive ``<state_dir>/news-paper/worker.lock`` (flock, non-blocking)."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "news-paper" / WORKER_LOCK_NAME
        self._fd: int | None = None

    def acquire(self, timeout_seconds: float) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    # STORE_UNAVAILABLE: a second writer must never start.
                    raise LedgerError("STORE_UNAVAILABLE") from exc
                time.sleep(0.05)
        self._fd = fd
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# heartbeat (atomic, separate from freshness)
# ---------------------------------------------------------------------------


def _ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now(clock: Callable[[], datetime]) -> datetime:
    current = clock()
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise LedgerError("CLOCK_UNTRUSTED")
    return current


def _sqlite_ro_uri(path: Path) -> str:
    return "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"


def write_health(state_dir: Path, payload: Mapping[str, object]) -> Path:
    """Write ``health.json`` atomically (owner-only, temp file + rename)."""

    directory = state_dir / "news-paper"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    target = directory / HEALTH_FILE_NAME
    temporary = directory / f".{HEALTH_FILE_NAME}.tmp"
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, target)
    return target


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------


class NewsPaperWorker:
    """One default-off PAPER worker. Call :meth:`start`/``run`` or ``run_once``."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        clock: Callable[[], datetime] | None = None,
        source: ObservationSource | None = None,
        candidates: CandidateSource | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._source = source
        self._candidates = candidates
        self._monotonic = monotonic
        self._queue = ObservationQueue(config.queue_size)
        self._lock = WorkerLock(config.state_dir)
        self._ledger: NewsLedger | None = None
        self._manager: PaperManager | None = None
        self._stop = threading.Event()
        self._ingest_thread: threading.Thread | None = None
        self._seq = 0
        self._started_at: datetime | None = None
        self._instance_token = uuid.uuid4().hex[:12]
        self._last_prices: dict[str, Decimal] = {}
        self._last_quote_at: dict[str, datetime] = {}
        self._last_market_available_at: datetime | None = None
        self._last_fill_at: datetime | None = None
        self._last_error_code: str | None = None
        self._status = STATUS_IDLE
        self._failed = False

    @property
    def phase(self) -> str:
        """Last health ``status`` written by this instance (for CLI exit code)."""
        return self._status

    @property
    def failed(self) -> bool:
        """True when a fatal store/clock error ended the loop."""
        return self._failed

    def request_stop(self) -> None:
        self._stop.set()

    def enqueue(self, observation: MarketObservation, *, priority: int = 0) -> bool:
        """Queue one observation (the N02 adapter seam; no I/O happens here)."""

        now = _now(self._clock)
        return self._queue.push(
            observation,
            priority=priority,
            deadline=now + timedelta(seconds=self.config.queue_deadline_seconds),
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Acquire locks, open the ledger and recover interrupted receipts."""
        self._lock.acquire(float(self.config.worker_lock_timeout_seconds))
        try:
            ledger = NewsLedger.open(
                self.config.state_dir,
                self.config.run_id,
                self.config.scenario_id,
                clock=self._clock,
                worker_id=self.config.worker_id,
            )
            self._ledger = ledger
            ledger.create_account(
                account_id=self.config.account_id,
                run_id=self.config.run_id,
                scenario_id=self.config.scenario_id,
                initial_cash=self.config.initial_cash_jpy,
            )
            manager = PaperManager(
                ledger,
                clock=self._clock,
                clock_quality=self.config.clock_quality,
                risk_policy=self.config.risk_policy,
                policy=ManagerPolicy(
                    execution_delay_ms=Decimal(self.config.execution_delay_ms)
                ),
            )
            manager.recover()
            self._manager = manager
            self._started_at = _now(self._clock)
            self._write_heartbeat(self._started_at, status=STATUS_STARTING)
            if self._source is not None:
                self._ingest_thread = threading.Thread(
                    target=self._ingest_loop, name="news-paper-ingest", daemon=True
                )
                self._ingest_thread.start()
        except BaseException:
            self._close()
            raise

    def stop(self) -> None:
        self._stop.set()
        thread = self._ingest_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        if self._ledger is not None and not self._failed:
            try:
                self._write_heartbeat(_now(self._clock), status=STATUS_STOPPING)
            except Exception:  # noqa: BLE001 - never mask shutdown
                pass
        self._close()

    def _close(self) -> None:
        ledger, self._ledger = self._ledger, None
        self._manager = None
        if ledger is not None:
            ledger.close()
        self._lock.release()

    # -- ingestion (N02 seam) ---------------------------------------------

    def _ingest_loop(self) -> None:
        interval = float(self.config.cycle_seconds)
        while not self._stop.is_set():
            self._poll_source()
            if self._stop.wait(interval):
                return

    def _poll_source(self) -> None:
        source = self._source
        if source is None:
            return
        try:
            observations = source.poll()
            now = _now(self._clock)
            deadline = now + timedelta(seconds=self.config.queue_deadline_seconds)
            for observation in observations:
                self._queue.push(observation, deadline=deadline)
        except Exception:  # noqa: BLE001 - a source failure is a fixed code
            self._last_error_code = "SOURCE_TIMEOUT"
            return

    # -- cycle --------------------------------------------------------------

    def run_once(self, now: datetime | None = None) -> dict[str, object]:
        """Run one cycle; returns a small summary (also used by ``--once``)."""
        if self._ledger is None or self._manager is None:
            raise LedgerError("STORE_UNAVAILABLE")
        moment = _now(self._clock) if now is None else now
        if moment.tzinfo is None:
            raise LedgerError("CLOCK_UNTRUSTED")
        self._seq += 1
        if self._ingest_thread is None:
            self._poll_source()
        observations = self._queue.drain(moment)
        by_asset: dict[str, list[MarketObservation]] = {}
        for observation in observations:
            by_asset.setdefault(observation.asset, []).append(observation)
        for observation in observations:
            price = ((observation.bid + observation.ask) / 2).quantize(Decimal("0.00000001"))
            self._last_prices[observation.asset] = price
            self._last_quote_at[observation.asset] = observation.market_available_at
            if (
                self._last_market_available_at is None
                or observation.market_available_at > self._last_market_available_at
            ):
                self._last_market_available_at = observation.market_available_at

        if self.config.entries_disabled:
            self._cancel_risk_increasing_pending(moment)
        filled = self._process_pending_orders(by_asset, moment)
        accepted = self._submit_candidates(moment)
        self._record_valuation(moment)
        self._write_heartbeat(moment, status=STATUS_RUNNING)
        return {
            "cycle": self._seq,
            "as_of": _ts(moment),
            "observations": len(observations),
            "queue_depth": len(self._queue),
            "fills": filled,
            "accepted": accepted,
            "entries_disabled": self.config.entries_disabled,
            "last_error_code": self._last_error_code,
        }

    def run(self) -> None:
        """Cycle until stopped. Fatal store/clock errors end the loop."""
        try:
            self.start()
        except (LedgerError, OSError) as exc:
            # Do not write health here: a lock conflict belongs to the other
            # worker's heartbeat, and a store failure has no verified store.
            self._last_error_code = getattr(exc, "code", "STORE_UNAVAILABLE")
            self._failed = True
            return
        try:
            while not self._stop.is_set():
                started = self._monotonic()
                try:
                    self.run_once()
                except OSError:
                    self._last_error_code = "STORE_UNAVAILABLE"
                    self._failed = True
                    try:
                        self._write_heartbeat(_now(self._clock), status=STATUS_FAILED)
                    except Exception:  # noqa: BLE001
                        pass
                    break
                except LedgerError as exc:
                    self._last_error_code = exc.code
                    if exc.code in FATAL_CODES:
                        self._failed = True
                        try:
                            self._write_heartbeat(_now(self._clock), status=STATUS_FAILED)
                        except Exception:  # noqa: BLE001
                            pass
                        break
                elapsed = self._monotonic() - started
                self._stop.wait(max(0.0, float(self.config.cycle_seconds) - elapsed))
        finally:
            self.stop()

    # -- work units ---------------------------------------------------------

    def _pending_order_rows(self) -> list[dict[str, object]]:
        assert self._ledger is not None
        try:
            connection = sqlite3.connect(
                _sqlite_ro_uri(self._ledger.path), uri=True, timeout=5.0
            )
            try:
                rows = connection.execute(
                    "SELECT order_id, asset, side, status, expires_at FROM paper_orders "
                    "WHERE account_id = ? AND status IN ('pending', 'partially_filled')",
                    (self.config.account_id,),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise LedgerError("STORE_UNAVAILABLE") from exc
        return [
            {
                "order_id": row[0],
                "asset": row[1],
                "side": row[2],
                "status": row[3],
                "expires_at": row[4],
            }
            for row in rows
        ]

    def _cancel_risk_increasing_pending(self, now: datetime) -> None:
        """entries_disabled: cancel pending buys; protective sells keep running."""

        assert self._ledger is not None
        for row in self._pending_order_rows():
            if row["side"] != "buy":
                continue
            try:
                self._ledger.cancel_pending_order(
                    str(row["order_id"]), reason_code="FEATURE_DISABLED"
                )
            except LedgerError:
                self._last_error_code = "STORE_UNAVAILABLE"

    def _process_pending_orders(
        self, by_asset: Mapping[str, Sequence[MarketObservation]], now: datetime
    ) -> int:
        assert self._manager is not None
        filled = 0
        for row in self._pending_order_rows():
            asset = str(row["asset"])
            observations = by_asset.get(asset)
            if not observations:
                continue
            constraints = self.config.constraints.get(asset)
            if constraints is None:
                # Unapproved/unconfigured instrument: no fill, fixed code.
                self._last_error_code = "FEATURE_DISABLED"
                continue
            try:
                outcome = self._manager.process_observations(
                    str(row["order_id"]), tuple(observations), constraints, now=now
                )
            except ExecutionError as exc:
                self._last_error_code = exc.code
                continue
            if outcome.fills:
                filled += len(outcome.fills)
                self._last_fill_at = now
                if outcome.reason_codes:
                    self._last_error_code = outcome.reason_codes[0]
        return filled

    def _submit_candidates(self, now: datetime) -> int:
        if self.config.entries_disabled or self._candidates is None:
            return 0
        assert self._manager is not None
        accepted = 0
        try:
            candidates = self._candidates.candidates(now)
        except Exception:  # noqa: BLE001 - N03 outage is a fixed code
            self._last_error_code = "CLASSIFIER_UNAVAILABLE"
            return 0
        for request in candidates:
            try:
                view: ReceiptView = self._manager.submit_candidate(request)
            except LedgerError as exc:
                if exc.code in FATAL_CODES:
                    raise
                self._last_error_code = exc.code
                continue
            if view.status == "accepted":
                accepted += 1
        return accepted

    def _record_valuation(self, now: datetime) -> None:
        assert self._ledger is not None
        account = self._ledger.account_snapshot(self.config.account_id)
        positions_value = Decimal("0")
        stale = False
        for asset, (quantity, average_cost) in account.positions.items():
            price = self._last_prices.get(asset)
            quoted_at = self._last_quote_at.get(asset)
            constraints = self.config.constraints.get(asset)
            if price is None or quoted_at is None or constraints is None:
                # No quote ever seen, or no approved constraints: never fresh.
                stale = True
                if price is None:
                    price = average_cost
            else:
                age = Decimal(str((now - quoted_at).total_seconds()))
                if age < 0 or age > constraints.max_quote_age_seconds:
                    stale = True
            positions_value += quantity * price
        equity = account.cash + positions_value
        self._ledger.record_valuation(
            account_id=self.config.account_id,
            valuation_id=f"val:{self._instance_token}:{self._seq}",
            as_of=now,
            positions_value=positions_value,
            total_equity=equity,
            stale=stale,
        )

    # -- heartbeat ----------------------------------------------------------

    def _write_heartbeat(self, moment: datetime, *, status: str) -> None:
        self._status = status
        payload = {
            "schema_version": WORKER_SCHEMA_VERSION,
            "worker_id": self.config.worker_id,
            "run_id": self.config.run_id,
            "scenario_id": self.config.scenario_id,
            "account_id": self.config.account_id,
            "mode": MODE,
            "live_eligible": LIVE_ELIGIBLE,
            "status": status,
            "heartbeat_at": _ts(moment),
            "started_at": _ts(self._started_at) if self._started_at is not None else None,
            "cycle_seq": self._seq,
            "entries_disabled": self.config.entries_disabled,
            "queue_depth": len(self._queue),
            "queue_dropped_full": self._queue.dropped_full,
            "queue_dropped_expired": self._queue.dropped_expired,
            "queue_dropped_duplicate": self._queue.dropped_duplicate,
            "last_market_available_at": (
                _ts(self._last_market_available_at)
                if self._last_market_available_at is not None
                else None
            ),
            "last_fill_at": _ts(self._last_fill_at) if self._last_fill_at is not None else None,
            "last_news_available_at": None,
            "last_classification_at": None,
            "last_error_code": self._last_error_code,
        }
        write_health(self.config.state_dir, payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-news-paper")
    parser.add_argument("--config", required=True, help="path to config/news-paper.toml")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except WorkerConfigError as exc:
        print(json.dumps({"state": "config_invalid", "reason_code": exc.code}, ensure_ascii=False))
        return 2
    if config is None:
        print(json.dumps({"state": "disabled", "mode": MODE, "live_eligible": LIVE_ELIGIBLE}))
        return 0
    worker = NewsPaperWorker(config)
    if args.once:
        try:
            worker.start()
        except (LedgerError, OSError, ExecutionError) as exc:
            code = getattr(exc, "code", "STORE_UNAVAILABLE")
            print(json.dumps({"state": "failed", "reason_code": code}, ensure_ascii=False))
            return 1
        try:
            summary = worker.run_once()
        except (LedgerError, OSError, ExecutionError) as exc:
            code = getattr(exc, "code", "STORE_UNAVAILABLE")
            worker._failed = True  # noqa: SLF001 - fixed failure record
            try:
                worker._write_heartbeat(_now(worker._clock), status=STATUS_FAILED)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
            print(json.dumps({"state": "failed", "reason_code": code}, ensure_ascii=False))
            return 1
        finally:
            worker.stop()
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    def _stop(_signum: int, _frame: object) -> None:
        worker.request_stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker.run()
    return 1 if worker.failed else 0


if __name__ == "__main__":
    sys.exit(main())
