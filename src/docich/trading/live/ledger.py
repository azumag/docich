"""LIVE ledger (sqlite).  Intents are persisted BEFORE anything is sent.

Single writer: every write goes through ``_tx`` (``BEGIN IMMEDIATE``) and, when
a lease is given, re-checks the writer lease inside the same transaction.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator

from . import safety
from .canonical import CanonicalError, canonical_json, decimal_text, sha256_hex
from .lease import LeaseLost, WriterLease, verify_in_tx
from .paths import LiveLayout, ensure_live_root
from .policy import (LivePolicy, LivePolicySet, policy_from_payload,
                     policy_set_from_policies)
from .safety import SafetyReason

LIVE_SCHEMA_VERSION = 1
FAULT_HOOK: Callable[[str], None] | None = None  # tests only

_ZERO_HASH = "0" * 64
NO_LEDGER = "no-ledger"

_DDL = """
BEGIN;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_uid TEXT NOT NULL UNIQUE,
  occurred_at REAL NOT NULL, kind TEXT NOT NULL, entity_type TEXT, entity_id TEXT,
  payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE policies(policy_digest TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE policy_sets(set_digest TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE policy_activations(
  activation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  set_digest TEXT NOT NULL REFERENCES policy_sets, previous_set_digest TEXT REFERENCES policy_sets,
  reason TEXT NOT NULL CHECK(reason IN('owner_initial','owner_manual','promotion','rollback')),
  actor TEXT NOT NULL CHECK(actor IN('owner','promotion_judge')),
  changed_symbols TEXT NOT NULL, evidence_digest TEXT, enable_digest TEXT,
  lease_epoch INTEGER NOT NULL, activated_at REAL NOT NULL, note TEXT);
CREATE TABLE positions(
  position_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
  entry_policy_digest TEXT NOT NULL REFERENCES policies,
  status TEXT NOT NULL CHECK(status IN('open','closed','residual_inventory')),
  opened_at REAL NOT NULL, closed_at REAL);
CREATE UNIQUE INDEX one_open_position ON positions(symbol, entry_policy_digest) WHERE status='open';
CREATE TABLE intents(
  intent_id TEXT PRIMARY KEY, decision_key TEXT NOT NULL UNIQUE, client_order_id TEXT UNIQUE,
  symbol TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN('buy','sell')),
  order_type TEXT NOT NULL CHECK(order_type IN('limit','market')),
  amount TEXT NOT NULL, price TEXT,
  policy_digest TEXT NOT NULL REFERENCES policies, position_id TEXT NOT NULL REFERENCES positions,
  rule_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN('PLANNED','SUBMITTING','UNKNOWN_SUBMISSION','ACKED',
     'PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED','ABANDONED','NOT_CREATED')),
  state_version INTEGER NOT NULL DEFAULT 0, lease_epoch INTEGER NOT NULL,
  exchange_order_id TEXT UNIQUE, reason_code TEXT, evidence TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE UNIQUE INDEX one_open_intent ON intents(symbol, side)
  WHERE state IN('PLANNED','SUBMITTING','UNKNOWN_SUBMISSION','ACKED','PARTIALLY_FILLED');
CREATE TABLE orders(
  exchange_order_id TEXT PRIMARY KEY, intent_id TEXT UNIQUE REFERENCES intents,
  symbol TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL, amount TEXT NOT NULL, price TEXT,
  status TEXT NOT NULL CHECK(status IN('INACTIVE','UNFILLED','PARTIALLY_FILLED','FULLY_FILLED',
     'CANCELED_UNFILLED','CANCELED_PARTIALLY_FILLED','REJECTED')),
  executed_amount TEXT NOT NULL, remaining_amount TEXT NOT NULL, average_price TEXT,
  raw_json TEXT, first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL);
CREATE TABLE fills(
  fill_id TEXT PRIMARY KEY, exchange_order_id TEXT NOT NULL REFERENCES orders,
  intent_id TEXT REFERENCES intents, position_id TEXT REFERENCES positions,
  symbol TEXT NOT NULL, side TEXT NOT NULL, amount TEXT NOT NULL, price TEXT NOT NULL,
  quote_notional TEXT NOT NULL,
  fee_status TEXT NOT NULL CHECK(fee_status IN('known','unknown')),
  fee_amount TEXT, fee_currency TEXT, maker_taker TEXT CHECK(maker_taker IN('maker','taker','unknown')),
  raw_json TEXT, executed_at REAL NOT NULL, recorded_at REAL NOT NULL,
  CHECK((fee_status='known' AND fee_amount IS NOT NULL AND fee_currency IS NOT NULL)
     OR (fee_status='unknown' AND fee_amount IS NULL)));
CREATE TABLE cashflows(
  cashflow_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN('deposit','withdrawal','adjustment')),
  currency TEXT NOT NULL, amount TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN('owner_manual','exchange_history')),
  occurred_at REAL NOT NULL, recorded_at REAL NOT NULL, note TEXT);
