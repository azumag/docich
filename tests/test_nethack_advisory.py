from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docich.nethack_advisory import (
    NethackAdvisoryConfig,
    NethackAdvisoryController,
    load_advisory_config,
)
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision
from docich.nethack_strategist import StrategistDispatchResult
from docich.nethack_strategy import StrategicProposal


def visible_screen(*, hp: str = "2(10)", message: str = "danger") -> str:
    return (
        f"{message}\n"
        "..@.....\n"
        "..#.....\n"
        f"Dlvl:3 HP:{hp} Pw:7(10) AC:2 Exp:4\n"
        "T:123\n"
    )


def normalized(*, hp: str = "2(10)", message: str = "danger"):
    return normalize_tty(visible_screen(hp=hp, message=message), cols=80, rows=5)


def emergency() -> PolicyDecision:
    return PolicyDecision(
        layer="strategic",
        intent="survival_emergency",
        reason="visible HP is critical",
        requires_llm=True,
    )


class FakeStrategist:
    def __init__(self, result: StrategistDispatchResult) -> None:
        self.result = result
        self.calls = []

    def dispatch(self, request):
        self.calls.append(request)
        return self.result


class TestAdvisoryConfig(unittest.TestCase):
    def test_disabled_standard_shape_does_not_require_command(self) -> None:
        game = SimpleNamespace(
            raw={
                "nethack": {
                    "strategist": {
                        "enabled": False,
                        "command": [],
                        "narration_enabled": False,
                    }
                }
            }
        )
        cfg = load_advisory_config(game)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.command, ())
        self.assertEqual(cfg.max_calls, 24)

    def test_enabled_requires_command_and_bounds(self) -> None:
        with self.assertRaises(ValueError):
            load_advisory_config(
                SimpleNamespace(raw={"nethack": {"strategist": {"enabled": True, "command": []}}})
            )
        with self.assertRaises(ValueError):
            load_advisory_config(
                SimpleNamespace(
                    raw={
                        "nethack": {
                            "strategist": {
                                "enabled": True,
                                "command": ["x"],
                                "max_calls": 0,
                            }
                        }
                    }
                )
            )


