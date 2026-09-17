from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from docich.actions import Action
from docich.nethack_canary_tactics import (
    CanaryTacticalPolicy,
    _attackable_neighbors,
    assert_canary_safe,
)
from docich.nethack_inventory import VisibleInventoryItem
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision

ROOT = Path(__file__).resolve().parents[1]
STATUS = "HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:1"


def obs(message: str = "", map_rows: tuple[str, ...] = (), status: str = STATUS):
    rows = 24
    body = list(map_rows) + [""] * (rows - 1 - 2 - len(map_rows)) + ["", status]
    text = "\n".join([message] + body) + "\n"
    return normalize_tty(text, cols=80, rows=rows)


def test_attacks_adjacent_cardinal_monster():
    decision = CanaryTacticalPolicy().decide(obs("", ("....", ".@k.", "....")))
    assert decision.intent == "attack_adjacent"
    assert tuple(a.text for a in decision.actions) == ("l",)
    assert_canary_safe(decision)


def test_attacks_adjacent_diagonal_monster():
    decision = CanaryTacticalPolicy().decide(obs("", ("....", ".@..", "..k.")))
    assert decision.intent == "attack_adjacent"
    assert tuple(a.text for a in decision.actions) == ("n",)


def test_does_not_target_human_glyph():
    # A visible "@" neighbour is never in the attack set even though it is
    # alpha; the attackable diagonal "k" still is.
    base = obs("", ("...", "@@k", "..."))
    observation = replace(base, player=(1, 1))
    neighbors = _attackable_neighbors(observation)
    assert tuple(glyph for _dx, _dy, glyph in neighbors) == ("k",)


def test_avoids_non_attackable_neighbour_instead_of_stalling():
    base = obs("", ("....", ".@@.", "...."))
    observation = replace(base, player=(1, 1))
    decision = CanaryTacticalPolicy().decide(observation)
    assert decision.intent == "explore_step"
    assert_canary_safe(decision)


def test_opens_adjacent_closed_door():
    decision = CanaryTacticalPolicy().decide(obs("", ("....", ".@+.", "....")))
    assert decision.intent == "open_door"
    assert tuple(a.text for a in decision.actions) == ("o", "l")
    assert_canary_safe(decision)


def test_locked_door_is_not_retried():
    policy = CanaryTacticalPolicy()
    observation = obs("", ("....", ".@+.", "...."))
    first = policy.decide(observation)
    assert first.intent == "open_door"
    second = policy.decide(observation)
    assert second.intent != "open_door"
    assert_canary_safe(second)


def test_accepts_really_attack_prompt():
    decision = CanaryTacticalPolicy().decide(
        obs("Really attack? [yn]", ("....", ".@k.", "...."))
    )
    assert decision.intent == "confirm_attack"
    assert tuple(a.text for a in decision.actions) == ("y",)
    assert_canary_safe(decision)


def test_declines_unreviewed_yes_no_prompt():
    decision = CanaryTacticalPolicy().decide(
        obs("Pick up the dagger? [ynq]", ("....", ".@..", "...."))
    )
    assert decision.intent == "decline_prompt"
    assert tuple(a.text for a in decision.actions) == ("n",)
    assert_canary_safe(decision)


HUNGRY_STATUS = "HP:10(10) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50 Hungry"


def food_item(letter="f", description="a food ration", *, unpaid=False, category="food"):
    return VisibleInventoryItem(
        letter=letter,
        description=description,
        quantity=1,
        buc="unknown",
        equipped=False,
        unpaid=unpaid,
        category_hint=category,
    )


def test_eats_visible_food_when_hungry():
    observation = obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS)
    decision = CanaryTacticalPolicy().decide(observation, inventory=(food_item(),))
    assert decision.intent == "eat_food"
    assert tuple(a.text for a in decision.actions) == ("e", "f")
    assert_canary_safe(decision)


def test_hungry_without_food_falls_through_to_exploration():
    observation = obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS)
    decision = CanaryTacticalPolicy().decide(observation, inventory=())
    assert decision.intent != "seek_food"
    assert decision.actions  # hunger is a priority, never a dead end
    assert_canary_safe(decision)


def test_never_eats_unpaid_food():
    observation = obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS)
    decision = CanaryTacticalPolicy().decide(
        observation, inventory=(food_item(unpaid=True),)
    )
    assert decision.intent != "eat_food"
    assert decision.actions
    assert_canary_safe(decision)


