"""Startup budgets bound prompt answers, not recognition of later gameplay."""
from dataclasses import replace

import pytest

from docich.nethack_observation import normalize_tty
from docich.nethack_startup import NethackStartup


def frame(message="You hear a distant sound.", *, player="@", status=None):
    lines = [""] * 24
    lines[0] = message
    lines[2] = " ---------"
    lines[3] = f" |..{player}....|"
    lines[4] = " ---------"
    lines[22] = "Docich the Rambler St:13 Dx:14 Co:14 In:13 Wi:9 Ch:12 Neutral"
    lines[23] = status or "Dlvl:1 $:474 HP:8(10) Pw:2(2) AC:10 Xp:1"
    return normalize_tty("\n".join(lines))


def exhausted_startup(cause):
    clock = [0.0]
    startup = NethackStartup(enabled=True, clock=lambda: clock[0])
    unknown = normalize_tty("Loading")
    assert startup.consider(unknown) == []
    if cause == "time":
        clock[0] = startup.TIMEOUT_S
    elif cause == "observations":
        startup.observations = startup.MAX_OBSERVATIONS
    elif cause == "actions":
        startup.actions = startup.MAX_ACTIONS
        unknown = normalize_tty("Shall I pick a character for you? [yn] (y)")
    else:
        raise AssertionError(cause)
    assert startup.consider(unknown) == []
    assert startup.state == "exhausted"
    return startup


@pytest.mark.parametrize("cause", ["time", "observations", "actions"])
def test_exhausted_startup_delegates_late_complete_gameplay_without_keys(cause):
    startup = exhausted_startup(cause)
    counters = (startup.observations, startup.actions, startup.started_at)
    assert startup.consider(frame()) is None
    assert startup.state == "gameplay"
    assert (startup.observations, startup.actions, startup.started_at) == counters
    assert startup.consider(frame()) is None


@pytest.mark.parametrize("cause", ["time", "observations"])
def test_complete_gameplay_on_budget_boundary_is_not_discarded(cause):
    clock = [0.0]
    startup = NethackStartup(enabled=True, clock=lambda: clock[0])
    assert startup.consider(normalize_tty("Loading")) == []
    if cause == "time":
        clock[0] = startup.TIMEOUT_S
    else:
        startup.observations = startup.MAX_OBSERVATIONS
    assert startup.consider(frame()) is None
    assert startup.state == "gameplay"


@pytest.mark.parametrize("message", [
    "Really save? [yn] (n)",
    "Really attack the shopkeeper? [yn] (n)",
    "In what direction?",
    "What do you want to eat?",
    "What do you want to call a potion?",
    "Unknown question?",
    "Inventory:",
    "You see a scroll. --More--",
    "Do you want a tutorial? [yn] (n)",
])
def test_exhaustion_does_not_authorize_prompts_with_stale_map(message):
    startup = exhausted_startup("time")
    obs = frame(message)
    assert obs.player is not None and obs.vitals.hp == 8
    assert obs.prompt != "none"
    assert startup.consider(obs) == []
    assert startup.state == "exhausted"
    assert startup.actions == 0


@pytest.mark.parametrize("kind", [
    "missing_player", "ambiguous_player", "missing_hp", "dead_hp", "negative_hp",
    "missing_hp_max", "zero_hp_max", "missing_depth",
])
def test_exhaustion_does_not_accept_incomplete_or_dead_frame(kind):
    startup = exhausted_startup("time")
    obs = frame()
    if kind == "missing_player":
        obs = frame(player=".")
    elif kind == "ambiguous_player":
        obs = frame(player="@@")
    else:
        change = {
            "missing_hp": {"hp": None},
            "dead_hp": {"hp": 0},
            "negative_hp": {"hp": -1},
            "missing_hp_max": {"hp_max": None},
            "zero_hp_max": {"hp_max": 0},
            "missing_depth": {"dungeon_level": None},
        }[kind]
        obs = replace(obs, vitals=replace(obs.vitals, **change))
    assert startup.consider(obs) == []
    assert startup.state == "exhausted"


@pytest.mark.parametrize("message", [
    "Shall I pick a character for you? [yn] (y)",
    "Is this ok? [yn] (y)",
    "Restoring save file...--More--",
    "Loading",
])
def test_exhaustion_never_rearms_startup_answers(message):
    startup = exhausted_startup("actions")
    before = dict(startup.__dict__)
    for _ in range(3):
        assert startup.consider(normalize_tty(message)) == []
    assert startup.__dict__ == before


def test_unknown_overlay_can_clear_without_restarting_exhausted_gate():
    startup = exhausted_startup("observations")
    assert startup.consider(frame("Unknown question?")) == []
    assert startup.consider(frame()) is None
    assert startup.state == "gameplay"


def test_tutorial_decline_before_exhaustion_is_preserved():
    startup = NethackStartup(enabled=True)
    obs = frame("Do you want a tutorial? [yn] (n)")
    assert [action.text for action in startup.consider(obs)] == ["n"]
    assert startup.state == "answering"
    assert startup.consider(frame()) is None


def test_disabled_startup_remains_disabled():
    startup = NethackStartup(enabled=False)
    assert startup.consider(frame()) is None
    assert startup.state == "disabled"


def test_late_completion_logs_only_fixed_state(capsys):
    startup = exhausted_startup("time")
    capsys.readouterr()
    assert startup.consider(frame("PRIVATE_SENTINEL ordinary message.")) is None
    output = capsys.readouterr().err
    assert output == "[nethack-startup] state=gameplay\n"
    assert "PRIVATE_SENTINEL" not in output


def test_tutorial_question_below_row_zero_is_still_answered_before_gameplay():
    # obs.prompt reads only row 0: a tutorial y/n on a lower row over a drawn
    # map must keep its startup answer instead of delegating to the policy.
    lines = [""] * 24
    lines[1] = "Do you want a tutorial? [yn] (n)"
    lines[3] = " |..@....|"
    lines[22] = "Docich the Rambler St:13 Dx:14 Co:14 In:13 Wi:9 Ch:12 Neutral"
    lines[23] = "Dlvl:1 $:474 HP:8(10) Pw:2(2) AC:10 Xp:1"
    startup = NethackStartup(enabled=True, clock=lambda: 0.0)
    actions = startup.consider(normalize_tty("\n".join(lines)))
    assert actions and actions[0].text == "n"
    assert startup.state != "gameplay"
