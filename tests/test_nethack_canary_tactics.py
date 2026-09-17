from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from docich.actions import Action
from docich.nethack_action_spec import ActionSpec, REVIEWED_EFFECTS, load_action_catalog
from docich.nethack_canary_rules import attackable_neighbors
from docich.nethack_canary_tactics import SUPPORTED_EFFECTS, CanaryTacticalPolicy
from docich.nethack_inventory import VisibleInventoryItem
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "nethack-canary-actions.json"
STATUS = "HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50"
HUNGRY_STATUS = "HP:10(10) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50 Hungry"
LOW_HP_STATUS = "HP:8(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50"
IMPAIRED_STATUS = "HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50 Blind"


def obs(message: str = "", map_rows: tuple[str, ...] = (), status: str = STATUS):
    rows = 24
    body = list(map_rows) + [""] * (rows - 1 - 2 - len(map_rows)) + ["", status]
    return normalize_tty("\n".join([message] + body) + "\n", cols=80, rows=rows)


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


def decide(observation, *, inventory=()):
    return CanaryTacticalPolicy().decide(observation, inventory=inventory)


def test_attacks_adjacent_cardinal_monster():
    decision = decide(obs("", ("....", ".@k.", "....")))
    assert decision.intent == "attack_adjacent"
    assert tuple(a.text for a in decision.actions) == ("l",)


def test_attacks_adjacent_diagonal_monster():
    decision = decide(obs("", ("....", ".@..", "..k.")))
    assert decision.intent == "attack_adjacent"
    assert tuple(a.text for a in decision.actions) == ("n",)


def test_does_not_target_human_glyph():
    observation = replace(obs("", ("...", "@@k", "...")), player=(1, 1))
    neighbors = attackable_neighbors(observation)
    assert tuple(glyph for _dx, _dy, glyph in neighbors) == ("k",)


def test_opens_adjacent_closed_door():
    decision = decide(obs("", ("....", ".@+.", "....")))
    assert decision.intent == "open_door"
    assert tuple(a.text for a in decision.actions) == ("o", "l")


def test_locked_door_is_not_retried():
    policy = CanaryTacticalPolicy()
    observation = obs("", ("....", ".@+.", "...."))
    assert policy.decide(observation).intent == "open_door"
    assert policy.decide(observation).intent != "open_door"


def test_accepts_really_attack_prompt():
    decision = decide(obs("Really attack? [yn]", ("....", ".@k.", "....")))
    assert decision.intent == "confirm_attack"
    assert tuple(a.text for a in decision.actions) == ("y",)


def test_declines_unreviewed_yes_no_prompt():
    decision = decide(obs("Pick up the dagger? [ynq]", ("....", ".@..", "....")))
    assert decision.intent == "decline_prompt"
    assert tuple(a.text for a in decision.actions) == ("n",)


def test_eats_visible_food_when_hungry():
    decision = decide(obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS), inventory=(food_item(),))
    assert decision.intent == "eat_food"
    assert tuple(a.text for a in decision.actions) == ("e", "f")


def test_hungry_without_food_falls_through_to_exploration():
    decision = decide(obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS), inventory=())
    assert decision.intent != "seek_food"
    assert decision.actions


def test_never_eats_unpaid_food():
    decision = decide(
        obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS),
        inventory=(food_item(unpaid=True),),
    )
    assert decision.intent != "eat_food"
    assert decision.actions


def test_prefers_a_non_tin_food_item():
    tin = food_item(letter="t", description="a tin of spinach")
    ration = food_item(letter="r", description="a food ration")
    decision = decide(obs("", ("....", ".@..", "...."), status=HUNGRY_STATUS), inventory=(tin, ration))
    assert tuple(a.text for a in decision.actions) == ("e", "r")


def test_low_hp_rests_without_adjacent_monster():
    decision = decide(obs("", ("....", ".@..", "...."), status=LOW_HP_STATUS))
    assert decision.intent == "rest_low_hp"
    assert tuple(a.text for a in decision.actions) == (".",)


def test_low_hp_fights_adjacent_monster():
    decision = decide(obs("", ("....", ".@k.", "...."), status=LOW_HP_STATUS))
    assert decision.intent == "attack_adjacent"


def test_impaired_rests_instead_of_holding():
    decision = decide(obs("", ("....", ".@..", "...."), status=IMPAIRED_STATUS))
    assert decision.intent == "rest_impaired"
    assert tuple(a.text for a in decision.actions) == (".",)


def test_blocked_without_door_searches():
    policy = CanaryTacticalPolicy()
    decision = policy.decide(obs("", ("-----", "-@---", "-----")))
    assert decision.intent == "search_when_blocked"
    assert tuple(a.text for a in decision.actions) == ("s",)
    policy.assert_safe(decision)


def test_disabled_search_preserves_rest_fallback():
    specs = tuple(
        replace(spec, enabled=False) if spec.id == "search_when_blocked" else spec
        for spec in load_action_catalog(CATALOG)
    )
    decision = CanaryTacticalPolicy(specs=specs).decide(obs("", ("-----", "-@---", "-----")))
    assert decision.intent == "rest"
    assert tuple(a.text for a in decision.actions) == (".",)


