"""Durable ledger, reservations and decision receipts for the news PAPER lane.

This module is the N05 single-writer store: one dedicated SQLite database per
``<state_dir>/news-paper/runs/<run_id>/<scenario_id>/paper.sqlite3``. It never
touches the crypto PAPER ledger, ``free_strategy`` LabStore, any existing
account or the active policy; the path rules below reject every attempt to
reach such a store through symlinks, hardlinks or relative paths.

Contract sources (frozen design, all approved in #775):

* N00 v2 §2 (typed schemas), §3.4 (``(stage, code) -> action`` table),
  §4.1 (T1/T2 durability), §4.2 (crash completion/cancel choice).
* N00 v3 §1 (fill fence sampled after a successful committed-marker SELECT,
  ``available_at > fence_at + 2 * max_error_ms``), §1.5 (two-clock budget).
* Parent #769 D6/D7/D8/D9 (receipt/order/fill, accounting separation,
  event-use uniqueness, sell proceeds never funding a buy).

Durability contract implemented here (mirrors N06's docstring):

* T1 (one transaction): receipt row with ``receipt_committed_at IS NULL``,
  cash/inventory reservation, pending order and the ``event_use`` consumption.
* T2 (a separate transaction, idempotent): the wall clock is read exactly once
  (``t2``); ``receipt_committed_at``, ``order.accepted_at``,
  ``order.not_before_at`` and the marker row are all derived from that single
  sample. A crash between T1 and T2 never fills (fail-closed); recovery either
  re-runs T2 or abandons the receipt and releases the reservation.
* Fills are keyed by ``(order_id, quote_ref, level_index)``. A restart, a
  re-delivered quote or a replay cannot create the same fill, fee or balance
  change twice.

Mode is fixed to ``paper`` and ``live_eligible`` is always false. No network
module is imported and no LLM/raw-news/secret payload is ever stored: receipt
and audit payloads accept fixed codes, identifiers and versions only.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .execution import (
    EXECUTION_MODEL_VERSION,
    LIVE_ELIGIBLE,
    MODE,
    TIME_IN_FORCE,
    ClockQuality,
    ExecutionOutcome,
    Fence,
    Fill,
)

D = Decimal
ZERO = D("0")
UTC = timezone.utc

LEDGER_SCHEMA_VERSION = "1"
DB_FILE_NAME = "paper.sqlite3"

RECEIPT_STATUSES = frozenset({"accepted", "held", "rejected"})
ORDER_STATUSES = frozenset({"pending", "partially_filled", "filled", "rejected", "canceled", "expired"})
TERMINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "expired", "rejected"})
RESERVATION_KINDS = frozenset({"cash", "inventory"})

# Parent #769 D10 plus the accepted success codes (N00 v2 §2.6). Anything else
# is a schema violation; the host never stores free-form exception text.
REASON_CODES = frozenset({
    "INVALID_JSON", "DUPLICATE_JSON_KEY", "UNKNOWN_KEY", "SCHEMA_VERSION_UNSUPPORTED",
    "TYPE_INVALID", "NONFINITE_VALUE", "VALUE_OUT_OF_RANGE", "PAYLOAD_LIMIT",
    "NON_PAPER_MODE", "LIVE_ELIGIBILITY_FORBIDDEN", "ACCOUNT_SCOPE_MISMATCH",
    "STATE_PATH_FORBIDDEN", "FORBIDDEN_CAPABILITY",
    "SOURCE_UNKNOWN", "SOURCE_NOT_APPROVED", "SOURCE_TIME_UNKNOWN", "TIMEZONE_MISSING",
    "FUTURE_NEWS", "NEWS_STALE", "NEWS_NOT_AVAILABLE", "CLOCK_UNTRUSTED",
    "ASSET_UNMAPPED", "ASSET_AMBIGUOUS",
    "EVIDENCE_MISSING", "EVIDENCE_NOT_AVAILABLE", "EVIDENCE_REVOKED",
    "NEWS_DUPLICATE", "EVENT_ALREADY_USED", "COOLDOWN_ACTIVE", "NEWS_CONFLICT",
    "NEWS_PRICE_CONFLICT",
    "NEWS_UNAVAILABLE", "NO_DIRECTION", "FEATURE_DISABLED", "SHADOW_ONLY",
    "CLASSIFIER_TIMEOUT", "CLASSIFIER_UNAVAILABLE", "CLASSIFIER_OUTPUT_INVALID",
    "CLASSIFIER_LOW_CONFIDENCE", "MATERIALITY_LOW", "SIGNAL_EXPIRED",
    "SOURCE_TIMEOUT", "SOURCE_RATE_LIMITED", "SOURCE_RESPONSE_TOO_LARGE",
    "SOURCE_CONTENT_INVALID", "SOURCE_URL_FORBIDDEN",
    "BUDGET_EXHAUSTED", "STORE_UNAVAILABLE",
    "MARKET_CLOSED", "CALENDAR_UNAVAILABLE", "QUOTE_STALE", "QUOTE_UNTRADEABLE",
    "QUOTE_NOT_NEW", "BAR_NOT_CLOSED", "VOLUME_UNAVAILABLE", "SPREAD_TOO_WIDE",
    "VOLATILITY_LIMIT", "LIQUIDITY_UNKNOWN", "LIQUIDITY_INSUFFICIENT",
    "INSUFFICIENT_FUNDS", "INSUFFICIENT_INVENTORY", "MIN_ORDER_NOT_MET",
    "POSITION_LIMIT", "CURRENCY_EXPOSURE_LIMIT", "DAILY_LOSS_LIMIT",
    "DRAWDOWN_LIMIT", "RISK_RESERVATION_CONFLICT",
    "ORDER_EXPIRED", "IOC_REMAINDER_CANCELED", "ORDER_ALREADY_FINAL",
    "RECEIPT_NOT_COMMITTED", "RISK_CHANGED_BEFORE_FILL",
    "NEWS_PRICE_AGREE", "NEWS_ONLY_ELIGIBLE", "RISK_EXIT",
})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEC_RE = re.compile(r"^-?[0-9]{1,18}(\.[0-9]{1,8})?$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class LedgerError(ValueError):
    """Contract violation; carries a fixed reason code only."""

    def __init__(self, code: str) -> None:
        if code not in REASON_CODES:
            raise ValueError(f"unknown ledger code: {code!r}")
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# scalar validation (N00 v2 §2 common types)
# ---------------------------------------------------------------------------


def _id(value: object, name: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise LedgerError("VALUE_OUT_OF_RANGE")
    del name
    return value


def _dec(value: object, *, positive: bool = False, signed: bool = False) -> Decimal:
    """Validate a bounded decimal-string amount and return it as Decimal."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise LedgerError("NONFINITE_VALUE")
        text = format(value, "f")
    elif isinstance(value, (int,)) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise LedgerError("TYPE_INVALID")
    if not _DEC_RE.match(text):
        raise LedgerError("VALUE_OUT_OF_RANGE")
    number = D(text)
    if not signed and number < 0:
        raise LedgerError("VALUE_OUT_OF_RANGE")
    if positive and number <= 0:
        raise LedgerError("VALUE_OUT_OF_RANGE")
    return number


