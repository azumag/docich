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


def test_exhausted_sweep_resets_bounded_then_holds(monkeypatch):
    # docich#4993: the old behaviour held forever once the cycles ran out and the
    # local driver froze on that screen. Now the panel is closed (B) for a
    # bounded number of fresh sweeps, and only then does the flow hold.
    monkeypatch.setattr(password, "pad", lambda key: key)
    events = []
    monkeypatch.setattr(password, "_record", lambda _, event, **kw: events.append(event))
    screen = SimpleNamespace(kind="password_entry")
    mem = {"password_entry": {
        "tops": password._TOP_PRESSES,
        "picks": password._SWEEP_PICKS,
        "cycles": password._MAX_CYCLES - 1,
    }}
    # Cycle boundary: the reset B is sent at once, not after a held frame.
    assert password.password_step(screen, mem) == ["b"]
    assert mem["password_entry"]["resets"] == 1
    assert mem["password_entry"]["cycles"] == 0
    assert events.count("password_entry_reset") == 1
    # Each reset buys _MAX_CYCLES fresh sweeps (about 13 observations each).
    # 300 observations cover the second reset and the final hold.
    actions = []
    for _ in range(300):
        actions.append(password.password_step(screen, mem))
    assert mem["password_entry"]["resets"] == password._MAX_RESETS
    # The first B was asserted above; exactly one more reset is allowed.
    assert actions.count(["b"]) == password._MAX_RESETS - 1   # never a B storm
    assert actions[-1] == []
    assert events.count("situation_held") == 1

