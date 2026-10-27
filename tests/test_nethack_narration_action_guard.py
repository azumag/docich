from types import SimpleNamespace
from unittest.mock import Mock

from docich.adapters.base import Observation
from docich.agent.brains import build_brain


def _game(*, narration_enabled: bool):
    return SimpleNamespace(
        name="nethack",
        adapter="cli",
        raw={
            "cli": {"cols": 80, "rows": 5},
            "nethack": {"narration": {"enabled": narration_enabled}},
        },
        agent=SimpleNamespace(brain="nethack"),
    )


def _observation():
    text = (
        'Something is written here in the dust. You read: "?d"\n'
        ".@....\n"
        "......\n"
        "Dlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\n"
        "T:12\n"
    )
    return Observation(
        game="nethack",
        title="NetHack",
        adapter="cli",
        ts=1,
        kind="text",
        text=text,
    )


class _InlineThread:
    def __init__(self, *, target, args, **kwargs):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


def test_message_narration_does_not_change_brain_actions(monkeypatch):
    """#2798: compare the same brain path with narration on and off.

    A direct NethackLayeredPolicy comparison is invalid after blocking-prompt
    handling moved into the brain. The contract we need is narrower: enabling
    narration must not add, remove, or replace the brain's reviewed action.
    """
    audio = Mock()
    monkeypatch.setattr("docich.nethack_narration.enqueue_audio_text", audio)
    monkeypatch.setattr("docich.nethack_narration.threading.Thread", _InlineThread)

    narrated = build_brain(SimpleNamespace(), _game(narration_enabled=True))
    silent = build_brain(SimpleNamespace(), _game(narration_enabled=False))
    obs = _observation()

    narrated_actions = narrated.decide(obs)
    silent_actions = silent.decide(obs)

    assert narrated_actions == silent_actions
    assert narrated.last_decision == silent.last_decision
    assert len(narrated_actions) == 1
    assert narrated_actions[0].key == "Escape"
    assert audio.call_count == 1
