from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from docich.adapters.base import Observation
from docich.agent.brains import build_brain
from docich.agent.loop import _run_iteration
from docich.nethack_narration import NethackNarrator
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import NethackLayeredPolicy, PolicyDecision
from docich.nethack_startup import NethackStartup


def frame(message="", hp="10(10)", level=2):
    return f"{message}\n.@....\n......\nDlvl:{level} HP:{hp} Pw:4(4) AC:5 Exp:2\nT:12\n"


def normalized(text):
    return normalize_tty(text, cols=80, rows=5)


def game(**sections):
    return SimpleNamespace(name="nethack", adapter="cli", raw={
        "cli": {"cols": 80, "rows": 5}, "nethack": sections,
    }, agent=SimpleNamespace(brain="nethack"))


def observation(text):
    return Observation(game="nethack", title="NetHack", adapter="cli", ts=1, kind="text", text=text)


@pytest.mark.parametrize("prompt,key", [
    ("Do you want a tutorial? [yn] (n)", "n"),
    ("Shall I pick a character for you? [ynaq] (y)", "y"),
    ("Shall I pick a character's race, role, gender and alignment for you? [ynq]", "y"),
    ("Shall I pick a character's role, race, gender, and alignment for you? [ynq] (y)", "y"),
    ("Shall I pick a character's role, race, gender and alignment for you? [ynq]", "y"),
    ("Shall I pick character's race, role, gender and alignment for you? [ynaq]", "y"),
    ("Pick a character? [yn]", "y"),
    ("Is this ok? [ynq]", "y"),
    ("Name: docich\nRole: Valkyrie\nIs this ok? [ynq] (y)", "y"),
])
def test_known_startup(prompt, key):
    startup = NethackStartup(enabled=True)
    assert [a.text for a in startup.consider(normalized(prompt))] == [key]
    assert startup.consider(normalized(prompt)) == []  # stale frame is not answered twice


@pytest.mark.parametrize("text", ["", "Who are you?", "Really attack? [yn]", "Restore save? [yn]", "Unknown --More--", "Is this ok? unrelated text", "Shall I pick your weapon? [yn]"])
def test_unknown_startup_holds(text):
    startup = NethackStartup(enabled=True)
    assert startup.consider(normalized(text)) == []
    assert startup.actions == 0


def test_restore_more_delegates_to_reviewed_policy():
    brain = build_brain(SimpleNamespace(), game(startup={"enabled": True}))
    actions = brain.decide(observation("Restoring save file...--More--"))
    assert [action.text for action in actions] == [" "]
    assert brain.startup.state == "gameplay"
    assert brain.last_decision.intent == "advance_message"


def test_tutorial_overlay_and_gameplay_completion():
    startup = NethackStartup(enabled=True)
    assert startup.consider(normalized(frame("Do you want a tutorial? [yn] (n)")))[0].text == "n"
    assert startup.consider(normalized(frame())) is None
    assert startup.consider(normalized(frame("Do you want a tutorial? [yn] (n)"))) is None


def test_narration_consider_failure_keeps_actions(monkeypatch):
    brain = build_brain(SimpleNamespace(), game())
    monkeypatch.setattr(brain.narrator, "consider", Mock(side_effect=RuntimeError("failure")))
    text = frame()
    assert brain.decide(observation(text)) == list(NethackLayeredPolicy().decide(normalized(text)).actions)


def test_startup_sequence_then_permanent_gameplay():
    startup = NethackStartup(enabled=True)
    assert startup.consider(normalized("Do you want a tutorial? [yn]"))[0].text == "n"
    assert startup.consider(normalized("Is this ok? [yn]"))[0].text == "y"
    assert startup.consider(normalized("Welcome to NetHack! --More--"))[0].text == " "
    assert startup.consider(normalized(frame())) is None
    assert startup.state == "gameplay"
    assert startup.consider(normalized("Is this ok? [yn]")) is None


def test_intro_more_with_status_lines_is_advanced():
    # The intro --More-- sits above the status lines, so it is not the last
    # line of the frame; the gate must still advance it instead of holding.
    startup = NethackStartup(enabled=True)
    assert startup.consider(
        normalized("Shall I pick character's race, role, gender and alignment for you? [ynaq]")
    )[0].text == "y"
    intro = (
        "Go bravely with Tyr!\n"
        "--More--\n"
        "[Docich the Stripling ] St:17 Dx:12\n"
        "Dlvl:1 $:0 HP:16(16) Pw:2(2) AC:6\n"
    )
    assert startup.consider(normalized(intro))[0].text == " "


@pytest.mark.parametrize("kind", ["time", "observations", "actions"])
def test_startup_bounds_latch_closed(kind):
    now = [0.0]
    startup = NethackStartup(enabled=True, clock=lambda: now[0])
    startup.consider(normalized("loading"))
    if kind == "time":
        now[0] = 60
    elif kind == "observations":
        for _ in range(39):
            startup.consider(normalized("loading"))
    else:
        for i in range(12):
            assert startup.consider(normalized(f"Character {i}\nIs this ok? [yn]"))
    assert startup.consider(normalized("Pick a character? [yn]")) == []
    assert startup.state == "exhausted"
    assert startup.consider(normalized(frame())) == []


