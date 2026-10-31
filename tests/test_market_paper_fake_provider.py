"""Tests for the Step 2 fake market-data provider (no network/credentials)."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from docich.trading.markets.fake_provider import (
    FakeMarketDataProvider, FakeProvider, MarketGuard, make_fake_provider,
)
from docich.trading.markets.program import MarketCorner, _normalize_provider_readiness
from docich.trading.markets.core import report_window


def moment(text="2026-09-16T03:10:00+09:00"):
    return datetime.fromisoformat(text).timestamp()


class FakeProviderTests(unittest.TestCase):
    def test_default_available(self):
        p = FakeMarketDataProvider()
        self.assertTrue(p.available)
        self.assertEqual(p.readiness(), (True, "ok"))

    def test_fail_and_recover(self):
        p = FakeMarketDataProvider(available=True)
        p.fail("down")
        self.assertFalse(p.available)
        self.assertEqual(p.readiness(), (False, "down"))
        p.recover()
        self.assertTrue(p.available)
        self.assertEqual(p.readiness()[0], True)

    def test_construct_with_ready_false(self):
        p = FakeMarketDataProvider(ready=False)
        self.assertFalse(p.available)
        self.assertEqual(p.readiness()[0], False)

    def test_construct_with_status_unavailable(self):
        p = FakeMarketDataProvider(status="unavailable")
        self.assertFalse(p.available)

    def test_aliases(self):
        p = FakeProvider(available=False)
        self.assertFalse(p.available)

    def test_market_guard_aborts(self):
        p = FakeMarketDataProvider(available=True)
        g = MarketGuard(provider=p)
        self.assertTrue(g.check()[0])
        g.request_abort("boom")
        self.assertFalse(g.check()[0])
        g.recover()
        self.assertTrue(g.check()[0])

    def test_normalize_provider_readiness(self):
        p = FakeMarketDataProvider(available=False, reason="nope")
        self.assertEqual(_normalize_provider_readiness(p, 1.0, None), (False, "nope"))
        p2 = FakeMarketDataProvider(available=True)
        self.assertEqual(_normalize_provider_readiness(p2, 1.0, None)[0], True)

    def test_make_factory(self):
        p = make_fake_provider(available=False)
        self.assertFalse(p.available)

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

    def _runtime(self, fake, corner_name="market-fx-corner.json"):
        return SimpleNamespace(market="fx", config={"enabled": True}, g=self.g,
                               corner_path=self.g_state / corner_name,
                               data_root=self.g_state / "market-data",
                               settings_path=self.root / "market.toml",
                               book=SimpleNamespace(
                                   report=lambda c: {"title": "FX", "period_pnl_jpy": 0, "complete": True,
                                                     "closed_trades": 0, "closed_net_pnl_jpy": 0,
                                                     "last": {"positions": {}}},
                                   snapshot=lambda now: {}))

    def test_prestart_skip_persists(self):
        fake = FakeMarketDataProvider(available=False, reason="down")
        now = moment()
        runtime = self._runtime(fake)
        corner = MarketCorner(runtime, clock=lambda: now, sleep=lambda s: None,
                              coordinator=SimpleNamespace(switch=lambda game: SimpleNamespace(status="succeeded"),
                                                         start=lambda game: SimpleNamespace(status="succeeded")),
                              provider=fake)
        corner.current = lambda: None
        first = corner.tick()
        self.assertEqual(first, "skipped_unavailable")
        state = json.loads(corner.path.read_text())
        self.assertEqual(state["status"], "skipped_unavailable")
        self.assertEqual(state["unavailable_reason"], "down")
        self.assertEqual(corner.tick(), "not_due")

    def test_active_cancel_persists(self):
        fake = FakeMarketDataProvider(available=False, reason="down")
        now = moment()
        begin, end = report_window("fx", now)
        initial = {"status": "active", "requested_at": now - 600, "ends_at": end,
                   "started_at": now - 600, "heartbeat": now - 1,
                   "previous_game": "sorengame", "deliveries": {}}
        runtime = self._runtime(fake)
        corner = MarketCorner(runtime, clock=lambda: now, sleep=lambda s: None,
                              coordinator=SimpleNamespace(switch=lambda game: SimpleNamespace(status="succeeded"),
                                                         start=lambda game: SimpleNamespace(status="succeeded")),
                              provider=fake)
        corner.current = lambda: "fx-paper-view"
        corner.path.write_text(json.dumps(initial))
        self.assertEqual(corner.tick(), "cancelled_unavailable")
        state = json.loads(corner.path.read_text())
        self.assertEqual(state["status"], "cancelled_unavailable")
        self.assertEqual(state["outcome"], "cancelled_unavailable")
        self.assertEqual(corner.tick(), "not_due")

    def test_provider_recovery_allows_future_run(self):
        fake = FakeMarketDataProvider(available=False, reason="down")
        now = moment("2026-09-16T03:05:00+09:00")
        runtime = self._runtime(fake)
        corner = MarketCorner(runtime, clock=lambda: now, sleep=lambda s: None,
                              coordinator=SimpleNamespace(switch=lambda game: SimpleNamespace(status="succeeded"),
                                                         start=lambda game: SimpleNamespace(status="succeeded")),
                              provider=fake)
        corner.current = lambda: None
        self.assertEqual(corner.tick(), "skipped_unavailable")
        fake.recover()
        self.assertTrue(corner._availability_check(now, {})[0])