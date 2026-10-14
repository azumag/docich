from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from docich.nethack_corner_p5 import (
    NethackP5CornerManager,
    viewer_retrospective_summary,
)
from docich.retro_corner import CornerResult, RetroCornerManager


class FakeRunStore:
    def __init__(self, run):
        self.run = run
        self.calls = []

    def record_finished(self, *, now, nethack_still_active):
        self.calls.append((now, nethack_still_active))
        return dict(self.run)


class FakeRetrospective:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate(self, *, run_id, now):
        self.calls.append((run_id, now))
        if self.error is not None:
            raise self.error
        return dict(self.result)


class TestViewerRetrospectiveSummary(unittest.TestCase):
    def test_repeated_survival_signal_is_short_and_evidence_bounded(self):
        text = viewer_retrospective_summary(
            {
                "terminal_status": "dead",
                "same_death_total_count": 3,
                "candidate_lessons": [
                    {"category": "repeated_death"},
                    {"category": "survival_signal"},
                ],
            }
        )
        self.assertIn("通算3回", text)
        self.assertIn("回復・退避", text)
        self.assertNotIn("必ず", text)

    def test_unknown_terminal_reason_does_not_guess(self):
        text = viewer_retrospective_summary(
            {"terminal_status": "ended_unknown", "candidate_lessons": []}
        )
        self.assertIn("原因は推測せず", text)

    def test_ascension_has_no_postmortem_warning(self):
        self.assertEqual(
            viewer_retrospective_summary(
                {"terminal_status": "ascended", "candidate_lessons": []}
            ),
            "",
        )


class TestP5FinishIntegration(unittest.TestCase):
    def manager(self, *, run, retrospective):
        mgr = object.__new__(NethackP5CornerManager)
        mgr._run_store = FakeRunStore(run)
        mgr._run_history_error = None
        mgr._retrospective = retrospective
        mgr._active_game_reader = lambda: None
        mgr._chat = []
        mgr._voice = []
        mgr._write_events = []
        mgr._chat = mgr._chat.append
        mgr._voice = mgr._voice.append
        mgr._write_state = lambda state: mgr._write_events.append(dict(state))
        return mgr

    def test_terminal_run_auto_generates_retrospective_before_announcement(self):
        now = datetime(2026, 9, 16, 22, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        run = {
            "run_id": "11111111-1111-4111-8111-111111111111",
            "expedition": 4,
            "status": "dead",
            "death_reason": "killed by a water elemental",
            "score": 100,
            "turns": 200,
            "max_depth": 5,
            "dump_file": "docich.dump",
        }
        retrospective = FakeRetrospective(
            {
                "terminal_status": "dead",
                "same_death_total_count": 2,
                "candidate_lessons": [
                    {"category": "repeated_death"},
                    {"category": "survival_signal"},
                ],
                "policy_effect": "none",
            }
        )
        mgr = self.manager(run=run, retrospective=retrospective)
        state = {"expedition": 4}

        with patch.object(
            RetroCornerManager,
            "_finish_locked",
            return_value=CornerResult("completed", game="nethack", previous_game="robots"),
        ):
            result = mgr._finish_locked(state, now)

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(retrospective.calls), 1)
        self.assertEqual(retrospective.calls[0][0], run["run_id"])
        self.assertEqual(state["retrospective_status"], "completed")
        self.assertEqual(state["retrospective_policy_effect"], "none")
        self.assertEqual(state["retrospective_same_death_total_count"], 2)
        self.assertEqual(
            state["retrospective_lesson_categories"],
            ["repeated_death", "survival_signal"],
        )
        self.assertTrue(state["end_announced"])
        self.assertEqual(len(mgr._chat.__self__), 1)
        spoken = mgr._chat.__self__[0]
        self.assertIn("記録上の死因は、killed by a water elemental", spoken)
        self.assertIn("通算2回", spoken)
        self.assertIn("回復・退避", spoken)
        self.assertEqual(mgr._voice.__self__, [spoken])

    def test_retrospective_failure_never_breaks_terminal_completion(self):
        now = datetime(2026, 9, 16, 22, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        run = {
            "run_id": "22222222-2222-4222-8222-222222222222",
            "expedition": 1,
            "status": "dead",
            "death_reason": "killed by a grid bug",
        }
        mgr = self.manager(
            run=run,
            retrospective=FakeRetrospective(error=RuntimeError("postmortem unavailable")),
        )
        state = {"expedition": 1}
        with patch.object(
            RetroCornerManager,
            "_finish_locked",
            return_value=CornerResult("completed", game="nethack", previous_game=None),
        ):
            result = mgr._finish_locked(state, now)
        self.assertEqual(result.status, "completed")
        self.assertEqual(state["retrospective_status"], "error")
        self.assertIn("postmortem unavailable", state["retrospective_error"])
        spoken = mgr._chat.__self__[0]
        self.assertIn("killed by a grid bug", spoken)
        self.assertNotIn("改善候補", spoken)

    def test_suspended_run_does_not_generate_postmortem(self):
        now = datetime(2026, 9, 16, 22, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        run = {
            "run_id": "33333333-3333-4333-8333-333333333333",
            "expedition": 2,
            "status": "suspended",
            "death_reason": None,
        }
        retrospective = FakeRetrospective(result={})
        mgr = self.manager(run=run, retrospective=retrospective)
        state = {"expedition": 2}
        with patch.object(
            RetroCornerManager,
            "_finish_locked",
            return_value=CornerResult("completed", game="nethack", previous_game=None),
        ):
            mgr._finish_locked(state, now)
        self.assertEqual(retrospective.calls, [])
        self.assertEqual(state["retrospective_status"], "not_terminal")
        self.assertIn("生存しています", mgr._chat.__self__[0])


if __name__ == "__main__":
    unittest.main()
