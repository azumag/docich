import importlib.util
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_market_paper", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MarketPaperDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="market-paper-diag-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.unit_dir = Path(self.tmp.name) / "units"
        self.state.mkdir(parents=True)
        self.unit_dir.mkdir(parents=True)
        self.now = int(time.time())
        self.module = load_collector()
        # Isolate from this checkout's actual config/market-paper.toml
        # (_market_paper_config() otherwise reads it directly via
        # PROD_ROOT), which is expected to change independently of these
        # tests (e.g. a temporary owner-approved file-feed verification
        # toggling fx.enabled). Tests that care about a specific config
        # override self.module._market_paper_config after this.
        self.module._market_paper_config = lambda: {
            "stocks": {"enabled": False, "mode": "paper"},
            "fx": {"enabled": False, "mode": "paper"},
        }

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_absent_worker_reports_disabled_defaults(self):
        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        for market in ("stocks", "fx"):
            entry = result[market]
            self.assertFalse(entry["enabled"])
            self.assertFalse(entry["unit_installed"])
            self.assertFalse(entry["health_present"])
            self.assertFalse(entry["worker_stale"])
            self.assertFalse(entry["feed_unavailable"])
            self.assertEqual(entry["last_quote_age_sec"], -1)
            self.assertEqual(entry["last_report_age_sec"], -1)
            self.assertFalse(entry["sqlite_present"])
        self.assertFalse(result["stocks"]["corner_active"])
        self.assertIsNone(result["fx"]["corner_active"])

    def test_stale_feed_unavailable_and_pending_liquidation_are_surfaced(self):
        health = self.state / "market-paper" / "fx" / "health.json"
        self.write_json(
            health,
            {
                "status": "price_feed_unavailable",
                "market": "fx",
                "mode": "paper",
                "pending_liquidation": True,
                "risk_stopped": False,
                "market_as_of": self.now - 500,
                "positions": {"USD_JPY": {"side": 1}},
            },
        )
        import os

        old = self.now - 3600
        os.utime(health, (old, old))

        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        fx = result["fx"]
        self.assertTrue(fx["health_present"])
        self.assertTrue(fx["health_readable"])
        self.assertEqual(fx["health_status"], "price_feed_unavailable")
        self.assertTrue(fx["feed_unavailable"])
        self.assertTrue(fx["worker_stale"])
        self.assertTrue(fx["pending_liquidation"])
        self.assertFalse(fx["risk_stopped"])
        self.assertEqual(fx["positions_count"], 1)
        self.assertEqual(fx["last_quote_age_sec"], 500)

    def test_corrupt_health_json_does_not_crash(self):
        health = self.state / "market-paper" / "stocks" / "health.json"
        health.parent.mkdir(parents=True, exist_ok=True)
        health.write_text("{not json", encoding="utf-8")
        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        stocks = result["stocks"]
        self.assertTrue(stocks["health_present"])
        self.assertFalse(stocks["health_readable"])
        self.assertIsNone(stocks["health_status"])

    def test_stocks_corner_lease_active_flag(self):
        self.write_json(self.state / "market-stocks-corner.json", {"status": "active", "ends_at": self.now + 60})
        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        self.assertTrue(result["stocks"]["corner_active"])

    def test_last_report_age_from_sqlite_reports_table(self):
        db_path = self.state / "market-paper" / "fx" / "paper.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, body TEXT)")
        end_ts = self.now - 120
        conn.execute("INSERT INTO reports VALUES (?,?)", (f"fx:{end_ts}", "{}"))
        conn.commit()
        conn.close()

        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        fx = result["fx"]
        self.assertTrue(fx["sqlite_present"])
        self.assertEqual(fx["last_report_age_sec"], 120)
        # stocks has its own separate sqlite file under market-paper/stocks/,
        # which does not exist here, so it must stay absent (-1), not fall
        # back to reading fx's ledger.
        self.assertFalse(result["stocks"]["sqlite_present"])
        self.assertEqual(result["stocks"]["last_report_age_sec"], -1)

    def test_missing_config_file_is_not_an_error(self):
        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        self.assertIn("config_readable", result)
        self.assertIn("linger_enabled", result)

    def test_reports_unit_dir_path_and_listing_for_forensics(self):
        (self.unit_dir / "docich-market-worker@fx.service").write_text("[Unit]\n", encoding="utf-8")
        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        self.assertEqual(result["unit_dir"], str(self.unit_dir))
        self.assertTrue(result["unit_dir_exists"])
        self.assertIn("docich-market-worker@fx.service", result["unit_dir_entries"])
        self.assertIn("prod_root", result)

    def test_unit_installed_reflects_shared_template_presence(self):
        # manage_market_paper_units.sh installs the shared systemd *template*
        # (docich-market-worker@.service, no instance name) once for both
        # markets -- an instantiated name like ...@fx.service is never
        # written to disk; systemd resolves it from the template at
        # is-active/is-enabled/start time. So the template's presence must
        # make both markets report unit_installed=true, not just one.
        (self.unit_dir / "docich-market-worker@.service").write_text("[Unit]\n", encoding="utf-8")
        result = self.module._collect_market_paper(self.state, self.now, unit_dir=self.unit_dir)
        self.assertTrue(result["fx"]["unit_installed"])
        self.assertTrue(result["stocks"]["unit_installed"])

    def test_systemctl_unavailable_yields_none_not_a_crash(self):
        # In CI/sandbox there is normally no user session bus; the helper
        # must degrade to None rather than raise.
        active = self.module._unit_is_active("docich-market-worker@fx.service")
        enabled = self.module._unit_is_enabled("docich-market-worker@fx.service")
        self.assertIn(active, (True, False, None))
        self.assertIn(enabled, (True, False, None))


if __name__ == "__main__":
    unittest.main()
