from __future__ import annotations

import json
import sys
from dataclasses import replace
import tempfile
import unittest
from unittest import mock
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.depth import DepthBook, DepthLevel  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.settlement import CircuitBreakStatus  # noqa: E402
from docich.trading.worker import run_paper_worker, run_worker_cycle  # noqa: E402

D = Decimal
NOW = 1_800_000_000.0


def _frame(symbol: str, *, momentum: bool = False) -> MarketFrame:
    timestamps = tuple(NOW - (23 - i) * 300 for i in range(24))
    closes = [D("100") for _ in range(24)]
    if momentum:
        closes[-1] = D("104")
    return MarketFrame(
        symbol=symbol,
        timeframe_seconds=300,
        timestamps=timestamps,
        closes=tuple(closes),
        volumes=tuple(D("1") for _ in range(24)),
    )


def _market(symbol: str) -> MarketInfo:
    base, quote = symbol.split("/")
    return MarketInfo(
        symbol=symbol,
        base=base,
        quote=quote,
        spot=True,
        active=True,
        amount_step=D("1"),
        min_amount=D("1"),
        min_cost=D("1"),
        market_order_enabled=True,
    )


class FakeStrategyGateway:
    def __init__(self, *, one_bad_symbol: bool = False):
        self.markets = {
            "BTC/JPY": _market("BTC/JPY"),
            "ETH/JPY": _market("ETH/JPY"),
            "SOL/JPY": _market("SOL/JPY"),
        }
        if one_bad_symbol:
            self.markets["BAD/JPY"] = _market("BAD/JPY")
        self.frames = {
            "BTC/JPY": _frame("BTC/JPY", momentum=True),
            "ETH/JPY": _frame("ETH/JPY"),
            "SOL/JPY": _frame("SOL/JPY"),
        }
        self.one_bad_symbol = one_bad_symbol

    def discover_markets(self):
        return dict(self.markets)

    def fetch_market_frames(self, symbols, *, timeframe, limit, now):
        self.last_frame_args = (timeframe, limit, now)
        symbol = list(symbols)[0]
        if self.one_bad_symbol and symbol == "BAD/JPY":
            raise RuntimeError("public history unavailable")
        return {symbol: self.frames[symbol]}


def _global(root: Path, *, capital: int = 10000):
    cfg = root / "docich.toml"
    cfg.write_text(
        "[paths]\nstate_dir = \"run\"\n"
        "[trading]\npaper_worker_enabled = true\ninterval_s = 60\n"
        f"paper_capital_jpy = {capital}\n",
        encoding="utf-8",
    )
    return config.load_global(root, config_path=cfg)


def _level(price, amount):
    return DepthLevel(D(str(price)), D(str(amount)))


class FakeTriangleGateway:
    def __init__(self, *, fail_depth: bool = False):
        self.fail_depth = fail_depth
        self.markets = {
            "BTC/JPY": MarketInfo("BTC/JPY", "BTC", "JPY", True, True, amount_step=D("0.0001"), min_amount=D("0.0001"), taker_fee_rate=D("0.001")),
            "ETH/BTC": MarketInfo("ETH/BTC", "ETH", "BTC", True, True, amount_step=D("0.0001"), min_amount=D("0.0001"), taker_fee_rate=D("0.001")),
            "ETH/JPY": MarketInfo("ETH/JPY", "ETH", "JPY", True, True, amount_step=D("0.0001"), min_amount=D("0.0001"), taker_fee_rate=D("0.001")),
        }

    def discover_markets(self):
        return dict(self.markets)

    def fetch_market_frames(self, symbols, *, timeframe, limit, now):
        symbol = list(symbols)[0]
        return {symbol: _frame(symbol)}

    def fetch_circuit_break_statuses(self, symbols):
        return {symbol: CircuitBreakStatus(symbol, "NONE", "NORMAL", NOW, fetched_at=NOW) for symbol in symbols}

    def fetch_depth_books(self, symbols, *, now, limit=20):
        if self.fail_depth:
            raise RuntimeError("depth unavailable")
        all_books = {
            "BTC/JPY": DepthBook("BTC/JPY", (_level(99, 1000),), (_level(100, 1000),), NOW),
            "ETH/BTC": DepthBook("ETH/BTC", (_level("0.049", 10000),), (_level("0.05", 10000),), NOW),
            "ETH/JPY": DepthBook("ETH/JPY", (_level("5.2", 10000),), (_level("5.3", 10000),), NOW),
        }
        return {symbol: all_books[symbol] for symbol in symbols}


