"""Reviewed prayer recovery for severe status emergencies (#759).

The evidence base and the fail-closed boundaries are documented in
``src/docich/nethack_recovery.py`` and
``docs/games/nethack-progress-contract.md``.  Every severe condition must
have one reviewed auto-execution contract, and every boundary must stay
fail-closed, exactly as the issue requires.
"""
from types import SimpleNamespace

import pytest

from docich.actions import Action
from docich.adapters.base import Observation
from docich.agent.brains import NethackPolicyBrain
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import NethackLayeredPolicy, PolicyDecision
from docich.nethack_progress import NethackProgressResolver, assert_production_safe
from docich.nethack_recovery import (
    PRAY_KEY,
    PRAY_SAFE_MAX_DUNGEON_LEVEL,
    RECOVERY_INTENT,
    SEVERE_RECOVERY_CONDITIONS,
    assert_pray_safe,
    pray_frame_ready,
    pray_recovery_action,
)


def frame(message: str = "", *, hp: str = "16(16)", condition: str = "", depth: int = 1, turn: int = 12) -> str:
    return (
        f"{message}\n"
        ".@....\n"
        "......\n"
        f"Dlvl:{depth} $:5 HP:{hp} Pw:4(4) AC:5 Exp:2\n"
        f"T:{turn} {condition}\n"
    )


def observation(text: str):
    return normalize_tty(text, cols=80, rows=5)


def emergency(obs) -> PolicyDecision:
    return NethackLayeredPolicy().decide(obs)


def agent():
    return NethackPolicyBrain(SimpleNamespace(), SimpleNamespace(
        name="nethack", adapter="cli", raw={"cli": {"cols": 80, "rows": 5}},
    ))


# --- completion criterion 1: reviewed auto-execute conditions per condition ---


@pytest.mark.parametrize("condition", sorted(SEVERE_RECOVERY_CONDITIONS))
def test_every_severe_condition_gets_the_same_reviewed_prayer(condition):
    obs = observation(frame(condition=condition))
    decision = emergency(obs)
    # The policy decision itself stays fail-closed and action-free; the
    # recovery action is a resolver substitution, exactly like rest_turn.
    assert decision.layer == "strategic"
    assert decision.intent == "status_emergency"
    assert decision.requires_llm is True
    assert decision.actions == ()

    pray = pray_recovery_action(decision, obs)
    assert pray is not None
    assert pray.type == "text"
    assert pray.text == PRAY_KEY

    resolver = NethackProgressResolver()
    resolved = resolver.resolve(decision, obs, NethackLayeredPolicy().explorer)
    assert resolved.intent == RECOVERY_INTENT
    assert [a.text for a in resolved.actions] == [PRAY_KEY]


def test_prayer_is_independent_of_adjacent_creatures():
    # Issue requirement: the treatment must not change on adjacency alone.
    # The condition is lethal on a timer, so an adjacent creature never
    # withholds the one reviewed cure.
    for text in (
        frame(condition="Sick"),
        frame(condition="Stone"),
        frame(condition="Strngl"),
    ):
        row = text.splitlines()[1]
        text = text.replace(row, row.replace(".@....", ".@d..:"), 1)
        obs = observation(text)
        decision = emergency(obs)
        assert decision.intent == "status_emergency"
        assert pray_recovery_action(decision, obs) is not None


# --- fail-closed boundaries ---------------------------------------------------


@pytest.mark.parametrize("condition", sorted(SEVERE_RECOVERY_CONDITIONS))
def test_prayer_never_answers_a_prompt_or_playerless_frame(condition):
    for text in (
        frame("Really quit? [yn]", condition=condition),
        frame("What do you want to drink?", condition=condition),
        "\n" + frame(condition=condition).split("\n", 1)[1].replace(".@....", "  ...."),
        frame(hp="0(16)", condition=condition),
    ):
        obs = observation(text)
        decision = emergency(obs)
        assert pray_recovery_action(decision, obs) is None
        resolver = NethackProgressResolver()
        resolved = resolver.resolve(decision, obs, NethackLayeredPolicy().explorer)
        # Prompt/unknown/player-less/zero-HP frames keep their no-input
        # decision; the resolver must not invent any key for them.
        assert resolved.actions == ()


@pytest.mark.parametrize("condition", sorted(SEVERE_RECOVERY_CONDITIONS))
def test_prayer_is_gated_above_the_gehennom_depth_boundary(condition):
    assert pray_frame_ready(observation(frame(condition=condition, depth=PRAY_SAFE_MAX_DUNGEON_LEVEL)))
    for depth in (PRAY_SAFE_MAX_DUNGEON_LEVEL + 1, 25, 40):
        obs = observation(frame(condition=condition, depth=depth))
        decision = emergency(obs)
        assert pray_recovery_action(decision, obs) is None
        resolver = NethackProgressResolver()
        resolved = resolver.resolve(decision, obs, NethackLayeredPolicy().explorer)
        # Fail-closed depth falls back to the explicit wait turn, not input.
        assert [a.text for a in resolved.actions] == ["."]
        assert resolved.intent == "rest_turn"


