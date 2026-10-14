import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "wait_oanda_practice_ready.py"
UNIT = ROOT / "scripts" / "systemd" / "docich-market-data-fx.service"


def load_module():
    spec = importlib.util.spec_from_file_location("wait_oanda_practice_ready", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OandaProviderReadinessTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tempdir = tempfile.TemporaryDirectory(prefix="oanda-readiness-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.output = self.base / "market-data"
        self.output.mkdir()
        self.marker = self.base / "start.marker"
        self.now = 1_800_000_000.0
        self.marker.write_text("", encoding="utf-8")
        os.utime(self.marker, (self.now - 2, self.now - 2))

    def write_json(self, name, payload, mtime=None):
        path = self.output / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        ts = self.now - 1 if mtime is None else mtime
        os.utime(path, (ts, ts))
        return path

    def good_health(self, **updates):
        payload = {
            "provider": "oanda-practice",
            "mode": "read_only_pricing_collector",
            "status": "ok",
            "quote_count": 2,
            "market_open": True,
            "as_of": self.now - 1,
            "live_order_capability": False,
        }
        payload.update(updates)
        return payload

    def good_quotes(self, rows=None):
        if rows is None:
            rows = [
                {
                    "symbol": "USD_JPY",
                    "ts": self.now - 1,
                    "bid": "149.90",
                    "ask": "149.91",
                    "bid_size": "100000",
                    "ask_size": "100000",
                    "tradeable": True,
                    "source": "oanda-practice-pricing",
                    "currency": "JPY",
                },
                {
                    "symbol": "EUR_JPY",
                    "ts": self.now - 2,
                    "bid": "162.10",
                    "ask": "162.12",
                    "bid_size": "90000",
                    "ask_size": "85000",
                    "tradeable": True,
                    "source": "oanda-practice-pricing",
                    "currency": "JPY",
                },
            ]
        return {"market": "fx", "realtime": True, "quotes": rows}

    def test_fresh_validated_practice_quotes_are_ready(self):
        self.write_json("market-fx-provider-health.json", self.good_health())
        self.write_json("market-fx-quotes.json", self.good_quotes())
        self.assertTrue(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_fresh_closed_health_is_ready_without_quotes(self):
        health = self.good_health(
            status="closed", quote_count=0, market_open=False, as_of=self.now - 1
        )
        self.write_json("market-fx-provider-health.json", health)
        self.assertTrue(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_unavailable_health_is_not_ready(self):
        health = self.good_health(status="unavailable", quote_count=0)
        self.write_json("market-fx-provider-health.json", health)
        self.assertFalse(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_prestart_health_and_quotes_are_rejected(self):
        self.write_json(
            "market-fx-provider-health.json", self.good_health(), mtime=self.now - 3
        )
        self.write_json("market-fx-quotes.json", self.good_quotes(), mtime=self.now - 3)
        self.assertFalse(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_stale_or_future_quotes_are_rejected(self):
        rows = self.good_quotes()["quotes"]
        rows[0]["ts"] = self.now - 16
        self.write_json("market-fx-provider-health.json", self.good_health())
        self.write_json("market-fx-quotes.json", self.good_quotes(rows))
        self.assertFalse(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_duplicate_symbols_are_rejected(self):
        rows = self.good_quotes()["quotes"]
        rows[1]["symbol"] = "USD_JPY"
        self.write_json("market-fx-provider-health.json", self.good_health())
        self.write_json("market-fx-quotes.json", self.good_quotes(rows))
        self.assertFalse(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_live_order_capability_can_never_be_ready(self):
        self.write_json(
            "market-fx-provider-health.json",
            self.good_health(live_order_capability=True),
        )
        self.write_json("market-fx-quotes.json", self.good_quotes())
        self.assertFalse(self.module.snapshot_ready(self.output, self.marker, self.now))

    def test_symlink_marker_is_rejected(self):
        target = self.base / "real.marker"
        target.write_text("", encoding="utf-8")
        link = self.base / "link.marker"
        link.symlink_to(target)
        self.assertFalse(self.module.snapshot_ready(self.output, link, self.now))

    def test_systemd_unit_waits_for_readiness_without_secret_arguments(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("docich-oanda-practice-start.marker", text)
        self.assertIn("wait_oanda_practice_ready.py", text)
        self.assertIn("--timeout 30", text)
        self.assertIn("TimeoutStartSec=40", text)
        for forbidden in (
            "DOCICH_OANDA_TOKEN=",
            "DOCICH_OANDA_ACCOUNT_ID=101-",
            "api-fxtrade.oanda.com",
            "/orders",
        ):
            self.assertNotIn(forbidden, text)

    def test_readiness_helper_never_prints_evidence(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("print(", text)
        self.assertNotIn("logging.", text)


if __name__ == "__main__":
    unittest.main()
