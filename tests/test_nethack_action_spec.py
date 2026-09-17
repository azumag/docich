from __future__ import annotations

import json
from pathlib import Path

import pytest

from docich.nethack_action_spec import (
    ActionContext,
    ActionSpec,
    REVIEWED_EFFECTS,
    STATUS_KEYS,
    STATUS_POSTCONDITION,
    STATUS_PRECONDITION,
    STATUS_VERIFIED,
    keys_match,
    load_action_catalog,
    parse_action_catalog,
    precondition,
    spec_by_id,
    validate_action_catalog,
    verify_action_spec,
    verify_trace_file,
)
from docich.nethack_inventory import VisibleInventoryItem
from docich.nethack_observation import normalize_tty

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "nethack-canary-actions.json"
STATUS = "HP:18(18) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50"


def frame(message: str = "", map_rows: tuple[str, ...] = (), status: str = STATUS):
    rows = 24
    body = list(map_rows) + [""] * (rows - 1 - 2 - len(map_rows)) + ["", status]
    return normalize_tty("\n".join([message] + body) + "\n", cols=80, rows=rows)


def food(letter="f"):
    return VisibleInventoryItem(
        letter=letter,
        description="a food ration",
        quantity=1,
        buc="unknown",
        equipped=False,
        unpaid=False,
        category_hint="food",
    )


def test_catalog_loads_and_matches_the_canary_policy_surface():
    specs = load_action_catalog(CATALOG)
    assert {spec.id for spec in specs} == {
        "advance_message",
        "confirm_attack",
        "decline_prompt",
        "directional_travel",
        "attack_adjacent",
        "open_door",
        "eat_food",
        "rest_impaired",
        "rest_low_hp",
        "rest",
        "explore_step",
        "search_when_blocked",
    }
    assert {spec.effect for spec in specs} <= set(REVIEWED_EFFECTS)


@pytest.mark.parametrize("message", ["", "In what direction? "])
@pytest.mark.parametrize(
    ("map_rows", "blocked"),
    [(("-----", "-@.--", "-----"), False), (("-----", "-@---", "-----"), True)],
)
def test_no_safe_step_is_symmetric_with_safe_step(map_rows, blocked, message):
    observation = frame(message, map_rows)
    ctx = ActionContext(observation)
    assert precondition("no_safe_step", ctx) is blocked
    assert precondition("safe_step", ctx) is not blocked
    assert observation.prompt == ("direction" if message else "none")


def test_search_catalog_parses_and_validates():
    specs = parse_action_catalog(json.loads(CATALOG.read_text(encoding="utf-8")))
    validate_action_catalog(specs)
    search = spec_by_id(specs, "search_when_blocked")
    assert search.effect == "keys"
    assert search.key_pattern == ("s",)
    assert search.risk_class == "movement"
    assert search.enabled
    assert search.preconditions == (
        "prompt:none", "player_visible", "no_safe_step", "no_adjacent_attackable",
    )
    assert search.postconditions == ("always",)
    assert spec_by_id(specs, "explore_step").priority < search.priority == 88
    assert search.priority < spec_by_id(specs, "rest").priority


@pytest.mark.parametrize(
    ("map_rows", "message", "expected"),
    [
        (("-----", "-@---", "-----"), "", STATUS_VERIFIED),
        (("-----", "-@.--", "-----"), "", STATUS_PRECONDITION),
        (("-----", "-@k--", "-----"), "", STATUS_PRECONDITION),
        (("-----", "-----", "-----"), "", STATUS_PRECONDITION),
        (("-----", "-@---", "-----"), "In what direction? ", STATUS_PRECONDITION),
    ],
)
def test_verify_search_trace(tmp_path, map_rows, message, expected):
    before = frame(message, map_rows)
    path = tmp_path / "search-trace.jsonl"
    path.write_text(json.dumps({
        "intent": "search_when_blocked",
        "keys": ["s"],
        "before": before.raw_text,
        "after": before.raw_text,
    }) + "\n", encoding="utf-8")
    (result,) = verify_trace_file(path, load_action_catalog(CATALOG))
    assert result.status == expected


def test_new_id_with_reviewed_effect_is_accepted_and_verifiable():
    # P6g acceptance: a brand-new id using a reviewed effect parses,
    # validates, and verifies a recorded trace.
    raw = {
        "schema_version": 1,
        "actions": [
            {
                "id": "descend_stairs",
                "effect": "keys",
                "risk_class": "movement",
                "preconditions": ["prompt:none", "player_visible"],
                "key_pattern": [">"],
                "postconditions": ["screen_changed"],
            }
        ],
    }
    (spec,) = parse_action_catalog(raw, allowed_effects=frozenset(REVIEWED_EFFECTS))
    assert spec.effect == "keys"
    before = frame("", ("....", ".@..", "...."))
    after = frame("You go down the stairs.", ("....", ".@..", "...."))
    assert (
        verify_action_spec(spec, before=before, after=after, keys=(">",)).status
        == STATUS_VERIFIED
    )


def test_parse_rejects_effects_outside_the_allowed_set():
    raw = {
        "schema_version": 1,
        "actions": [
            {
                "id": "descend_stairs",
                "effect": "keys",
                "risk_class": "movement",
                "preconditions": ["prompt:none", "player_visible"],
                "key_pattern": [">"],
                "postconditions": ["screen_changed"],
            }
        ],
    }
    with pytest.raises(ValueError, match="outside the allowed set"):
        parse_action_catalog(raw, allowed_effects=frozenset({"attack_direction"}))


