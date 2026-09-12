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

    def test_worker_cmdline_proves_runtime_and_confines_trading_dir(self):
        raw = b"\0".join([
            b"/tmp/python", b"-m", b"docich", b"free-strategy-worker",
            b"--trading-dir", str(self.trading).encode(), b"--image", self.image.encode(),
            b"--interval", b"300", b"--enabled", b"",
        ])
        trading, image = smoke._parse_worker_cmdline(raw, root=self.root)
        self.assertEqual(trading, self.trading.resolve())
        self.assertEqual(image, self.image)

        outside = Path(self.tempdir.name) / "outside"
        outside.mkdir()
        bad = b"\0".join([
            b"python", b"-m", b"docich", b"free-strategy-worker",
            b"--trading-dir", str(outside).encode(), b"--image", self.image.encode(), b"",
        ])
        with self.assertRaises(smoke.SmokeError):
            smoke._parse_worker_cmdline(bad, root=self.root)

    def test_worker_cmdline_accepts_only_reviewed_soren_projection(self):
        base = Path(self.tempdir.name) / "projection-case"
        root = base / "docich"
        soren = base / "soren"
        (soren / "trading").mkdir(parents=True)
        root.mkdir(parents=True)
        (root / "run-soren-live").symlink_to(soren, target_is_directory=True)
        projected = root / "run-soren-live" / "trading"
        raw = b"\0".join([
            b"python", b"-m", b"docich", b"free-strategy-worker",
            b"--trading-dir", str(projected).encode(), b"--image", self.image.encode(), b"",
        ])
        trading, _ = smoke._parse_worker_cmdline(raw, root=root, soren_root=soren)
        self.assertEqual(trading, (soren / "trading").resolve())

        sibling = soren / "other"
        sibling.mkdir()
        bad = b"\0".join([
            b"python", b"-m", b"docich", b"free-strategy-worker",
            b"--trading-dir", str(sibling).encode(), b"--image", self.image.encode(), b"",
        ])
        with self.assertRaises(smoke.SmokeError):
            smoke._parse_worker_cmdline(bad, root=root, soren_root=soren)

    def test_error_exit_codes_are_fixed_and_bounded(self):
        values = list(smoke.ERROR_EXIT_CODES.values())
        self.assertEqual(len(values), len(set(values)))
        self.assertTrue(all(10 <= value <= 125 for value in values))
        self.assertEqual(smoke.CLI_ERROR_MAP["experiment_capacity"], "cli_experiment_capacity")

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
