import unittest
from pathlib import Path

from docich.trading.markets.health_worker import preserve_last_market_as_of


class MarketPaperHealthWorkerTests(unittest.TestCase):
    def test_fresh_tick_advances_last_accepted_timestamp(self):
        result = {"status": "ok", "market_as_of": 100.0, "accepted_quotes": 1, "rejected_quotes": []}
        merged, last = preserve_last_market_as_of(result, 90.0)
        self.assertIs(merged, result)
        self.assertEqual(last, 100.0)
        self.assertEqual(merged["accepted_quotes"], 1)

    def test_stale_tick_keeps_last_timestamp_but_stays_rejected(self):
        result = {
            "status": "ok",
            "market_as_of": 0,
            "accepted_quotes": 0,
            "rejected_quotes": ["USD_JPY"],
        }
        merged, last = preserve_last_market_as_of(result, 100.0)
        self.assertEqual(last, 100.0)
        self.assertEqual(merged["market_as_of"], 100.0)
        self.assertEqual(merged["accepted_quotes"], 0)
        self.assertEqual(merged["rejected_quotes"], ["USD_JPY"])

    def test_disabled_worker_does_not_invent_health_timestamp(self):
        result = {"status": "disabled", "mode": "paper"}
        merged, last = preserve_last_market_as_of(result, 100.0)
        self.assertEqual(merged, result)
        self.assertNotIn("market_as_of", merged)
        self.assertEqual(last, 100.0)

    def test_invalid_future_state_does_not_replace_previous(self):
        for value in (None, True, "NaN", float("inf"), -1, 0):
            with self.subTest(value=value):
                merged, last = preserve_last_market_as_of(
                    {"status": "ok", "market_as_of": value, "accepted_quotes": 0}, 100.0
                )
                self.assertEqual(last, 100.0)
                self.assertEqual(merged["market_as_of"], 100.0)

    def test_launcher_routes_only_resident_worker_to_wrapper(self):
        launcher = Path(__file__).resolve().parents[1] / "bin" / "docich-market-paper"
        text = launcher.read_text(encoding="utf-8")
        self.assertIn('if [[ "${*: -1}" == "worker" ]]', text)
        self.assertIn("docich.trading.markets.health_worker", text)
        self.assertIn("docich.trading.markets \"$@\"", text)


if __name__ == "__main__":
    unittest.main()