def test_validate_rejects_unknown_effect_and_effect_pattern_mismatch():
    # Unknown effect.
    with pytest.raises(ValueError, match="unreviewed effect"):
        validate_action_catalog(
            (
                ActionSpec(
                    "x",
                    "movement",
                    ("prompt:none",),
                    (">",),
                    ("screen_changed",),
                    effect="zap_wand",
                ),
            )
        )
    # eat_item requires ["e", "{item_letter}"].
    with pytest.raises(ValueError, match="requires key_pattern"):
        validate_action_catalog(
            (
                ActionSpec(
                    "x",
                    "item",
                    ("prompt:none",),
                    ("e",),
                    ("screen_changed",),
                    effect="eat_item",
                ),
            )
        )
    # open_door requires ["o", "{direction}"].
    with pytest.raises(ValueError, match="requires key_pattern"):
        validate_action_catalog(
            (
                ActionSpec(
                    "x",
                    "door",
                    ("prompt:none",),
                    ("{direction}",),
                    ("screen_changed",),
                    effect="open_door",
                ),
            )
        )
    # The literal "keys" effect must not use placeholders.
    with pytest.raises(ValueError, match="must not use placeholders"):
        validate_action_catalog(
            (
                ActionSpec(
                    "x",
                    "movement",
                    ("prompt:none",),
                    ("{direction}",),
                    ("screen_changed",),
                    effect="keys",
                ),
            )
        )


def test_validate_rejects_unreviewed_catalog_entries():
    with pytest.raises(ValueError):
        validate_action_catalog(
            (
                ActionSpec("x", "unreviewed", ("prompt:more",), (" ",), ("always",)),
            )
        )
    with pytest.raises(ValueError):
        validate_action_catalog(
            (ActionSpec("x", "message", ("not_a_predicate",), (" ",), ("always",)),)
        )
    with pytest.raises(ValueError):
        validate_action_catalog(
            (ActionSpec("x", "message", ("prompt:more",), ("{bogus}",), ("always",)),)
        )


def test_keys_match_handles_placeholders():
    assert keys_match(("{direction}",), ("l",))
    assert keys_match(("{direction}",), ("n",))
    assert keys_match(("o", "{direction}"), ("o", "n"))
    assert keys_match(("e", "{item_letter}"), ("e", "f"))
    assert not keys_match(("{direction}",), ("x",))
    assert not keys_match(("o", "{direction}"), ("o", "5"))
    assert not keys_match((" ",), ("x",))


def test_verify_advance_message():
    before = frame("You see here a potion. --More--", ("....", ".@..", "...."))
    after = frame("You see here a potion.", ("....", ".@..", "...."))
    spec = spec_by_id(load_action_catalog(CATALOG), "advance_message")
    assert verify_action_spec(spec, before=before, after=after, keys=(" ",)).status == STATUS_VERIFIED
    # Wrong key.
    assert verify_action_spec(spec, before=before, after=after, keys=("x",)).status == STATUS_KEYS
    # Prompt not cleared.
    assert (
        verify_action_spec(spec, before=before, after=before, keys=(" ",)).status
        == STATUS_POSTCONDITION
    )
    # Precondition not met.
    assert (
        verify_action_spec(spec, before=after, after=after, keys=(" ",)).status
        == STATUS_PRECONDITION
    )


def test_verify_attack_adjacent():
    before = frame("", ("....", ".@k.", "...."))
    after = frame("You hit the kobold.", ("....", ".@..", "...."))
    spec = spec_by_id(load_action_catalog(CATALOG), "attack_adjacent")
    assert verify_action_spec(spec, before=before, after=after, keys=("l",)).status == STATUS_VERIFIED
    # No adjacent monster.
    calm = frame("", ("....", ".@..", "...."))
    assert (
        verify_action_spec(spec, before=calm, after=after, keys=("l",)).status
        == STATUS_PRECONDITION
    )


def test_verify_open_door_and_eat_food():
    specs = load_action_catalog(CATALOG)
    door_before = frame("", ("....", ".@+.", "...."))
    door_after = frame("The door opens.", ("....", ".@..", "...."))
    assert (
        verify_action_spec(
            spec_by_id(specs, "open_door"), before=door_before, after=door_after, keys=("o", "l")
        ).status
        == STATUS_VERIFIED
    )
    hungry = frame("", ("....", ".@..", "...."), status="HP:10(10) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:50 Hungry")
    fed = frame("You eat the food ration.", ("....", ".@..", "...."), status="HP:10(10) Pw:1(1) AC:6 Exp:1 Dlvl:1 T:51")
    assert (
        verify_action_spec(
            spec_by_id(specs, "eat_food"),
            before=hungry,
            after=fed,
            keys=("e", "f"),
            inventory=(food("f"),),
        ).status
        == STATUS_VERIFIED
    )
    # No food in inventory.
    assert (
        verify_action_spec(
            spec_by_id(specs, "eat_food"), before=hungry, after=fed, keys=("e", "f"), inventory=()
        ).status
        == STATUS_PRECONDITION
    )


def test_verify_trace_file(tmp_path):
    specs = load_action_catalog(CATALOG)
    before = frame("You see here a potion. --More--", ("....", ".@..", "...."))
    after = frame("You see here a potion.", ("....", ".@..", "...."))
    lines = [
        json.dumps(
            {
                "intent": "advance_message",
                "keys": [" "],
                "before": before.raw_text,
                "after": after.raw_text,
            }
        ),
        json.dumps({"intent": "not_a_reviewed_action", "keys": [], "before": "x", "after": "y"}),
    ]
    path = tmp_path / "action-trace.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    results = verify_trace_file(path, specs)
    assert [result.status for result in results] == [STATUS_VERIFIED, STATUS_PRECONDITION]


def test_worker_append_trace_writes_jsonl(tmp_path):
    from docich.nethack_canary_worker import _append_trace

    path = tmp_path / "trace.jsonl"
    _append_trace(path, {"intent": "rest", "keys": ["."]})
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["intent"] == "rest"