def _money(value: Decimal, *, charge: bool) -> str:
    """Quantize a computed money amount to the stored 8-fraction bound.

    Charges round up and credits round down so rounding can never create cash
    out of nothing (parent #769 D8: negative PnL is recorded, never hidden by
    a negative balance).
    """
    quantum = D("0.00000001")
    rounded = value.quantize(quantum, rounding=ROUND_CEILING if charge else ROUND_FLOOR)
    if rounded < 0 and not charge:
        rounded = ZERO
    text = format(rounded, "f")
    if not _DEC_RE.match(text):  # pragma: no cover - defensive
        raise LedgerError("VALUE_OUT_OF_RANGE")
    return text


def _ts(value: object, name: str = "ts") -> str:
    """Canonical RFC3339 UTC microsecond timestamp string (N00 v2 §2 TS)."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise LedgerError("TIMEZONE_MISSING")
        moment = value.astimezone(UTC)
    elif isinstance(value, str):
        if not _TS_RE.match(value):
            raise LedgerError("VALUE_OUT_OF_RANGE")
        return value
    else:
        raise LedgerError("TYPE_INVALID")
    del name
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _code_list(value: object, *, limit: int = 16) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LedgerError("TYPE_INVALID")
    if len(value) > limit:
        raise LedgerError("PAYLOAD_LIMIT")
    codes: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in REASON_CODES:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        if item in codes:
            raise LedgerError("DUPLICATE_JSON_KEY")
        codes.append(item)
    return codes


def _id_list(value: object, *, low: int, high: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LedgerError("TYPE_INVALID")
    if not low <= len(value) <= high:
        raise LedgerError("PAYLOAD_LIMIT")
    items: list[str] = []
    for item in value:
        items.append(_id(item, "ref"))
    if len(set(items)) != len(items):
        raise LedgerError("DUPLICATE_JSON_KEY")
    return items


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


# ---------------------------------------------------------------------------
# path rules (issue #782: ID/path deviations are rejected)
# ---------------------------------------------------------------------------


def resolve_ledger_path(state_dir: str | Path, run_id: str, scenario_id: str) -> Path:
    """Canonical ``<state_dir>/news-paper/runs/<run>/<scenario>/paper.sqlite3``.

    Every existing path component is checked with ``lstat``; a symlink (or a
    relative ``state_dir``) is rejected with ``STATE_PATH_FORBIDDEN`` so no
    other database can be reached through an alias. Directories are created
    owner-only when missing.
    """
    run_id = _id(run_id, "run_id")
    scenario_id = _id(scenario_id, "scenario_id")
    # A relative state_dir must never be resolved against an arbitrary
    # process CWD (issue #782: relative-path specs are rejected).
    if not Path(state_dir).is_absolute() or ".." in Path(state_dir).parts:
        raise LedgerError("STATE_PATH_FORBIDDEN")
    # Resolve the given root first (macOS temp dirs live behind /var -> /private/var),
    # then verify that *no component of the resolved path* is a symlink. The
    # canonical target is therefore fixed: an alias cannot reach another store.
    base = Path(os.path.realpath(state_dir))
    if not base.is_absolute():
        raise LedgerError("STATE_PATH_FORBIDDEN")
    current = Path(base.anchor)
    for part in (*base.parts[1:], "news-paper", "runs", run_id, scenario_id):
        current = current / part
        if os.path.lexists(current):
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise LedgerError("STATE_PATH_FORBIDDEN")
            if not stat.S_ISDIR(info.st_mode):
                raise LedgerError("STATE_PATH_FORBIDDEN")
        else:
            current.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(current, 0o700)
    target = current / DB_FILE_NAME
    if os.path.lexists(target):
        info = os.lstat(target)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise LedgerError("STATE_PATH_FORBIDDEN")
        if info.st_nlink != 1:
            # A hardlink would alias another store; refuse it.
            raise LedgerError("STATE_PATH_FORBIDDEN")
    resolved = Path(os.path.realpath(target))
    if resolved != target:
        raise LedgerError("STATE_PATH_FORBIDDEN")
    return target


# ---------------------------------------------------------------------------
# input drafts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReservationDraft:
    reservation_id: str
    kind: str
    asset: str
    quantity: str | Decimal = ZERO
    amount: str | Decimal = ZERO


@dataclass(frozen=True)
class EventUseDraft:
    event_use_id: str
    event_group_id: str
    asset: str


@dataclass(frozen=True)
class OrderDraft:
    order_id: str
    asset: str
    side: str
    requested_quantity: str | Decimal
    expires_at: str | datetime
    reservation_id: str
    event_use_id: str


@dataclass(frozen=True)
class ReceiptDraft:
    receipt_id: str
    run_id: str
    scenario_id: str
    account_id: str
    decision_at: str | datetime
    manager_seen_at: str | datetime
    input_cutoff_sequence: int
    signal_refs: Sequence[str]
    market_refs: Sequence[str]
    decision_quote: tuple[str, str, str, str] | None
    policy_version: str
    risk_policy_version: str
    status: str
    reason_codes: Sequence[str]
    supersedes_receipt_id: str | None = None


@dataclass(frozen=True)
class AccountState:
    account_id: str
    run_id: str
    scenario_id: str
    quote_currency: str
    initial_cash: Decimal
    cash: Decimal
    revision: int
    created_at: str
    positions: Mapping[str, tuple[Decimal, Decimal]] = field(default_factory=dict)
    cash_reserved: Decimal = ZERO
    inventory_reserved: Mapping[str, Decimal] = field(default_factory=dict)
    mode: str = MODE
    live_eligible: bool = LIVE_ELIGIBLE

    @property
    def cash_available(self) -> Decimal:
        """Settled cash minus active reservations: an unsettled sell never funds a buy."""
        return self.cash - self.cash_reserved

    def inventory_available(self, asset: str) -> Decimal:
        position = self.positions.get(asset, (ZERO, ZERO))[0]
        return position - self.inventory_reserved.get(asset, ZERO)


@dataclass(frozen=True)
class ApplyResult:
    order_id: str
    previous_status: str
    status: str
    applied_fills: int
    canceled_quantity: Decimal
    reservation_released: bool
    already_final: bool = False


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  scenario_id TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode = 'paper'),
  live_eligible INTEGER NOT NULL CHECK (live_eligible = 0),
  quote_currency TEXT NOT NULL,
  initial_cash TEXT NOT NULL,
  cash TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
  account_id TEXT NOT NULL REFERENCES accounts(account_id),
  asset TEXT NOT NULL,
  quantity TEXT NOT NULL,
  avg_cost TEXT NOT NULL,
  PRIMARY KEY (account_id, asset)
);
CREATE TABLE IF NOT EXISTS reservations (
  reservation_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id),
  order_id TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('cash', 'inventory')),
  asset TEXT NOT NULL,
  quantity TEXT NOT NULL,
  amount TEXT NOT NULL,
  created_at TEXT NOT NULL,
  released_at TEXT,
  release_reason TEXT
);
CREATE TABLE IF NOT EXISTS event_use (
  event_use_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  scenario_id TEXT NOT NULL,
  event_group_id TEXT NOT NULL,
  asset TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  used_at TEXT NOT NULL,
  UNIQUE (run_id, scenario_id, event_group_id, asset)
);
CREATE TABLE IF NOT EXISTS decision_receipts (
  receipt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  scenario_id TEXT NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(account_id),
  mode TEXT NOT NULL CHECK (mode = 'paper'),
  live_eligible INTEGER NOT NULL CHECK (live_eligible = 0),
  decision_at TEXT NOT NULL,
  manager_seen_at TEXT NOT NULL,
  input_cutoff_sequence INTEGER NOT NULL CHECK (input_cutoff_sequence >= 1),
  signal_refs TEXT NOT NULL,
  market_refs TEXT NOT NULL,
  decision_quote TEXT,
  policy_version TEXT NOT NULL,
  risk_policy_version TEXT NOT NULL,
  execution_model_version TEXT NOT NULL CHECK (execution_model_version = 'next_observation_ioc_v1'),
  status TEXT NOT NULL CHECK (status IN ('accepted', 'held', 'rejected')),
  reason_codes TEXT NOT NULL,
  order_ref TEXT,
  supersedes_receipt_id TEXT,
  receipt_committed_at TEXT,
  abandoned_at TEXT,
  created_at TEXT NOT NULL,
  CHECK (status != 'accepted' OR order_ref IS NOT NULL),
  CHECK (status = 'accepted' OR order_ref IS NULL),
  CHECK (supersedes_receipt_id IS NULL OR supersedes_receipt_id != receipt_id)
);
CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY,
  receipt_id TEXT NOT NULL UNIQUE REFERENCES decision_receipts(receipt_id),
  run_id TEXT NOT NULL,
  scenario_id TEXT NOT NULL,
  account_id TEXT NOT NULL REFERENCES accounts(account_id),
  mode TEXT NOT NULL CHECK (mode = 'paper'),
  live_eligible INTEGER NOT NULL CHECK (live_eligible = 0),
  asset TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  requested_quantity TEXT NOT NULL,
  filled_quantity TEXT NOT NULL,
  remaining_quantity TEXT NOT NULL,
  time_in_force TEXT NOT NULL CHECK (time_in_force = 'next_observation_ioc'),
  status TEXT NOT NULL CHECK (status IN (
    'pending', 'partially_filled', 'filled', 'rejected', 'canceled', 'expired')),
  accepted_at TEXT,
  not_before_at TEXT,
  expires_at TEXT NOT NULL,
  reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(reservation_id),
  event_use_id TEXT NOT NULL,
  execution_model_version TEXT NOT NULL CHECK (execution_model_version = 'next_observation_ioc_v1')
);
CREATE TABLE IF NOT EXISTS fills (
  fill_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
  receipt_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  asset TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  quantity TEXT NOT NULL,
  price TEXT NOT NULL,
  fee_jpy TEXT NOT NULL,
  slippage_bps TEXT NOT NULL,
  quote_ref TEXT NOT NULL,
  level_index INTEGER NOT NULL CHECK (level_index >= 0),
  market_event_at TEXT NOT NULL,
  market_available_at TEXT NOT NULL,
  filled_at TEXT NOT NULL,
  partial INTEGER NOT NULL CHECK (partial IN (0, 1)),
  execution_model_version TEXT NOT NULL CHECK (execution_model_version = 'next_observation_ioc_v1'),
  UNIQUE (order_id, quote_ref, level_index)
);
CREATE TABLE IF NOT EXISTS cancel_events (
  cancel_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
  quantity TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  canceled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_markers (
  marker_id TEXT PRIMARY KEY,
  receipt_id TEXT NOT NULL UNIQUE REFERENCES decision_receipts(receipt_id),
  not_before_at TEXT NOT NULL,
  committed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fill_fences (
  fence_id TEXT PRIMARY KEY,
  marker_id TEXT NOT NULL UNIQUE REFERENCES execution_markers(marker_id),
  receipt_id TEXT NOT NULL,
  fence_at TEXT NOT NULL,
  clock_quality TEXT NOT NULL,
  worker_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS valuations (
  valuation_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(account_id),
  as_of TEXT NOT NULL,
  cash TEXT NOT NULL,
  positions_value TEXT NOT NULL,
  total_equity TEXT NOT NULL,
  stale INTEGER NOT NULL CHECK (stale IN (0, 1))
);
CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  payload TEXT NOT NULL
);
"""


