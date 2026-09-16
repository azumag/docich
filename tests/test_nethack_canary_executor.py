from __future__ import annotations

from docich.nethack_canary_executor import canary_execution_plan
from docich.nethack_inventory import VisibleInventoryItem
from docich.nethack_observation import normalize_tty
from docich.nethack_strategist import ProposalEvaluation
from docich.nethack_strategy import StrategicProposal, StrategicRequest


def observation(message="msg"):
    return normalize_tty(
        f"{message}\n..@.....\n..#.....\nDlvl:3 HP:2(10) Pw:7(10) AC:2 Exp:4\nT:123\n",
        cols=80,
        rows=5,
    )


def item(letter, category, *, unpaid=False, equipped=False):
    return VisibleInventoryItem(
        letter=letter,
        description=f"an uncursed {category}",
        quantity=1,
        buc="uncursed",
        equipped=equipped,
        unpaid=unpaid,
        category_hint=category,
    )


def approved(kind, *, letter=None, answer=None):
    proposal = StrategicProposal(
        schema_version=1,
        kind=kind,
        rationale="fixture",
        inventory_letter=letter,
        prompt_answer=answer,
    )
    return ProposalEvaluation(status="approved", reason="ok", proposal=proposal)


def request(intent="survival_emergency", inventory=()):
    return StrategicRequest(
        schema_version=1,
        intent=intent,
        reason="fixture",
        observation={},
        inventory=[entry.public_dict() for entry in inventory],
        constraints=(),
    )


def values(plan):
    return [(key.kind, key.value) for key in plan.keys]


def test_hold_and_inspect_are_noop_only():
    obs = observation()
    for kind in ("hold", "inspect"):
        plan = canary_execution_plan(request(), approved(kind), current_observation=obs)
        assert plan.allowed is True
        assert plan.keys == ()


def test_visible_food_and_potion_can_be_consumed():
    obs = observation()
    food = item("a", "food")
    potion = item("b", "potion")

    plan = canary_execution_plan(
        request(inventory=(food, potion)),
        approved("consume", letter="a"),
        current_observation=obs,
        current_inventory=(food, potion),
    )
    assert plan.allowed is True
    assert values(plan) == [("literal", "e"), ("literal", "a")]

    plan = canary_execution_plan(
        request(inventory=(food, potion)),
        approved("consume", letter="b"),
        current_observation=obs,
        current_inventory=(food, potion),
    )
    assert plan.allowed is True
    assert values(plan) == [("literal", "q"), ("literal", "b")]


def test_weapon_armor_and_tool_have_narrow_canary_mappings():
    obs = observation()
    weapon = item("a", "weapon")
    armor = item("b", "armor")
    tool = item("c", "tool")

    wield = canary_execution_plan(
        request(inventory=(weapon,)),
        approved("equip", letter="a"),
        current_observation=obs,
        current_inventory=(weapon,),
    )
    assert values(wield) == [("literal", "w"), ("literal", "a")]

    wear = canary_execution_plan(
        request(inventory=(armor,)),
        approved("equip", letter="b"),
        current_observation=obs,
        current_inventory=(armor,),
    )
    assert values(wear) == [("literal", "W"), ("literal", "b")]

    apply = canary_execution_plan(
        request(inventory=(tool,)),
        approved("use", letter="c"),
        current_observation=obs,
        current_inventory=(tool,),
    )
    assert values(apply) == [("literal", "a"), ("literal", "c")]


def test_wand_stairs_unknown_and_unpaid_are_not_guessed():
    obs = observation()
    wand = item("a", "wand")
    unknown = item("b", "unknown")
    unpaid = item("c", "food", unpaid=True)

    for evaluation, inv in (
        (approved("use", letter="a"), (wand,)),
        (approved("consume", letter="b"), (unknown,)),
        (approved("consume", letter="c"), (unpaid,)),
        (approved("descend"), ()),
        (approved("ascend"), ()),
    ):
        plan = canary_execution_plan(
            request(inventory=inv),
            evaluation,
            current_observation=obs,
            current_inventory=inv,
        )
        assert plan.allowed is False
        assert plan.keys == ()


def test_prompt_answer_uses_fresh_prompt_shape():
    yes_no = normalize_tty(
        "Really attack? [yn] (n)\n..@.....\n..#.....\nDlvl:3 HP:8(10) Pw:7(10) AC:2 Exp:4\nT:123\n",
        cols=80,
        rows=5,
    )
    plan = canary_execution_plan(
        request(intent="prompt_decision"),
        approved("answer_prompt", answer="n"),
        current_observation=yes_no,
    )
    assert values(plan) == [("literal", "n")]


def test_rejected_proposal_never_reaches_canary_keys():
    proposal = StrategicProposal(schema_version=1, kind="hold", rationale="fixture")
    evaluation = ProposalEvaluation(status="rejected", reason="fresh state changed", proposal=proposal)
    plan = canary_execution_plan(request(), evaluation, current_observation=observation())
    assert plan.allowed is False
    assert plan.keys == ()