CREATE TABLE writer_lease(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), holder_id TEXT NOT NULL, epoch INTEGER NOT NULL,
  pid INTEGER NOT NULL, acquired_at REAL NOT NULL, released_at REAL);
CREATE TABLE safety_trips(
  trip_id INTEGER PRIMARY KEY AUTOINCREMENT,
  reason TEXT NOT NULL CHECK(reason IN('unknown_order','balance_mismatch','data_anomaly',
     'fee_missing','enable_missing','lease_lost')),
  detail TEXT NOT NULL, tripped_at REAL NOT NULL,
  cleared_at REAL, cleared_by TEXT, clear_note TEXT, clear_evidence TEXT);
CREATE UNIQUE INDEX one_active_trip ON safety_trips(reason) WHERE cleared_at IS NULL;
"""
for _table in ("events", "policies", "policy_sets", "policy_activations", "cashflows"):
    _DDL += (f"CREATE TRIGGER {_table}_no_update BEFORE UPDATE ON {_table} "
             f"BEGIN SELECT RAISE(ABORT,'{_table} is append-only'); END;\n"
             f"CREATE TRIGGER {_table}_no_delete BEFORE DELETE ON {_table} "
             f"BEGIN SELECT RAISE(ABORT,'{_table} is append-only'); END;\n")
_DDL += """
CREATE TRIGGER fills_no_delete BEFORE DELETE ON fills BEGIN SELECT RAISE(ABORT,'fills are append-only'); END;
CREATE TRIGGER safety_trips_no_delete BEFORE DELETE ON safety_trips BEGIN SELECT RAISE(ABORT,'trips are kept'); END;
CREATE TRIGGER safety_trips_clear_only BEFORE UPDATE ON safety_trips
  WHEN OLD.cleared_at IS NOT NULL OR NEW.trip_id!=OLD.trip_id OR NEW.reason!=OLD.reason
    OR NEW.detail!=OLD.detail OR NEW.tripped_at!=OLD.tripped_at
  BEGIN SELECT RAISE(ABORT,'a trip can only be cleared once'); END;
CREATE TRIGGER sell_uses_entry_policy BEFORE INSERT ON intents
  WHEN NEW.side='sell' AND NEW.policy_digest != (SELECT entry_policy_digest FROM positions WHERE position_id=NEW.position_id)
  BEGIN SELECT RAISE(ABORT,'a sell must use the policy the position was entered under'); END;
