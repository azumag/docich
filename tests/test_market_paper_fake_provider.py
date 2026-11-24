"""Step 2 fake market-data provider: readiness contract, fail-closed normalization, skip/cancel persistence."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from docich.trading.markets.core import report_window
from docich.trading.markets.fake_provider import FakeMarketDataProvider
from docich.trading.markets.program import MarketCorner, provider_readiness


def moment(text="2026-09-16T03:10:00+09:00"):
    return datetime.fromisoformat(text).timestamp()


class ProviderContractTests(unittest.TestCase):
    def test_fake_fail_and_recover(self):
        p = FakeMarketDataProvider()
        self.assertEqual(p.readiness(1.0, None), (True, "ok"))
        p.fail("down")
        self.assertEqual(p.readiness(1.0, None), (False, "down"))
        p.recover()
        self.assertEqual(p.readiness(1.0, None), (True, "ok"))

    def test_bool_and_valid_tuple_are_accepted(self):
        class Ready:
            def readiness(self, now, state):
                return True

        class Tuple:
            def readiness(self, now, state):
                return False, "maintenance"

        self.assertEqual(provider_readiness(Ready(), 1.0), (True, "ok"))
        self.assertEqual(provider_readiness(Tuple(), 1.0), (False, "maintenance"))

    def test_unknown_shapes_are_fail_closed(self):
        class Shape:
            def __init__(self, value):
                self.value = value

            def readiness(self, now, state):
                return self.value

        for value in (None, (), (True,), ("yes", "ok"), (1, "ok"), (True, ""), {"ready": True}, [True, "ok"], object()):
            with self.subTest(value=value):
                self.assertEqual(provider_readiness(Shape(value), 1.0), (False, "invalid_readiness_result"))

    def test_exception_is_fail_closed(self):
        class Broken:
            def readiness(self, now, state):
                raise RuntimeError("socket gone")

        self.assertEqual(provider_readiness(Broken(), 1.0), (False, "provider_readiness_error"))

    def test_missing_readiness_is_fail_closed(self):
        self.assertEqual(provider_readiness(object(), 1.0), (False, "provider_readiness_missing"))


class FakeProviderCornerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.g_state = self.root / "state"
        self.g_state.mkdir()
        (self.root / "soren" / "tmp" / "state").mkdir(parents=True, exist_ok=True)
        self.g = SimpleNamespace(state_dir=self.g_state, repo_root=self.root,
                                 webui=SimpleNamespace(soren_root=str(self.root / "soren")),
                                 config_path=self.root / "docich.toml")

    def tearDown(self):
        self.tmp.cleanup()

    def _runtime(self):
        return SimpleNamespace(market="fx", config={"enabled": True}, g=self.g,
                               corner_path=self.g_state / "market-fx-corner.json",
                               data_root=self.g_state / "market-data",
                               settings_path=self.root / "market.toml",
                               book=SimpleNamespace(
                                   report=lambda c: {"title": "FX", "period_pnl_jpy": 0, "complete": True,
                                                     "closed_trades": 0, "closed_net_pnl_jpy": 0,
                                                     "last": {"positions": {}}},
                                   snapshot=lambda now: {}))

    def _corner(self, provider, now):
        coordinator = SimpleNamespace(switch=lambda game: SimpleNamespace(status="succeeded"),
                                      start=lambda game: SimpleNamespace(status="succeeded"))
        corner = MarketCorner(self._runtime(), clock=lambda: now, sleep=lambda s: None,
                              coordinator=coordinator, provider=provider)
        return corner

    def test_prestart_skip_persists(self):
        fake = FakeMarketDataProvider().fail("down")
        now = moment()
        corner = self._corner(fake, now)
        corner.current = lambda: None
        self.assertEqual(corner.tick(), "skipped_unavailable")
        state = json.loads(corner.path.read_text())
        self.assertEqual(state["status"], "skipped_unavailable")
        self.assertEqual(state["unavailable_reason"], "down")
        self.assertEqual(corner.tick(), "not_due")

    def test_unknown_readiness_prestart_skips(self):
        class Unknown:
            def readiness(self, now, state):
                return None

        now = moment()
        corner = self._corner(Unknown(), now)
        corner.current = lambda: None
        self.assertEqual(corner.tick(), "skipped_unavailable")
        state = json.loads(corner.path.read_text())
        self.assertEqual(state["unavailable_reason"], "invalid_readiness_result")

    def test_active_cancel_persists(self):
        fake = FakeMarketDataProvider().fail("down")
        now = moment()
        begin, end = report_window("fx", now)
        initial = {"status": "active", "requested_at": now - 600, "ends_at": end,
                   "started_at": now - 600, "heartbeat": now - 1,
                   "previous_game": "sorengame", "deliveries": {}}
        corner = self._corner(fake, now)
        corner.current = lambda: "fx-paper-view"
        corner.path.write_text(json.dumps(initial))
        self.assertEqual(corner.tick(), "cancelled_unavailable")
        state = json.loads(corner.path.read_text())
        self.assertEqual(state["status"], "cancelled_unavailable")
        self.assertEqual(state["outcome"], "cancelled_unavailable")
        self.assertEqual(corner.tick(), "not_due")

    def test_recovery_clears_readiness(self):
        fake = FakeMarketDataProvider().fail("down")
        corner = self._corner(fake, moment("2026-09-16T03:05:00+09:00"))
        self.assertEqual(corner._availability_check(1.0, {}), (False, "down"))
        fake.recover()
        self.assertEqual(corner._availability_check(1.0, {}), (True, "ok"))

    def test_no_provider_stays_available(self):
        corner = self._corner(None, moment())
        self.assertEqual(corner._availability_check(1.0, {}), (True, "ok"))


if __name__ == "__main__":
    unittest.main()