@pytest.mark.parametrize("condition", sorted(SEVERE_RECOVERY_CONDITIONS))
def test_unknown_dungeon_level_stays_fail_closed(condition):
    text = frame(condition=condition).replace("Dlvl:1", "Dlvl:", 1)
    obs = observation(text)
    assert obs.vitals.dungeon_level is None
    decision = emergency(obs)
    assert pray_recovery_action(decision, obs) is None
    resolved = NethackProgressResolver().resolve(decision, obs, NethackLayeredPolicy().explorer)
    # No dungeon level is not a gameplay turn: the resolver must not invent
    # any key (neither a prayer nor a wait).
    assert resolved.actions == ()


def test_non_severe_intents_never_pray():
    # food/survival emergencies and holds keep their established contracts;
    # only the severe status emergency maps to the recovery surface.
    for condition, intent in (
        ("Weak", "food_emergency"),
        ("Fainted", "food_emergency"),
        ("Hungry", "seek_food"),
        ("Blind", "hold_impaired"),
    ):
        obs = observation(frame(condition=condition))
        decision = emergency(obs)
        assert decision.intent == intent
        assert pray_recovery_action(decision, obs) is None


def test_empty_and_preoccupied_decisions_are_ignored():
    obs = observation(frame(condition="Sick"))
    assert pray_recovery_action(None, obs) is None
    moved = PolicyDecision(
        "midlevel", "explore_step", "test", (Action(type="text", text="h"),)
    )
    assert pray_recovery_action(moved, obs) is None
    assert pray_recovery_action(emergency(obs), observation(frame())) is None


# --- production guard ----------------------------------------------------------


def test_production_guard_accepts_only_the_reviewed_prayer():
    obs = observation(frame(condition="Sick"))
    good = PolicyDecision(
        "tactical", RECOVERY_INTENT, "test", (Action(type="text", text="p"),)
    )
    assert_production_safe(good, obs)
    assert_pray_safe(good, obs)


@pytest.mark.parametrize("actions", [
    (Action(type="text", text="."),),
    (Action(type="text", text="P"),),
    (Action(type="text", text="q"),),
    (Action(type="text", text=" "),),
    (Action(type="text", text="p"), Action(type="text", text="p")),
])
def test_production_guard_rejects_wrong_keys_or_counts(actions):
    obs = observation(frame(condition="Sick"))
    decision = PolicyDecision("tactical", RECOVERY_INTENT, "test", tuple(actions))
    with pytest.raises(RuntimeError):
        assert_production_safe(decision, obs)
    with pytest.raises(RuntimeError):
        assert_pray_safe(decision, obs)


@pytest.mark.parametrize("text", [
    frame(condition="Sick", depth=21),           # Gehennom gate
    frame(condition="Weak"),                      # not a severe condition
    frame("Really quit? [yn]", condition="Sick"),  # prompt frame
    frame(hp="0(16)", condition="Sick"),          # no positive HP
])
def test_production_guard_rejects_wrong_context(text):
    obs = observation(text)
    decision = PolicyDecision(
        "tactical", RECOVERY_INTENT, "test", (Action(type="text", text="p"),)
    )
    with pytest.raises(RuntimeError):
        assert_production_safe(decision, obs)
    with pytest.raises(RuntimeError):
        assert_pray_safe(decision, obs)


# --- episode budget and pending-prayer hold -----------------------------------


def test_one_prayer_per_episode_then_fail_closed_wait():
    obs = observation(frame(condition="Sick"))
    decision = emergency(obs)
    resolver = NethackProgressResolver()

    resolved = resolver.resolve(decision, obs, NethackLayeredPolicy().explorer)
    assert resolved.intent == RECOVERY_INTENT
    resolver.sent(resolved, obs)

    # The identical frame must not receive any key: a typed-ahead key would
    # interrupt the three-turn prayer freeze.
    held = resolver.resolve(decision, obs, NethackLayeredPolicy().explorer)
    assert held.intent == "progress_blocked"
    assert held.actions == ()

    # The prayer did not cure (frame changed, condition persists): the
    # episode budget is spent, so the resolver must fall back to the
    # fail-closed explicit wait and never pray again.
    after = observation(frame(condition="Sick", turn=16, hp="14(16)"))
    resolver.observe(after, NethackLayeredPolicy().explorer)
    resolved_after = resolver.resolve(decision, after, NethackLayeredPolicy().explorer)
    assert resolved_after.intent == "rest_turn"
    assert [a.text for a in resolved_after.actions] == ["."]

    later = observation(frame(condition="Sick", turn=40, hp="12(16)"))
    resolver.observe(later, NethackLayeredPolicy().explorer)
    assert resolver.resolve(decision, later, NethackLayeredPolicy().explorer).intent == "rest_turn"


