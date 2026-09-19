from __future__ import annotations

import unittest

from docich.actions import Action
from docich.nethack_exploration import NethackExplorer, PASSABLE
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import (
    NethackLayeredPolicy,
    PolicyDecision,
    REST_EMERGENCY_INTENTS,
    REST_HOLD_INTENTS,
    assert_p3b_safe,
    assert_rest_safe,
    rest_action_for_hold,
)


def obs(map_rows: tuple[str, str], *, hp="10(10)", condition=""):
    text = (
        "msg\n"
        f"{map_rows[0]}\n"
        f"{map_rows[1]}\n"
        f"Dlvl:2 HP:{hp} Pw:4(4) AC:5 Exp:2\n"
        f"T:12 {condition}\n"
    )
    # Real NetHack TTY uses 80 columns. Keep the status line intact here;
    # shrinking cols for a compact map would truncate HP/condition evidence.
    return normalize_tty(text, cols=80, rows=5)


class TestNethackExplorer(unittest.TestCase):
    def test_step_is_only_onto_visible_passable_cardinal_cell(self) -> None:
        observation = obs(("###@.      ", "           "))
        step = NethackExplorer().plan_step(observation)
        self.assertIsNotNone(step)
        assert step is not None
        self.assertIn(step.key, {"h", "j", "k", "l"})
        self.assertIn(step.target_glyph, PASSABLE)
        self.assertEqual(step.source, observation.player)

    def test_creature_item_trap_door_and_unknown_are_never_targets(self) -> None:
        # Player has a single safe left corridor. Right/vertical neighbors are
        # creature/item/unknown; the visible trap/door are also non-passable.
        observation = obs(("#@d!+^     ", "            "))
        step = NethackExplorer().plan_step(observation)
        self.assertIsNotNone(step)
        assert step is not None
        self.assertEqual(step.key, "h")
        self.assertEqual(step.target_glyph, "#")
        self.assertNotIn(step.target_glyph, {"d", "!", "+", "^", " "})

    def test_no_safe_neighbor_returns_none(self) -> None:
        observation = obs(("d@!+^      ", "            "))
        self.assertIsNone(NethackExplorer().plan_step(observation))

    def test_visit_memory_changes_tie_breaking(self) -> None:
        explorer = NethackExplorer()
        first = obs(("..@..      ", "            "))
        step1 = explorer.plan_step(first)
        self.assertIsNotNone(step1)
        assert step1 is not None
        # Simulate that the chosen cell became the next player location while
        # the same visible corridor remains.
        row = list(".....      ")
        row[step1.target[0]] = "@"
        second = obs(("".join(row), "            "))
        explorer.plan_step(second)
        depth = second.vitals.dungeon_level
        assert depth is not None
        memory = explorer.memory.level(depth)
        self.assertGreaterEqual(memory.visits.get(step1.target, 0), 1)


class TestP3bPolicy(unittest.TestCase):
    def test_normal_state_can_emit_one_reviewed_exploration_step(self) -> None:
        decision = NethackLayeredPolicy().decide(obs(("###@.      ", "            ")))
        self.assertEqual(decision.intent, "explore_step")
        self.assertEqual(len(decision.actions), 1)
        self.assertIn(decision.actions[0].text, {"h", "j", "k", "l"})
        assert_p3b_safe(decision)

    def test_low_hp_and_impairment_hold_position(self) -> None:
        low = NethackLayeredPolicy().decide(
            obs(("###@.      ", "            "), hp="4(10)")
        )
        self.assertEqual(low.intent, "hold_low_hp")
        self.assertEqual(low.actions, ())

        blind = NethackLayeredPolicy().decide(
            obs(("###@.      ", "            "), condition="Blind")
        )
        self.assertEqual(blind.intent, "hold_impaired")
        self.assertEqual(blind.actions, ())

    def test_adjacent_creature_still_blocks_explorer(self) -> None:
        decision = NethackLayeredPolicy().decide(obs(("##@d.      ", "            ")))
        self.assertEqual(decision.intent, "assess_contact")
        self.assertEqual(decision.actions, ())

    def test_p3b_guard_rejects_attack_or_item_key(self) -> None:
        for key in ("a", "q", ">", "o"):
            with self.subTest(key=key):
                bad = PolicyDecision(
                    layer="midlevel",
                    intent="explore_step",
                    reason="bad",
                    actions=(Action(type="text", text=key),),
                )
                with self.assertRaises(RuntimeError):
                    assert_p3b_safe(bad)


