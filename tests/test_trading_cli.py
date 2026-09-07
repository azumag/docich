from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.cli import main  # noqa: E402
from docich.trading.exchanges.bitbank_ccxt import CCXTUnavailableError  # noqa: E402


class TestTradingCli(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def snapshot(self, *, mode="paper"):
        return {
            "mode": mode,
            "as_of": 1_800_000_000.0,
            "capital_reference": "100000",
            "deployed_reference": "0",
            "quote_to_reference": {"JPY": "1"},
            "available_quote": {"JPY": "100000"},
            "markets": {
                "BTC/JPY": {
                    "base": "BTC", "quote": "JPY", "spot": True, "active": True,
                    "amount_step": "0.0001", "min_amount": "0.0001", "min_cost": "1",
                }
            },
            "prices": {"BTC/JPY": "1000000"},
            "opportunities": [{
                "opportunity_id": "opp-1", "strategy_id": "fixture-v1",
                "symbol": "BTC/JPY", "side": "buy", "score": "0.9",
                "expected_edge_bps": "20", "max_notional_fraction": "1",
                "expires_at": 1_800_000_100.0, "reason_code": "fixture_signal",
            }],
        }

    def test_absent_status_is_safe_and_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self.run_cli(["trading", "--state-dir", tmp, "status"])
            self.assertEqual(rc, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["mode"], "paper")
            self.assertEqual(payload["worker_state"], "absent")

    def test_paper_cycle_writes_fill_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            state_dir = Path(tmp) / "state"
            snapshot.write_text(json.dumps(self.snapshot()), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(state_dir), "paper-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["mode"], "paper")
            self.assertEqual(len(payload["recent_fills"]), 1)
            self.assertEqual(payload["recent_fills"][0]["opportunity_id"], "opp-1")
            self.assertTrue((state_dir / "paper.sqlite3").is_file())
            self.assertTrue((state_dir / "status.json").is_file())

    def test_replaying_same_snapshot_does_not_duplicate_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            state_dir = Path(tmp) / "state"
            snapshot.write_text(json.dumps(self.snapshot()), encoding="utf-8")
            argv = ["trading", "--state-dir", str(state_dir), "paper-cycle", "--snapshot", str(snapshot)]
            self.assertEqual(self.run_cli(argv)[0], 0)
            rc, out, err = self.run_cli(argv)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(json.loads(out)["recent_fills"]), 1)

    def test_malformed_numeric_snapshot_is_stable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot()
            data["capital_reference"] = "not-a-number"
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(Path(tmp) / "state"), "paper-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 2)
            self.assertIn("invalid", err.lower())
            self.assertNotIn("Traceback", err)

    def test_negative_capital_is_stable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot()
            data["capital_reference"] = "-1"
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(Path(tmp) / "state"), "paper-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 2)
            self.assertIn("capital", err.lower())
            self.assertNotIn("Traceback", err)

    def test_duplicate_opportunity_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot()
            data["opportunities"].append(dict(data["opportunities"][0]))
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(Path(tmp) / "state"), "paper-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 2)
            self.assertIn("duplicate", err.lower())
            self.assertNotIn("Traceback", err)

    def test_live_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(self.snapshot(mode="live")), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(Path(tmp) / "state"), "paper-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 2)
            self.assertIn("paper", err.lower())

    def test_unknown_credential_like_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot()
            data["api_key"] = "must-not-be-read"
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps(data), encoding="utf-8")
            rc, out, err = self.run_cli([
                "trading", "--state-dir", str(Path(tmp) / "state"), "paper-cycle", "--snapshot", str(snapshot)
            ])
            self.assertEqual(rc, 2)
            self.assertIn("unknown", err.lower())
            self.assertNotIn("must-not-be-read", err)

    def test_discover_missing_optional_ccxt_is_stable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "docich.trading.cli.BitbankPublicGateway",
                side_effect=CCXTUnavailableError("install requirements-trading.txt"),
            ):
                rc, out, err = self.run_cli(["trading", "--state-dir", tmp, "discover"])
            self.assertEqual(rc, 2)
            self.assertIn("requirements-trading", err)

    def test_ordinary_docich_command_does_not_require_ccxt(self):
        rc, out, err = self.run_cli(["games"])
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