def test_clean_frame_rearms_the_episode_budget():
    obs = observation(frame(condition="Sick"))
    resolver = NethackProgressResolver()
    resolved = resolver.resolve(emergency(obs), obs, NethackLayeredPolicy().explorer)
    resolver.sent(resolved, obs)
    # Cured: a severe-free observation clears the block.
    cured = observation(frame(condition="", turn=16))
    resolver.observe(cured, NethackLayeredPolicy().explorer)
    # A new episode may pray again.
    relapse = observation(frame(condition="Stone", turn=30))
    resolver.observe(relapse, NethackLayeredPolicy().explorer)
    again = resolver.resolve(emergency(relapse), relapse, NethackLayeredPolicy().explorer)
    assert again.intent == RECOVERY_INTENT


def test_more_prompt_advances_while_prayer_is_pending():
    obs = observation(frame(condition="Sick"))
    resolver = NethackProgressResolver()
    resolved = resolver.resolve(emergency(obs), obs, NethackLayeredPolicy().explorer)
    resolver.sent(resolved, obs)
    more = observation(frame("A message from the gods. --More--", condition="Sick"))
    resolver.observe(more, NethackLayeredPolicy().explorer)
    decision = emergency(more)
    assert decision.intent == "advance_message"
    assert resolver.resolve(decision, more, NethackLayeredPolicy().explorer).actions == decision.actions


def test_scene_change_clears_the_pending_prayer_hold():
    obs = observation(frame(condition="Sick"))
    resolver = NethackProgressResolver()
    resolved = resolver.resolve(emergency(obs), obs, NethackLayeredPolicy().explorer)
    resolver.sent(resolved, obs)
    moved = observation(frame(condition="Sick", turn=13).replace(".@....", "....@."))
    resolver.observe(moved, NethackLayeredPolicy().explorer)
    # The scene changed: the pending hold no longer blocks a reviewed
    # decision, but the spent episode budget still forces the wait turn.
    result = resolver.resolve(emergency(moved), moved, NethackLayeredPolicy().explorer)
    assert [a.text for a in result.actions] == ["."]


# --- brain and agent-loop real path -------------------------------------------


def _send_from(brain, actions, text: str, *, ts: float) -> None:
    """Drive the real validate/send boundary for already-planned actions."""
    assert len(actions) == 1
    fresh = Observation(
        game="nethack", title="NetHack", adapter="cli", ts=ts + 0.1, kind="text",
        text=text,
    )
    assert brain.validate_action(actions[0], fresh)
    brain.action_sent(actions[0])


def _send(brain, obs_action_text: str, *, ts: float) -> None:
    """Decide and transport one action through the real boundary."""
    actions = brain.decide(Observation(
        game="nethack", title="NetHack", adapter="cli", ts=ts, kind="text",
        text=obs_action_text,
    ))
    _send_from(brain, actions, obs_action_text, ts=ts)


def test_brain_sends_one_prayer_for_a_severe_frame():
    subject = agent()
    sick = frame(condition="Sick")
    obs = Observation(
        game="nethack", title="NetHack", adapter="cli", ts=1.0, kind="text",
        text=sick,
    )
    actions = subject.decide(obs)
    assert [(a.type, a.text) for a in actions] == [("text", "p")]
    assert subject.last_decision.intent == "status_emergency"
    assert subject.last_progress_decision.intent == RECOVERY_INTENT
    # Transport the prayer through the real validate/send boundary; this is
    # what starts the pending-prayer hold and spends the episode budget.
    _send(subject, sick, ts=1.0)
    # A repeated identical frame in the pending window produces no input.
    held = subject.decide(Observation(
        game="nethack", title="NetHack", adapter="cli", ts=2.0, kind="text",
        text=sick,
    ))
    assert held == []


def test_brain_falls_back_to_wait_after_a_failed_prayer():
    subject = agent()
    sick = frame(condition="Sick")
    first = subject.decide(Observation(
        game="nethack", title="NetHack", adapter="cli", ts=1.0, kind="text",
        text=sick,
    ))
    assert [(a.type, a.text) for a in first] == [("text", "p")]
    _send_from(subject, first, sick, ts=1.0)
    # Post-prayer frame: changed, condition persists -> wait turn, no re-pray.
    actions = subject.decide(Observation(
        game="nethack", title="NetHack", adapter="cli", ts=2.0, kind="text",
        text=frame(condition="Sick", hp="14(16)", turn=16),
    ))
    assert [a.text for a in actions] == ["."]
    assert subject.last_progress_decision.intent == "rest_turn"


def test_loop_transports_exactly_one_prayer_key():
    from unittest.mock import Mock, patch

    from docich.agent.loop import _run_iteration

    adapter = Mock()
    text = frame(condition="Stone")
    adapter.observe.return_value = Observation(
        game="nethack", title="NetHack", adapter="cli", ts=1.0, kind="text", text=text,
    )
    with patch("docich.agent.loop.read_canonical", return_value=None), patch(
        "docich.agent.loop.shared_section", side_effect=lambda _, operation: operation()
    ):
        count = _run_iteration(adapter, agent(), 1500, state_dir=None)
    actions = [call.args[0] for call in adapter.act.call_args_list]
    assert count == len(actions) == 1
    assert actions[0].type == "text" and actions[0].text == PRAY_KEY