class TestAdvisoryController(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.g = SimpleNamespace(state_dir=self.root / "state", repo_root=self.root)
        self.game = SimpleNamespace(name="nethack", raw={})
        self.clock = [100.0]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def controller(self, strategist, *, max_calls=24, cooldown=30.0, narrator=None):
        cfg = NethackAdvisoryConfig(
            enabled=True,
            command=("fake",),
            cooldown_s=cooldown,
            max_calls=max_calls,
            narration_enabled=narrator is not None,
            narration_cooldown_s=20.0,
        )
        return NethackAdvisoryController(
            self.g,
            self.game,
            config=cfg,
            strategist=strategist,
            narrator=narrator,
            monotonic=lambda: self.clock[0],
            wall_time=lambda: 1234.5,
        )

    def test_approved_rest_proposal_is_logged_narrated_and_returned_as_action(self) -> None:
        spoken = []
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(
                    schema_version=1,
                    kind="rest",
                    rationale="wait one turn with dot",
                    narration="ここは . で1ターン待機します。",
                ),
            )
        )
        ctl = self.controller(strategist, narrator=spoken.append)
        text = visible_screen()
        outcome = ctl.consider(text, normalized(), emergency())

        self.assertEqual(outcome.status, "proposed")
        self.assertEqual(outcome.proposal_kind, "rest")
        self.assertEqual(outcome.evaluation_status, "approved")
        self.assertTrue(outcome.narrated)
        self.assertEqual(spoken, ["ここは . で1ターン待機します。"])
        self.assertEqual([(a.type, a.text) for a in outcome.actions], [("text", ".")])
        self.assertEqual(len(strategist.calls), 1)

        log_path = self.root / "state" / "nethack" / "strategist" / "advisory.jsonl"
        event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["execution"], "strategist_action")
        self.assertEqual(event["proposal"]["kind"], "rest")
        self.assertEqual(event["evaluation"]["status"], "approved")

    def test_same_intent_is_cooled_down_and_budget_is_bounded(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot"),
            )
        )
        ctl = self.controller(strategist, max_calls=2, cooldown=10.0)
        text = visible_screen()
        observation = normalized()

        self.assertEqual(ctl.consider(text, observation, emergency()).status, "proposed")
        self.assertEqual(ctl.consider(text, observation, emergency()).status, "cooldown")
        self.assertEqual(len(strategist.calls), 1)

        self.clock[0] += 11.0
        self.assertEqual(ctl.consider(text, observation, emergency()).status, "proposed")
        self.clock[0] += 11.0
        self.assertEqual(ctl.consider(text, observation, emergency()).status, "budget_exhausted")
        self.assertEqual(len(strategist.calls), 2)
        self.assertEqual(ctl.calls_used, 2)

    def test_dispatch_error_and_narration_error_are_fail_open(self) -> None:
        sentinel = "SECRET-provider-error-3d4e"
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="error",
                error=f"provider down: {sentinel}",
                error_kind="process_failed",
            )
        )

        def broken_narrator(text: str) -> None:
            raise RuntimeError("audio down")

        ctl = self.controller(strategist, narrator=broken_narrator)
        outcome = ctl.consider(visible_screen(), normalized(), emergency())
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.error, f"provider down: {sentinel}")
        self.assertFalse(outcome.narrated)
        log_path = self.root / "state" / "nethack" / "strategist" / "advisory.jsonl"
        text = log_path.read_text(encoding="utf-8")
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["error_kind"], "process_failed")
        self.assertNotIn("error", event)
        self.assertNotIn(sentinel, text)

    def test_dispatch_exception_is_sanitized_before_advisory_persistence(self) -> None:
        sentinel = "SECRET-exception-detail-9a7b"

        class ExplodingStrategist:
            def dispatch(self, request):
                raise RuntimeError(f"provider exploded: {sentinel}")

        ctl = self.controller(ExplodingStrategist())
        outcome = ctl.consider(visible_screen(), normalized(), emergency())
        self.assertEqual(outcome.status, "error")
        self.assertIn(sentinel, outcome.error or "")
        log_path = self.root / "state" / "nethack" / "strategist" / "advisory.jsonl"
        text = log_path.read_text(encoding="utf-8")
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["error_kind"], "internal_error")
        self.assertNotIn("error", event)
        self.assertNotIn(sentinel, text)

    def test_local_meaningful_decision_can_narrate_without_model_dispatch(self) -> None:
        spoken = []
        cfg = NethackAdvisoryConfig(
            enabled=False,
            command="",
            narration_enabled=True,
            narration_cooldown_s=20.0,
        )
        ctl = NethackAdvisoryController(
            self.g,
            self.game,
            config=cfg,
            narrator=spoken.append,
            monotonic=lambda: self.clock[0],
        )
        contact = PolicyDecision(
            layer="midlevel",
            intent="assess_contact",
            reason="visible contact",
        )
        outcome = ctl.consider(visible_screen(hp="10(10)"), normalized(hp="10(10)"), contact)
        self.assertEqual(outcome.status, "disabled")
        self.assertTrue(outcome.narrated)
        self.assertIn("生物", spoken[0])

        # Same intent inside narration cooldown is silent.
        again = ctl.consider(visible_screen(hp="10(10)"), normalized(hp="10(10)"), contact)
        self.assertFalse(again.narrated)

    def test_blocking_prompt_is_answered_with_default_when_strategist_is_disabled(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(status="proposed", proposal=StrategicProposal(schema_version=1, kind="rest", rationale="x"))
        )
        ctl = self.controller(strategist)
        ctl.config = NethackAdvisoryConfig(enabled=False, command="")
        prompt = PolicyDecision(layer="strategic", intent="prompt_decision", reason="prompt", requires_llm=True)
        outcome = ctl.consider(visible_screen(message="Really attack? [yn]"), normalized(message="Really attack? [yn]"), prompt)
        self.assertEqual(outcome.status, "disabled")
        self.assertEqual([(a.type, a.text) for a in outcome.actions], [("text", "n")])
        self.assertEqual(strategist.calls, [])

    def test_blocking_prompt_uses_approved_answer_then_default_on_rejection(self) -> None:
        msg = "What direction? "
        prompt = PolicyDecision(layer="strategic", intent="prompt_decision", reason="prompt", requires_llm=True)
        answered = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="answer_prompt", rationale="go west", prompt_answer="h"),
            )
        )
        ctl = self.controller(answered)
        outcome = ctl.consider(visible_screen(message=msg), normalized(message=msg), prompt)
        self.assertEqual([(a.type, a.text) for a in outcome.actions], [("text", "h")])

        rejected = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="answer_prompt", rationale="bad", prompt_answer="q"),
            )
        )
        ctl2 = self.controller(rejected)
        outcome2 = ctl2.consider(visible_screen(message=msg), normalized(message=msg), prompt)
        self.assertEqual([(a.type, a.key) for a in outcome2.actions], [("special", "Escape")])
        log_path = self.root / "state" / "nethack" / "strategist" / "advisory.jsonl"
        event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["execution"], "default_prompt_answer")

    def test_blocking_prompt_is_still_answered_when_budget_or_cooldown_is_exhausted(self) -> None:
        msg = "Call a potion: "
        prompt = PolicyDecision(layer="strategic", intent="prompt_decision", reason="prompt", requires_llm=True)
        strategist = FakeStrategist(
            StrategistDispatchResult(status="error", error="down", error_kind="timeout")
        )
        ctl = self.controller(strategist, max_calls=1, cooldown=100.0)
        ctl.consider(visible_screen(message=msg), normalized(message=msg), prompt)
        self.clock[0] += 1.0
        # A new visible frame is a new prompt, so the answer is not suppressed.
        next_msg = "Call a scroll: "
        cooled = ctl.consider(visible_screen(message=next_msg), normalized(message=next_msg), prompt)
        self.assertEqual(cooled.status, "budget_exhausted")
        self.assertEqual([(a.type, a.key) for a in cooled.actions], [("special", "Escape")])
        self.assertEqual(len(strategist.calls), 1)

    def test_same_visible_prompt_frame_is_answered_only_once(self) -> None:
        msg = "Call a potion: "
        prompt = PolicyDecision(layer="strategic", intent="prompt_decision", reason="prompt", requires_llm=True)
        strategist = FakeStrategist(
            StrategistDispatchResult(status="error", error="down", error_kind="timeout")
        )
        ctl = self.controller(strategist, cooldown=0.0)
        first = ctl.consider(visible_screen(message=msg), normalized(message=msg), prompt)
        self.assertEqual([(a.type, a.key) for a in first.actions], [("special", "Escape")])
        # The frozen frame is not answered again: no key, no strategist call.
        for _ in range(3):
            self.clock[0] += 1.0
            frozen = ctl.consider(visible_screen(message=msg), normalized(message=msg), prompt)
            self.assertEqual(frozen.status, "suppressed")
            self.assertEqual(frozen.actions, ())
        self.assertEqual(len(strategist.calls), 1)
        # A non-prompt frame clears the mark, so the same prompt text is answered again later.
        ctl.consider(visible_screen(message="You see here a dagger."), normalized(message="You see here a dagger."), PolicyDecision(layer="tactical", intent="explore", reason="x"))
        again = ctl.consider(visible_screen(message=msg), normalized(message=msg), prompt)
        self.assertEqual([(a.type, a.key) for a in again.actions], [("special", "Escape")])
        self.assertEqual(len(strategist.calls), 2)

    def test_proposed_prayer_is_logged_but_never_sent_by_the_brain_path(self) -> None:
        sick = normalize_tty(
            "msg\n..@.....\n..#.....\nDlvl:3 HP:10(10) Pw:7(10) AC:2 Exp:4 Sick\nT:123\n", cols=80, rows=5
        )
        decision = PolicyDecision(
            layer="strategic",
            intent="status_emergency",
            reason="visible severe status",
            requires_llm=True,
        )
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="pray", rationale="severe status"),
            )
        )
        ctl = self.controller(strategist)
        outcome = ctl.consider("msg\n..@.....\n..#.....\nDlvl:3 HP:10(10) Pw:7(10) AC:2 Exp:4 Sick\nT:123\n", sick, decision)
        self.assertEqual(outcome.evaluation_status, "approved")
        self.assertEqual(outcome.actions, ())
        log_path = self.root / "state" / "nethack" / "strategist" / "advisory.jsonl"
        event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["execution"], "advisory_only")

    def test_non_strategic_exploration_never_calls_model(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot"),
            )
        )
        ctl = self.controller(strategist)
        decision = PolicyDecision(
            layer="midlevel",
            intent="explore_step",
            reason="safe visible step",
        )
        outcome = ctl.consider(visible_screen(hp="10(10)"), normalized(hp="10(10)"), decision)
        self.assertEqual(outcome.status, "not_needed")
        self.assertEqual(strategist.calls, [])


