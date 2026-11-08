"""Do not emit repeated battle start/end bulletins during a single melee.

Soren's gap ticker emits notifications when the run's battles_started /
battles_finished counters rise. A menu, dialogue, fade, or event screen may
appear while a single fight is still active; these must not close that fight.
"""
from unittest import mock

from docich import hanjuku_run
from docich.hanjuku_pixels import Frame


IDENTITY = {
    "game": "hanjuku-hero",
    "runtime_id": "g99-aabbccdd",
    "generation": 99,
    "lease_id": "battle-bulletin-test",
}


def observe_phases(tmp_path, phases):
    results = []
    for index, phase in enumerate(phases):
        # Change pixels each tick to avoid a screen-stall terminal unrelated to
        # the phase counter under test.
        rgb = bytes([(index % 250) + 1]) * (256 * 224 * 3)
        frame = Frame(256, 224, rgb)
        with mock.patch.object(hanjuku_run, "classify", return_value=phase):
            results.append(hanjuku_run.observe(
                tmp_path, IDENTITY, frame, now=1000.0 + index, wall=1000.0 + index))
    return results


def test_dialogue_and_transition_within_battle_never_emit_extra_notifications(tmp_path):
    phases = [
        "field", "battle", "battle",
        "transition", "dialogue", "dialogue", "event", "battle_intro",
        "battle", "battle", "transition", "dialogue", "battle", "battle",
        "field", "field",
    ]
    states = observe_phases(tmp_path, phases)
    for state in states[2:-1]:
        assert state["battles_started"] == 1
        assert state["battles_finished"] == 0
        assert state["battle_active"] is True
    assert states[-1]["battles_started"] == 1
    assert states[-1]["battles_finished"] == 1
    assert states[-1]["battle_active"] is False


def test_real_map_exit_then_next_fight_still_emits_each_boundary_once(tmp_path):
    states = observe_phases(tmp_path, [
        "field", "battle", "battle",
        "dialogue", "transition", "field", "field",
        "battle", "battle", "field", "field",
    ])
    assert (states[3]["battles_started"], states[3]["battles_finished"]) == (1, 0)
    assert (states[6]["battles_started"], states[6]["battles_finished"]) == (1, 1)
    assert (states[8]["battles_started"], states[8]["battles_finished"]) == (2, 1)
    assert (states[-1]["battles_started"], states[-1]["battles_finished"]) == (2, 2)


def test_unconfirmed_exit_resets_when_battle_panel_returns(tmp_path):
    states = observe_phases(tmp_path, [
        "battle", "battle", "field", "battle",
        "dialogue", "field", "battle", "battle",
    ])
    assert states[2]["battle_away_streak"] == 1
    assert states[3]["battle_away_streak"] == 0
    assert states[5]["battle_away_streak"] == 1
    assert states[-1]["battle_active"] is True
    assert states[-1]["battles_started"] == 1
    assert states[-1]["battles_finished"] == 0
