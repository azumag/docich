"""Keep the collection status compatible without consuming pre-proposal data."""
import datetime as dt
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from docich.trading.markets.core import Limits, PaperBook, Policy, Quote
from docich.trading.markets.lab import active_policy, advance_challenger, write_json


class ForwardWaitTests(unittest.TestCase):
    def test_wait_keeps_collecting_without_creating_evidence_or_adopting(self):
        created_at = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        for mode in ("fixed", "trailing"):
            for offset in (-1, 0):
                with self.subTest(mode=mode, offset=offset), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    baseline = Policy()
                    candidate = (replace(baseline, entry_bps=20) if mode == "fixed"
                                 else replace(baseline, exit_mode="trailing"))
                    # Legacy markers omit mode/live_enabled; they remain PAPER.
                    marker = {"id": "a" * 24, "created_at": created_at,
                              "baseline": asdict(baseline), "policy": asdict(candidate)}
                    write_json(root / "challenger.json", marker)
                    book = PaperBook(root / "paper.sqlite3", "stocks", Limits())
                    try:
                        before = book.state()
                        quote = Quote("TEST", created_at + offset, "100", "100",
                                      "100000", "100000", True, "synthetic-fixture")
                        result = advance_challenger(book, root, [quote], now=created_at + offset,
                                                    allow_entries=True, force_flat=False)
                        self.assertEqual(result["status"], "collecting")
                        self.assertEqual(result["reason"], "awaiting_future_quotes")
                        self.assertEqual(book.state(), before)
                        self.assertFalse((root / "experiments").exists())
                        self.assertFalse((root / "active-policy.json").exists())
                        self.assertEqual(active_policy(root), baseline)
                        self.assertEqual(json.loads((root / "challenger.json").read_text()), marker)
                    finally:
                        book.close()

    def test_future_quote_starts_evidence_after_wait(self):
        created_at = dt.datetime.fromisoformat("2026-09-16T09:30:00+09:00").timestamp()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = Policy()
            write_json(root / "challenger.json", {
                "id": "a" * 24, "created_at": created_at,
                "baseline": asdict(baseline), "policy": asdict(replace(baseline, exit_mode="trailing")),
            })
            book = PaperBook(root / "paper.sqlite3", "stocks", Limits())
            try:
                for offset in (0, 1):
                    quote = Quote("TEST", created_at + offset, "100", "100",
                                  "100000", "100000", True, "synthetic-fixture")
                    result = advance_challenger(book, root, [quote], now=created_at + offset,
                                                allow_entries=True, force_flat=False)
                    self.assertEqual(result["status"], "collecting")
                self.assertNotIn("reason", result)
                self.assertEqual(result["closed_trades"], {"baseline": 0, "policy": 0})
                for name in ("baseline", "policy"):
                    test = PaperBook(root / "experiments" / ("a" * 24) / f"{name}.sqlite3",
                                     "stocks", Limits())
                    try:
                        self.assertEqual(test.state()["history"]["TEST"], [[created_at + 1, "100"]])
                        self.assertFalse(test.state()["positions"])
                    finally:
                        test.close()
                self.assertEqual(active_policy(root), baseline)
            finally:
                book.close()


if __name__ == "__main__":
    unittest.main()
