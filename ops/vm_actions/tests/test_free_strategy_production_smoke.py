from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "free_strategy_production_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "free-strategy-production-smoke.yml"

spec = importlib.util.spec_from_file_location("free_strategy_production_smoke", HELPER)
smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(smoke)


class FreeStrategyProductionSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "docich"
        self.trading = self.root / "run-soren-live" / "trading"
        self.trading.mkdir(parents=True)
        self.env_file = Path(self.tempdir.name) / "worker.env"
        self.image = "sha256:" + "a" * 64
        self.db = self.trading / "free-strategies" / "lab.sqlite3"
        self.db.parent.mkdir(parents=True)
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE artifacts (digest TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE experiments (
                    id TEXT PRIMARY KEY, artifact TEXT NOT NULL, phase TEXT NOT NULL,
                    revision INTEGER NOT NULL, account BLOB NOT NULL, strategy_state BLOB NOT NULL,
                    pending BLOB NOT NULL, last_bar REAL, last_error TEXT, last_success REAL,
                    end_at REAL NOT NULL
                );
                CREATE TABLE decisions (
                    run_id TEXT PRIMARY KEY, experiment TEXT NOT NULL, bar REAL NOT NULL,
                    accepted_at REAL NOT NULL, receipt BLOB NOT NULL
                );
                CREATE TABLE fills (
                    id TEXT PRIMARY KEY, experiment TEXT NOT NULL, observed_at REAL NOT NULL,
                    payload BLOB NOT NULL
                );
            """)

    def tearDown(self):
        self.tempdir.cleanup()

    def _insert(self, *, family=smoke.FAMILY, name=smoke.NAME, positions=None, pending=None):
        artifact = "b" * 64
        identity = "c" * 32
        payload = {
            "schema_version": 1,
            "source": "def decide(context): pass",
            "image": self.image,
            "name": name,
            "family": family,
            "thesis": "smoke",
            "symbols": [smoke.SYMBOL],
            "parameters": {},
            "initial_state": {},
        }
        account = {"cash_jpy": "10000", "positions": positions or {}, "peak_equity": "10000"}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO artifacts VALUES (?,?)", (artifact, json.dumps(payload).encode()))
            db.execute(
                """INSERT INTO experiments
                   (id,artifact,phase,revision,account,strategy_state,pending,last_bar,last_error,last_success,end_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity, artifact, "paper_validating", 1, json.dumps(account).encode(),
                    json.dumps({"smoke_runs": 1}).encode(), json.dumps(pending or {}).encode(),
                    1234.0, None, 1235.0, 999999.0,
                ),
            )
            db.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?)",
                ("run", identity, 1234.0, 1235.0, b"{}"),
            )
        return identity, artifact

    def test_worker_env_is_strict_and_confined_to_docich_root(self):
        old_root = smoke.ROOT
        try:
            smoke.ROOT = self.root
            self.env_file.write_text(
                f"DOCICH_FREE_STRATEGY_TRADING_DIR={self.trading}\n"
                f"DOCICH_FREE_STRATEGY_IMAGE={self.image}\n"
                "DOCICH_FREE_STRATEGY_INTERVAL=300\n",
                encoding="utf-8",
            )
            trading, image = smoke._read_worker_env(self.env_file)
            self.assertEqual(trading, self.trading.resolve())
            self.assertEqual(image, self.image)

            outside = Path(self.tempdir.name) / "outside"
            outside.mkdir()
            self.env_file.write_text(
                f"DOCICH_FREE_STRATEGY_TRADING_DIR={outside}\n"
                f"DOCICH_FREE_STRATEGY_IMAGE={self.image}\n",
                encoding="utf-8",
            )
            with self.assertRaises(smoke.SmokeError):
                smoke._read_worker_env(self.env_file)
        finally:
            smoke.ROOT = old_root

    def test_snapshot_proves_candidate_execution_without_fills(self):
        identity, artifact = self._insert()
        result = smoke._snapshot(self.db, identity, artifact)
        self.assertEqual(result["phase"], "paper_validating")
        self.assertEqual(result["decisions"], 1)
        self.assertEqual(result["fills"], 0)
        self.assertEqual(result["state"]["smoke_runs"], 1)
        self.assertIsNone(result["last_error"])

    def test_finalize_only_exact_no_position_ops_smoke(self):
        identity, artifact = self._insert()
        smoke._finalize(self.db, identity, artifact, now=2000.0)
        with sqlite3.connect(self.db) as db:
            row = db.execute(
                "SELECT phase,end_at,revision,last_error FROM experiments WHERE id=?", (identity,)
            ).fetchone()
        self.assertEqual(row[0], "review_due")
        self.assertLess(row[1], 2000.0)
        self.assertEqual(row[2], 2)
        self.assertIsNone(row[3])

    def test_finalize_refuses_non_smoke_or_positioned_experiment(self):
        identity, artifact = self._insert(family="real_strategy", name="real")
        with self.assertRaises(smoke.SmokeError):
            smoke._finalize(self.db, identity, artifact, now=2000.0)

        self.db.unlink()
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE artifacts (digest TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE experiments (
                    id TEXT PRIMARY KEY, artifact TEXT NOT NULL, phase TEXT NOT NULL,
                    revision INTEGER NOT NULL, account BLOB NOT NULL, strategy_state BLOB NOT NULL,
                    pending BLOB NOT NULL, last_bar REAL, last_error TEXT, last_success REAL,
                    end_at REAL NOT NULL
                );
                CREATE TABLE decisions (
                    run_id TEXT PRIMARY KEY, experiment TEXT NOT NULL, bar REAL NOT NULL,
                    accepted_at REAL NOT NULL, receipt BLOB NOT NULL
                );
                CREATE TABLE fills (
                    id TEXT PRIMARY KEY, experiment TEXT NOT NULL, observed_at REAL NOT NULL,
                    payload BLOB NOT NULL
                );
            """)
        identity, artifact = self._insert(positions={smoke.SYMBOL: "0.001"})
        with self.assertRaises(smoke.SmokeError):
            smoke._finalize(self.db, identity, artifact, now=2000.0)

    def test_workflow_has_no_arbitrary_dispatch_and_uses_fixed_owner_only_command(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ops/vm_actions/free_strategy_smoke_epoch", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn(
            "'python3 ops/vm_actions/free_strategy_production_smoke.py'",
            text,
        )
        self.assertIn('"exec docich production $SHA"', text)
        self.assertIn('"diagnostics docich production $SHA"', text)


if __name__ == "__main__":
    unittest.main()
