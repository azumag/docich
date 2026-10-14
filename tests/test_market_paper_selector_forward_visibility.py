import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from docich.trading.markets.lab import write_json
from docich.trading.markets.selector import (Candidate, SelectorPolicy,
                                             rank_candidates, read_file_candidates)


class SelectorForwardVisibilityTests(unittest.TestCase):
    def test_feed_keeps_candidate_excluded_only_by_baseline_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = 1789517400.0
            low_turnover = Candidate(
                symbol="7203", ts=now, price="1000", turnover_jpy="6000000",
                momentum_bps="100", volume_accel="3", volatility_bps="50",
                spread_bps="10", tradeable=True,
            )
            write_json(root / "candidates.json", {
                "market": "stocks", "realtime": True, "as_of": now,
                "candidates": [asdict(low_turnover)],
            })
            baseline = SelectorPolicy(min_turnover_jpy="10000000")
            challenger = SelectorPolicy(min_turnover_jpy="5000000")
            rows = read_file_candidates(
                {"candidate_file": "candidates.json"}, root, now, baseline,
            )
            self.assertEqual([item.symbol for item in rows], ["7203"])
            self.assertEqual(rank_candidates(rows, baseline, now), [])
            self.assertEqual(
                [item.symbol for item, _score in rank_candidates(rows, challenger, now)],
                ["7203"],
            )


if __name__ == "__main__":
    unittest.main()
