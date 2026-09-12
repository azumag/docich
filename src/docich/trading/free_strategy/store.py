"""Host-owned artifact registry, isolated accounts, and atomic decision receipts."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import uuid

from .contract import Artifact, StrategyError, decode, encode, quantity

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (digest TEXT PRIMARY KEY, payload BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, artifact TEXT NOT NULL REFERENCES artifacts(digest),
 created_at REAL NOT NULL, end_at REAL NOT NULL, phase TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, policy BLOB NOT NULL, account BLOB NOT NULL,
 strategy_state BLOB NOT NULL, pending BLOB NOT NULL, last_bar REAL,
 last_error TEXT, last_success REAL
);
CREATE TABLE IF NOT EXISTS decisions (
 run_id TEXT PRIMARY KEY, experiment TEXT NOT NULL, bar REAL NOT NULL,
 accepted_at REAL NOT NULL, receipt BLOB NOT NULL,
 UNIQUE(experiment, bar)
);
CREATE TABLE IF NOT EXISTS fills (
 id TEXT PRIMARY KEY, experiment TEXT NOT NULL, observed_at REAL NOT NULL,
 payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
 experiment TEXT NOT NULL, bucket INTEGER NOT NULL, observed_at REAL NOT NULL,
 equity TEXT NOT NULL, PRIMARY KEY(experiment, bucket)
);
CREATE TABLE IF NOT EXISTS equity_observations (
 experiment TEXT NOT NULL, observed_at REAL NOT NULL, equity TEXT NOT NULL,
 PRIMARY KEY(experiment, observed_at)
);
CREATE TRIGGER IF NOT EXISTS samples_equity_observation_insert
AFTER INSERT ON samples
BEGIN
 INSERT OR REPLACE INTO equity_observations(experiment,observed_at,equity)
 VALUES (NEW.experiment,NEW.observed_at,NEW.equity);
END;
CREATE TRIGGER IF NOT EXISTS samples_equity_observation_update
AFTER UPDATE OF observed_at,equity ON samples
BEGIN
 INSERT OR REPLACE INTO equity_observations(experiment,observed_at,equity)
 VALUES (NEW.experiment,NEW.observed_at,NEW.equity);
END;
CREATE TABLE IF NOT EXISTS evaluations (
 experiment TEXT PRIMARY KEY, evaluated_at REAL NOT NULL, report BLOB NOT NULL
);
"""


class LabStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        # Existing WIP databases may predate the observation journal. Backfill
        # their latest hourly samples; future in-bucket updates are preserved by
        # the triggers above instead of being lost to the samples upsert.
        self.db.execute("""INSERT OR IGNORE INTO equity_observations(experiment,observed_at,equity)
            SELECT experiment,observed_at,equity FROM samples""")
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def register(self, artifact: Artifact) -> str:
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?)",
                            (artifact.digest, encode(artifact.payload)))
        return artifact.digest

    def artifact(self, digest: str) -> Artifact:
        row = self.db.execute("SELECT payload FROM artifacts WHERE digest=?", (digest,)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != digest:
            raise StrategyError("artifact_integrity_failed")
        payload = decode(row[0], limit=512 * 1024)
        rebuilt = Artifact.create(**{key: value for key, value in payload.items() if key != "schema_version"})
        if rebuilt.digest != digest:
            raise StrategyError("artifact_integrity_failed")
        return rebuilt

    def create(self, digest: str, *, now: float, capital: str = "10000", days: int = 30) -> str:
        artifact = self.artifact(digest)
        amount = quantity(capital)
        if not 1000 <= amount <= 100000000 or type(days) is not int or not 1 <= days <= 365:
            raise StrategyError("invalid_experiment_policy")
        if not math.isfinite(now) or now <= 0:
            raise StrategyError("invalid_time")
        # Values below are PAPER execution/research budgets, never live thresholds.
        policy = {"capital_jpy": capital, "max_deployed_fraction": "0.30",
                  "stop_drawdown_fraction": "0.10", "slippage_bps": "10",
                  "book_participation": "0.01", "sample_seconds": 3600,
                  "evaluator_version": 1, "execution_model": "next_observation_depth_v1"}
        identity = uuid.uuid4().hex
        with self.transaction():
            count = self.db.execute("SELECT COUNT(*) FROM experiments WHERE phase IN ('research','paper_validating')").fetchone()[0]
            if count >= 2:
                raise StrategyError("experiment_capacity")
            if self.db.execute("SELECT 1 FROM experiments WHERE artifact=?", (digest,)).fetchone():
                raise StrategyError("artifact_already_tested")
            account = {"cash_jpy": capital, "positions": {}, "peak_equity": capital}
            self.db.execute("""INSERT INTO experiments
                (id,artifact,created_at,end_at,phase,policy,account,strategy_state,pending)
                VALUES (?,?,?,?,?,?,?,?,?)""", (identity, digest, now, now + days * 86400,
                "research", encode(policy), encode(account), encode(artifact.payload["initial_state"]), encode({})))
        return identity

    def experiment(self, identity: str) -> dict:
        row = self.db.execute("SELECT * FROM experiments WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise StrategyError("experiment_missing")
        result = dict(row)
        for field in ("policy", "account", "strategy_state", "pending"):
            result[field] = decode(result[field], limit=512 * 1024)
        return result

    def active_ids(self) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT id FROM experiments WHERE phase IN ('research','paper_validating') ORDER BY created_at,id LIMIT 2")]

    def recent_fills(self, identity: str) -> list[dict]:
        return [decode(row[0]) for row in self.db.execute(
            "SELECT payload FROM fills WHERE experiment=? ORDER BY observed_at DESC,id DESC LIMIT 20", (identity,))]

    def pause(self, identity: str) -> None:
        with self.transaction():
            self.experiment(identity)
            self.db.execute("UPDATE experiments SET phase='paused', revision=revision+1, pending=? WHERE id=?",
                            (encode({}), identity))

    def resume(self, identity: str, *, now: float) -> None:
        with self.transaction():
            exp = self.experiment(identity)
            if exp["phase"] != "paused" or now >= exp["end_at"] or exp["last_error"] == "drawdown_limit":
                raise StrategyError("resume_not_allowed")
            if len(self.active_ids()) >= 2:
                raise StrategyError("experiment_capacity")
            self.db.execute("UPDATE experiments SET phase='paper_validating', revision=revision+1 WHERE id=?", (identity,))

    def error(self, identity: str, revision: int, code: str, *, quarantine: bool = False) -> None:
        with self.transaction():
            exp = self.experiment(identity)
            if exp["revision"] != revision or exp["phase"] not in {"research", "paper_validating"}:
                return
            self.db.execute("UPDATE experiments SET last_error=?, phase=?, pending=?, revision=revision+1 WHERE id=?",
                            (code, "quarantined" if quarantine else exp["phase"], encode({}), identity))

    def accept(self, identity: str, revision: int, *, bar: float, accepted_at: float,
               run_id: str, decision: dict, prices: dict[str, Decimal]) -> dict:
        """State and target receipt commit together. Identity is never supplied by guest."""
        with self.transaction():
            previous = self.db.execute("SELECT receipt FROM decisions WHERE run_id=?", (run_id,)).fetchone()
            if previous:
                return decode(previous[0])
            exp = self.experiment(identity)
            if (exp["revision"] != revision or exp["phase"] not in {"research", "paper_validating"}
                    or accepted_at >= exp["end_at"] or (exp["last_bar"] is not None and bar <= exp["last_bar"])):
                raise StrategyError("decision_superseded")
            desired = {symbol: Decimal(amount) for symbol, amount in exp["account"]["positions"].items()}
            desired.update({symbol: quantity(target["quantity"]) for symbol, target in exp["pending"].items()})
            desired.update({target["symbol"]: quantity(target["target_base_quantity"]) for target in decision["target_positions"]})
            cap = Decimal(exp["policy"]["capital_jpy"]) * Decimal(exp["policy"]["max_deployed_fraction"])
            allowed = all(symbol in prices for symbol in desired)
            allowed = allowed and sum(amount * prices[symbol] for symbol, amount in desired.items()) <= cap
            pending = dict(exp["pending"])
            if allowed:
                for target in decision["target_positions"]:
                    pending[target["symbol"]] = {"quantity": target["target_base_quantity"],
                        "accepted_at": accepted_at, "run_id": run_id}
            receipt = {"run_id": run_id, "status": "accepted" if allowed else "risk_rejected",
                       "state_revision": revision + 1}
            self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?)",
                            (run_id, identity, bar, accepted_at, encode(receipt)))
            self.db.execute("""UPDATE experiments SET strategy_state=?,pending=?,last_bar=?,
                last_success=?,last_error=?,revision=revision+1,phase='paper_validating' WHERE id=?""",
                (encode(decision["state"]), encode(pending), bar, accepted_at,
                 None if allowed else "target_cap_exceeded", identity))
            return receipt
