from types import SimpleNamespace

from docich.hanjuku.flows import password


def test_top_then_the_measured_order_opens_the_door(monkeypatch):
    monkeypatch.setattr(password, "pad", lambda key, frames=6: key)
    monkeypatch.setattr(password, "_record", lambda *a, **kw: None)
    screen = SimpleNamespace(kind="password_entry")
    mem = {}
    for _ in range(password._TOP_PRESSES):
        assert password.password_step(screen, mem) == ["up"]
    a_presses = 0
    seen = []
    while a_presses < len(password._TARGET_ORDER):
        acts = password.password_step(screen, mem)
        assert acts, "sweep must not hold before all target words"
        seen.append(acts[0])
        if acts[0] == "a":
            a_presses += 1
    assert a_presses == len(password._TARGET_ORDER) == 10
    assert mem["password_entry"]["step"] == len(password._TARGET_ORDER)
    # 15 ups, then the live-list movement for [1,0,5,3,2,4,9,8,6,7].
    assert seen.count("a") == 10
    assert seen.count("down") + seen.count("up") == 21


def test_cycle_cap_holds_instead_of_spamming(monkeypatch):
    monkeypatch.setattr(password, "pad", lambda key, frames=6: key)
    screen = SimpleNamespace(kind="password_entry")
    mem = {"password_entry": {"cycles": password._MAX_CYCLES}}
    for _ in range(5):
        assert password.password_step(screen, mem) == []


def test_a_failed_cycle_re_tops_and_records(monkeypatch):
    monkeypatch.setattr(password, "pad", lambda key, frames=6: key)
    events = []
    monkeypatch.setattr(password, "_record", lambda _, event, **kw: events.append(event))
    screen = SimpleNamespace(kind="password_entry")
    mem = {"password_entry": {
        "tops": password._TOP_PRESSES,
        "idx": 7,
        "step": len(password._TARGET_ORDER),
        "advance": password._ADVANCE_PRESSES,
    }}
    assert password.password_step(screen, mem) == ["up"]
    assert mem["password_entry"]["cycles"] == 1
    assert events == ["password_entry_resweep"]
