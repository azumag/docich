"""P0-2 regression tests (Issue #198): freshness, snapshot retention, budget."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading import freshness as F  # noqa: E402
from docich.trading.market_cache import (  # noqa: E402
    load_cache,
    prune_cache,
    save_cache,
    cache_path,
    store_frames,
)
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.worker import (  # noqa: E402
    CYCLE_BUDGET_S,
    _rotation_order,
    run_paper_worker,
    run_worker_cycle,
)

D = Decimal
NOW = 1_800_000_000.0


def _frame(symbol, *, age_s=60.0, future=False):
    last = NOW - age_s
    if future:
        last = NOW + 3600.0
    timestamps = tuple(last - (23 - i) * 300 for i in range(24))
    return MarketFrame(
        symbol=symbol,
        timeframe_seconds=300,
        timestamps=timestamps,
        closes=tuple(D("100") for _ in range(24)),
        volumes=tuple(D("1") for _ in range(24)),
    )


def _market(symbol):
    base, quote = symbol.split("/")
    return MarketInfo(symbol, base, quote, True, True, amount_step=D("1"),
                      min_amount=D("1"), min_cost=D("1"), market_order_enabled=True)


class Gateway:
    def __init__(self, symbols, *, bad=(), ages=None, future=()):
        self.markets = {s: _market(s) for s in symbols}
        self.bad = set(bad)
        self.ages = ages or {}
        self.future = set(future)
        self.fetched = []

    def discover_markets(self):
        return dict(self.markets)

    def fetch_market_frames(self, symbols, *, timeframe, limit, now):
        symbol = list(symbols)[0]
        self.fetched.append(symbol)
        if symbol in self.bad:
            raise RuntimeError("public history unavailable")
        return {symbol: _frame(symbol, age_s=self.ages.get(symbol, 60.0),
                               future=symbol in self.future)}


def _global(root, *, capital=10000):
    cfg = root / "docich.toml"
    cfg.write_text(
        "[paths]\nstate_dir = \"run\"\n"
        "[trading]\npaper_worker_enabled = true\ninterval_s = 60\n"
        f"paper_capital_jpy = {capital}\n",
        encoding="utf-8",
    )
    return config.load_global(root, config_path=cfg)


def _status(g):
    return json.loads((g.state_dir / "trading" / "status.json").read_text(encoding="utf-8"))


class TestFreshnessAssessment(unittest.TestCase):
    def test_fresh_and_closed_bar(self):
        assessed = F.assess_market_freshness(
            "BTC/JPY", fetched_at=NOW, data_as_of=NOW - 60, timeframe_s=300,
            last_bar_start=NOW - 360, now=NOW)
        self.assertEqual((assessed.quality, assessed.reason_code), ("fresh", "ok"))
        self.assertTrue(assessed.bar_closed)

    def test_forming_bar_is_fresh_but_not_closed(self):
        assessed = F.assess_market_freshness(
            "BTC/JPY", fetched_at=NOW, data_as_of=NOW - 60, timeframe_s=300,
            last_bar_start=NOW - 60, now=NOW)
        self.assertEqual(assessed.quality, "fresh")
        self.assertFalse(assessed.bar_closed)

    def test_stale_bar(self):
        assessed = F.assess_market_freshness(
            "BTC/JPY", fetched_at=NOW, data_as_of=NOW - 601, timeframe_s=300,
            last_bar_start=NOW - 601, now=NOW)
        self.assertEqual((assessed.quality, assessed.reason_code), ("stale", "stale_data"))

    def test_future_bar_is_never_fresh(self):
        assessed = F.assess_market_freshness(
            "BTC/JPY", fetched_at=NOW, data_as_of=NOW + 3600, timeframe_s=300,
            last_bar_start=NOW + 3600, now=NOW)
        self.assertEqual(assessed.quality, "invalid")
        self.assertEqual(assessed.reason_code, "future_data")

    def test_nan_is_invalid(self):
        assessed = F.assess_market_freshness(
            "BTC/JPY", fetched_at=float("nan"), data_as_of=NOW - 60,
            timeframe_s=300, last_bar_start=NOW - 360, now=NOW)
        self.assertEqual(assessed.quality, "invalid")

    def test_not_attempted_and_fetch_error(self):
        missing = F.assess_market_freshness(
            "BTC/JPY", fetched_at=None, data_as_of=None, timeframe_s=None,
            last_bar_start=None, now=NOW, attempted=False)
        self.assertEqual((missing.quality, missing.reason_code), ("missing", "not_attempted_budget"))
        failed = F.assess_market_freshness(
            "BTC/JPY", fetched_at=None, data_as_of=None, timeframe_s=None,
            last_bar_start=None, now=NOW, error="fetch_error")
        self.assertEqual((failed.quality, failed.reason_code), ("missing", "fetch_error"))


class TestSnapshotRetention(unittest.TestCase):
    def test_running_heartbeat_preserves_last_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY", "ETH/JPY"])
            first = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW)
            status = _status(g)
            self.assertEqual(status["market_count"], 2)
            self.assertEqual(status["snapshot_seq"], 1)
            self.assertEqual(status["snapshot_generated_at"], NOW)
            # Second cycle with total discovery failure keeps the snapshot.
            class DeadGateway:
                def discover_markets(self):
                    raise RuntimeError("down")
            second = run_worker_cycle(
                g, gateway=DeadGateway(), cycle_index=2, now=NOW + 60,
                observation_now_fn=lambda: NOW + 60)
            self.assertEqual(second.worker_state, "paper_worker_degraded")
            status = _status(g)
            self.assertEqual(status["market_count"], 2)
            self.assertEqual(status["eligible_symbols"], ["BTC/JPY", "ETH/JPY"])
            self.assertEqual(status["snapshot_seq"], 1)
            self.assertEqual(status["snapshot_generated_at"], NOW)
            self.assertEqual(status["heartbeat_at"], NOW + 60)

    def test_partial_failure_keeps_fresh_and_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY", "ETH/JPY", "SOL/JPY"], bad={"ETH/JPY"})
            result = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW)
            self.assertIn("frame_fetch_error", result.error_codes)
            status = _status(g)
            fresh = status["market_freshness"]
            self.assertEqual(fresh["BTC/JPY"]["quality"], "fresh")
            self.assertEqual(fresh["ETH/JPY"]["quality"], "missing")
            self.assertEqual(fresh["ETH/JPY"]["reason_code"], "fetch_error")
            self.assertEqual(fresh["SOL/JPY"]["quality"], "fresh")
            # Snapshot still advances on a partial batch.
            self.assertEqual(status["snapshot_seq"], 1)
            self.assertEqual(status["coverage"]["attempted"], 3)
            self.assertEqual(status["coverage"]["total"], 3)

    def test_all_failed_keeps_snapshot_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            dead = Gateway(["BTC/JPY"], bad={"BTC/JPY"})
            result = run_worker_cycle(
                g, gateway=dead, cycle_index=2, now=NOW + 60,
                observation_now_fn=lambda: NOW + 60)
            self.assertEqual(result.worker_state, "paper_worker_degraded")
            status = _status(g)
            self.assertEqual(status["snapshot_seq"], 1)
            self.assertEqual(status["snapshot_generated_at"], NOW)
            self.assertEqual(status["heartbeat_at"], NOW + 60)

    def test_unexpected_exception_preserves_snapshot(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            times = iter([NOW + 60, NOW + 61, NOW + 120])
            with mock.patch("docich.trading.worker.scan_opportunities",
                            side_effect=RuntimeError("boom-mid-cycle")):
                run_paper_worker(
                    g, gateway_factory=lambda: Gateway(["BTC/JPY"]),
                    sleep_fn=lambda seconds: None,
                    now_fn=lambda: next(times), max_cycles=1,
                )
            status = _status(g)
            self.assertEqual(status["worker_state"], "paper_worker_degraded")
            self.assertIn("worker_cycle_error", status["worker_summary"]["error_codes"])
            self.assertEqual(status["snapshot_seq"], 1)
            self.assertEqual(status["snapshot_generated_at"], NOW)
            self.assertNotIn("boom-mid-cycle", json.dumps(status))

    def test_stale_frames_are_not_used_for_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"], ages={"BTC/JPY": 3600.0})
            result = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW)
            self.assertIn("frame_fetch_error", result.error_codes)
            status = _status(g)
            self.assertEqual(status["market_freshness"]["BTC/JPY"]["quality"], "stale")
            # Nothing tradeable from a stale-only cycle.
            self.assertEqual(result.candidate_count, 0)

    def test_future_frames_are_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"], future={"BTC/JPY"})
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            status = _status(g)
            entry = status["market_freshness"]["BTC/JPY"]
            self.assertEqual((entry["quality"], entry["reason_code"]), ("invalid", "future_data"))


class TestBudgetAndRotation(unittest.TestCase):
    def test_budget_exceeded_marks_rest_not_attempted(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["AAA/JPY", "BBB/JPY", "CCC/JPY", "DDD/JPY"])
            clock = {"t": 1000.0}

            def monotonic():
                return clock["t"]

            orig_fetch = gateway.fetch_market_frames

            def slow_fetch(symbols, *, timeframe, limit, now):
                clock["t"] += CYCLE_BUDGET_S + 1
                return orig_fetch(symbols, timeframe=timeframe, limit=limit, now=now)

            gateway.fetch_market_frames = slow_fetch
            result = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW, monotonic_fn=monotonic)
            self.assertIn("cycle_budget_exceeded", result.error_codes)
            status = _status(g)
            attempted = status["coverage"]["attempted"]
            self.assertEqual(attempted, 1)
            self.assertEqual(status["coverage"]["total"], 4)
            self.assertTrue(status["coverage"]["budget_exceeded"])
            not_attempted = [s for s, e in status["market_freshness"].items()
                             if e["reason_code"] == "not_attempted_budget"]
            self.assertEqual(len(not_attempted), 3)

    def test_rotation_revisits_every_symbol(self):
        symbols = ["A/JPY", "B/JPY", "C/JPY"]
        seen = set()
        for cycle in (1, 2, 3):
            order = _rotation_order(symbols, cycle)
            self.assertEqual(order[0], symbols[(cycle - 1) % 3])
            seen.update(order)
        self.assertEqual(seen, set(symbols))

    def test_rotation_spreads_budget_cuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            symbols = ["AAA/JPY", "BBB/JPY", "CCC/JPY"]
            first_attempted, second_attempted = [], []
            for cycle, sink in ((1, first_attempted), (2, second_attempted)):
                gateway = Gateway(symbols)
                clock = {"t": 1000.0}

                def slow_fetch(symbols, *, timeframe, limit, now, _gw=gateway, _clock=clock):
                    _clock["t"] += CYCLE_BUDGET_S + 1
                    return Gateway.fetch_market_frames(_gw, symbols, timeframe=timeframe, limit=limit, now=now)

                gateway.fetch_market_frames = slow_fetch
                run_worker_cycle(
                    g, gateway=gateway, cycle_index=cycle, now=NOW,
                    observation_now_fn=lambda: NOW, monotonic_fn=lambda: clock["t"])
                sink.extend(gateway.fetched)
            self.assertNotEqual(first_attempted, second_attempted)


class TestMarketCache(unittest.TestCase):
    def test_cache_survives_failed_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            cache = load_cache(cache_path(g.state_dir))
            self.assertIn("BTC/JPY", cache)
            self.assertEqual(cache["BTC/JPY"]["last_close"], "100")
            dead = Gateway(["BTC/JPY"], bad={"BTC/JPY"})
            run_worker_cycle(g, gateway=dead, cycle_index=2, now=NOW + 60,
                             observation_now_fn=lambda: NOW + 60)
            cache = load_cache(cache_path(g.state_dir))
            self.assertIn("BTC/JPY", cache)

    def test_prune_drops_old_entries(self):
        cache: dict = {}
        store_frames(cache, "OLD/JPY", fetched_at=1000.0, data_as_of=900.0,
                     last_bar_start=900.0, timeframe_s=300, last_close="1",
                     closes=["1"], now=1000.0 + 8 * 24 * 3600)
        self.assertNotIn("OLD/JPY", cache)

    def test_malformed_cache_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market_cache.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_cache(path), {})
            path.write_text("[1,2]", encoding="utf-8")
            self.assertEqual(load_cache(path), {})

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market_cache.json"
            cache: dict = {}
            store_frames(cache, "BTC/JPY", fetched_at=NOW, data_as_of=NOW - 60,
                         last_bar_start=NOW - 360, timeframe_s=300,
                         last_close="100", closes=["100"], now=NOW)
            save_cache(path, cache)
            reloaded = load_cache(path)
            self.assertEqual(reloaded["BTC/JPY"]["last_close"], "100")


class TestSummaryUsesSnapshotAge(unittest.TestCase):
    def _corner_manager(self, root, now):
        from docich.paper_corner import PaperCornerManager
        cfg = root / "docich.toml"
        cfg.write_text(
            '[paths]\nstate_dir = "run"\n[trading]\npaper_worker_enabled = true\n'
            'notifications_enabled = true\nnotification_speech_enabled = true\n'
            f'[webui]\nsoren_root = "{root}/soren"\n[paper_corner]\nenabled = true\n'
            'start_hour = 22\nduration_minutes = 30\n')
        g = config.load_global(root, cfg)
        return PaperCornerManager(g, clock=lambda: now, sleep=lambda s: None,
                                  overlay=lambda g, p: None, speech=lambda g, t, **k: None)

    def test_fresh_snapshot_has_no_stale_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = _global(root)
            gateway = Gateway(["BTC/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            mgr = self._corner_manager(root, NOW + 60)
            # PaperCornerManager uses g.state_dir; point it at the worker state.
            mgr.g = g
            self.assertNotIn("集計が古い", mgr.summary())

    def test_old_snapshot_is_stale_despite_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = _global(root)
            gateway = Gateway(["BTC/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            # A later failing cycle advances the heartbeat but not the snapshot.
            dead = Gateway(["BTC/JPY"], bad={"BTC/JPY"})
            run_worker_cycle(g, gateway=dead, cycle_index=2, now=NOW + 3600,
                             observation_now_fn=lambda: NOW + 3600)
            mgr = self._corner_manager(root, NOW + 3600 + 60)
            mgr.g = g
            self.assertIn("集計が古い", mgr.summary())

    def test_legacy_status_without_snapshot_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = _global(root)
            state_dir = g.state_dir / "trading"
            state_dir.mkdir(parents=True)
            (state_dir / "status.json").write_text(json.dumps({
                "capital_reference": "10000", "deployed_reference": "3000",
                "open_positions": {}, "last_cycle_at": NOW,
            }), encoding="utf-8")
            mgr = self._corner_manager(root, NOW + 60)
            mgr.g = g
            self.assertNotIn("集計が古い", mgr.summary())


if __name__ == "__main__":
    unittest.main()


class TestPreservedSnapshotValidation(unittest.TestCase):
    def test_malformed_status_starts_clean(self):
        from docich.trading.worker import _read_previous_status
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.json"
            self.assertEqual(_read_previous_status(missing), {})
            broken = Path(tmp) / "broken.json"
            broken.write_text("{oops", encoding="utf-8")
            self.assertEqual(_read_previous_status(broken), {})
            array = Path(tmp) / "array.json"
            array.write_text("[1]", encoding="utf-8")
            self.assertEqual(_read_previous_status(array), {})

    def test_nonfinite_snapshot_is_rejected(self):
        from docich.trading.worker import _preserved_snapshot
        seq, generated, _freshness, _coverage = _preserved_snapshot({
            "snapshot_seq": True, "snapshot_generated_at": float("nan"),
            "market_freshness": [], "coverage": None})
        self.assertIsNone(seq)
        self.assertIsNone(generated)

    def test_heartbeat_intermediate_write_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY", "ETH/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            seen = {}

            class SpyingGateway(Gateway):
                def fetch_market_frames(self, symbols, *, timeframe, limit, now):
                    seen.update(json.loads(
                        (g.state_dir / "trading" / "status.json").read_text(encoding="utf-8")))
                    return super().fetch_market_frames(
                        symbols, timeframe=timeframe, limit=limit, now=now)

            run_worker_cycle(g, gateway=SpyingGateway(["BTC/JPY", "ETH/JPY"]),
                             cycle_index=2, now=NOW + 60,
                             observation_now_fn=lambda: NOW + 60)
            self.assertEqual(seen.get("worker_state"), "paper_worker_running")
            self.assertEqual(seen.get("market_count"), 2)
            self.assertEqual(seen.get("snapshot_seq"), 1)
            self.assertEqual(seen.get("snapshot_generated_at"), NOW)

    def test_idle_cycle_with_no_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"])
            result = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW)
            # A quiet cycle with no fills is still a healthy idle cycle,
            # not an anomaly: data advanced, snapshot advanced.
            self.assertEqual(result.worker_state, "paper_worker_idle")
            status = _status(g)
            self.assertEqual(status["snapshot_seq"], 1)


class TestCliStatusReaderContracts(unittest.TestCase):
    def test_safe_existing_status_keeps_new_fields(self):
        from docich.trading.cli import _safe_existing_status
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = Gateway(["BTC/JPY"])
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW,
                             observation_now_fn=lambda: NOW)
            safe = _safe_existing_status(g.state_dir / "trading" / "status.json")
            self.assertEqual(safe["snapshot_seq"], 1)
            self.assertIn("BTC/JPY", safe["market_freshness"])
            self.assertEqual(safe["coverage"]["total"], 1)

    def test_safe_existing_status_tolerates_absent_new_fields(self):
        from docich.trading.cli import _safe_existing_status
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({
                "mode": "paper", "recent_fills": [], "signal_summary": {},
                "worker_summary": {}}), encoding="utf-8")
            safe = _safe_existing_status(path)
            self.assertIsNone(safe["snapshot_seq"])
            self.assertEqual(safe["market_freshness"], {})