class TestPaperWorkerCycle(unittest.TestCase):
    def test_cycle_isolates_bad_frame_and_reuses_thirty_percent_allocator(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp), capital=10000)
            result = run_worker_cycle(
                g,
                gateway=FakeStrategyGateway(one_bad_symbol=True),
                cycle_index=1,
                now=NOW,
            )
            self.assertEqual(result.frame_error_count, 1)
            self.assertGreaterEqual(result.new_fill_count, 1)
            self.assertIn("frame_fetch_error", result.error_codes)
            ledger = PaperLedger(g.state_dir / "trading" / "paper.sqlite3")
            try:
                self.assertLessEqual(ledger.deployed_reference(), D("3000"))
                self.assertGreater(ledger.deployed_reference(), D("0"))
            finally:
                ledger.close()
            status = json.loads((g.state_dir / "trading" / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["worker_state"], "paper_worker_degraded")
            self.assertEqual(status["worker_summary"]["frame_error_count"], 1)
            self.assertNotIn("public history unavailable", json.dumps(status))

    def test_replaying_same_frames_does_not_duplicate_fill_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = FakeStrategyGateway()
            first = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW, observation_now_fn=lambda: NOW
            )
            second = run_worker_cycle(
                g, gateway=gateway, cycle_index=2, now=NOW, observation_now_fn=lambda: NOW
            )
            self.assertGreaterEqual(first.new_fill_count, 1)
            self.assertEqual(second.new_fill_count, 0)
            rows = [
                json.loads(line)
                for line in (g.state_dir / "trading" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            fills = [row for row in rows if row["event_type"] == "paper_fill"]
            self.assertEqual(len(fills), 1)
            ledger = PaperLedger(g.state_dir / "trading" / "paper.sqlite3")
            try:
                self.assertEqual(len(ledger.recent_fills(limit=20)), 1)
            finally:
                ledger.close()

    def test_cycle_uses_fixed_public_history_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = FakeStrategyGateway()
            run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW)
            self.assertEqual(gateway.last_frame_args, ("5m", 24, NOW))


class TestPaperWorkerArbitrageAndLoop(unittest.TestCase):
    def test_triangle_cycle_records_settlements_and_events_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            gateway = FakeTriangleGateway()
            first = run_worker_cycle(
                g, gateway=gateway, cycle_index=1, now=NOW, observation_now_fn=lambda: NOW
            )
            second = run_worker_cycle(
                g, gateway=gateway, cycle_index=2, now=NOW, observation_now_fn=lambda: NOW
            )
            self.assertGreaterEqual(first.arbitrage_candidate_count, 1)
            self.assertGreaterEqual(first.new_settlement_count, 1)
            self.assertEqual(second.new_settlement_count, 0)
            ledger = PaperLedger(g.state_dir / "trading" / "paper.sqlite3")
            try:
                saved = ledger.recent_multileg_settlements(limit=20)
                self.assertGreaterEqual(len(saved), 1)
            finally:
                ledger.close()
            events = [json.loads(line) for line in (g.state_dir / "trading" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len([e for e in events if e["event_type"] == "multileg_settlement"]), first.new_settlement_count)

    def test_arbitrage_freshness_uses_time_after_depth_fetch(self):
        class DelayedDepthGateway(FakeTriangleGateway):
            def fetch_depth_books(self, symbols, *, now, limit=20):
                books = super().fetch_depth_books(symbols, now=now, limit=limit)
                return {symbol: replace(book, as_of=NOW + 1.5) for symbol, book in books.items()}

        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            result = run_worker_cycle(
                g, gateway=DelayedDepthGateway(), cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW + 2,
            )
            self.assertGreaterEqual(result.arbitrage_candidate_count, 1)
            self.assertNotIn("arbitrage_data_error", result.error_codes)

    def test_arbitrage_fetch_failure_is_degraded_without_partial_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            result = run_worker_cycle(
                g, gateway=FakeTriangleGateway(fail_depth=True), cycle_index=1, now=NOW,
                observation_now_fn=lambda: NOW,
            )
            self.assertIn("arbitrage_data_error", result.error_codes)
            self.assertEqual(result.new_settlement_count, 0)
            ledger = PaperLedger(g.state_dir / "trading" / "paper.sqlite3")
            try:
                self.assertEqual(ledger.recent_multileg_settlements(limit=20), [])
            finally:
                ledger.close()

    def test_loop_catches_cycle_error_and_sleeps_without_exiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            sleep_calls = []
            class BrokenGateway:
                def discover_markets(self):
                    raise RuntimeError("boom-public")
            times = iter([NOW, NOW + 1, NOW + 60, NOW + 61])
            run_paper_worker(
                g, gateway_factory=BrokenGateway, sleep_fn=lambda seconds: sleep_calls.append(seconds),
                now_fn=lambda: next(times), max_cycles=2,
            )
            self.assertEqual(sleep_calls, [D("59")])
            status = json.loads((g.state_dir / "trading" / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["worker_state"], "paper_worker_degraded")
            self.assertIn("worker_cycle_error", status["worker_summary"]["error_codes"])
            self.assertNotIn("boom-public", json.dumps(status))



    def test_notification_timestamp_is_sampled_after_market_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            g.trading.notifications_enabled = True
            observed = []
            result = type("Cycle", (), {"last_success_at": NOW})()
            times = iter([NOW, NOW + 30])
            with mock.patch("docich.trading.worker.run_worker_cycle", return_value=result), \
                 mock.patch("docich.trading.worker.deliver_pending_notifications", side_effect=lambda *a, **k: observed.append(k["now"])):
                run_paper_worker(
                    g, gateway_factory=lambda: object(), sleep_fn=lambda _seconds: None,
                    now_fn=lambda: next(times), max_cycles=1,
                )
            self.assertEqual(observed, [NOW + 30])

    def test_notification_failure_does_not_stop_market_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = _global(Path(tmp))
            g.trading.notifications_enabled = True
            cycle_calls = []
            notification_calls = []
            result = type("Cycle", (), {"last_success_at": NOW})()
            times = iter([NOW, NOW + 1, NOW + 2, NOW + 60, NOW + 61])
            with mock.patch("docich.trading.worker.run_worker_cycle", side_effect=lambda *a, **k: cycle_calls.append(k["cycle_index"]) or result),                  mock.patch("docich.trading.worker.deliver_pending_notifications", side_effect=lambda *a, **k: notification_calls.append(1) or (_ for _ in ()).throw(RuntimeError("sink"))):
                run_paper_worker(
                    g, gateway_factory=lambda: object(), sleep_fn=lambda _seconds: None,
                    now_fn=lambda: next(times), max_cycles=2,
                )
            self.assertEqual(cycle_calls, [1, 2])
            self.assertEqual(len(notification_calls), 2)

if __name__ == "__main__":
    unittest.main()
