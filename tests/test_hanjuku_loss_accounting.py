"""Regression tests: the corner status must not read "0 losses" while losing.

2026-10-02 production measurement on g534:

- The status panel showed ``戦闘結果: 23勝 / 1敗 / 未分類 0`` while
  ``castle_lost_observed`` fired 6 times in the same run (ジョンリギ,
  キカンドン, スペンソニア, ゴーメン, ナキューメラ, ジョンリギ) and the panel
  itself listed three of them under 失った城.
- Only one of those defeats became a ``loss``: ``stats`` advances solely in
  ``battle_end`` when the final HP panel is decisive, so a defeat observed on
  the world map left no trace in the win/loss line.
- ``未分類 0`` made it read as fully accounted, because that counter only
  covers battles that reached ``battle_end``.
- The corner's own ``戦闘: 開始 / 終了`` pair counted raw frame transitions into
  the battle phase, so a blinking battle panel inflated it (45 frame entries
  against 24 real battles; reported as 34 started / 33 finished).

These tests pin the accounting: every started battle is either judged or
explicitly reported as unjudged, castle losses are counted on their own, and
the frame-transition counters are debounced.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import hanjuku_policy, hanjuku_run  # noqa: E402


def _tally(mem: dict) -> dict:
    return mem.get("tally") if isinstance(mem.get("tally"), dict) else {}


class LossAccountingTests(unittest.TestCase):
    """A castle loss is defeat evidence even without a decisive HP panel."""

    def test_castle_loss_observation_counts_even_without_a_battle_verdict(self):
        mem: dict = {}
        hanjuku_policy._tally(mem, "battles_started")
        hanjuku_policy._tally(mem, "battles_judged")
        # The defeat that mattered was only ever seen on the world map.
        hanjuku_policy._tally(mem, "castle_losses")
        summary = hanjuku_policy.summary(mem)
        self.assertEqual(summary["losses"], None)
        self.assertEqual(summary["castle_losses"], 1)
        self.assertEqual(summary["battles_started"], 1)
        self.assertEqual(summary["battles_judged"], 1)
        self.assertEqual(summary["battles_unjudged"], 0)

    def test_repeated_castle_loss_events_are_all_counted(self):
        # ジョンリギ fell twice in g534; both events are defeat evidence.
        mem: dict = {}
        hanjuku_policy._tally(mem, "castle_losses")
        hanjuku_policy._tally(mem, "castle_losses")
        self.assertEqual(hanjuku_policy.summary(mem)["castle_losses"], 2)

    def test_a_battle_without_a_verdict_is_unjudged_not_a_win_or_loss(self):
        # A started battle whose battle_end never produced a verdict must show
        # up in the gap instead of vanishing from the accounting.
        mem: dict = {"tally": {"battles_started": 5, "battles_judged": 2}}
        summary = hanjuku_policy.summary(mem)
        self.assertEqual(summary["battles_unjudged"], 3)

    def test_unjudged_never_goes_negative_on_a_torn_or_replayed_record(self):
        # judged > started can only come from a torn or replayed record. The
        # panel must not render a negative gap.
        mem: dict = {"tally": {"battles_started": 1, "battles_judged": 4}}
        self.assertEqual(hanjuku_policy.summary(mem)["battles_unjudged"], 0)

    def test_missing_tally_reports_unknown_rather_than_zero(self):
        # A run with no tally yet is unknown, not "0 started, 0 unjudged",
        # which would read as a clean account.
        summary = hanjuku_policy.summary({})
        self.assertIsNone(summary["battles_started"])
        self.assertIsNone(summary["battles_judged"])
        self.assertIsNone(summary["battles_unjudged"])
        self.assertIsNone(summary["castle_losses"])

    def test_tally_ignores_a_corrupt_counter_instead_of_crashing(self):
        mem: dict = {"tally": {"battles_started": "22", "castle_losses": -3}}
        hanjuku_policy._tally(mem, "battles_started")
        hanjuku_policy._tally(mem, "castle_losses")
        summary = hanjuku_policy.summary(mem)
        self.assertEqual(summary["battles_started"], 1)
        self.assertEqual(summary["castle_losses"], 1)

    def test_tally_does_not_touch_the_strategy_facing_stats(self):
        # stats feeds experience learning and sortie retries. The new counters
        # are display-only and must not move wins/losses/unclassified.
        mem: dict = {"stats": {"wins": 23, "losses": 1, "unclassified": 0}}
        hanjuku_policy._tally(mem, "castle_losses")
        hanjuku_policy._tally(mem, "battles_started")
        summary = hanjuku_policy.summary(mem)
        self.assertEqual(summary["wins"], 23)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["unclassified"], 0)


class BattlePhaseCounterTests(unittest.TestCase):
    """A blinking battle panel must not inflate the start/finish counters.

    Phase classification is stubbed so the test drives the observation
    debounce directly. Painting pixels that `classify` reads as a real battle
    screen is not the subject here, and the production classifier has its own
    dedicated tests.
    """

    def _drive(self, phases: list[str]) -> dict:
        import tempfile
        from unittest import mock

        from docich.hanjuku_pixels import Frame

        identity = {
            "game": "hanjuku-hero",
            "runtime_id": "g999-abcdef01",
            "generation": 999,
            "lease_id": "lease-hanjuku-loss-accounting",
        }
        with tempfile.TemporaryDirectory() as raw:
            runtime_dir = Path(raw)
            state: dict = {}
            clock = 1000.0
            for index, phase in enumerate(phases):
                # A distinct image per step, so the unchanged-screen timer
                # never turns a phase change into stasis evidence.
                frame = Frame(256, 224, bytes([index % 251]) * (256 * 224 * 3))
                clock += 1.0
                with mock.patch.object(hanjuku_run, "classify", return_value=phase):
                    state = hanjuku_run.observe(
                        runtime_dir, identity, frame, now=clock, wall=clock)
            return state

    def test_a_single_battle_frame_does_not_count_as_a_battle(self):
        # One battle frame that immediately flickers away is not a battle.
        state = self._drive(["map", "battle", "map", "map", "map"])
        self.assertEqual(state["battles_started"], 0)
        self.assertEqual(state["battles_finished"], 0)

    def test_a_real_battle_is_counted_once_after_the_debounce(self):
        state = self._drive(
            ["map", "battle", "battle", "battle", "map", "map", "map"])
        self.assertEqual(state["battles_started"], 1)
        self.assertEqual(state["battles_finished"], 1)

    def test_a_battle_that_blinks_mid_melee_is_still_one_battle(self):
        # The panel blinks during the melee. Each blink must not add a start.
        state = self._drive(
            ["map", "battle", "battle", "map", "battle", "battle",
             "battle", "map", "map"])
        self.assertEqual(state["battles_started"], 1)
        self.assertEqual(state["battles_finished"], 1)

    def test_battles_finished_counts_a_battle_that_leaves_to_map(self):
        # The old whitelist only finished a battle when the exit phase was
        # field/field_menu/dialogue/shop/month_menu, so a battle that ended
        # back on the map was never counted as finished.
        state = self._drive(["map", "battle", "battle", "map", "map"])
        self.assertEqual(state["battles_started"], 1)
        self.assertEqual(state["battles_finished"], 1)

    def test_two_separate_battles_are_counted_twice(self):
        state = self._drive(
            ["battle", "battle", "map", "map", "map",
             "battle", "battle", "map", "map"])
        self.assertEqual(state["battles_started"], 2)
        self.assertEqual(state["battles_finished"], 2)


class PanelRenderTests(unittest.TestCase):
    """The renderer must not present the judged subset as the whole run."""

    def _render(self, snippet: dict) -> str:
        # The pinned submodule, not a local working checkout: CI checks out
        # submodules recursively, so this verifies the committed renderer
        # instead of whatever happens to be on someone's disk.
        root = ROOT / "games" / "soviet_now"
        if not (root / "status_dashboard.py").is_file():
            self.skipTest("games/soviet_now submodule not checked out")
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]);"
             "import status_dashboard as sd;"
             "print('\\n'.join(sd.render_hanjuku_status(__import__('json').loads(sys.argv[2]))))",
             str(root), json.dumps(snippet)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return proc.stdout

    def _base(self) -> dict:
        return {
            "availability": "fresh", "age": 1, "chapter": 1, "gold": 30,
            "year": 1, "month": 10, "soldiers": 100, "captured": 1,
            "captured_names": ["キカントン"], "lost_names": ["スペンソニア"],
            "garrison": [], "marching": [], "eggs": [],
            "wins": 23, "losses": 1, "unclassified": 0,
            "battles_recorded": 24, "battles_judged": 23,
            "battles_unjudged": 1, "castle_losses": 6,
            "battles_started": 24, "battles_finished": 23,
            "cards_confirmed": 0, "orders_launched": 8, "orders_failed": 0,
            "observations": 100, "unchanged_seconds": 0,
        }

    def test_unjudged_battles_are_disclosed(self):
        text = self._render(self._base())
        self.assertIn("判定 23", text)
        self.assertIn("未判定 1", text)

    def test_castle_losses_are_shown_with_their_evidence_source(self):
        text = self._render(self._base())
        self.assertIn("城失陥: 6件", text)
        self.assertIn("敵色", text)

    def test_a_fully_judged_run_does_not_claim_an_unjudged_count(self):
        snippet = self._base()
        snippet.update({"battles_unjudged": 0, "castle_losses": 0})
        text = self._render(snippet)
        self.assertNotIn("未判定", text)
        self.assertNotIn("城失陥", text)

    def test_unjudged_gap_is_shown_even_when_it_is_zero_and_losses_are_zero(self):
        # The original complaint shape: nothing lost on record, yet the run is
        # visibly losing. The gap and the castle losses must still surface.
        snippet = self._base()
        snippet.update({
            "wins": 0, "losses": 0, "unclassified": 0,
            "battles_recorded": 6, "battles_judged": 0,
            "battles_unjudged": 6, "castle_losses": 6,
            "battles_started": 6, "battles_finished": 6,
        })
        text = self._render(snippet)
        self.assertIn("0勝 / 0敗", text)
        self.assertIn("未判定 6", text)
        self.assertIn("城失陥: 6件", text)


if __name__ == "__main__":
    unittest.main()