def test_prefers_a_non_tin_food_item():
    observation = obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS)
    tin = food_item(letter="t", description="a tin of spinach")
    ration = food_item(letter="r", description="a food ration")
    decision = CanaryTacticalPolicy().decide(observation, inventory=(tin, ration))
    assert tuple(a.text for a in decision.actions) == ("e", "r")


LOW_HP_STATUS = "HP:8(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50"


def test_low_hp_rests_without_adjacent_monster():
    observation = obs("", ("....", ".@..", "...."), status=LOW_HP_STATUS)
    decision = CanaryTacticalPolicy().decide(observation)
    assert decision.intent == "rest_low_hp"
    assert tuple(a.text for a in decision.actions) == (".",)
    assert_canary_safe(decision)


def test_low_hp_fights_adjacent_monster():
    observation = obs("", ("....", ".@k.", "...."), status=LOW_HP_STATUS)
    decision = CanaryTacticalPolicy().decide(observation)
    assert decision.intent == "attack_adjacent"
    assert_canary_safe(decision)


IMPAIRED_STATUS = "HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50 Blind"


def test_impaired_rests_instead_of_holding():
    observation = obs("", ("....", ".@..", "...."), status=IMPAIRED_STATUS)
    decision = CanaryTacticalPolicy().decide(observation)
    assert decision.intent == "rest"
    assert tuple(a.text for a in decision.actions) == (".",)
    assert_canary_safe(decision)


def test_blocked_without_door_rests():
    observation = obs("", ("-----", "-@--", "-----"))
    decision = CanaryTacticalPolicy().decide(observation)
    assert decision.intent == "rest"
    assert tuple(a.text for a in decision.actions) == (".",)
    assert_canary_safe(decision)


def test_answers_direction_prompt_toward_frontier():
    decision = CanaryTacticalPolicy().decide(
        obs("In what direction? ", ("-----", "-@. -", "-----"))
    )
    assert decision.intent == "directional_travel"
    assert tuple(a.text for a in decision.actions) == ("l",)
    assert_canary_safe(decision)


def test_direction_prompt_without_any_safe_move_stalls_for_llm():
    decision = CanaryTacticalPolicy().decide(
        obs("In what direction? ", ("-----", "-@--", "-----"))
    )
    assert decision.requires_llm is True
    assert decision.actions == ()


def test_canary_safety_gate_is_an_allowlist():
    for decision in (
        PolicyDecision("tactical", "advance_message", "", (Action(type="text", text=" "),)),
        PolicyDecision("tactical", "confirm_attack", "", (Action(type="text", text="y"),)),
        PolicyDecision("tactical", "decline_prompt", "", (Action(type="text", text="n"),)),
        PolicyDecision(
            "tactical",
            "eat_food",
            "",
            (Action(type="text", text="e"), Action(type="text", text="f")),
        ),
        PolicyDecision("tactical", "rest_low_hp", "", (Action(type="text", text="."),)),
        PolicyDecision("tactical", "rest", "", (Action(type="text", text="."),)),
        PolicyDecision("tactical", "attack_adjacent", "", (Action(type="text", text="y"),)),
        PolicyDecision("tactical", "open_door", "", (Action(type="text", text="o"), Action(type="text", text="n"))),
    ):
        assert_canary_safe(decision)

    with pytest.raises(RuntimeError):
        assert_canary_safe(
            PolicyDecision("tactical", "attack_adjacent", "", (Action(type="text", text="x"),))
        )
    with pytest.raises(RuntimeError):
        assert_canary_safe(
            PolicyDecision("tactical", "open_door", "", (Action(type="text", text="o"), Action(type="text", text="s")))
        )
    with pytest.raises(RuntimeError):
        assert_canary_safe(
            PolicyDecision("strategic", "prompt_decision", "", (Action(type="text", text="y"),))
        )
    with pytest.raises(RuntimeError):
        assert_canary_safe(
            PolicyDecision(
                "tactical",
                "eat_food",
                "",
                (Action(type="text", text="e"), Action(type="text", text="1")),
            )
        )


def test_worker_uses_tactical_policy_and_disables_tutorial():
    source = (ROOT / "src" / "docich" / "nethack_canary_worker.py").read_text(encoding="utf-8")
    assert "CanaryTacticalPolicy" in source
    assert "assert_canary_safe" in source
    assert "!tutorial" in source
    assert ",time" in source


def test_production_policy_module_is_untouched_by_canary_tactics():
    source = (ROOT / "src" / "docich" / "nethack_canary_tactics.py").read_text(encoding="utf-8")
    assert "from .nethack_policy import NethackLayeredPolicy, PolicyDecision" in source
    # The tactical module must not import the production brain.
    assert "agent.brains" not in source
