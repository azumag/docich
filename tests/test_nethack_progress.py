"""Observed-context and multi-frame production contracts for Issue #490."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docich.actions import Action
from docich.adapters.base import Observation
from docich.agent.brains import NethackPolicyBrain
from docich.nethack_exploration import DIRECTIONS, NethackExplorer
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import NethackLayeredPolicy, PolicyDecision, rest_action_for_hold, step_out_of_hold
from docich.nethack_progress import assert_production_safe


def frame(neighbors=None, *, message="", hp="16(16)", condition="", turn=12, depth=1):
    grid = [list(" " * 80) for _ in range(18)]
    grid[14][40] = "@"
    for key, glyph in (neighbors or {}).items():
        dx, dy, _ = next(direction for direction in DIRECTIONS if direction[2] == key)
        grid[14 + dy][40 + dx] = glyph
    return "\n".join([
        message, *("".join(row) for row in grid),
        f"Dlvl:{depth} HP:{hp} Pw:2(2) AC:6 Xp:1",
        f"T:{turn} {condition}" if turn is not None else condition,
    ])


def brain():
    return NethackPolicyBrain(SimpleNamespace(), SimpleNamespace(
        name="nethack", adapter="cli", raw={"cli": {"cols": 80, "rows": 24}},
    ))


READY = {"phase": "ready", "active": {"game": "nethack"}, "operation": None, "request_id": None}


def act(agent, text, *, canonical=None):
    from unittest.mock import Mock, patch
    from docich.agent.loop import _run_iteration
    adapter = Mock()
    adapter.observe.return_value = Observation(
        game="nethack", title="NetHack", adapter="cli", ts=1.0, kind="text", text=text,
    )
    with patch("docich.agent.loop.read_canonical", return_value=canonical), patch("docich.agent.loop.shared_section", side_effect=lambda _, operation: operation()):
        count = _run_iteration(adapter, agent, 1500, state_dir="test-state" if canonical is not None else None)
    actions = [call.args[0] for call in adapter.act.call_args_list]
    assert count == len(actions)
    assert len(actions) <= 1
    assert all(action.type == "text" and len(action.text) == 1 for action in actions)
    return [action.text for action in actions]


def test_reported_generation_252_diagonal_only_escape():
    # Attachment's public evidence: player (40,14), HP 4/16, adjacent ':' and
    # feline, with '#' only southeast. No production state/credentials needed.
    text = frame({"k": ":", "j": "f", "n": "#"}, hp="4(16)")
    obs = normalize_tty(text)
    assert obs.player == (40, 14)
    assert obs.visible_neighbors() == (" ", ":", " ", " ", " ", " ", "f", "#")
    agent = brain()
    assert act(agent, text) == ["n"]
    assert agent.last_decision.intent == "survival_emergency"
    assert agent.last_progress_decision.intent == "retreat_step"


@pytest.mark.parametrize("key", ["y", "u", "b", "n"])
@pytest.mark.parametrize("glyph", [".", "#", "<", ">"])
def test_each_diagonal_safe_terrain_is_available_to_policy_and_fallback(key, glyph):
    agent = brain()
    assert act(agent, frame({key: glyph})) == [key]
    assert agent.last_progress_decision.intent == "explore_step"
    assert act(brain(), frame({key: glyph, "k": "d"}, hp="4(16)")) == [key]


@pytest.mark.parametrize("key", [key for _dx, _dy, key in DIRECTIONS])
@pytest.mark.parametrize("glyph", ["d", "f", ":", "&", "'"])
def test_bump_is_last_resort_in_each_direction(key, glyph):
    agent = brain()
    assert act(agent, frame({key: glyph}, hp="4(16)")) == [key]
    assert agent.last_progress_decision.intent == "bump_creature"


def test_cardinal_then_diagonal_escape_precede_bump_even_at_low_hp():
    agent = brain()
    assert act(agent, frame({"k": "d", "h": ".", "n": "#"}, hp="4(16)")) == ["h"]
    assert agent.last_progress_decision.intent == "retreat_step"
    assert act(brain(), frame({"k": "d", "n": "#"}, hp="4(16)")) == ["n"]


def test_feline_is_not_a_fountain():
    assert act(brain(), frame({"h": "f"}, hp="4(16)")) == ["h"]
    assert act(brain(), frame({"h": "{"}, hp="4(16)")) == ["."]


@pytest.mark.parametrize("glyph", ["+", "^", "!", "$", "}", "{", " ", "I", "0", "é"])
def test_terrain_planner_and_bump_never_target_unreviewed_glyphs(glyph):
    text = frame({"n": glyph, "k": "d"})
    assert NethackExplorer().plan_step(normalize_tty(text)) is None
    assert act(brain(), text) == ["k"]  # only actual visible creature is eligible


@pytest.mark.parametrize("condition", ["Sick", "FoodPois", "Ill", "Slime", "Strngl", "Stone", "TermIll", "Weak", "Fainting", "Fainted", "Starved"])
@pytest.mark.parametrize("hp", ["16(16)", "4(16)"])
def test_severe_status_and_starvation_dominate_hp_and_all_fallbacks(condition, hp):
    text = frame({"k": "d", "n": "#"}, hp=hp, condition=condition)
    agent = brain()
    assert act(agent, text) == []
    assert agent.last_decision.intent in {"status_emergency", "food_emergency"}
    # Helpers also guard the observation even if a caller supplies a misleading
    # survival intent (the exact critical-HP masking regression).
    obs = normalize_tty(text)
    fake = PolicyDecision("strategic", "survival_emergency", "test", requires_llm=True)
    assert step_out_of_hold(fake, obs, NethackExplorer()) is None
    assert rest_action_for_hold(fake, normalize_tty(frame(hp=hp, condition=condition))) is None


@pytest.mark.parametrize("condition", ["Blind", "Conf", "Stun", "Hallu"])
@pytest.mark.parametrize("hp", ["16(16)", "4(16)"])
def test_impairment_never_moves_or_bumps_even_if_hp_masks_its_intent(condition, hp):
    assert act(brain(), frame({"k": "d", "n": "#"}, hp=hp, condition=condition)) == []
    assert act(brain(), frame({"n": "#"}, hp=hp, condition=condition)) == ["."]


def test_hungry_explores_instead_of_repeatedly_resting_until_weak():
    assert act(brain(), frame({"n": "#"}, condition="Hungry")) == ["n"]
    assert act(brain(), frame(condition="Hungry")) == []


@pytest.mark.parametrize("message", [
    "Really quit? [yn] (n)", "Eat this corpse? [yn]", "In what direction?",
    "What do you want to drink?", "Call a potion:", "Unknown question?",
    "Choose an option [abc]", "Inventory (end)", "Inventory (1 of 2)",
    "Really attack the kitten? [yn] (n) y", "Really save? [yn] (n) n",
    "History: Really attack the kitten? [yn]", "Really attack? [ynq]",
    "Really attack the kitten? [yn] --More--",  # More cannot override a question
    "What do you want to drink? --More--", "Really attack the kitten?",
    "Remember Really save? [yn] (n)", "Really attack the kitten? [yn] (y)",
    "Really attack? [yn]" + " " * 80 + "y",
])
def test_unknown_dangerous_stale_or_malformed_prompts_are_fail_closed(message):
    text = frame({"n": "#", "h": "d"}, message=message, hp="4(16)")
    assert normalize_tty(text).prompt != "none"
    assert act(brain(), text) == []


@pytest.mark.parametrize("message", [
    "Would you like to inspect " + "this unusual object " * 5 + "before continuing?",
    "Really attack the " + "very " * 20 + "peaceful kitten? [yn] (n)",
    " " * 68 + "Really save? [yn] (n)",
])
@pytest.mark.parametrize("wrap", ["hard", "word", "joined"])
def test_wrapped_prompt_never_sends_diagonal_or_confirmation_n(message, wrap):
    import textwrap
    if wrap == "hard":
        message = "\n".join(message[i:i + 80] for i in range(0, len(message), 80))
    elif wrap == "word":
        message = "\n".join(textwrap.wrap(message, width=80))
    # Without the prompt guard, the only safe terrain would select 'n'. Save
    # is tested with an eligible canonical state, not masked by its owner gate.
    text = frame({"n": "#", "k": "d"}, message=message, hp="4(16)")
    assert normalize_tty(text).prompt == "unknown"
    assert act(brain(), text, canonical=READY) == []


def test_full_width_attack_with_continuation_never_authorizes_decline():
    message = "Really attack " + "x" * 55 + "? [yn] (n)"
    assert len(message) == 79
    text = frame({"n": "#"}, message=message + "\ny")
    assert normalize_tty(text).prompt == "unknown"
    assert act(brain(), text, canonical=READY) == []


def test_map_yes_no_glyphs_do_not_block_diagonal_progress():
    text = frame({"n": "#", "k": "d"}, hp="4(16)")
    lines = text.splitlines()
    lines[5] = " " * 20 + "|.[yn.|"
    text = "\n".join(lines)
    assert normalize_tty(text).prompt == "none"
    assert act(brain(), text) == ["n"]


@pytest.mark.parametrize("length", [60, 73, 78])
@pytest.mark.parametrize("map_row", ["-" * 80, "#" * 80, "dfyn" * 20, "[yn" * 26])
def test_long_normal_message_and_next_map_row_keep_progress(length, map_row):
    message = "You see " + "x" * (length - len("You see .")) + "."
    lines = frame({"n": "#", "k": "d"}, message=message, hp="4(16)").splitlines()
    lines[1] = map_row
    text = "\n".join(lines)
    assert normalize_tty(text).prompt == "none"
    assert act(brain(), text) == ["n"]


@pytest.mark.parametrize("message", ["Really", "Really save", "Really attack the kitten", "Would you like to inspect"])
def test_incomplete_question_stem_never_sends_map_or_decline_key(message):
    assert act(brain(), frame({"n": "#"}, message=message), canonical=READY) == []


def test_hard_wrapped_unknown_stem_never_sends_map_key():
    message = "An unusual object " + "x" * 62 + "\ncontinue?"
    assert act(brain(), frame({"n": "#"}, message=message), canonical=READY) == []


@pytest.mark.parametrize("message,intent", [
    ("Really attack? [yn]", "decline_attack"),
    ("Really attack the kitten? [yn] (n)", "decline_attack"),
    ("Really save? [yn] (n)", "decline_save"),
])
def test_reviewed_confirmation_is_declined_once_per_visible_frame(message, intent):
    agent = brain()
    text = frame({"n": "#"}, message=message)
    state = READY if intent == "decline_save" else None
    assert act(agent, text, canonical=state) == ["n"]
    assert agent.last_progress_decision.intent == intent
    assert act(agent, text, canonical=state) == []
    assert agent.last_progress_decision.intent == "progress_blocked"
    # n on the subsequent map is now southeast, context checked independently.
    assert act(agent, frame({"n": "#"})) == ["n"]
    assert agent.last_progress_decision.intent == "explore_step"


def test_peaceful_attack_rejection_does_not_loop_bump_no_bump():
    agent = brain()
    occupied = {"h": "f", "k": "d"}
    assert act(agent, frame(occupied)) == ["k"]
    assert act(agent, frame(occupied, message="Really attack the dog? [yn] (n)")) == ["n"]
    assert act(agent, frame(occupied, message="Never mind.")) == ["h"]
    assert act(agent, frame(occupied, message="Really attack the cat? [yn] (n)")) == ["n"]
    # New messages/turns do not establish a peaceful creature is gone.
    assert act(agent, frame(occupied, message="Never mind.", turn=13)) == []
    assert agent.last_progress_decision.intent == "progress_blocked"
    assert act(agent, frame({"h": "."}, turn=14)) == ["h"]


def test_rejected_diagonal_yields_to_another_route_then_bump():
    agent = brain()
    text = frame({"k": "d", "b": ".", "n": "#"}, hp="4(16)")
    assert act(agent, text) == ["b"]
    assert act(agent, text) == ["b"]  # allow one observation/actuation lag
    assert act(agent, text) == ["n"]
    assert act(agent, text) == ["n"]
    assert act(agent, text) == ["k"]
    assert agent.last_progress_decision.intent == "bump_creature"
    assert act(agent, text) == ["k"]
    assert act(agent, text) == []  # no endless key spam mistaken for progress
    # New map/player/depth invalidates transient rejected edges.
    assert act(agent, frame({"k": "d", "b": ".", "n": "#"}, hp="4(16)", depth=2)) == ["b"]


def test_stationary_combat_is_progress_when_turns_advance():
    agent = brain()
    for turn in range(12, 32):
        assert act(agent, frame({"h": ":"}, turn=turn, message="You miss the newt.")) == ["h"]
        assert agent.last_progress_decision.intent == "bump_creature"


def test_more_page_preserves_pending_contact_until_attack_confirmation():
    agent = brain()
    assert act(agent, frame({"h": "f"})) == ["h"]
    assert act(agent, frame({"h": "f"}, message="A message --More--")) == [" "]
    assert act(agent, frame({"h": "f"}, message="Really attack the cat? [yn] (n)")) == ["n"]
    assert act(agent, frame({"h": "f"})) == []


@pytest.mark.parametrize("key", ["Fh", "h.", "y\n", ">", "<", "o", "e", "q", "s", "\x1b"])
def test_final_guard_rejects_macros_items_stairs_and_unreviewed_commands(key):
    obs = normalize_tty(frame({"h": "."}))
    with pytest.raises(RuntimeError):
        assert_production_safe(PolicyDecision("tactical", "retreat_step", "bad", (Action(type="text", text=key),)), obs)


def test_final_guard_rechecks_context_instead_of_trusting_policy_intent():
    move = PolicyDecision("midlevel", "explore_step", "bad", (Action(type="text", text="n"),))
    attack = replace(move, intent="bump_creature")
    rest = replace(move, intent="rest_turn", actions=(Action(type="text", text="."),))
    for decision, text in [
        (move, frame({"n": "#"}, message="Really quit? [yn]")),
        (move, frame({"n": "d"})), (move, frame({"n": "^"})),
        (move, frame({"n": "#"}, condition="Conf")),
        (attack, frame({"n": "."})), (attack, frame({"n": "I"})),
        (rest, frame({"h": "f"})), (rest, frame(condition="Slime", hp="4(16)")),
        (move, frame({"n": "#"}, hp="0(16)")),
        (replace(move, intent="decline_attack"), frame({"n": "#"})),
    ]:
        with pytest.raises(RuntimeError):
            assert_production_safe(decision, normalize_tty(text))


def test_missing_status_does_not_make_stale_map_actionable():
    text = "\n @#\n   \n"
    assert act(brain(), text) == []


def test_no_turn_field_bounds_unchanged_terrain_and_combat():
    agent = brain()
    text = frame({"k": "d", "n": "#"}, hp="4(16)", turn=None)
    assert act(agent, text) == ["n"]
    assert act(agent, text) == ["n"]
    assert act(agent, text) == ["k"]
    assert act(agent, text) == ["k"]
    for _ in range(4):
        assert act(agent, text) == []
        assert agent.last_progress_decision.intent == "progress_blocked"
    assert act(agent, frame({"k": "d", "n": "#"}, hp="4(16)", turn=None, depth=2)) == ["n"]


def test_agent_loop_sends_resolved_diagonal_then_declines_observed_attack():
    from unittest.mock import Mock
    from docich.agent.loop import _run_iteration

    agent = brain()
    adapter = Mock()
    frames = [
        frame({"k": "d", "n": "#"}, hp="4(16)"),
        frame({"k": "d", "n": "#"}, hp="4(16)", message="Really attack the dog? [yn] (n)"),
        frame({"k": "d", "n": "#"}, hp="4(16)", message="Really attack the dog? [yn] (n)"),
    ]
    adapter.observe.side_effect = [Observation(
        game="nethack", title="NetHack", adapter="cli", ts=float(i), kind="text", text=text,
    ) for i, text in enumerate([frames[0], frames[0], frames[1], frames[1], frames[2]])]
    assert [_run_iteration(adapter, agent, 1500) for _ in frames] == [1, 1, 0]
    assert [call.args[0].text for call in adapter.act.call_args_list] == ["n", "n"]


def test_candidate_sidecar_cannot_replace_resolved_actions():
    agent = brain()
    calls = []
    def candidate(*args):
        calls.append(args[-1])
        return {"actions": ["F", "y"]}
    agent.candidate_shadow.consider = candidate
    assert act(agent, frame({"k": "d", "n": "#"}, hp="4(16)")) == ["n"]
    assert [action.text for action in calls[0]] == ["n"]


def test_new_contract_suite_is_in_explicit_ci_list():
    from pathlib import Path
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    assert "tests/test_nethack_progress.py" in workflow.read_text()


@pytest.mark.parametrize("condition", ["Stone", "TermIll"])
def test_real_tty_severe_condition_spelling_is_normalized(condition):
    text = frame({"n": "#"}, condition=condition, hp="4(16)")
    assert condition in normalize_tty(text).conditions
    assert act(brain(), text) == []


@pytest.mark.parametrize("hp", ["4(16)", "8(16)", "16(16)"])
def test_hungry_at_any_hp_uses_terrain_then_bump_then_hold(hp):
    assert act(brain(), frame({"n": "#", "k": "d"}, hp=hp, condition="Hungry")) == ["n"]
    assert act(brain(), frame({"n": "#"}, hp=hp, condition="Hungry")) == ["n"]
    assert act(brain(), frame({"k": "d"}, hp=hp, condition="Hungry")) == ["k"]
    assert act(brain(), frame(hp=hp, condition="Hungry")) == []
    rest = PolicyDecision("tactical", "rest_turn", "test", (Action(type="text", text="."),))
    with pytest.raises(RuntimeError):
        assert_production_safe(rest, normalize_tty(frame(hp=hp, condition="Hungry")))


def test_same_more_frame_is_sent_once_until_frame_changes():
    agent = brain()
    text = frame(message="You hit the newt. --More--", turn=None)
    assert act(agent, text) == [" "]
    for _ in range(6):
        assert act(agent, text) == []
        assert agent.last_progress_decision.intent == "progress_blocked"
    assert act(agent, frame(message="Another page --More--", turn=None)) == [" "]


def observation(text):
    return Observation(game="nethack", title="NetHack", adapter="cli", ts=1.0, kind="text", text=text)


@pytest.mark.parametrize("fresh", [
    frame({"n": "#"}, message="Really quit? [yn]"),
    frame({"n": "d"}),  # the movement target became a creature
    frame({"n": "^"}),
    frame({"n": "#", "k": "d"}),  # another map cell changed
    frame({"n": "#"}, depth=2),
    frame({"n": "#"}, hp="8(16)"),
    frame({"n": "#"}, condition="Stone"),
    frame({"n": "#"}, condition="TermIll"),
    frame({"n": "#"}, condition="Conf"),
    frame({"n": "#"}, turn=13),
    frame({"n": "#"}).replace("@", " @"),  # player moved
    frame({"n": "#"}).replace("@", " "),
    frame({"n": "#"}, message="A different message."),
])
def test_loop_rejects_changed_context_before_act_without_spending_progress(fresh):
    from unittest.mock import Mock
    from docich.agent.loop import _run_iteration
    agent = brain()
    planned = frame({"n": "#"})
    adapter = Mock()
    # Repeat rejected plans: they must never poison the direction budget.
    for _ in range(4):
        adapter.observe.side_effect = [observation(planned), observation(fresh)]
        assert _run_iteration(adapter, agent, 1500) == 0
        adapter.act.assert_not_called()
        assert agent.progress._pending is None
        assert agent.progress._answered_prompt is None
        assert agent.policy.explorer.blocked_steps == set()
    assert act(agent, planned) == ["n"]


@pytest.mark.parametrize("error_at", ["fresh", "act"])
def test_capture_or_transport_error_does_not_acknowledge_action(error_at):
    from unittest.mock import Mock
    from docich.agent.loop import _run_iteration
    agent = brain()
    text = frame({"h": "d"})
    adapter = Mock()
    for _ in range(4):
        adapter.observe.side_effect = [observation(text), RuntimeError("capture")] if error_at == "fresh" else [observation(text), observation(text)]
        if error_at == "act":
            adapter.act.side_effect = RuntimeError("transport")
        with pytest.raises(RuntimeError):
            _run_iteration(adapter, agent, 1500)
        assert agent.progress._pending is None
        assert agent.policy.explorer.blocked_steps == set()
    assert act(agent, text) == ["h"]


@pytest.mark.parametrize("text", [
    frame(message="A message --More--"),
    frame({"h": "f"}, message="Really attack the cat? [yn] (n)"),
    frame(message="Really save? [yn] (n)"),
])
def test_rejected_prompt_action_does_not_spend_answer_budget(text):
    from unittest.mock import Mock
    from docich.agent.loop import _run_iteration
    agent = brain()
    adapter = Mock()
    adapter.observe.side_effect = [observation(text), observation(frame({"n": "#"}))]
    assert _run_iteration(adapter, agent, 1500) == 0
    adapter.act.assert_not_called()
    assert agent.progress._answered_prompt is None
    assert act(agent, text, canonical=READY) in ([" "], ["n"])


def test_startup_answer_also_needs_fresh_frame_and_rejected_answer_is_retryable():
    from unittest.mock import Mock
    from docich.agent.loop import _run_iteration
    agent = NethackPolicyBrain(SimpleNamespace(), SimpleNamespace(
        name="nethack", adapter="cli", raw={"nethack": {"startup": {"enabled": True}}},
    ))
    text = "Shall I pick a character for you? [yn]"
    adapter = Mock()
    adapter.observe.side_effect = [observation(text), observation("Really quit? [yn]")]
    assert _run_iteration(adapter, agent, 1500) == 0
    adapter.act.assert_not_called()
    assert agent.startup.actions == 0
    assert act(agent, text) == ["y"]
    assert agent.startup.actions == 1


def test_fresh_observe_validation_and_send_share_fence_lock(monkeypatch):
    from docich.agent import loop
    from docich.agent.fence import AgentFence
    agent = brain()
    text = frame({"n": "#"})
    events = []
    locked = False
    def section(state_dir, operation):
        nonlocal locked
        assert not locked
        locked = True
        events.append("lock")
        try:
            return operation()
        finally:
            events.append("unlock")
            locked = False
    def check(*args):
        events.append("fence")
    def observe():
        assert locked
        events.append("observe")
        return observation(text)
    def send(action):
        assert locked
        assert agent.progress._pending is None
        events.append("send")
    original = agent.validate_action
    def validate(action, fresh, **kwargs):
        assert locked
        events.append("validate")
        return original(action, fresh, **kwargs)
    monkeypatch.setattr(agent, "validate_action", validate)
    monkeypatch.setattr(loop, "shared_section", section)
    monkeypatch.setattr(loop, "_check_loop_fence", check)
    monkeypatch.setattr(loop, "read_canonical", lambda _: {})
    monkeypatch.setattr(loop, "resolve_fence", lambda *_: "run")
    fence = AgentFence(game="nethack", runtime_id="r", generation=1, lease_id="l")
    assert loop._run_iteration(SimpleNamespace(observe=observe, act=send), agent, 1500, fence=fence, state_dir="unused") == 1
    assert events == ["lock", "fence", "observe", "unlock", "fence", "lock", "fence", "observe", "fence", "validate", "send", "unlock"]
    assert agent.progress._pending is not None


def test_fence_loss_after_fresh_capture_prevents_send_and_ack(monkeypatch):
    from unittest.mock import Mock
    from docich.agent import loop
    from docich.agent.fence import AgentFence, FenceLost
    agent = brain()
    adapter = Mock()
    adapter.observe.return_value = observation(frame({"n": "#"}))
    monkeypatch.setattr(loop, "shared_section", lambda _, operation: operation())
    monkeypatch.setattr(loop, "read_canonical", lambda _: {})
    monkeypatch.setattr(loop, "resolve_fence", lambda *_: "run")
    monkeypatch.setattr(loop, "_check_loop_fence", Mock(side_effect=[None, None, None, FenceLost("changed")]))
    fence = AgentFence(game="nethack", runtime_id="r", generation=1, lease_id="l")
    with pytest.raises(FenceLost):
        loop._run_iteration(adapter, agent, 1500, fence=fence, state_dir="unused")
    adapter.act.assert_not_called()
    assert agent.progress._pending is None
    assert agent._action_plan is None


def test_decide_without_transport_does_not_spend_retries_or_create_pending():
    agent = brain()
    text = frame({"h": "d"})
    for _ in range(6):
        assert [a.text for a in agent.decide(observation(text))] == ["h"]
        assert agent.progress._pending is None
        assert agent.policy.explorer.blocked_steps == set()


@pytest.mark.parametrize("canonical", [
    None, {}, {"phase": "draining", "active": {"game": "nethack"}},
    {**READY, "phase": "recovery_required"},
    {**READY, "phase": "quiescing"},
    {**READY, "operation": "switch", "request_id": "boundary-owner"},
    {**READY, "active": {"game": "robots"}},
])
def test_save_decline_requires_confirmed_idle_boundary_ownership(canonical):
    agent = brain()
    text = frame(message="Really save? [yn] (n)")
    assert act(agent, text, canonical=canonical) == []
    assert agent.progress._answered_prompt is None
    # Rejecting an unsent n cannot suppress a later permitted answer.
    assert act(agent, text, canonical=READY) == ["n"]


def test_draining_save_prompt_is_left_for_coordinator_inside_same_lock(monkeypatch):
    from docich.agent import loop
    from docich.agent.fence import AgentFence
    agent = brain()
    text = frame(message="Really save? [yn] (n)")
    locked = False
    reads = []
    def section(_, operation):
        nonlocal locked
        locked = True
        try:
            return operation()
        finally:
            locked = False
    def canonical(_):
        reads.append(locked)
        return {**READY, "phase": "draining", "operation": "stop", "request_id": "boundary-owner"} if locked else READY
    def send(action):
        pytest.fail("agent must not answer the coordinator's save prompt")
    monkeypatch.setattr(loop, "shared_section", section)
    monkeypatch.setattr(loop, "read_canonical", canonical)
    monkeypatch.setattr(loop, "resolve_fence", lambda *_: "run")
    monkeypatch.setattr(loop, "_check_loop_fence", lambda *_: None)
    fence = AgentFence(game="nethack", runtime_id="r", generation=1, lease_id="l")
    assert loop._run_iteration(SimpleNamespace(observe=lambda: observation(text), act=send), agent, 1500, fence=fence, state_dir="unused") == 0
    assert reads == [False, True]  # phase changed between planning and send
    assert agent.progress._answered_prompt is None
    assert agent.progress._pending is None


def test_unknown_and_severe_states_survive_candidate_public_replay():
    from docich.nethack_candidate_eval import parse_public_replay_request
    from docich.nethack_strategy import build_strategic_request
    for condition in ("Stone", "TermIll"):
        obs = normalize_tty(frame(message="Unknown question?", condition=condition))
        decision = NethackLayeredPolicy().decide(obs)
        request = build_strategic_request(obs, decision)
        _, replay, _ = parse_public_replay_request(request.to_dict())
        assert replay.prompt == "unknown"
        assert condition in replay.conditions
