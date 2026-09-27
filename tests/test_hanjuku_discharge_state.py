from types import SimpleNamespace

from docich import hanjuku_policy as policy


def test_discharge_step_initializes_press_counter_without_month_header():
    screen = SimpleNamespace(
        text="どのしょうぐんをかいこに",
        header={},
        kind="discharge_menu",
        selected="ミント",
        hand=(),
    )
    mem = {}

    actions = policy.discharge_step(screen, mem)

    assert actions == [{"type": "pad", "buttons": ["a"], "hold_ms": 100}]
    assert mem["discharge"] == {"key": None, "presses": 1, "exits": 0}
    assert mem["_records"][-1]["observed_metric"]["month"] is None