PRAGMA user_version = 1;
COMMIT;
"""

_TRANSITIONS = {
    "PLANNED": {"SUBMITTING", "ABANDONED"},
    "SUBMITTING": {"ACKED", "REJECTED", "UNKNOWN_SUBMISSION"},
    "UNKNOWN_SUBMISSION": {"ACKED", "NOT_CREATED"},
    "ACKED": {"PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELED"},
}
_OPEN_STATES = ("PLANNED", "SUBMITTING", "UNKNOWN_SUBMISSION", "ACKED", "PARTIALLY_FILLED")

LIVE_EVENT_KINDS = frozenset({"live_decision", "shadow_fill", "shadow_residual",
                              "worker_state"})


class LiveLedgerError(RuntimeError):
    pass


class SafetyBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class IntentRequest:
    decision_key: str
    symbol: str
    side: str
    order_type: str
    amount: str
    policy_digest: str
    rule_id: str
    price: str | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class Intent:
    intent_id: str
    decision_key: str
    symbol: str
    side: str
    order_type: str
    amount: str
    price: str | None
    policy_digest: str
    position_id: str
    rule_id: str
    state: str
    state_version: int
    exchange_order_id: str | None
    reason_code: str | None
    client_order_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(frozen=True)
class OrderObservation:
    exchange_order_id: str
    symbol: str
    side: str
    order_type: str
    amount: str
    status: str
    executed_amount: str
    remaining_amount: str
    intent_id: str | None = None
    price: str | None = None
    average_price: str | None = None
    raw_json: str | None = None


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    exchange_order_id: str
    symbol: str
    side: str
    amount: str
    price: str
    quote_notional: str
    executed_at: float
    fee_status: str = "unknown"
    fee_amount: str | None = None
    fee_currency: str | None = None
    maker_taker: str = "unknown"
    raw_json: str | None = None


@dataclass(frozen=True)
class Cashflow:
    cashflow_id: str
    kind: str
    currency: str
    amount: str
    source: str
    occurred_at: float
    note: str | None = None


@dataclass(frozen=True)
class Event:
    event_id: int
    occurred_at: float
    kind: str
    entity_type: str | None
    entity_id: str | None
    payload: str


_INTENT_COLS = ("intent_id, decision_key, symbol, side, order_type, amount, price, policy_digest, "
                "position_id, rule_id, state, state_version, exchange_order_id, reason_code, "
                "client_order_id, created_at, updated_at")


def _fault(point: str) -> None:
    if FAULT_HOOK is not None:
        FAULT_HOOK(point)


class LiveLedger:
    def __init__(self, conn: sqlite3.Connection, path: Path):
        self._conn = conn
        self.path = path

    # -- opening ----------------------------------------------------------
    @classmethod
    def open(cls, layout: LiveLayout, *, paper_dir, now: float,
             require_new: bool = False) -> "LiveLedger":
        """Open (and migrate) the LIVE ledger.

        ``require_new`` makes the first creation atomic: the path is created with
        ``O_CREAT|O_EXCL`` after live-layout validation and before sqlite opens it.
        An existing path (including another process's empty ledger) raises
        ``FileExistsError`` and is never adopted or written.
        """
        ensure_live_root(layout, paper_dir=paper_dir)
        old_umask = os.umask(0o077)
        conn = None
        try:
            if require_new:
                fd = os.open(layout.ledger_path,
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
            conn = sqlite3.connect(str(layout.ledger_path), isolation_level=None, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            ledger = cls(conn, layout.ledger_path)
            ledger._migrate(now)
        except BaseException:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            raise
        finally:
            os.umask(old_umask)
        os.chmod(layout.ledger_path, 0o600)
        return ledger

    @classmethod
    def open_readonly(cls, layout: LiveLayout) -> "LiveLedger":
        if not layout.ledger_path.is_file():
            raise LiveLedgerError("ledger does not exist")
        conn = sqlite3.connect(f"file:{layout.ledger_path}?mode=ro", uri=True, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        return cls(conn, layout.ledger_path)

    def close(self) -> None:
        self._conn.close()

    def _migrate(self, now: float) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        tables = self._conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if version > LIVE_SCHEMA_VERSION:
            raise LiveLedgerError("schema_too_new")
        if version == 0:
            if tables:
                raise LiveLedgerError("wrong_kind: not a LIVE ledger")
            self._conn.executescript(_DDL)
            with self._tx(None) as cur:
                cur.execute("INSERT INTO meta(key,value) VALUES('kind','docich-live-ledger')")
                cur.execute("INSERT INTO meta(key,value) VALUES('ledger_id',?)", (uuid.uuid4().hex,))
                cur.execute("INSERT INTO meta(key,value) VALUES('created_at',?)", (repr(float(now)),))
                self._emit(cur, "ledger_created", None, None, {"schema_version": LIVE_SCHEMA_VERSION}, now)
            return
        row = self._conn.execute("SELECT value FROM meta WHERE key='kind'").fetchone() if tables else None
        if row is None or row[0] != "docich-live-ledger":
            raise LiveLedgerError("wrong_kind: not a LIVE ledger")

    # -- transactions -----------------------------------------------------
    @contextmanager
    def _tx(self, lease: WriterLease | None) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            if lease is not None:
                verify_in_tx(cur, lease)
            yield cur
            cur.execute("COMMIT")
        except BaseException:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    @contextmanager
    def read_cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def _emit(self, cur, kind: str, entity_type, entity_id, payload: dict, now: float) -> None:
        row = cur.execute("SELECT hash FROM events ORDER BY event_id DESC LIMIT 1").fetchone()
        prev = row[0] if row else _ZERO_HASH
        uid = uuid.uuid4().hex
        body = canonical_json_text({"uid": uid, "at": repr(float(now)), "kind": kind,
                                    "entity_type": entity_type, "entity_id": entity_id,
                                    "payload": payload})
        digest = sha256_hex((prev + body).encode("utf-8"))
        cur.execute("INSERT INTO events(event_uid, occurred_at, kind, entity_type, entity_id, "
                    "payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, float(now), kind, entity_type, entity_id, body, prev, digest))

    def events(self, *, after_id: int = 0, limit: int = 100) -> list[Event]:
        with self.read_cursor() as cur:
            rows = cur.execute("SELECT event_id, occurred_at, kind, entity_type, entity_id, payload "
                               "FROM events WHERE event_id>? ORDER BY event_id LIMIT ?",
                               (int(after_id), int(limit))).fetchall()
        return [Event(*row) for row in rows]

    def verify_event_chain(self) -> bool:
        prev = _ZERO_HASH
        with self.read_cursor() as cur:
            for stored_prev, body, digest in cur.execute(
                    "SELECT prev_hash, payload, hash FROM events ORDER BY event_id"):
                if stored_prev != prev or sha256_hex((prev + body).encode("utf-8")) != digest:
                    return False
                prev = digest
        return True

    # -- lease ------------------------------------------------------------
    def claim_lease_row(self, *, holder_id: str, pid: int, now: float) -> int:
        with self._tx(None) as cur:
            row = cur.execute("SELECT epoch, released_at FROM writer_lease WHERE singleton=1").fetchone()
            epoch = (row[0] if row else 0) + 1
            cur.execute("INSERT INTO writer_lease(singleton, holder_id, epoch, pid, acquired_at, released_at) "
                        "VALUES(1,?,?,?,?,NULL) ON CONFLICT(singleton) DO UPDATE SET holder_id=excluded.holder_id, "
                        "epoch=excluded.epoch, pid=excluded.pid, acquired_at=excluded.acquired_at, released_at=NULL",
                        (holder_id, epoch, int(pid), float(now)))
            self._emit(cur, "lease_acquired", "lease", holder_id,
                       {"epoch": epoch, "takeover": bool(row and row[1] is None)}, now)
        return epoch

    def release_lease_row(self, lease: WriterLease, *, now: float) -> None:
        with self._tx(lease) as cur:
            cur.execute("UPDATE writer_lease SET released_at=? WHERE singleton=1", (float(now),))
            self._emit(cur, "lease_released", "lease", lease.holder_id, {"epoch": lease.epoch}, now)

    # -- policies ---------------------------------------------------------
    def store_policy_set(self, lease: WriterLease, ps: LivePolicySet, *, now: float) -> str:
        with self._tx(lease) as cur:
            for policy in ps.policies.values():
                cur.execute("INSERT OR IGNORE INTO policies(policy_digest, payload, created_at) VALUES (?,?,?)",
                            (policy.digest, canonical_json_text(policy.payload()), float(now)))
            cur.execute("INSERT OR IGNORE INTO policy_sets(set_digest, payload, created_at) VALUES (?,?,?)",
                        (ps.digest, canonical_json_text(ps.payload()), float(now)))
            self._emit(cur, "policy_set_stored", "policy_set", ps.digest, {"symbols": sorted(ps.policies)}, now)
        return ps.digest

    def load_policy(self, digest: str) -> LivePolicy:
        with self.read_cursor() as cur:
            row = cur.execute("SELECT payload FROM policies WHERE policy_digest=?", (digest,)).fetchone()
        if row is None:
            raise LiveLedgerError("unknown policy digest")
        import json
        policy = policy_from_payload(json.loads(row[0]))
        if policy.digest != digest:
            raise LiveLedgerError("stored policy does not match its digest")
        return policy

    def load_policy_set(self, set_digest: str) -> LivePolicySet:
        import json
        with self.read_cursor() as cur:
            row = cur.execute("SELECT payload FROM policy_sets WHERE set_digest=?", (set_digest,)).fetchone()
        if row is None:
            raise LiveLedgerError("unknown policy set digest")
        mapping = json.loads(row[0])["symbols"]
        ps = policy_set_from_policies({sym: self.load_policy(pol) for sym, pol in mapping.items()})
        if ps.digest != set_digest:
            raise LiveLedgerError("stored policy set does not match its digest")
        return ps

    def active_policy_set_digest(self) -> str | None:
        with self.read_cursor() as cur:
            row = cur.execute("SELECT set_digest FROM policy_activations ORDER BY activation_id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def _activate(self, lease, ps, *, reason, actor, evidence_digest, enable_digest, note, now,
                  require_empty: bool = False) -> None:
        self.store_policy_set(lease, ps, now=now)
        with self._tx(lease) as cur:
            prev = cur.execute("SELECT set_digest FROM policy_activations ORDER BY activation_id DESC LIMIT 1").fetchone()
            if require_empty and prev is not None:
                raise LiveLedgerError("an active policy set already exists")
            if reason in ("promotion", "rollback") and cur.execute(
                    "SELECT 1 FROM policy_activations WHERE reason='owner_initial'").fetchone() is None:
                raise LiveLedgerError("no owner_initial activation recorded")
            changed = sorted(ps.policies)
            if prev:
                old = cur.execute("SELECT payload FROM policy_sets WHERE set_digest=?", (prev[0],)).fetchone()
                import json
                old_map = json.loads(old[0])["symbols"]
                changed = sorted(s for s in set(old_map) | set(ps.policies)
                                 if old_map.get(s) != (ps.policies[s].digest if s in ps.policies else None))
            cur.execute("INSERT INTO policy_activations(set_digest, previous_set_digest, reason, actor, "
                        "changed_symbols, evidence_digest, enable_digest, lease_epoch, activated_at, note) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (ps.digest, prev[0] if prev else None, reason, actor, ",".join(changed),
                         evidence_digest, enable_digest, lease.epoch, float(now), note))
            self._emit(cur, "policy_activated", "policy_set", ps.digest,
                       {"reason": reason, "actor": actor, "changed": changed}, now)

    def record_owner_activation(self, lease, ps: LivePolicySet, *, operator: str, note: str,
                                now: float) -> None:
        """HUMAN-ONLY (owner operation).  The on/off control file is not part of the record."""
        if not operator or not note:
            raise ValueError("operator and note are required")
        first = self.active_policy_set_digest() is None
        self._activate(lease, ps, reason="owner_initial" if first else "owner_manual", actor="owner",
                       evidence_digest=None, enable_digest=None, note=f"{operator}: {note}"[:500], now=now)

    def record_initial_activation(self, lease, ps: LivePolicySet, *, operator: str, note: str,
                                  now: float) -> None:
        """HUMAN-ONLY: the first activation; never replaces a different active policy."""
        if not operator or not note:
            raise ValueError("operator and note are required")
        with self._tx(lease) as cur:
            if cur.execute("SELECT 1 FROM policy_activations LIMIT 1").fetchone() is not None:
                raise LiveLedgerError("an active policy set already exists")
        self._activate(lease, ps, reason="owner_initial", actor="owner", evidence_digest=None,
                       enable_digest=None, note=f"{operator}: {note}"[:500], now=now,
                       require_empty=True)

    def record_activation(self, lease, ps: LivePolicySet, *, reason: str, evidence_digest: str, now: float) -> None:
        if reason not in ("promotion", "rollback"):
            raise ValueError("reason must be promotion or rollback")
        self._activate(lease, ps, reason=reason, actor="promotion_judge", evidence_digest=evidence_digest,
                       enable_digest=None, note=None, now=now)

    # -- intents ----------------------------------------------------------
    def _intent(self, cur, intent_id: str) -> Intent:
        row = cur.execute(f"SELECT {_INTENT_COLS} FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise LiveLedgerError("unknown intent")
        return Intent(*row)

    def create_intent(self, lease: WriterLease, req: IntentRequest, *, now: float) -> Intent:
        try:
            amount = decimal_text(req.amount, name="amount")
            price = None if req.price is None else decimal_text(req.price, name="price")
        except CanonicalError as exc:
            raise LiveLedgerError(str(exc)) from exc
        if req.order_type == "limit" and price is None:
            raise LiveLedgerError("a limit intent needs a price")
        if not req.decision_key or len(req.decision_key) > 200 or not req.rule_id:
            raise LiveLedgerError("decision_key and rule_id are required")
        with self._tx(lease) as cur:
            row = cur.execute(f"SELECT {_INTENT_COLS} FROM intents WHERE decision_key=?",
                              (req.decision_key,)).fetchone()
            if row is not None:
                existing = Intent(*row)
                if (existing.symbol, existing.side, existing.order_type, existing.amount, existing.price,
                        existing.policy_digest, existing.rule_id) != (
                        req.symbol, req.side, req.order_type, amount, price, req.policy_digest, req.rule_id):
                    raise LiveLedgerError("decision_key already used with different content")
                return existing
            if cur.execute("SELECT 1 FROM policies WHERE policy_digest=?", (req.policy_digest,)).fetchone() is None:
                raise LiveLedgerError("unknown policy digest")
            position = cur.execute("SELECT position_id FROM positions WHERE symbol=? AND entry_policy_digest=? "
                                   "AND status='open'", (req.symbol, req.policy_digest)).fetchone()
            if position is None:
                if req.side == "sell":
                    raise LiveLedgerError("no open position under that policy")
                position_id = uuid.uuid4().hex
                cur.execute("INSERT INTO positions(position_id, symbol, entry_policy_digest, status, opened_at) "
                            "VALUES (?,?,?,'open',?)", (position_id, req.symbol, req.policy_digest, float(now)))
            else:
                position_id = position[0]
            intent_id = uuid.uuid4().hex
            try:
                cur.execute("INSERT INTO intents(intent_id, decision_key, client_order_id, symbol, side, order_type, "
                            "amount, price, policy_digest, position_id, rule_id, state, lease_epoch, created_at, "
                            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'PLANNED',?,?,?)",
                            (intent_id, req.decision_key, req.client_order_id, req.symbol, req.side, req.order_type,
                             amount, price, req.policy_digest, position_id, req.rule_id, lease.epoch,
                             float(now), float(now)))
            except sqlite3.IntegrityError as exc:
                raise LiveLedgerError(f"intent rejected: {exc}") from exc
            self._emit(cur, "intent_created", "intent", intent_id,
                       {"symbol": req.symbol, "side": req.side, "rule_id": req.rule_id}, now)
            _fault("intent_inserted")
            return self._intent(cur, intent_id)

    def _move(self, cur, lease, intent_id, expected_state, to_state, now, *, exchange_order_id=None,
              reason_code=None, evidence=None) -> Intent:
        current = self._intent(cur, intent_id)
        if current.state != expected_state:
            raise LiveLedgerError(f"intent is {current.state}, expected {expected_state}")
        if to_state not in _TRANSITIONS.get(current.state, set()):
            raise LiveLedgerError(f"transition {current.state} -> {to_state} is not allowed")
        if current.state == "UNKNOWN_SUBMISSION" and to_state == "NOT_CREATED" and not evidence:
            raise LiveLedgerError("NOT_CREATED needs evidence that the exchange has no such order")
        cur.execute("UPDATE intents SET state=?, state_version=state_version+1, lease_epoch=?, updated_at=?, "
                    "exchange_order_id=COALESCE(?, exchange_order_id), reason_code=COALESCE(?, reason_code), "
                    "evidence=COALESCE(?, evidence) WHERE intent_id=?",
                    (to_state, lease.epoch, float(now), exchange_order_id, reason_code, evidence, intent_id))
        self._emit(cur, "intent_state", "intent", intent_id,
                   {"from": current.state, "to": to_state, "reason": reason_code}, now)
        return self._intent(cur, intent_id)

    def get_intent(self, intent_id: str) -> Intent | None:
        with self.read_cursor() as cur:
            row = cur.execute(f"SELECT {_INTENT_COLS} FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        return Intent(*row) if row else None

    def list_intents(self, *, states: tuple[str, ...]) -> list[Intent]:
        """Read-only; oldest first."""
        if not states:
            return []
        marks = ",".join("?" for _ in states)
        with self.read_cursor() as cur:
            rows = cur.execute(f"SELECT {_INTENT_COLS} FROM intents WHERE state IN ({marks}) "
                               "ORDER BY created_at, intent_id", tuple(states)).fetchall()
        return [Intent(*row) for row in rows]

    def mark_submitting(self, lease: WriterLease, intent_id: str, *, now: float) -> Intent:
        """The send may happen only after this returns (the commit is the barrier)."""
        with self._tx(lease) as cur:
            current = self._intent(cur, intent_id)
            if current.side == "buy":
                if safety.active_trips(cur) or safety.unresolved_unknowns(cur):
                    raise SafetyBlocked("safety stop is active: new buys are blocked")
            intent = self._move(cur, lease, intent_id, "PLANNED", "SUBMITTING", now)
        _fault("after_submitting_commit")
        return intent

    def transition_intent(self, lease, intent_id, *, expected_state, to_state, now,
                          exchange_order_id=None, reason_code=None, evidence=None) -> Intent:
        with self._tx(lease) as cur:
            return self._move(cur, lease, intent_id, expected_state, to_state, now,
                              exchange_order_id=exchange_order_id, reason_code=reason_code, evidence=evidence)

    def mark_in_flight_unknown(self, lease, *, now: float) -> tuple[str, ...]:
        with self._tx(lease) as cur:
            ids = [row[0] for row in cur.execute("SELECT intent_id FROM intents WHERE state='SUBMITTING'")]
            for intent_id in ids:
                self._move(cur, lease, intent_id, "SUBMITTING", "UNKNOWN_SUBMISSION", now,
                           reason_code="restart_in_flight")
        return tuple(ids)

    def close_position(self, lease, position_id: str, *, now: float) -> None:
        with self._tx(lease) as cur:
            if cur.execute("SELECT 1 FROM intents WHERE position_id=? AND state IN (?,?,?,?,?)",
                           (position_id, *_OPEN_STATES)).fetchone():
                raise LiveLedgerError("position still has open intents")
            cur.execute("UPDATE positions SET status='closed', closed_at=? WHERE position_id=? AND status='open'",
                        (float(now), position_id))
            self._emit(cur, "position_closed", "position", position_id, {}, now)

    def mark_residual_inventory(self, lease, position_id: str, *, now: float,
                                min_amount: Decimal, amount_step: Decimal) -> None:
        if lease is None:
            raise ValueError("a writer lease is required")
        if (not isinstance(min_amount, Decimal) or not isinstance(amount_step, Decimal)
                or not min_amount.is_finite() or not amount_step.is_finite()
                or min_amount <= 0 or amount_step <= 0):
            raise ValueError("min_amount and amount_step must be positive decimals")
        with self._tx(lease) as cur:
            row = cur.execute("SELECT status FROM positions WHERE position_id=?", (position_id,)).fetchone()
            if row is None or row[0] != "open":
                raise LiveLedgerError("position is not open")
            if cur.execute("SELECT 1 FROM intents WHERE position_id=? AND state IN (?,?,?,?,?)",
                           (position_id, *_OPEN_STATES)).fetchone():
                raise LiveLedgerError("position still has open intents")
            net = Decimal(0)
            for symbol, side, amount, fee_status, fee_amount, fee_currency in cur.execute(
                    "SELECT symbol, side, amount, fee_status, fee_amount, fee_currency FROM fills "
                    "WHERE position_id=?", (position_id,)):
                net += Decimal(amount) if side == "buy" else -Decimal(amount)
                if fee_status == "known" and fee_currency == symbol.split("/")[0]:
                    net -= Decimal(fee_amount)
            if not (net > 0 and (net // amount_step) * amount_step < min_amount):
                raise LiveLedgerError("position is not residual inventory")
            cur.execute("UPDATE positions SET status='residual_inventory', closed_at=? "
                        "WHERE position_id=? AND status='open'", (float(now), position_id))
            self._emit(cur, "position_residual", "position", position_id,
                       {"net_base": decimal_text(net, name="net_base")}, now)

    def record_live_event(self, lease, kind: str, payload: dict, *, now: float) -> None:
        if lease is None:
            raise ValueError("a writer lease is required")
        if not isinstance(kind, str) or kind not in LIVE_EVENT_KINDS:
            raise ValueError("kind is not an allowed live event kind")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        def check(value: object) -> None:
            if isinstance(value, (str, int)) or value is None:
                return
            if isinstance(value, (float, Decimal)):
                raise ValueError("numbers must be passed as strings or integers")
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ValueError("payload keys must be strings")
                    check(item)
                return
            if isinstance(value, list):
                for item in value:
                    check(item)
                return
            raise ValueError("payload values must be strings, integers, null, dictionaries or lists")

        check(payload)
        try:
            body = canonical_json(payload)
        except CanonicalError as exc:
            raise ValueError(str(exc)) from exc
        if len(body) > 16384:
            raise ValueError("payload exceeds 16384 bytes")
        with self._tx(lease) as cur:
            self._emit(cur, kind, "live", None, payload, now)

    # -- exchange facts -----------------------------------------------------
    def record_order_observation(self, lease, obs: OrderObservation, *, now: float) -> None:
        with self._tx(lease) as cur:
            known_intent = obs.intent_id
            if known_intent is None:
                row = cur.execute("SELECT intent_id FROM orders WHERE exchange_order_id=?",
                                  (obs.exchange_order_id,)).fetchone()
                known_intent = row[0] if row else None
            cur.execute("INSERT INTO orders(exchange_order_id, intent_id, symbol, side, order_type, amount, price, "
                        "status, executed_amount, remaining_amount, average_price, raw_json, first_seen_at, "
                        "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(exchange_order_id) DO UPDATE "
                        "SET status=excluded.status, executed_amount=excluded.executed_amount, "
                        "remaining_amount=excluded.remaining_amount, average_price=excluded.average_price, "
                        "raw_json=excluded.raw_json, last_seen_at=excluded.last_seen_at, "
                        "intent_id=COALESCE(orders.intent_id, excluded.intent_id)",
                        (obs.exchange_order_id, known_intent, obs.symbol, obs.side, obs.order_type, obs.amount,
                         obs.price, obs.status, obs.executed_amount, obs.remaining_amount, obs.average_price,
                         obs.raw_json, float(now), float(now)))
            if known_intent is None and safety.insert_trip(cur, SafetyReason.UNKNOWN_ORDER,
                                                           f"order {obs.exchange_order_id} has no intent", now):
                self._emit(cur, "safety_trip", "safety", SafetyReason.UNKNOWN_ORDER.value,
                           {"order": obs.exchange_order_id}, now)

    def record_fill(self, lease, fill: FillRecord, *, now: float) -> bool:
        """True when newly recorded; False for an identical duplicate."""
        conflict = False
        key = (fill.exchange_order_id, fill.symbol, fill.side, fill.amount, fill.price, fill.quote_notional,
               fill.fee_status, fill.fee_amount, fill.fee_currency, float(fill.executed_at))
        with self._tx(lease) as cur:
            row = cur.execute("SELECT exchange_order_id, symbol, side, amount, price, quote_notional, fee_status, "
                              "fee_amount, fee_currency, executed_at FROM fills WHERE fill_id=?",
                              (fill.fill_id,)).fetchone()
            if row is not None:
                if tuple(row) == key:
                    return False
                conflict = True
                if safety.insert_trip(cur, SafetyReason.DATA_ANOMALY, f"conflicting fill {fill.fill_id}", now):
                    self._emit(cur, "safety_trip", "safety", SafetyReason.DATA_ANOMALY.value,
                               {"fill": fill.fill_id}, now)
            else:
                order = cur.execute("SELECT intent_id FROM orders WHERE exchange_order_id=?",
                                    (fill.exchange_order_id,)).fetchone()
                if order is None:
                    raise LiveLedgerError("fill references an unknown order")
                position = None
                if order[0]:
                    position = cur.execute("SELECT position_id FROM intents WHERE intent_id=?", (order[0],)).fetchone()
                try:
                    cur.execute("INSERT INTO fills(fill_id, exchange_order_id, intent_id, position_id, symbol, side, "
                                "amount, price, quote_notional, fee_status, fee_amount, fee_currency, maker_taker, "
                                "raw_json, executed_at, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (fill.fill_id, fill.exchange_order_id, order[0], position[0] if position else None,
                                 fill.symbol, fill.side, fill.amount, fill.price, fill.quote_notional,
                                 fill.fee_status, fill.fee_amount, fill.fee_currency, fill.maker_taker,
                                 fill.raw_json, float(fill.executed_at), float(now)))
                except sqlite3.IntegrityError as exc:
                    raise LiveLedgerError(f"fill rejected: {exc}") from exc
                self._emit(cur, "fill_recorded", "fill", fill.fill_id, {"order": fill.exchange_order_id}, now)
                if fill.fee_status == "unknown" and safety.insert_trip(
                        cur, SafetyReason.FEE_MISSING, f"fill {fill.fill_id} has no fee", now):
                    self._emit(cur, "safety_trip", "safety", SafetyReason.FEE_MISSING.value,
                               {"fill": fill.fill_id}, now)
        if conflict:
            raise LiveLedgerError("a fill with this id already exists with different content")
        return True

    def record_cashflow(self, lease, flow: Cashflow, *, now: float) -> None:
        try:
            amount = decimal_text(flow.amount, name="amount", allow_negative=flow.kind == "adjustment")
        except CanonicalError as exc:
            raise LiveLedgerError(str(exc)) from exc
        with self._tx(lease) as cur:
            try:
                cur.execute("INSERT INTO cashflows(cashflow_id, kind, currency, amount, source, occurred_at, "
                            "recorded_at, note) VALUES (?,?,?,?,?,?,?,?)",
                            (flow.cashflow_id, flow.kind, flow.currency, amount, flow.source,
                             float(flow.occurred_at), float(now), flow.note))
            except sqlite3.IntegrityError as exc:
                raise LiveLedgerError(f"cashflow rejected: {exc}") from exc
            self._emit(cur, "cashflow_recorded", "cashflow", flow.cashflow_id, {"kind": flow.kind}, now)

    # -- safety (see safety.py) ----------------------------------------------
    def trip_safety(self, reason: SafetyReason, *, detail: str, now: float) -> bool:
        with self._tx(None) as cur:
            inserted = safety.insert_trip(cur, reason, detail, now)
            if inserted:
                self._emit(cur, "safety_trip", "safety", reason.value, {"detail": str(detail)[:200]}, now)
        return inserted

    def clear_safety(self, reason: SafetyReason, *, operator: str, note: str, evidence: str, now: float) -> None:
        if not (operator and note and evidence):
            raise ValueError("operator, note and evidence are required")
        with self._tx(None) as cur:
            row = cur.execute("SELECT trip_id FROM safety_trips WHERE reason=? AND cleared_at IS NULL",
                              (reason.value,)).fetchone()
            if row is None:
                raise LiveLedgerError("no active trip for that reason")
            cur.execute("UPDATE safety_trips SET cleared_at=?, cleared_by=?, clear_note=?, clear_evidence=? "
                        "WHERE trip_id=?", (float(now), operator, note[:500], evidence[:500], row[0]))
            self._emit(cur, "safety_cleared", "safety", reason.value, {"operator": operator}, now)


def read_ledger_id(layout: LiveLayout) -> str:
    """The ledger's own id, or ``NO_LEDGER`` when there is none (nothing is created here)."""
    if not layout.ledger_path.is_file():
        return NO_LEDGER
    ledger = LiveLedger.open_readonly(layout)
    try:
        with ledger.read_cursor() as cur:
            row = cur.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
    finally:
        ledger.close()
    return row[0] if row else NO_LEDGER


def canonical_json_text(obj: object) -> str:
    return canonical_json(obj).decode("utf-8")
