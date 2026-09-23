from __future__ import annotations

import unittest

from docich.actions import Action
from docich.nethack_exploration import ExplorationStep, NethackExplorer, PASSABLE
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import (
    NethackLayeredPolicy,
    PolicyDecision,
    REST_EMERGENCY_INTENTS,
    REST_HOLD_INTENTS,
    STEP_OUT_INTENTS,
    assert_p3b_safe,
    assert_step_out_safe,
    assert_rest_safe,
    rest_action_for_hold,
    step_out_of_hold,
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
    """A no-action policy decision must become one explicit wait turn."""

    FREE = ("###@.      ", "            ")

    def _decide(self, map_rows, **kw):
        observation = obs(map_rows, **kw)
        return observation, NethackLayeredPolicy().decide(observation)

    def test_each_safe_stalled_hold_gets_exactly_one_rest_key(self):
        cases = {
            "hold_low_hp": (self.FREE, {"hp": "4(10)"}),
            "hold_impaired": (self.FREE, {"condition": "Blind"}),
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

    def test_visible_creature_contact_may_still_spend_one_wait_turn(self):
        # owner decision 2026-09-23: `.` はどんな時でも可。resolver は
        # 退避 -> 通常接触 -> `.` の順を維持するので、rest だけが許可される。
        rows = ("##@d.      ", "            ")
        cases = {
            "assess_contact": {},
            "hold_low_hp": {"hp": "4(10)"},
            "hold_impaired": {"condition": "Blind"},
            "seek_food": {"condition": "Hungry"},
        }
        for intent, kw in cases.items():
            with self.subTest(intent=intent):
                observation, decision = self._decide(rows, **kw)
                self.assertEqual(decision.intent, intent)
                action = rest_action_for_hold(decision, observation)
                self.assertIsNotNone(action)
                self.assertEqual(action.text, ".")
                assert_rest_safe([action])

    def test_feline_is_contact_and_still_may_rest(self):
        # f is always feline under default symbols; { is the fountain.
        observation, decision = self._decide(("#-@f#      ", "-----       "))
        self.assertEqual(decision.intent, "assess_contact")
        action = rest_action_for_hold(decision, observation)
        self.assertIsNotNone(action)
        self.assertEqual(action.text, ".")

    def test_no_rest_when_the_policy_already_has_its_own_action(self):
        observation, decision = self._decide(self.FREE)
        self.assertEqual(decision.intent, "explore_step")
        self.assertTrue(decision.actions)
        self.assertIsNone(rest_action_for_hold(decision, observation))

    def test_no_action_states_including_hunger_tiers_may_rest(self):
        # owner decision 2026-09-23: Hungry / Fainting でも完全なframeなら `.`。
        for kw in ({"condition": "Hungry"}, {"condition": "Fainting"}):
            with self.subTest(kw=kw):
                observation, decision = self._decide(self.FREE, **kw)
                self.assertEqual(decision.actions, ())
                action = rest_action_for_hold(decision, observation)
                self.assertIsNotNone(action)
                self.assertEqual(action.text, ".")
                assert_rest_safe([action])

    def test_critical_hp_emergency_waits_a_turn_rather_than_freeze(self):
        # No strategist is configured, so "requires a recovery plan" means "no
        # action at all" -- and on a turn-based game that is a permanent stop.
        self.assertEqual(REST_EMERGENCY_INTENTS, frozenset({"survival_emergency"}))
        observation, decision = self._decide(self.FREE, hp="2(10)")
        self.assertEqual(decision.intent, "survival_emergency")
        self.assertTrue(decision.requires_llm)
        self.assertEqual(decision.actions, ())  # the policy itself is unchanged
        action = rest_action_for_hold(decision, observation)
        self.assertEqual((action.type, action.text), ("text", "."))
        assert_rest_safe([action])

    def test_severe_status_still_spends_one_wait_turn(self):
        # owner decision 2026-09-23: `.` はどんな時でも可。policy の判断は
        # (strategic / requires_llm) のまま変わらず、rest だけが許可される。
        for condition in ("Sick", "FoodPois", "Ill", "Slime", "Strngl"):
            with self.subTest(condition=condition):
                observation, decision = self._decide(self.FREE, condition=condition)
                self.assertEqual(decision.intent, "status_emergency")
                self.assertTrue(decision.requires_llm)
                self.assertEqual(decision.actions, ())
                action = rest_action_for_hold(decision, observation)
                self.assertIsNotNone(action)
                self.assertEqual(action.text, ".")
                assert_rest_safe([action])

    def test_a_critical_or_severe_hero_may_wait_even_beside_a_creature(self):
        # owner decision 2026-09-23: 退避/接触を試した後の最後の手段として
        # `.` を送れる（resolver 側が順序を維持する）。
        observation, decision = self._decide(
            ("##@d.      ", "            "), hp="2(10)"
        )
        self.assertEqual(decision.intent, "survival_emergency")
        self.assertIn(decision.intent, REST_EMERGENCY_INTENTS)
        self.assertEqual(rest_action_for_hold(decision, observation).text, ".")

        sick_observation, sick_decision = self._decide(
            ("##@d.      ", "            "), condition="Sick"
        )
        self.assertEqual(sick_decision.intent, "status_emergency")
        self.assertNotIn(sick_decision.intent, REST_EMERGENCY_INTENTS)
        self.assertEqual(rest_action_for_hold(sick_decision, sick_observation).text, ".")

    def test_no_rest_on_a_prompt_or_without_a_visible_player(self):
        observation = obs(self.FREE)
        hold = PolicyDecision("midlevel", "exploration_blocked", "blocked")
        self.assertEqual(rest_action_for_hold(hold, observation).text, ".")

        no_player = obs(("### .      ", "            "))
        self.assertIsNone(no_player.player)
        self.assertIsNone(rest_action_for_hold(hold, no_player))  # inspect_screen territory

        more = normalize_tty("Message --More--\n###@.\n.....\nDlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\nT:12\n", cols=80, rows=5)
        self.assertEqual(more.prompt, "more")
        self.assertIsNone(rest_action_for_hold(hold, more))

    def test_rest_depends_on_the_frame_not_on_the_intent_or_layer(self):
        # owner decision 2026-09-23: intent/layer/requires_llm は rest の
        # 条件ではない。frame 完全性と「既に自分のactionがあるか」だけ。
        observation = obs(self.FREE)
        cases = (
            (PolicyDecision("midlevel", "exploration_blocked", "x"), True),
            (PolicyDecision("strategic", "survival_emergency", "x"), True),
            (PolicyDecision("midlevel", "hold_low_hp", "x"), True),
            (PolicyDecision("tactical", "exploration_blocked", "x"), True),
            (PolicyDecision("strategic", "exploration_blocked", "x"), True),
            (PolicyDecision("strategic", "food_emergency", "x", requires_llm=True), True),
            (PolicyDecision("strategic", "status_emergency", "x", requires_llm=True), True),
            (PolicyDecision("midlevel", "survival_emergency", "x"), True),
            (PolicyDecision("midlevel", "exploration_blocked", "x", requires_llm=True), True),
            (PolicyDecision("midlevel", "inspect_screen", "x"), True),
            (PolicyDecision("midlevel", "advance_message", "x"), True),
            (PolicyDecision("midlevel", "assess_contact", "x"), True),
            (PolicyDecision("midlevel", "exploration_blocked", "x", actions=(Action(type="text", text="h"),)), False),
        )
        for decision, qualifies in cases:
            with self.subTest(decision=decision):
                action = rest_action_for_hold(decision, observation)
                if qualifies:
                    self.assertIsNotNone(action)
                    self.assertEqual(action.text, ".")
                else:
                    self.assertIsNone(action)

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


class TestStepOutOfDeadlock(unittest.TestCase):
    """A creature contact must not leave a turn-based game without input."""

    FREE_ROWS = ("##@d.      ", "...........")

    def _decide(self, map_rows, **kw):
        observation = obs(map_rows, **kw)
        policy = NethackLayeredPolicy()
        return observation, policy.decide(observation), policy

    def test_every_reviewed_contact_tries_a_safe_step_before_rest(self):
        rows = ("##@d.      ", "...........")
        cases = {
            "assess_contact": {},
            "hold_low_hp": {"hp": "4(10)"},
            "seek_food": {"condition": "Hungry"},
            "survival_emergency": {"hp": "2(10)"},
        }
        for intent, kw in cases.items():
            with self.subTest(intent=intent):
                observation, decision, policy = self._decide(rows, **kw)
                self.assertEqual(decision.intent, intent)
                # The resolver prefers this step; the explicit dot is only the
                # fallback after retreat/contact are exhausted (owner decision
                # 2026-09-23 made that fallback available in every state).
                action = step_out_of_hold(decision, observation, policy.explorer)
                self.assertIsNotNone(action)
                self.assertIn(action.text, {"h", "j", "k", "l"})
                assert_step_out_safe([action])
                rest = rest_action_for_hold(decision, observation)
                self.assertIsNotNone(rest)
                self.assertEqual(rest.text, ".")
                assert_rest_safe([rest])

    def test_severe_status_blocks_movement_beside_a_creature_but_may_rest(self):
        rows = ("##@d.      ", "...........")
        for condition in ("Sick", "FoodPois", "Ill", "Slime", "Strngl"):
            with self.subTest(condition=condition):
                observation, decision, policy = self._decide(rows, condition=condition)
                self.assertEqual(decision.intent, "status_emergency")
                self.assertTrue(decision.requires_llm)
                self.assertNotIn(decision.intent, REST_EMERGENCY_INTENTS)
                self.assertNotIn(decision.intent, STEP_OUT_INTENTS)
                # movement/step-out stays fail-closed ...
                self.assertIsNone(step_out_of_hold(decision, observation, policy.explorer))
                # ... but the wait itself is allowed (owner decision 2026-09-23)
                rest = rest_action_for_hold(decision, observation)
                self.assertIsNotNone(rest)
                self.assertEqual(rest.text, ".")

    def test_no_step_is_invented_when_nothing_safe_is_reachable(self):
        # Walls on every side but the creature: no safe step exists, so the
        # resolver remains fail-closed rather than inventing a risky action.
        observation, decision, policy = self._decide(("-d@-       ", "-----------"), hp="4(10)")
        # no safe step may be invented ...
        self.assertIsNone(step_out_of_hold(decision, observation, policy.explorer))
        # ... but an explicit wait turn is always available on a complete frame
        rest = rest_action_for_hold(decision, observation)
        self.assertIsNotNone(rest)
        self.assertEqual(rest.text, ".")

    def test_food_emergency_is_never_stepped_out_of_but_may_rest(self):
        rows = ("##@d.      ", "...........")
        observation, decision, policy = self._decide(rows, condition="Fainting")
        self.assertEqual(decision.intent, "food_emergency")
        # moving cannot help hunger ...
        self.assertIsNone(step_out_of_hold(decision, observation, policy.explorer))
        self.assertNotIn("food_emergency", STEP_OUT_INTENTS)
        self.assertNotIn("inspect_screen", STEP_OUT_INTENTS)
        # ... but the wait itself is allowed (owner decision 2026-09-23)
        rest = rest_action_for_hold(decision, observation)
        self.assertIsNotNone(rest)
        self.assertEqual(rest.text, ".")

    def test_every_hunger_tier_rests_one_turn_on_a_complete_frame(self):
        free = ("###@.      ", "            ")
        for condition in ("Hungry", "Weak", "Fainting", "Fainted", "Starved"):
            with self.subTest(condition=condition):
                observation, decision, _ = self._decide(free, condition=condition)
                self.assertEqual(decision.actions, ())
                rest = rest_action_for_hold(decision, observation)
                self.assertIsNotNone(rest)
                self.assertEqual(rest.text, ".")
                assert_rest_safe([rest])

    def test_a_decision_that_already_acts_is_left_alone(self):
        observation, decision, policy = self._decide(("###@.      ", "           "))
        self.assertEqual(decision.intent, "explore_step")
        self.assertIsNone(step_out_of_hold(decision, observation, policy.explorer))

    def test_a_prompt_or_missing_player_never_gets_a_step(self):
        policy = NethackLayeredPolicy()
        hold = PolicyDecision("midlevel", "assess_contact", "x")
        more = normalize_tty("Really? --More--\n###@.\n.....\nDlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\nT:12\n", cols=80, rows=5)
        self.assertIsNone(step_out_of_hold(hold, more, policy.explorer))
        no_player = obs(("### .      ", "           "))
        self.assertIsNone(step_out_of_hold(hold, no_player, policy.explorer))

    def test_the_guards_do_not_rely_on_the_explorer_being_careful(self):
        # The explorer happens to refuse prompts itself, so pass one that does
        # not: this function must still never move on a prompt, without a
        # player, for a decision that already acts, or for an excluded intent.
        class EagerExplorer:
            def plan_step(self, obs):
                return ExplorationStep(
                    key="l", source=(0, 0), target=(1, 0), target_glyph=".", reason="eager"
                )

        eager = EagerExplorer()
        hold = PolicyDecision("midlevel", "assess_contact", "x")
        free = obs(self.FREE_ROWS)
        # Stub's right key actually targets a creature: independently rejected.
        self.assertIsNone(step_out_of_hold(hold, free, eager))

        more = normalize_tty(
            "Message --More--\n###@.\n.....\nDlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\nT:12\n",
            cols=80,
            rows=5,
        )
        self.assertEqual(more.prompt, "more")
        self.assertIsNone(step_out_of_hold(hold, more, eager))
        self.assertIsNone(step_out_of_hold(hold, obs(("### .      ", "           ")), eager))
        acting = PolicyDecision(
            "midlevel", "assess_contact", "x", actions=(Action(type="text", text="h"),)
        )
        self.assertIsNone(step_out_of_hold(acting, free, eager))
        self.assertIsNone(
            step_out_of_hold(PolicyDecision("strategic", "food_emergency", "x"), free, eager)
        )

    def test_step_out_guard_accepts_only_one_cardinal_move(self):
        assert_step_out_safe([Action(type="text", text="h")])
        for bad in (
            [],
            [Action(type="text", text=".")],
            [Action(type="text", text="a")],
            [Action(type="text", text=">")],
            [Action(type="key", key="Enter")],
            [Action(type="text", text="h"), Action(type="text", text="j")],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(RuntimeError):
                    assert_step_out_safe(bad)


if __name__ == "__main__":
    unittest.main()
