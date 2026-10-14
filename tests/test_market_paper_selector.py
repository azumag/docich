import datetime as dt
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docich.trading.markets.__main__ import Runtime
from docich.trading.markets.core import Policy, Quote, report_window
from docich.trading.markets.lab import write_json
from docich.trading.markets.selector import (
    Candidate,
    SelectorPolicy,
    candidate_score,
    rank_candidates,
    read_file_candidates,
    select_universe,
)


def moment(text="2026-09-16T09:30:00+09:00"):
    return dt.datetime.fromisoformat(text).timestamp()


def candidate(now, symbol="7203", *, momentum="20", volume="2", turnover="50000000",
              volatility="30", spread="5", tradeable=True):
    return Candidate(
        symbol=symbol,
        ts=now,
        price="1000",
        turnover_jpy=turnover,
        momentum_bps=momentum,
        volume_accel=volume,
        volatility_bps=volatility,
        spread_bps=spread,
        tradeable=tradeable,
    )


def quote(now, price=100, symbol="7203"):
    return Quote(symbol, now, str(price), str(price + .01), "100000", "100000", True, "test-fixture")


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = moment()
        self.policy = SelectorPolicy(
            focused_universe_count=2,
            candidate_count=5,
            replace_cooldown_s=15,
            replace_margin="0.20",
            warmup_s=60,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_policy_is_bounded(self):
        with self.assertRaises(ValueError):
            SelectorPolicy(focused_universe_count=21)
        with self.assertRaises(ValueError):
            SelectorPolicy(replace_margin="3")
        with self.assertRaises(ValueError):
            SelectorPolicy(candidate_age_s=0)

    def test_ranking_rewards_fast_liquid_move_and_rejects_wide_spread(self):
        quiet = candidate(self.now, "7203", momentum="5", volume="1.1", volatility="10")
        fast = candidate(self.now, "9984", momentum="80", volume="4", volatility="60")
        wide = candidate(self.now, "6758", momentum="300", volume="8", spread="100")
        ranked = rank_candidates([quiet, fast, wide], self.policy, self.now)
        self.assertEqual([row[0].symbol for row in ranked], ["9984", "7203"])
        self.assertGreater(candidate_score(fast, self.policy), candidate_score(quiet, self.policy))

    def test_hysteresis_and_cooldown_prevent_ranking_churn(self):
        initial = [candidate(self.now, "7203", momentum="80"), candidate(self.now, "9984", momentum="70")]
        first = select_universe(
            initial,
            self.policy,
            now=self.now,
            current_symbols=[],
            held_symbols=[],
            last_replaced_at=0,
            session_start=self.now - 120,
        )
        self.assertEqual(first["focused_symbols"], ["7203", "9984"])

        during_cooldown = [
            candidate(self.now + 5, "7203", momentum="75"),
            candidate(self.now + 5, "9984", momentum="70"),
            candidate(self.now + 5, "6758", momentum="78"),
        ]
        cooled = select_universe(
            during_cooldown,
            self.policy,
            now=self.now + 5,
            current_symbols=first["focused_symbols"],
            held_symbols=[],
            last_replaced_at=first["last_replaced_at"],
            session_start=self.now - 120,
        )
        self.assertEqual(cooled["focused_symbols"], first["focused_symbols"])

        # Use a fresh observation at +30s. The earlier +5s rows are correctly
        # stale under the 15-second scalping freshness contract.
        after_cooldown_rows = [
            candidate(self.now + 30, "7203", momentum="75"),
            candidate(self.now + 30, "9984", momentum="70"),
            candidate(self.now + 30, "6758", momentum="78"),
        ]
        stable = select_universe(
            after_cooldown_rows,
            self.policy,
            now=self.now + 30,
            current_symbols=first["focused_symbols"],
            held_symbols=[],
            last_replaced_at=first["last_replaced_at"],
            session_start=self.now - 120,
        )
        self.assertEqual(stable["focused_symbols"], first["focused_symbols"])

        breakout = [
            candidate(self.now + 31, "7203", momentum="75"),
            candidate(self.now + 31, "9984", momentum="50"),
            candidate(self.now + 31, "6758", momentum="200"),
        ]
        replaced = select_universe(
            breakout,
            self.policy,
            now=self.now + 31,
            current_symbols=first["focused_symbols"],
            held_symbols=[],
            last_replaced_at=first["last_replaced_at"],
            session_start=self.now - 120,
        )
        self.assertIn("6758", replaced["focused_symbols"])
        self.assertTrue(replaced["changed"])

    def test_held_symbol_is_pinned_even_if_scanner_drops_it(self):
        result = select_universe(
            [candidate(self.now, "9984", momentum="100")],
            self.policy,
            now=self.now,
            current_symbols=[],
            held_symbols=["7203"],
            last_replaced_at=0,
            session_start=self.now - 120,
        )
        self.assertEqual(result["held_symbols"], ["7203"])
        self.assertIn("7203", result["symbols"])
        self.assertIn("9984", result["symbols"])

    def test_warmup_blocks_entries_without_blocking_selection(self):
        result = select_universe(
            [candidate(self.now, "7203")],
            self.policy,
            now=self.now,
            current_symbols=[],
            held_symbols=[],
            last_replaced_at=0,
            session_start=self.now - 30,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["focused_symbols"], ["7203"])

    def test_file_candidate_feed_is_realtime_and_fresh_only(self):
        path = self.root / "market-stocks-candidates.json"
        write_json(path, {
            "market": "stocks",
            "realtime": True,
            "as_of": self.now,
            "candidates": [asdict(candidate(self.now))],
        })
        rows = read_file_candidates({"candidate_file": path.name}, self.root, self.now, self.policy)
        self.assertEqual([row.symbol for row in rows], ["7203"])

        with self.assertRaises(ValueError):
            read_file_candidates(
                {"candidate_file": path.name}, self.root,
                self.now + self.policy.candidate_age_s + 1, self.policy,
            )

        write_json(path, {
            "market": "stocks",
            "realtime": True,
            "as_of": self.now + 1,
            "candidates": [asdict(candidate(self.now))],
        })
        with self.assertRaises(ValueError):
            read_file_candidates({"candidate_file": path.name}, self.root, self.now, self.policy)

        write_json(path, {
            "market": "stocks",
            "realtime": False,
            "as_of": self.now,
            "candidates": [asdict(candidate(self.now))],
        })
        with self.assertRaises(ValueError):
            read_file_candidates({"candidate_file": path.name}, self.root, self.now, self.policy)


class RuntimeSelectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source = Path(__file__).resolve().parents[1] / "config/market-paper.toml"
        text = source.read_text()
        text = text.replace("[stocks]\nenabled = false", "[stocks]\nenabled = true", 1)
        text = text.replace("[stocks.selector]\n", "[stocks.selector]\nenabled = true\n", 1)
        self.settings = self.root / "market.toml"
        self.settings.write_text(text)
        self.profile = self.root / "docich.toml"
        self.profile.write_text("[paper_corner]\nimprove_agents = ''\n")
        self.g = SimpleNamespace(state_dir=self.root / "run", config_path=self.profile)
        self.runtime = Runtime(self.g, "stocks", self.settings)
        self.now = moment()
        data_root = self.g.state_dir / "market-data"
        data_root.mkdir(parents=True, exist_ok=True)
        write_json(data_root / "jpx-calendar.json", {
            "valid_from": "2026-09-01",
            "valid_through": "2026-09-30",
            "sessions": ["2026-09-16"],
        })

    def tearDown(self):
        self.runtime.book.close()
        self.tmp.cleanup()

    def lease(self, now):
        _, end = report_window("stocks", now)
        write_json(self.runtime.corner_path, {"status": "active", "ends_at": end, "heartbeat": now})

    def test_dynamic_selection_supplies_quote_symbols(self):
        calls = []

        def candidates(_config, _root, now, _policy):
            return [candidate(now, "7203", momentum="100"), candidate(now, "9984", momentum="80")]

        def quotes(config, _market, _root, _now):
            calls.append(list(config["symbols"]))
            return []

        self.lease(self.now)
        with patch("docich.trading.markets.__main__.read_file_candidates", side_effect=candidates), \
             patch("docich.trading.markets.__main__.read_quotes", side_effect=quotes):
            result = self.runtime.tick(clock=lambda: self.now)
        self.assertTrue(result["selector"]["ready"])
        self.assertTrue(result["allow_entries"])
        self.assertEqual(calls[0][:2], ["7203", "9984"])

    def test_entry_cutoff_accounts_for_max_hold_and_exit_buffer(self):
        write_json(self.runtime.root / "active-policy.json", {"policy": asdict(Policy(max_hold_s=900))})

        def candidates(_config, _root, now, _policy):
            return [candidate(now, "7203", momentum="100")]

        before = moment("2026-09-16T09:44:29+09:00")
        after = moment("2026-09-16T09:44:30+09:00")
        with patch("docich.trading.markets.__main__.read_file_candidates", side_effect=candidates), \
             patch("docich.trading.markets.__main__.read_quotes", return_value=[]):
            self.lease(before)
            allowed = self.runtime.tick(clock=lambda: before)
            self.lease(after)
            blocked = self.runtime.tick(clock=lambda: after)
        self.assertTrue(allowed["allow_entries"])
        self.assertFalse(blocked["allow_entries"])
        self.assertEqual(blocked["selector"]["entry_cutoff_at"], after)

    def test_scanner_failure_stops_entries_but_keeps_held_exit_quote(self):
        entry_policy = Policy(lookback=3, entry_bps=2)
        for i in range(3):
            self.runtime.book.process(
                [quote(self.now + i, 100 + i * .1)],
                entry_policy,
                now=self.now + i,
                allow_entries=True,
            )
        self.assertIn("7203", self.runtime.book.state()["positions"])
        seen = []

        def quotes(config, _market, _root, now):
            seen.extend(config["symbols"])
            return [quote(now, 101)]

        self.lease(self.now + 10)
        with patch("docich.trading.markets.__main__.read_file_candidates", side_effect=ValueError("feed down")), \
             patch("docich.trading.markets.__main__.read_quotes", side_effect=quotes):
            result = self.runtime.tick(clock=lambda: self.now + 10)
        self.assertFalse(result["allow_entries"])
        self.assertEqual(result["selector"]["status"], "unavailable")
        self.assertEqual(seen, ["7203"])
        self.assertFalse(self.runtime.book.state()["positions"])


if __name__ == "__main__":
    unittest.main()