class TestRestOnStalledHold(unittest.TestCase):
    """A hold on a turn-based game never resolves by itself; let one turn pass safely."""

    FREE = ("###@.      ", "            ")

    def _decide(self, map_rows, **kw):
        observation = obs(map_rows, **kw)
        return observation, NethackLayeredPolicy().decide(observation)

    def test_each_safe_stalled_hold_gets_exactly_one_rest_key(self):
        cases = {
            "hold_low_hp": (self.FREE, {"hp": "4(10)"}),
            "hold_impaired": (self.FREE, {"condition": "Blind"}),
            "seek_food": (self.FREE, {"condition": "Hungry"}),
            # hero boxed in by walls: the planner has no cardinal step
            "exploration_blocked": (("#-@-#      ", "-----       "), {}),
        }
        self.assertEqual(set(cases), set(REST_HOLD_INTENTS))
        for intent, (rows, kw) in cases.items():
            with self.subTest(intent=intent):
                observation, decision = self._decide(rows, **kw)
                self.assertEqual(decision.intent, intent)
                self.assertEqual(decision.actions, ())  # the policy's own decision is unchanged
                assert_p3b_safe(decision)
                action = rest_action_for_hold(decision, observation)
                self.assertEqual((action.type, action.text), ("text", "."))
                assert_rest_safe([action])

    def test_visible_creature_contact_never_gets_rest(self):
        rows = ("##@d.      ", "            ")
        cases = {
            "assess_contact": {},
            # These holds are evaluated before contact in policy.decide(), so
            # the rest guard must independently notice the adjacent creature.
            "hold_low_hp": {"hp": "4(10)"},
            "hold_impaired": {"condition": "Blind"},
            "seek_food": {"condition": "Hungry"},
        }
        for intent, kw in cases.items():
            with self.subTest(intent=intent):
                observation, decision = self._decide(rows, **kw)
                self.assertEqual(decision.intent, intent)
                self.assertIsNone(rest_action_for_hold(decision, observation))

    def test_ambiguous_f_can_still_unblock_by_resting(self):
        # Production's plain-text TTY cannot distinguish fountain vs cat for
        # glyph 'f'.  The planner still refuses to step onto it, but the
        # existing bounded rest escape remains available for the observed pet
        # corridor stall until colour-aware observation exists.
        observation, decision = self._decide(("#-@f#      ", "-----       "))
        self.assertEqual(decision.intent, "exploration_blocked")
        action = rest_action_for_hold(decision, observation)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.text, ".")

    def test_no_rest_when_the_policy_already_acts_or_cannot_be_helped_by_time(self):
        cases = {
            # has its own action
            "explore_step": (self.FREE, {}),
            # resting burns nutrition, so passing turns makes starvation worse
            "food_emergency": (self.FREE, {"condition": "Weak"}),
        }
        for intent, (rows, kw) in cases.items():
            with self.subTest(intent=intent):
                observation, decision = self._decide(rows, **kw)
                self.assertEqual(decision.intent, intent)
                self.assertIsNone(rest_action_for_hold(decision, observation))

    def test_survivable_emergencies_wait_a_turn_rather_than_freeze(self):
        # No strategist is configured, so "requires a recovery plan" means "no
        # action at all" -- and on a turn-based game that is a permanent stop.
        cases = {
            "survival_emergency": (self.FREE, {"hp": "2(10)"}),
            "status_emergency": (self.FREE, {"condition": "Sick"}),
        }
        self.assertEqual(set(cases), set(REST_EMERGENCY_INTENTS))
        for intent, (rows, kw) in cases.items():
            with self.subTest(intent=intent):
                observation, decision = self._decide(rows, **kw)
                self.assertEqual(decision.intent, intent)
                self.assertTrue(decision.requires_llm)
                self.assertEqual(decision.actions, ())  # the policy itself is unchanged
                action = rest_action_for_hold(decision, observation)
                self.assertEqual((action.type, action.text), ("text", "."))
                assert_rest_safe([action])

    def test_a_weakened_hero_never_rests_beside_a_creature(self):
        for kw in ({"hp": "2(10)"}, {"condition": "Sick"}):
            with self.subTest(kw=kw):
                observation, decision = self._decide(("##@d.      ", "            "), **kw)
                self.assertIn(decision.intent, REST_EMERGENCY_INTENTS)
                self.assertIsNone(rest_action_for_hold(decision, observation))

    def test_no_rest_on_a_prompt_or_without_a_visible_player(self):
        observation = obs(self.FREE)
        hold = PolicyDecision("midlevel", "exploration_blocked", "blocked")
        self.assertEqual(rest_action_for_hold(hold, observation).text, ".")

        no_player = obs(("### .      ", "            "))
        self.assertIsNone(no_player.player)
        self.assertIsNone(rest_action_for_hold(hold, no_player))  # inspect_screen territory

        more = normalize_tty("Really? --More--\n###@.\n.....\nDlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\nT:12\n", cols=80, rows=5)
        self.assertEqual(more.prompt, "more")
        self.assertIsNone(rest_action_for_hold(hold, more))

    def test_only_reviewed_holds_qualify(self):
        observation = obs(self.FREE)
        for decision in (
            PolicyDecision("tactical", "exploration_blocked", "x"),
            PolicyDecision("tactical", "survival_emergency", "x"),
            PolicyDecision("strategic", "exploration_blocked", "x"),
            PolicyDecision("strategic", "food_emergency", "x", requires_llm=True),
            PolicyDecision("midlevel", "survival_emergency", "x"),
            PolicyDecision("midlevel", "exploration_blocked", "x", requires_llm=True),
            PolicyDecision("midlevel", "inspect_screen", "x"),
            PolicyDecision("midlevel", "advance_message", "x"),
            PolicyDecision("midlevel", "assess_contact", "x"),
            PolicyDecision("midlevel", "exploration_blocked", "x", actions=(Action(type="text", text="h"),)),
        ):
            with self.subTest(decision=decision):
                self.assertIsNone(rest_action_for_hold(decision, observation))

    def test_rest_guard_accepts_only_a_single_dot(self):
        assert_rest_safe([Action(type="text", text=".")])
        for bad in (
            [],
            [Action(type="text", text="s")],
            [Action(type="text", text="h")],
            [Action(type="text", text=" ")],
            [Action(type="text", text="..")],
            [Action(type="key", key="Enter")],
            [Action(type="text", text="."), Action(type="text", text=".")],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError):
                    assert_rest_safe(bad)


if __name__ == "__main__":
    unittest.main()
