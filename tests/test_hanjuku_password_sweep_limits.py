from types import SimpleNamespace

from docich.hanjuku.flows import password


def test_sweep_counts_actual_selections(monkeypatch):
    monkeypatch.setattr(password, "pad", lambda key: key)
    monkeypatch.setattr(password, "_record", lambda *a, **kw: None)
    screen = SimpleNamespace(kind="password_entry")
    mem = {"password_entry": {"tops": password._TOP_PRESSES}}
    actions = []
    for _ in range(7):
        actions.extend(password.password_step(screen, mem))
    assert actions.count("a") == password._SWEEP_PICKS == 14
    assert mem["password_entry"]["picks"] == 14
    assert password.password_step(screen, mem) == ["up"] * 4


def test_exhausted_sweep_stays_held(monkeypatch):
    monkeypatch.setattr(password, "pad", lambda key: key)
    events = []
    monkeypatch.setattr(password, "_record", lambda _, event, **kw: events.append(event))
    screen = SimpleNamespace(kind="password_entry")
    mem = {"password_entry": {
        "tops": password._TOP_PRESSES,
        "picks": password._SWEEP_PICKS,
        "cycles": password._MAX_CYCLES - 1,
    }}
    for _ in range(10):
        assert password.password_step(screen, mem) == []
    assert mem["password_entry"]["cycles"] == password._MAX_CYCLES
    assert events.count("situation_held") == 1