class TestBrainAdvisoryCanonicalGate(unittest.TestCase):
    """Advisory plan on a non-prompt hold is rejected by the canonical gate."""

    def setUp(self) -> None:
        from docich.adapters.base import Observation
        from docich.agent.brains import NethackPolicyBrain

        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        g = SimpleNamespace(state_dir=root, repo_root=root)
        game = SimpleNamespace(
            name="nethack",
            adapter="cli",
            raw={"cli": {"cols": 80, "rows": 5}},
            agent=SimpleNamespace(brain="nethack", command=""),
        )
        self.brain = NethackPolicyBrain(g, game)
        self.Observation = Observation
        self.ready = {"phase": "ready", "operation": None, "request_id": None, "active": {"game": "nethack"}}

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def observation(self, text: str):
        return self.Observation(
            game="nethack", title="NetHack", adapter="cli", ts=1.0, kind="text", text=text
        )

    def test_advisory_plan_rejected_on_frame_change_or_bad_canonical(self) -> None:
        from docich.nethack_advisory import AdvisoryOutcome
        from docich.actions import Action
        from docich.nethack_policy import PolicyDecision

        text = "msg\n.@....\n......\nDlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\nT:12\n"
        obs = self.observation(text)

        held = PolicyDecision(layer="strategic", intent="inspect_screen", reason="test", requires_llm=True)
        self.brain.progress.observe = lambda *a, **k: None
        self.brain.progress.resolve = lambda decision, o, explorer: held
        self.brain.advisory.consider = lambda raw, o, d: AdvisoryOutcome(
            status="proposed", actions=(Action(type="text", text="."),)
        )

        actions = self.brain.decide(obs)
        self.assertEqual([(a.type, a.text) for a in actions], [("text", ".")])

        # A changed frame rejects the advisory plan.
        self.assertFalse(
            self.brain.validate_action(actions[0], self.observation("changed --More--"), canonical=self.ready)
        )
        # A missing/not-ready canonical state rejects the advisory plan.
        self.brain.discard_action_plan()
        actions2 = self.brain.decide(obs)
        self.assertFalse(self.brain.validate_action(actions2[0], obs, canonical=None))
        self.brain.discard_action_plan()


if __name__ == "__main__":
    unittest.main()
