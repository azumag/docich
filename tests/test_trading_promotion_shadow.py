"""Shadow judge for PAPER promotion (Issue #3623 rule v4): read-only, never raises."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docich.trading import promotion_shadow as shadow


def _days(values, start=17):
    return [(f"2026-11-{start + i:02d}", value) for i, value in enumerate(values)]


class EProcessTests(unittest.TestCase):
    def test_matches_hand_computed_rule_v2_values(self):
        # Y = 0.012 - 0.002 = 0.01 for every block. t=1: lam 0, E 1.
        # t=2: mu = 0.01/2, var = (1e-4 + 0.005^2)/2 -> lam clipped to 45, E = 1.45.
        # t=3: lam clipped to 45 again, E = 1.45 ** 2 = 2.1025.
        result = shadow.e_process(_days([0.012, 0.012, 0.012]))
        self.assertAlmostEqual(result["e_value"], 2.1025, places=9)
        self.assertEqual(result["blocks"], 3)
        self.assertFalse(result["ruined"])

    def test_no_effect_is_never_promoted(self):
        result = shadow.e_process(_days([shadow.DELTA] * 30))
        self.assertAlmostEqual(result["e_value"], 1.0, places=9)
        self.assertIsNone(result["promoted_day"])

    def test_strong_steady_effect_is_promoted_after_at_least_min_blocks(self):
        result = shadow.e_process(_days([0.019, 0.021, 0.017, 0.02, 0.018, 0.022] * 3))
        self.assertIsNotNone(result["promoted_day"])
        self.assertGreaterEqual(int(result["promoted_day"][-2:]) - 17 + 1, shadow.MIN_BLOCKS)

    def test_too_few_blocks_are_never_promoted_even_above_the_threshold(self):
        with mock.patch.object(shadow, "THRESHOLD", 5.0):
            few = shadow.e_process(_days([0.5, 0.5, 0.5, 0.5]))
            enough = shadow.e_process(_days([0.5, 0.5, 0.5, 0.5, 0.5]))
        self.assertGreaterEqual(few["e_value"], 5.0)
        self.assertIsNone(few["promoted_day"])
        self.assertEqual(enough["promoted_day"], "2026-11-21")  # the 5th block

    def test_a_strong_effect_is_promoted_within_one_seven_day_slot(self):
        result = shadow.e_process(_days([0.019, 0.022, 0.017, 0.02, 0.018, 0.021, 0.019]))
        self.assertIsNotNone(result["promoted_day"])
        self.assertLessEqual(result["promoted_day"], "2026-11-23")

    def test_break_even_is_not_promoted_within_one_slot(self):
        result = shadow.e_process(_days([0.004, 0.0, 0.003, -0.002, 0.005, 0.001, 0.002]))
        self.assertIsNone(result["promoted_day"])

    def test_threshold_is_frozen_at_five(self):
        self.assertEqual(shadow.THRESHOLD, 5.0)

    def test_a_loss_beyond_the_cap_zeroes_the_cell_for_good(self):
        result = shadow.e_process(_days([0.02, 0.02, 0.02, -0.03, 0.05, 0.05, 0.05, 0.05]))
        self.assertTrue(result["ruined"])
        self.assertEqual(result["e_value"], 0.0)
        self.assertIsNone(result["promoted_day"])


def _cell(symbol, experiment_id, values, start=17, closed_sells=10, v3=True):
    blocks = [
        {"day": day, "count": 1, "mean_return_corrected": str(value),
         "mean_return": str(value)}
        for day, value in _days(values, start)
    ]
    cell = {
        "symbol": symbol, "experiment_id": experiment_id, "closed_sells": closed_sells,
        "return_blocks": blocks,
    }
    if v3:
        cell["return_blocks_v3"] = blocks
    return cell


class BuildShadowTests(unittest.TestCase):
    def _build(self, cells, status="ok"):
        with tempfile.TemporaryDirectory() as directory:
            before = sorted(path.name for path in Path(directory).iterdir())
            with mock.patch.object(shadow, "aggregate_cell_metrics",
                                   return_value={"status": status, "cells": cells}):
                result = shadow.build_promotion_shadow(directory)
            after = sorted(path.name for path in Path(directory).iterdir())
        self.assertEqual(before, after)  # read-only: nothing written
        return result

    def test_only_slot_experiment_cells_are_judged(self):
        strong = [0.019, 0.021, 0.017, 0.02, 0.018, 0.022] * 3
        result = self._build([
            _cell("ADA/JPY", "ada_261116", strong),            # slot experiment
            _cell("OAS/JPY", "snapback_harvest_v25", strong),  # common experiment
            _cell("ADA/JPY", "xrp_261116", strong),            # id of another symbol
        ])
        self.assertEqual(result["cells_evaluated"], 1)
        self.assertEqual(result["promotable_count"], 1)
        self.assertEqual(result["promotable"][0]["symbol"], "ADA/JPY")
        self.assertEqual(result["promotable"][0]["experiment_id"], "ada_261116")
        self.assertEqual(result["rule_version"], "v4")
        self.assertNotIn("first_block_day", result)

    def test_blocks_before_the_old_rule_v3_date_are_now_included(self):
        result = self._build([_cell("ADA/JPY", "ada_261116", [0.5] * 8, start=9)])
        self.assertEqual(result["cells_evaluated"], 1)
        self.assertEqual(result["promotable_count"], 1)

    def test_v2_only_blocks_are_never_promoted(self):
        result = self._build([_cell("ADA/JPY", "ada_261116", [0.5] * 8, v3=False)])
        self.assertEqual(result["cells_evaluated"], 0)
        self.assertEqual(result["promotable_count"], 0)

    def test_five_strong_v3_day_blocks_are_promotable(self):
        result = self._build([_cell("ADA/JPY", "ada_261116", [0.019] * 5)])
        self.assertEqual(result["cells_with_min_blocks"], 1)
        self.assertEqual(result["promotable_count"], 1)

    def test_counts_and_ruin(self):
        result = self._build([
            _cell("ADA/JPY", "ada_261116", [0.01] * 6),
            _cell("XRP/JPY", "xrp_261116", [0.02, -0.05, 0.02]),
        ])
        self.assertEqual(result["cells_evaluated"], 2)
        self.assertEqual(result["cells_with_min_blocks"], 1)
        self.assertEqual(result["ruined_count"], 1)
        self.assertEqual(result["promotable_count"], 0)

    def test_unavailable_ledger_and_exceptions_never_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = shadow.build_promotion_shadow(Path(directory) / "nope")
            self.assertEqual(missing["status"], "unavailable")
            self.assertEqual(missing["promotable_count"], 0)
            with mock.patch.object(shadow, "aggregate_cell_metrics", side_effect=RuntimeError("x")):
                crashed = shadow.build_promotion_shadow(directory)
            self.assertEqual(crashed["status"], "unavailable")

    def test_malformed_blocks_are_skipped(self):
        cell = _cell("ADA/JPY", "ada_261116", [0.01] * 6)
        cell["return_blocks_v3"].extend([{"day": "2026-11-30", "mean_return_corrected": "nan"},
                                         {"day": "2026-11-30", "mean_return_corrected": "oops"}, 7])
        result = self._build([cell])
        self.assertEqual(result["cells_evaluated"], 1)

    def test_result_is_json_serialisable(self):
        result = self._build([_cell("ADA/JPY", "ada_261116", [0.02] * 12)])
        json.dumps(result, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