def test_brain_startup_and_loop_use_normal_action_path():
    brain = build_brain(SimpleNamespace(), game(startup={"enabled": True}))
    adapter = Mock()
    adapter.observe.return_value = observation("Is this ok? [yn]")
    assert _run_iteration(adapter, brain, 1500) == 1
    assert adapter.act.call_args.args[0].text == "y"
    adapter.observe.return_value = observation("Unknown question? [yn]")
    adapter.act.reset_mock()
    assert _run_iteration(adapter, brain, 1500) == 0
    adapter.act.assert_not_called()
    adapter.observe.return_value = observation(frame())
    assert _run_iteration(adapter, brain, 1500) == 1
    assert brain.last_decision.intent == "explore_step"
    adapter.observe.return_value = observation(frame("Is this ok? [yn]"))
    assert _run_iteration(adapter, brain, 1500) == 0


class InlineThread:
    def __init__(self, *, target, args, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def audio(monkeypatch):
    mock = Mock()
    monkeypatch.setattr("docich.nethack_narration.enqueue_audio_text", mock)
    monkeypatch.setattr("docich.nethack_narration.threading.Thread", InlineThread)
    return mock


@pytest.mark.parametrize("intent,expected", [
    ("exploration_blocked", "安全に進める道が見えないので、探索を保留します。"),
    ("hold_low_hp", "体力が半分以下なので、無理に動かず探索を保留します。"),
    ("explore_step", "未探索部分に近い安全な地形を選び、一歩ずつ探索します。"),
])
def test_narration_text_and_api(audio, intent, expected):
    g = SimpleNamespace()
    narrator = NethackNarrator(g, game(narration={"enabled": True}))
    narrator._last_level = 2
    narrator.consider(normalized(frame()), PolicyDecision("midlevel", intent, "visible frontier with lowest visit count"))
    audio.assert_called_once_with(g, expected, context="nethack:policy", speaker="")


def test_narration_cooldown_dedup_and_depth(audio):
    now = [0.0]
    narrator = NethackNarrator(SimpleNamespace(), game(narration={"enabled": True}), clock=lambda: now[0])
    policy = NethackLayeredPolicy()
    obs = normalized(frame())
    narrator.consider(obs, policy.decide(obs))
    assert "地下2階" in audio.call_args.args[1]
    now[0] = 19
    narrator.consider(obs, policy.decide(obs))
    assert audio.call_count == 1
    now[0] = 20
    narrator.consider(obs, policy.decide(obs))
    assert audio.call_count == 2
    now[0] = 100
    narrator.consider(obs, policy.decide(obs))
    assert audio.call_count == 2  # same intent even with different target/reason
    obs = normalized(frame(level=3))
    narrator.consider(obs, policy.decide(obs))
    assert "地下3階" in audio.call_args.args[1]
    now[0] = 120
    obs = normalized(frame("You die. --More--", hp="0(10)", level=3))
    narrator.consider(obs, policy.decide(obs))
    assert "死亡の表示" in audio.call_args.args[1]


def test_delivery_failure_does_not_change_policy_actions(audio, capsys):
    audio.side_effect = RuntimeError("private payload must not be logged")
    brain = build_brain(SimpleNamespace(), game(narration={"enabled": True}))
    obs = observation(frame())
    actions = brain.decide(obs)
    expected = NethackLayeredPolicy().decide(normalized(obs.text))
    assert actions == list(expected.actions)
    assert brain.last_decision == expected
    assert brain.narrator.status == "delivery_failed"
    assert "private payload" not in capsys.readouterr().err


def test_slow_enqueue_is_nonblocking_and_single_inflight(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr("docich.nethack_narration.enqueue_audio_text", slow)
    now = [0.0]
    narrator = NethackNarrator(SimpleNamespace(), game(narration={"enabled": True}), clock=lambda: now[0])
    obs = normalized(frame())
    try:
        narrator.consider(obs, NethackLayeredPolicy().decide(obs))
        assert entered.wait(2)
        now[0] = 100
        narrator.consider(obs, PolicyDecision("midlevel", "hold_low_hp", "low HP"))
        assert narrator._last_key == "level:2"
    finally:
        release.set()


def test_default_disabled_and_explicit_opt_out(audio):
    brain = build_brain(SimpleNamespace(), game())
    assert brain.startup.state == "disabled"
    brain.decide(observation(frame()))
    audio.assert_not_called()


@pytest.mark.parametrize("raw", [{"enabled": "true"}, {"cooldown_s": 0}, {"cooldown_s": float("nan")}, {"speaker": "bad\nvoice"}])
def test_invalid_narration_config(raw):
    with pytest.raises(ValueError):
        NethackNarrator(SimpleNamespace(), game(narration=raw))


def test_checked_in_config_enables_only_reviewed_runtime():
    import tomllib
    from pathlib import Path
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "config/games/nethack.toml").read_text())
    assert cfg["agent"]["enabled"] is True
    assert cfg["agent"]["brain"] == "nethack"
    assert cfg["nethack"]["startup"]["enabled"] is True
    assert cfg["nethack"]["narration"]["enabled"] is True
    for key in ("strategist", "shadow", "shadow_source", "candidate_shadow"):
        assert cfg["nethack"][key]["enabled"] is False
