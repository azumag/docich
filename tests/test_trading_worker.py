from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import MarketInfo  # noqa: E402
from docich.trading.worker import run_worker_cycle  # noqa: E402

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
            first = run_worker_cycle(g, gateway=gateway, cycle_index=1, now=NOW)
            second = run_worker_cycle(g, gateway=gateway, cycle_index=2, now=NOW)
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


if __name__ == "__main__":
    unittest.main()