class NewsLedger:
    """Single-writer durable store for one news-PAPER run/scenario account."""

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None,
                 worker_id: str = "n05") -> None:
        self.path = Path(path)
        if self.path.name != DB_FILE_NAME:
            raise LedgerError("STATE_PATH_FORBIDDEN")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._worker_id = _id(worker_id, "worker_id")
        self._lock_fd = os.open(f"{self.path}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            raise LedgerError("STORE_UNAVAILABLE") from exc
        try:
            self._conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=5.0)
            try:
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = FULL")
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.execute("PRAGMA busy_timeout = 5000")
                tables = {
                    row[0]
                    for row in self._conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "accounts" not in tables:
                    # executescript manages its own transaction; run it in autocommit.
                    self._conn.executescript(_SCHEMA)
                    self._conn.execute(
                        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                        (LEDGER_SCHEMA_VERSION,),
                    )
                else:
                    version = self._conn.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                    if version is None or version[0] != LEDGER_SCHEMA_VERSION:
                        # Fail closed on an unknown schema; no migration here.
                        raise LedgerError("SCHEMA_VERSION_UNSUPPORTED")
                self._check_health()
            except sqlite3.DatabaseError as exc:
                # A corrupt or unreadable store opens nothing: no writes at all.
                raise LedgerError("STORE_UNAVAILABLE") from exc
        except BaseException:
            self.close()
            raise

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, state_dir: str | Path, run_id: str, scenario_id: str, *,
             clock: Callable[[], datetime] | None = None,
             worker_id: str = "n05") -> "NewsLedger":
        path = resolve_ledger_path(state_dir, run_id, scenario_id)
        return cls(path, clock=clock, worker_id=worker_id)

    def _check_health(self) -> None:
        """Fail closed on an unhealthy store: no writes at all, not even cancels."""
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise LedgerError("STORE_UNAVAILABLE")
        if self._conn.execute("PRAGMA foreign_key_check").fetchall():
            raise LedgerError("STORE_UNAVAILABLE")

    def close(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
            self._conn = None  # type: ignore[assignment]
        fd = getattr(self, "_lock_fd", None)
        if fd is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(fd, fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                os.close(fd)
            self._lock_fd = None  # type: ignore[assignment]

    def __enter__(self) -> "NewsLedger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextlib.contextmanager
    def _write_txn(self):
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("PRAGMA defer_foreign_keys = ON")
        try:
            yield cur
        except BaseException:
            with contextlib.suppress(Exception):
                self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def _now(self) -> str:
        return _ts(self._clock())

    @property
    def clock(self) -> Callable[[], datetime]:
        """The injected wall clock (shared with the manager by default)."""
        return self._clock

    def _audit(self, cur: sqlite3.Cursor, kind: str, subject_id: str,
               payload: Mapping[str, object]) -> None:
        clean: dict[str, object] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not _ID_RE.match(key):
                raise LedgerError("VALUE_OUT_OF_RANGE")
            if isinstance(value, bool):
                clean[key] = int(value)
            elif isinstance(value, int):
                clean[key] = value
            elif isinstance(value, str):
                clean[key] = value[:256]
            elif value is None:
                clean[key] = None
            else:
                raise LedgerError("TYPE_INVALID")
        cur.execute(
            "INSERT INTO audit_events(occurred_at, kind, subject_id, payload) VALUES (?, ?, ?, ?)",
            (self._now(), kind, subject_id, _json_dumps(clean)),
        )

    # -- account -----------------------------------------------------------

    def create_account(self, *, account_id: str, run_id: str, scenario_id: str,
                       initial_cash: str | Decimal, quote_currency: str = "JPY",
                       created_at: str | datetime | None = None) -> AccountState:
        account_id = _id(account_id, "account_id")
        run_id = _id(run_id, "run_id")
        scenario_id = _id(scenario_id, "scenario_id")
        cash = _dec(initial_cash, positive=True)
        currency = _id(quote_currency, "quote_currency")
        created = _ts(created_at) if created_at is not None else self._now()
        with self._write_txn() as cur:
            row = cur.execute(
                "SELECT run_id, scenario_id, initial_cash, cash, revision, created_at, "
                "quote_currency FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is not None:
                if (row[0], row[1], row[2], row[6]) != (run_id, scenario_id, format(cash, "f"), currency):
                    raise LedgerError("ACCOUNT_SCOPE_MISMATCH")
                return self.account_snapshot(account_id)
            cur.execute(
                "INSERT INTO accounts(account_id, run_id, scenario_id, mode, live_eligible, "
                "quote_currency, initial_cash, cash, revision, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?, 1, ?)",
                (account_id, run_id, scenario_id, MODE, currency,
                 format(cash, "f"), format(cash, "f"), created),
            )
            self._audit(cur, "account_created", account_id,
                        {"run_id": run_id, "scenario_id": scenario_id})
        return self.account_snapshot(account_id)

    def account_snapshot(self, account_id: str) -> AccountState:
        account_id = _id(account_id, "account_id")
        row = self._conn.execute(
            "SELECT run_id, scenario_id, quote_currency, initial_cash, cash, revision, created_at "
            "FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_KEY")
        positions = {
            asset: (D(quantity), D(avg_cost))
            for asset, quantity, avg_cost in self._conn.execute(
                "SELECT asset, quantity, avg_cost FROM positions WHERE account_id = ?",
                (account_id,),
            )
        }
        cash_reserved = ZERO
        inventory_reserved: dict[str, Decimal] = {}
        for kind, asset, quantity, amount in self._conn.execute(
            "SELECT kind, asset, quantity, amount FROM reservations "
            "WHERE account_id = ? AND released_at IS NULL",
            (account_id,),
        ):
            if kind == "cash":
                cash_reserved += D(amount)
            else:
                inventory_reserved[asset] = inventory_reserved.get(asset, ZERO) + D(quantity)
        return AccountState(
            account_id=account_id,
            run_id=row[0],
            scenario_id=row[1],
            quote_currency=row[2],
            initial_cash=D(row[3]),
            cash=D(row[4]),
            revision=row[5],
            created_at=row[6],
            positions=positions,
            cash_reserved=cash_reserved,
            inventory_reserved=inventory_reserved,
        )

    def record_valuation(self, *, account_id: str, valuation_id: str, as_of: str | datetime,
                         positions_value: str | Decimal, total_equity: str | Decimal,
                         stale: bool = False) -> None:
        account_id = _id(account_id, "account_id")
        valuation_id = _id(valuation_id, "valuation_id")
        snapshot = self.account_snapshot(account_id)
        positions_value_dec = _dec(positions_value)
        equity = _dec(total_equity)
        with self._write_txn() as cur:
            cur.execute(
                "INSERT INTO valuations(valuation_id, account_id, as_of, cash, positions_value, "
                "total_equity, stale) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (valuation_id, account_id, _ts(as_of), format(snapshot.cash, "f"),
                 format(positions_value_dec, "f"), format(equity, "f"), int(bool(stale))),
            )

    def equity_history(self, account_id: str) -> list[tuple[str, Decimal]]:
        account_id = _id(account_id, "account_id")
        return [
            (as_of, D(equity))
            for as_of, equity in self._conn.execute(
                "SELECT as_of, total_equity FROM valuations WHERE account_id = ? ORDER BY as_of",
                (account_id,),
            )
        ]

    # -- T1: receipt + reservation + pending order + event_use -------------

    def begin_receipt(self, *, receipt: ReceiptDraft, order: OrderDraft,
                      reservation: ReservationDraft,
                      event_use: EventUseDraft) -> None:
        """T1 (N00 v2 §4.1): one transaction for the whole acceptance bundle.

        An accepted receipt always consumes its ``event_use`` here, so a crash
        before T2 still prevents a re-entry of the same event.
        """
        receipt_row = self._receipt_values(receipt, order_ref=order.order_id)
        order_row = self._order_values(order, receipt_id=receipt.receipt_id)
        with self._write_txn() as cur:
            if cur.execute(
                "SELECT 1 FROM accounts WHERE account_id = ?", (receipt.account_id,)
            ).fetchone() is None:
                raise LedgerError("UNKNOWN_KEY")
            if cur.execute(
                "SELECT 1 FROM decision_receipts WHERE receipt_id = ?", (receipt.receipt_id,)
            ).fetchone() is not None:
                # Re-send of a known receipt id: never advance state twice.
                raise LedgerError("ORDER_ALREADY_FINAL")
            if event_use is not None:
                cur.execute(
                    "INSERT INTO event_use(event_use_id, run_id, scenario_id, event_group_id, "
                    "asset, receipt_id, used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        _id(event_use.event_use_id, "event_use_id"),
                        receipt_row["run_id"],
                        receipt_row["scenario_id"],
                        _id(event_use.event_group_id, "event_group_id"),
                        _id(event_use.asset, "asset"),
                        receipt.receipt_id,
                        self._now(),
                    ),
                )
            else:
                # D7: an accepted order always carries its event-use binding.
                raise LedgerError("VALUE_OUT_OF_RANGE")
            cur.execute(
                "INSERT INTO reservations(reservation_id, account_id, order_id, kind, asset, "
                "quantity, amount, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _id(reservation.reservation_id, "reservation_id"),
                    receipt.account_id,
                    order.order_id,
                    reservation.kind,
                    reservation.asset,
                    format(_dec(reservation.quantity), "f"),
                    format(_dec(reservation.amount), "f"),
                    self._now(),
                ),
            )
            cur.execute(
                "INSERT INTO paper_orders(order_id, receipt_id, run_id, scenario_id, account_id, "
                "mode, live_eligible, asset, side, requested_quantity, filled_quantity, "
                "remaining_quantity, time_in_force, status, accepted_at, not_before_at, "
                "expires_at, reservation_id, event_use_id, execution_model_version) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, '0', ?, ?, 'pending', NULL, NULL, ?, ?, ?, ?)",
                (
                    order.order_id,
                    receipt.receipt_id,
                    receipt_row["run_id"],
                    receipt_row["scenario_id"],
                    receipt.account_id,
                    MODE,
                    order_row["asset"],
                    order_row["side"],
                    order_row["requested"],
                    order_row["requested"],
                    TIME_IN_FORCE,
                    _ts(order.expires_at),
                    reservation.reservation_id,
                    event_use.event_use_id if event_use is not None else "",
                    EXECUTION_MODEL_VERSION,
                ),
            )
            cur.execute(
                "INSERT INTO decision_receipts(receipt_id, run_id, scenario_id, account_id, mode, "
                "live_eligible, decision_at, manager_seen_at, input_cutoff_sequence, signal_refs, "
                "market_refs, decision_quote, policy_version, risk_policy_version, "
                "execution_model_version, status, reason_codes, order_ref, supersedes_receipt_id, "
                "receipt_committed_at, abandoned_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                (
                    receipt.receipt_id,
                    receipt_row["run_id"],
                    receipt_row["scenario_id"],
                    receipt.account_id,
                    MODE,
                    receipt_row["decision_at"],
                    receipt_row["manager_seen_at"],
                    receipt_row["cutoff"],
                    receipt_row["signal_refs"],
                    receipt_row["market_refs"],
                    receipt_row["decision_quote"],
                    receipt_row["policy_version"],
                    receipt_row["risk_policy_version"],
                    EXECUTION_MODEL_VERSION,
                    "accepted",
                    receipt_row["reason_codes"],
                    order.order_id,
                    receipt_row["supersedes"],
                    self._now(),
                ),
            )
            self._audit(cur, "receipt_t1", receipt.receipt_id,
                        {"order_id": order.order_id, "side": order.side,
                         "asset": order.asset,
                         "event_use_id": event_use.event_use_id if event_use else ""})

    def record_decision_only(self, receipt: ReceiptDraft) -> None:
        """Persist a held/rejected decision: receipt only, no order or reservation."""
        if receipt.status not in {"held", "rejected"}:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        row = self._receipt_values(receipt, order_ref=None)
        with self._write_txn() as cur:
            if cur.execute(
                "SELECT 1 FROM accounts WHERE account_id = ?", (receipt.account_id,)
            ).fetchone() is None:
                raise LedgerError("UNKNOWN_KEY")
            if cur.execute(
                "SELECT 1 FROM decision_receipts WHERE receipt_id = ?", (receipt.receipt_id,)
            ).fetchone() is not None:
                raise LedgerError("ORDER_ALREADY_FINAL")
            cur.execute(
                "INSERT INTO decision_receipts(receipt_id, run_id, scenario_id, account_id, mode, "
                "live_eligible, decision_at, manager_seen_at, input_cutoff_sequence, signal_refs, "
                "market_refs, decision_quote, policy_version, risk_policy_version, "
                "execution_model_version, status, reason_codes, order_ref, supersedes_receipt_id, "
                "receipt_committed_at, abandoned_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?)",
                (
                    receipt.receipt_id,
                    row["run_id"],
                    row["scenario_id"],
                    receipt.account_id,
                    MODE,
                    row["decision_at"],
                    row["manager_seen_at"],
                    row["cutoff"],
                    row["signal_refs"],
                    row["market_refs"],
                    row["decision_quote"],
                    row["policy_version"],
                    row["risk_policy_version"],
                    EXECUTION_MODEL_VERSION,
                    receipt.status,
                    row["reason_codes"],
                    row["supersedes"],
                    self._now(),
                ),
            )
            self._audit(cur, "receipt_decision_only", receipt.receipt_id,
                        {"status": receipt.status})

    # -- T2: single-sample commit + durable marker --------------------------

    def commit_receipt(self, receipt_id: str, *, execution_delay_ms: str | Decimal = ZERO) -> str:
        """T2 (N00 v2 §4.1): one clock sample derives every committed value.

        Idempotent: a re-run with an existing marker changes nothing. Returns
        ``"committed"``, ``"already_committed"``, or ``"expired_before_commit"``
        (the caller must then abandon the receipt; the clock is never re-read).
        """
        receipt_id = _id(receipt_id, "receipt_id")
        delay = _dec(execution_delay_ms)
        with self._write_txn() as cur:
            receipt = cur.execute(
                "SELECT r.status, r.receipt_committed_at, r.abandoned_at, o.expires_at, o.order_id "
                "FROM decision_receipts r JOIN paper_orders o ON o.receipt_id = r.receipt_id "
                "WHERE r.receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if receipt is None:
                raise LedgerError("RECEIPT_NOT_COMMITTED")
            status, committed_at, abandoned_at, expires_at, order_id = receipt
            if committed_at is not None:
                return "already_committed"
            if status != "accepted" or abandoned_at is not None:
                raise LedgerError("ORDER_ALREADY_FINAL")
            t2 = self._now()
            if _parse_ts(t2) >= _parse_ts(expires_at):
                return "expired_before_commit"
            not_before = _parse_ts(t2) + timedelta(seconds=float(delay / D(1000)))
            if not_before > _parse_ts(expires_at):
                not_before = _parse_ts(expires_at)
            marker_id = f"marker:{receipt_id}"
            cur.execute(
                "UPDATE decision_receipts SET receipt_committed_at = ? "
                "WHERE receipt_id = ? AND receipt_committed_at IS NULL",
                (t2, receipt_id),
            )
            if cur.rowcount != 1:  # pragma: no cover - defensive
                raise LedgerError("STORE_UNAVAILABLE")
            cur.execute(
                "UPDATE paper_orders SET accepted_at = ?, not_before_at = ? "
                "WHERE order_id = ? AND accepted_at IS NULL",
                (t2, _ts(not_before), order_id),
            )
            if cur.rowcount != 1:  # pragma: no cover - defensive
                raise LedgerError("STORE_UNAVAILABLE")
            cur.execute(
                "INSERT INTO execution_markers(marker_id, receipt_id, not_before_at, committed_at) "
                "VALUES (?, ?, ?, ?)",
                (marker_id, receipt_id, _ts(not_before), t2),
            )
            self._audit(cur, "receipt_t2", receipt_id,
                        {"order_id": order_id, "not_before_at": _ts(not_before)})
        return "committed"

    def abandon_receipt(self, receipt_id: str, *,
                        reason_code: str = "RECEIPT_NOT_COMMITTED") -> bool:
        """N00 v2 §4.2(b): release the reservation and cancel the pending order."""
        receipt_id = _id(receipt_id, "receipt_id")
        if reason_code not in REASON_CODES:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        with self._write_txn() as cur:
            row = cur.execute(
                "SELECT abandoned_at, receipt_committed_at FROM decision_receipts "
                "WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise LedgerError("RECEIPT_NOT_COMMITTED")
            abandoned_at, committed_at = row
            if abandoned_at is not None or committed_at is not None:
                return False
            order_id = cur.execute(
                "SELECT order_id FROM paper_orders WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            if order_id is not None:
                self._cancel_in_txn(cur, order_id[0], reason_code)
            cur.execute(
                "UPDATE decision_receipts SET abandoned_at = ? WHERE receipt_id = ? "
                "AND abandoned_at IS NULL",
                (self._now(), receipt_id),
            )
            self._audit(cur, "receipt_abandoned", receipt_id, {"reason_code": reason_code})
        return True

    # -- fill fence (N00 v3 §1) ---------------------------------------------

    def establish_fence(self, receipt_id: str, clock_quality: ClockQuality) -> Fence | None:
        """Sample ``fence_at`` only after a successful committed-marker SELECT.

        The first stored fence wins (``marker_id`` UNIQUE) and is reused by
        every worker and restart; a missing marker forbids every fill.
        """
        receipt_id = _id(receipt_id, "receipt_id")
        with self._write_txn() as cur:
            marker = cur.execute(
                "SELECT marker_id, committed_at FROM execution_markers WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if marker is None or marker[1] is None:
                return None
            marker_id = marker[0]
            existing = cur.execute(
                "SELECT fence_id, fence_at, worker_id, clock_quality FROM fill_fences "
                "WHERE marker_id = ?",
                (marker_id,),
            ).fetchone()
            if existing is None:
                fence_at = self._now()
                fence_id = f"fence:{receipt_id}"
                cur.execute(
                    "INSERT OR IGNORE INTO fill_fences(fence_id, marker_id, receipt_id, fence_at, "
                    "clock_quality, worker_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        fence_id,
                        marker_id,
                        receipt_id,
                        fence_at,
                        _json_dumps({
                            "synced": bool(clock_quality.synced),
                            "source": clock_quality.source,
                            "max_error_ms": int(clock_quality.max_error_ms),
                        }),
                        self._worker_id,
                    ),
                )
                existing = cur.execute(
                    "SELECT fence_id, fence_at, worker_id, clock_quality FROM fill_fences "
                    "WHERE marker_id = ?",
                    (marker_id,),
                ).fetchone()
            stored_quality = json.loads(existing[3])
            # The first stored fence wins as a whole: fence_at *and* the clock
            # quality it was sampled with are reused by every worker/restart.
            quality = ClockQuality(
                synced=bool(stored_quality["synced"]),
                source=str(stored_quality["source"]),
                max_error_ms=int(stored_quality["max_error_ms"]),
            )
            return Fence(
                fence_id=existing[0],
                marker_id=marker_id,
                receipt_id=receipt_id,
                fence_at=_parse_ts(existing[1]),
                clock_quality=quality,
                worker_id=existing[2],
            )

    # -- durable fill journal + liquidity (N06 durability ports) ------------

    def fill_journal(self) -> "SqliteFillJournal":
        return SqliteFillJournal(self)

    def filled_keys(self, order_id: str) -> frozenset[tuple[str, int]]:
        order_id = _id(order_id, "order_id")
        return frozenset(
            (quote_ref, level_index)
            for quote_ref, level_index in self._conn.execute(
                "SELECT quote_ref, level_index FROM fills WHERE order_id = ?", (order_id,)
            )
        )

    def liquidity_used(self, account_id: str, observation_id: str) -> dict[int, Decimal]:
        """Durable per-level consumption across restarts for one observation."""
        account_id = _id(account_id, "account_id")
        used: dict[int, Decimal] = {}
        for level_index, quantity in self._conn.execute(
            "SELECT level_index, quantity FROM fills WHERE account_id = ? AND quote_ref = ?",
            (account_id, observation_id),
        ):
            used[level_index] = used.get(level_index, ZERO) + D(quantity)
        return used

    # -- order lifecycle -----------------------------------------------------

    def order_row(self, order_id: str) -> dict[str, object]:
        order_id = _id(order_id, "order_id")
        row = self._conn.execute("SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_KEY")
        columns = [item[0] for item in self._conn.execute(
            "SELECT * FROM paper_orders LIMIT 0").description]
        return dict(zip(columns, row))

    def receipt_row(self, receipt_id: str) -> dict[str, object]:
        receipt_id = _id(receipt_id, "receipt_id")
        row = self._conn.execute(
            "SELECT * FROM decision_receipts WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_KEY")
        columns = [item[0] for item in self._conn.execute(
            "SELECT * FROM decision_receipts LIMIT 0").description]
        return dict(zip(columns, row))

    def event_use_exists(self, *, run_id: str, scenario_id: str, event_group_id: str,
                         asset: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM event_use WHERE run_id = ? AND scenario_id = ? "
            "AND event_group_id = ? AND asset = ?",
            (_id(run_id, "run_id"), _id(scenario_id, "scenario_id"),
             _id(event_group_id, "event_group_id"), _id(asset, "asset")),
        ).fetchone() is not None

    def reservation_for_order(self, order_id: str) -> ReservationDraft | None:
        """Active or released reservation of one order (for fill-time headroom)."""
        order_id = _id(order_id, "order_id")
        row = self._conn.execute(
            "SELECT reservation_id, kind, asset, quantity, amount FROM reservations "
            "WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        return ReservationDraft(
            reservation_id=row[0], kind=row[1], asset=row[2],
            quantity=D(row[3]), amount=D(row[4]),
        )

    def pending_receipt_ids(self) -> list[str]:
        """Accepted receipts whose T2 never completed (recovery candidates)."""
        return [
            row[0]
            for row in self._conn.execute(
                "SELECT receipt_id FROM decision_receipts "
                "WHERE status = 'accepted' AND receipt_committed_at IS NULL "
                "AND abandoned_at IS NULL"
            )
        ]

    def cancel_pending_order(self, order_id: str, *, reason_code: str,
                             canceled_at: str | datetime | None = None) -> ApplyResult:
        """Cancel an unfilled pending order and release its reservation."""
        order_id = _id(order_id, "order_id")
        if reason_code not in REASON_CODES:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        with self._write_txn() as cur:
            return self._cancel_in_txn(cur, order_id, reason_code,
                                       canceled_at=canceled_at)

    def _cancel_in_txn(self, cur: sqlite3.Cursor, order_id: str, reason_code: str,
                       canceled_at: str | datetime | None = None) -> ApplyResult:
        row = cur.execute(
            "SELECT status, remaining_quantity, requested_quantity, account_id, receipt_id "
            "FROM paper_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("UNKNOWN_KEY")
        status, remaining, _requested, account_id, receipt_id = row
        if status in TERMINAL_ORDER_STATUSES:
            return ApplyResult(
                order_id=order_id,
                previous_status=status,
                status=status,
                applied_fills=0,
                canceled_quantity=ZERO,
                reservation_released=False,
                already_final=True,
            )
        quantity = D(remaining)
        cur.execute(
            "INSERT INTO cancel_events(cancel_id, order_id, quantity, reason_code, canceled_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._next_cancel_id(cur, order_id), order_id, format(quantity, "f"),
             reason_code, _ts(canceled_at) if canceled_at is not None else self._now()),
        )
        cur.execute(
            "UPDATE paper_orders SET status = 'canceled', remaining_quantity = '0' "
            "WHERE order_id = ?",
            (order_id,),
        )
        released = self._release_reservation_in_txn(cur, order_id, reason_code)
        self._bump_revision(cur, account_id)
        self._audit(cur, "order_canceled", order_id,
                    {"reason_code": reason_code, "receipt_id": receipt_id})
        return ApplyResult(
            order_id=order_id,
            previous_status=status,
            status="canceled",
            applied_fills=0,
            canceled_quantity=quantity,
            reservation_released=released,
        )

    # -- fill application: one transaction for the whole outcome -------------

    def apply_execution(self, outcome: ExecutionOutcome) -> ApplyResult:
        """Apply an N06 outcome atomically (fills, fees, cancel, balances).

        Fills keyed by ``(order_id, quote_ref, level_index)``: a replay or a
        re-delivered quote cannot double-count. Sell proceeds credit cash but
        the reservation of *other* orders never counted them, so an unsettled
        sell cannot fund a buy.
        """
        order_id = _id(outcome.order_id, "order_id")
        with self._write_txn() as cur:
            row = cur.execute(
                "SELECT status, side, asset, account_id, receipt_id, requested_quantity, "
                "filled_quantity, remaining_quantity FROM paper_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if row is None:
                raise LedgerError("UNKNOWN_KEY")
            (prev_status, side, asset, account_id, receipt_id,
             requested, filled_now, _remaining_now) = row
            already = {
                (quote_ref, level_index)
                for quote_ref, level_index in cur.execute(
                    "SELECT quote_ref, level_index FROM fills WHERE order_id = ?", (order_id,)
                )
            }
            new_fills = [f for f in outcome.fills if (f.quote_ref, f.level_index) not in already]
            if prev_status in TERMINAL_ORDER_STATUSES:
                if new_fills:
                    raise LedgerError("ORDER_ALREADY_FINAL")
                return ApplyResult(
                    order_id=order_id,
                    previous_status=prev_status,
                    status=prev_status,
                    applied_fills=0,
                    canceled_quantity=ZERO,
                    reservation_released=False,
                    already_final=True,
                )
            marker = cur.execute(
                "SELECT committed_at FROM execution_markers WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            if marker is None:
                # Fail closed: never fill from an uncommitted receipt.
                raise LedgerError("RECEIPT_NOT_COMMITTED")
            account = cur.execute(
                "SELECT cash FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            cash = D(account[0])
            position_row = cur.execute(
                "SELECT quantity, avg_cost FROM positions WHERE account_id = ? AND asset = ?",
                (account_id, asset),
            ).fetchone()
            position = D(position_row[0]) if position_row else ZERO
            avg_cost = D(position_row[1]) if position_row else ZERO
            applied = 0
            for fill in new_fills:
                if fill.order_id != order_id or fill.side != side or fill.asset != asset \
                        or fill.account_id != account_id or fill.receipt_id != receipt_id:
                    raise LedgerError("ACCOUNT_SCOPE_MISMATCH")
                quantity = _dec(fill.quantity, positive=True)
                price = _dec(fill.price, positive=True)
                fee = _dec(fill.fee_jpy)
                notional = quantity * price
                if fill.side == "buy":
                    cost = D(_money(notional + fee, charge=True))
                    cash_new = cash - cost
                    if cash_new < 0:
                        raise LedgerError("INSUFFICIENT_FUNDS")
                    cash = cash_new
                    # Buys move the average cost basis; fees stay separate from
                    # the basis (parent #769 D8: cost/proceeds/realized split).
                    position = position + quantity
                    avg_cost = ((avg_cost * (position - quantity)) + notional) / position
                else:
                    if position - quantity < 0:
                        raise LedgerError("INSUFFICIENT_INVENTORY")
                    position = position - quantity
                    # A sell never changes the basis; proceeds minus fee only
                    # become settled cash here (never while merely reserved).
                    credit = D(_money(notional - fee, charge=False))
                    cash = cash + credit
                    if cash < 0:
                        raise LedgerError("INSUFFICIENT_FUNDS")
                applied += 1
                cur.execute(
                    "INSERT INTO fills(fill_id, order_id, receipt_id, account_id, asset, side, "
                    "quantity, price, fee_jpy, slippage_bps, quote_ref, level_index, "
                    "market_event_at, market_available_at, filled_at, partial, "
                    "execution_model_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _id(fill.fill_id, "fill_id"),
                        order_id,
                        receipt_id,
                        account_id,
                        asset,
                        side,
                        format(quantity, "f"),
                        format(price, "f"),
                        format(fee, "f"),
                        format(_dec(fill.slippage_bps), "f"),
                        _id(fill.quote_ref, "quote_ref"),
                        int(fill.level_index),
                        _ts(fill.market_event_at),
                        _ts(fill.market_available_at),
                        _ts(fill.filled_at),
                        int(bool(fill.partial)),
                        EXECUTION_MODEL_VERSION,
                    ),
                )
            filled_total = D(filled_now) + sum((f.quantity for f in new_fills), ZERO)
            canceled_total = D(cur.execute(
                "SELECT COALESCE(SUM(quantity), '0') FROM cancel_events WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0])
            if outcome.canceled_quantity > 0:
                cancel_code = (
                    outcome.reason_codes[0] if outcome.reason_codes else "IOC_REMAINDER_CANCELED"
                )
                if cancel_code not in REASON_CODES:
                    raise LedgerError("VALUE_OUT_OF_RANGE")
                cur.execute(
                    "INSERT INTO cancel_events(cancel_id, order_id, quantity, reason_code, "
                    "canceled_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        self._next_cancel_id(cur, order_id),
                        order_id,
                        format(_dec(outcome.canceled_quantity), "f"),
                        cancel_code,
                        self._now(),
                    ),
                )
                canceled_total = D(canceled_total) + _dec(outcome.canceled_quantity)
            if D(requested) != filled_total + D(outcome.remaining_quantity) + canceled_total:
                raise LedgerError("VALUE_OUT_OF_RANGE")
            new_status = outcome.status
            if new_status not in ORDER_STATUSES:
                raise LedgerError("VALUE_OUT_OF_RANGE")
            cur.execute(
                "UPDATE paper_orders SET status = ?, filled_quantity = ?, remaining_quantity = ? "
                "WHERE order_id = ?",
                (
                    new_status,
                    format(filled_total, "f"),
                    format(_dec(outcome.remaining_quantity), "f"),
                    order_id,
                ),
            )
            if new_status in TERMINAL_ORDER_STATUSES:
                self._release_reservation_in_txn(cur, order_id, "IOC_REMAINDER_CANCELED")
            if position == 0:
                cur.execute(
                    "DELETE FROM positions WHERE account_id = ? AND asset = ?", (account_id, asset)
                )
            else:
                cur.execute(
                    "INSERT INTO positions(account_id, asset, quantity, avg_cost) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(account_id, asset) DO UPDATE SET quantity = excluded.quantity, "
                    "avg_cost = excluded.avg_cost",
                    (account_id, asset, format(position, "f"), format(avg_cost, "f")),
                )
            cur.execute(
                "UPDATE accounts SET cash = ?, revision = revision + 1 WHERE account_id = ?",
                (format(cash, "f"), account_id),
            )
            self._audit(cur, "execution_applied", order_id,
                        {"fills": applied, "status": new_status, "receipt_id": receipt_id})
            released = new_status in TERMINAL_ORDER_STATUSES
            return ApplyResult(
                order_id=order_id,
                previous_status=prev_status,
                status=new_status,
                applied_fills=applied,
                canceled_quantity=D(outcome.canceled_quantity),
                reservation_released=released,
            )

    def _release_reservation_in_txn(self, cur: sqlite3.Cursor, order_id: str,
                                    reason_code: str) -> bool:
        cur.execute(
            "UPDATE reservations SET released_at = ?, release_reason = ? "
            "WHERE order_id = ? AND released_at IS NULL",
            (self._now(), reason_code, order_id),
        )
        return cur.rowcount == 1

    @staticmethod
    def _next_cancel_id(cur: sqlite3.Cursor, order_id: str) -> str:
        count = cur.execute(
            "SELECT COUNT(*) FROM cancel_events WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        return f"cancel:{order_id}:{int(count) + 1}"

    def _bump_revision(self, cur: sqlite3.Cursor, account_id: str) -> None:
        cur.execute(
            "UPDATE accounts SET revision = revision + 1 WHERE account_id = ?", (account_id,)
        )

    # -- draft validation ----------------------------------------------------

    def _receipt_values(self, receipt: ReceiptDraft, *, order_ref: str | None) -> dict[str, object]:
        if receipt.status not in RECEIPT_STATUSES:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        if receipt.status == "accepted" and order_ref is None:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        if receipt.status != "accepted" and order_ref is not None:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        codes = _code_list(receipt.reason_codes)
        if receipt.status == "accepted" and not codes:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        signal_refs = _id_list(receipt.signal_refs, low=0, high=20)
        if receipt.status == "accepted" and not signal_refs:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        market_refs = _id_list(receipt.market_refs, low=0, high=20)
        if receipt.status == "accepted" and not market_refs:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        cutoff = receipt.input_cutoff_sequence
        if not isinstance(cutoff, int) or isinstance(cutoff, bool) or cutoff < 1:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        decision_quote: str | None = None
        if receipt.decision_quote is not None:
            source, asset, moment, digest = receipt.decision_quote
            decision_quote = _json_dumps([
                _id(source, "source"), _id(asset, "asset"), _ts(moment),
                _validate_digest(digest),
            ])
        return {
            "run_id": _id(receipt.run_id, "run_id"),
            "scenario_id": _id(receipt.scenario_id, "scenario_id"),
            "decision_at": _ts(receipt.decision_at),
            "manager_seen_at": _ts(receipt.manager_seen_at),
            "cutoff": cutoff,
            "signal_refs": _json_dumps(signal_refs),
            "market_refs": _json_dumps(market_refs),
            "decision_quote": decision_quote,
            "policy_version": _id(receipt.policy_version, "policy_version"),
            "risk_policy_version": _id(receipt.risk_policy_version, "risk_policy_version"),
            "reason_codes": _json_dumps(codes),
            "supersedes": _id(receipt.supersedes_receipt_id, "supersedes")
            if receipt.supersedes_receipt_id is not None else None,
        }

    def _order_values(self, order: OrderDraft, *, receipt_id: str) -> dict[str, object]:
        if order.side not in {"buy", "sell"}:
            raise LedgerError("VALUE_OUT_OF_RANGE")
        del receipt_id
        return {
            "asset": _id(order.asset, "asset"),
            "side": order.side,
            "requested": format(_dec(order.requested_quantity, positive=True), "f"),
            "expires_at": _ts(order.expires_at),
        }


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or not _HEX64_RE.match(value):
        raise LedgerError("VALUE_OUT_OF_RANGE")
    return value


class SqliteFillJournal:
    """Durable ``execution.FillJournal`` backed by the fills table.

    ``record_fills`` is deliberately a no-op: N06 keeps its in-memory journal
    for one evaluation, while durability belongs to
    :meth:`NewsLedger.apply_execution`, the only place that may insert fill
    rows (inside the single apply transaction).
    """

    def __init__(self, ledger: NewsLedger) -> None:
        self._ledger = ledger

    def filled_keys(self, order_id: str) -> frozenset[tuple[str, int]]:
        return self._ledger.filled_keys(order_id)

    def record_fills(self, order_id: str, fills: object) -> None:
        return None


def recover(ledger: NewsLedger, *, execution_delay_ms: str | Decimal = ZERO,
            abandon_expired: bool = True) -> dict[str, list[str]]:
    """Recovery pass (N00 v2 §4.2): re-run T2 or abandon, never backfill fills."""
    committed: list[str] = []
    abandoned: list[str] = []
    for receipt_id in ledger.pending_receipt_ids():
        result = ledger.commit_receipt(receipt_id, execution_delay_ms=execution_delay_ms)
        if result == "committed":
            committed.append(receipt_id)
        elif result == "expired_before_commit" and abandon_expired:
            ledger.abandon_receipt(receipt_id)
            abandoned.append(receipt_id)
    return {"committed": committed, "abandoned": abandoned}