@pytest.mark.parametrize(
    ("map_rows", "status", "intent", "keys"),
    [
        (("-----", "-@.--", "-----"), STATUS, "explore_step", ("l",)),
        (("-----", "-@k--", "-----"), STATUS, "attack_adjacent", ("l",)),
        (("-----", "-@+--", "-----"), STATUS, "open_door", ("o", "l")),
        (("-----", "-@---", "-----"), LOW_HP_STATUS, "rest_low_hp", (".",)),
        (("-----", "-@---", "-----"), IMPAIRED_STATUS, "rest_impaired", (".",)),
    ],
)
def test_search_preserves_higher_priority_actions(map_rows, status, intent, keys):
    decision = decide(obs("", map_rows, status=status))
    assert decision.intent == intent
    assert tuple(a.text for a in decision.actions) == keys


def test_search_rejects_adjacent_monster_even_when_attack_is_disabled():
    specs = tuple(
        replace(spec, enabled=False) if spec.id == "attack_adjacent" else spec
        for spec in load_action_catalog(CATALOG)
    )
    decision = CanaryTacticalPolicy(specs=specs).decide(obs("", ("-----", "-@k--", "-----")))
    assert decision.intent == "rest"
    assert tuple(a.text for a in decision.actions) == (".",)


def test_answers_direction_prompt_toward_frontier():
    decision = decide(obs("In what direction? ", ("-----", "-@. -", "-----")))
    assert decision.intent == "directional_travel"
    assert tuple(a.text for a in decision.actions) == ("l",)


def test_direction_prompt_without_any_safe_move_stalls_for_llm():
    decision = decide(obs("In what direction? ", ("-----", "-@--", "-----")))
    assert decision.requires_llm is True
    assert decision.actions == ()


def test_prompt_is_never_answered_with_a_movement_key():
    # A yes/no prompt with an adjacent monster must not move into it.
    decision = decide(obs("Really attack? [yn]", ("....", ".@k.", "....")))
    assert decision.intent == "confirm_attack"
    assert tuple(a.text for a in decision.actions) == ("y",)


def test_safety_gate_is_catalog_driven():
    policy = CanaryTacticalPolicy()
    policy.assert_safe(
        PolicyDecision("tactical", "eat_food", "", (Action(type="text", text="e"), Action(type="text", text="f")))
    )
    with pytest.raises(RuntimeError):
        policy.assert_safe(
            PolicyDecision("tactical", "eat_food", "", (Action(type="text", text="x"), Action(type="text", text="f")))
        )
    with pytest.raises(RuntimeError):
        policy.assert_safe(
            PolicyDecision("tactical", "not_in_catalog", "", (Action(type="text", text="."),))
        )


def test_supported_effects_cover_the_reviewed_vocabulary():
    assert set(SUPPORTED_EFFECTS) == set(REVIEWED_EFFECTS)


def test_new_catalog_id_with_reviewed_effect_is_executed():
    # P6g acceptance: a brand-new id needs no handler code; the policy
    # dispatches on its declarative effect.
    descend = ActionSpec(
        id="descend_stairs",
        effect="keys",
        risk_class="movement",
        preconditions=("prompt:none", "player_visible"),
        key_pattern=(">",),
        postconditions=("screen_changed",),
        description="Descend a visible staircase.",
        enabled=True,
        priority=5,
    )
    specs = load_action_catalog(CATALOG) + (descend,)
    policy = CanaryTacticalPolicy(specs=specs)
    decision = policy.decide(obs("", ("....", ".@..", "....")))
    assert decision.intent == "descend_stairs"
    assert tuple(a.text for a in decision.actions) == (">",)
    policy.assert_safe(decision)


def test_spec_with_unreviewed_effect_is_skipped_fail_closed():
    # A spec whose effect is outside the reviewed vocabulary never produces
    # keys: the policy falls through instead of executing it.
    weird = ActionSpec(
        id="zap_wand",
        effect="zap_wand",
        risk_class="item",
        preconditions=("prompt:none", "player_visible"),
        key_pattern=("z",),
        postconditions=("always",),
        enabled=True,
        priority=1,
    )
    policy = CanaryTacticalPolicy(specs=(weird,) + load_action_catalog(CATALOG))
    decision = policy.decide(obs("", ("....", ".@..", "....")))
    assert decision.intent != "zap_wand"
    assert decision.actions


def test_disabling_a_catalog_action_changes_behaviour():
    # A catalog candidate that disables attack_adjacent must stop attacking:
    # this is the behavioural lever the P6d promotion gate compares.
    observation = obs("", ("....", ".@k.", "...."))
    assert CanaryTacticalPolicy().decide(observation).intent == "attack_adjacent"
    from docich.nethack_canary_tactics import load_default_catalog

    specs = tuple(
        replace(spec, enabled=False) if spec.id == "attack_adjacent" else spec
        for spec in load_default_catalog()
    )
    policy = CanaryTacticalPolicy(specs=specs)
    decision = policy.decide(observation)
    assert decision.intent != "attack_adjacent"
    assert decision.intent == "explore_step"


def test_worker_uses_catalog_policy_and_disables_tutorial():
    source = (ROOT / "src" / "docich" / "nethack_canary_worker.py").read_text(encoding="utf-8")
    assert "CanaryTacticalPolicy" in source
    assert "assert_safe" in source
    assert "!tutorial" in source
    assert ",time" in source


def test_production_policy_module_is_untouched_by_canary_tactics():
    source = (ROOT / "src" / "docich" / "nethack_canary_tactics.py").read_text(encoding="utf-8")
    assert "from .nethack_policy import NethackLayeredPolicy, PolicyDecision" in source
    assert "agent.brains" not in source
