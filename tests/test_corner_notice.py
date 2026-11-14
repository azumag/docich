"""Offline contract tests for the next-corner advance notice.

All execution, clocks and speech delivery are injected: no rotation side
effects and no shared audio queue.
"""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.config import load_global
from docich.corner_catalog import Corner
from docich.corner_rotation import CornerRotationManager
from docich import corner_notice


class Adapter:
    def __init__(self, g, corner):
        self.corner = corner
        self.available = True
        self.states = []

    def eligible(self):
        return self.available

    def observations(self):
        return self.states


class Executor:
    def __init__(self):
        self.calls = []
        self.result = "completed"

    def execute(self, adapter, request):
        self.calls.append(dict(request))
        return self.result


CATALOG = [
    Corner("retro", "game", "nsnake"),
    Corner("paper", "paper", "paper-view"),
    Corner("meriken", "meriken", "soren91"),
]


def _manager(tmp_path, *, mode="interval", cooldown=1, **kwargs):
    path = tmp_path / "config.toml"
    path.write_text(
        "[corner_rotation]\n"
        "enabled=true\n"
        f'schedule_mode="{mode}"\n'
        "interval_minutes=60\n"
        f"cooldown_hours={cooldown}\n"
        "[paths]\n"
        'state_dir="run"\n',
        encoding="utf-8",
    )
    g = load_global(tmp_path, path)
    clock = [1_000_000.0]
    executor = Executor()
    spoken = []

    def make(**extra):
        options = dict(
            clock=lambda: clock[0], seed="test-seed", catalog=CATALOG,
            adapter_factory=Adapter, executor=executor,
            speech=spoken.append,
        )
        options.update(kwargs)
        options.update(extra)
        return CornerRotationManager(g, **options)

    return g, clock, executor, spoken, make


def test_notice_is_published_after_a_slot_and_spoken_once(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    manager = make()
    manager.tick()
    assert executor.calls[0]["corner"] == "meriken"
    # The promise belongs to sorengame only: it is withdrawn at dispatch.
    assert not corner_notice.notice_path(g).exists()

    manager.tick()
    record = corner_notice.read(g)
    ledger = json.loads(manager.path.read_text())
    assert record is not None
    assert record["corner"] == "paper"          # seeded ranking at slot 1
    assert record["due_at"] == int(ledger["next_due_at"])
    assert record["when"] == "23:46"            # 1_003_600 epoch in JST
    assert record["source"] == "auto"
    assert record["hold"] is None
    assert spoken == ["次のコーナーはペーパートレード、23時46分からの予定です。"]

    # One line per ended slot, not one per tick.
    manager.tick()
    assert len(spoken) == 1

    # The corner that was promised is the one that actually starts.
    clock[0] += 3600
    manager.tick()
    assert executor.calls[-1]["corner"] == record["corner"]


def test_dispatch_honours_the_announced_corner(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    manager = make()
    fresh = manager.load(clock[0])
    # Premise: the seeded ranking alone would start meriken, not paper.
    assert manager._pick(fresh, ["meriken", "paper", "retro"]) == "meriken"

    corner_notice.publish(g, corner="paper", due_at=clock[0] + 600,
                          source="auto", now=clock[0])
    manager.tick()
    assert executor.calls[-1]["corner"] == "paper"


def test_prediction_hold_keeps_the_notice_but_suppresses_the_speech(tmp_path):
    g, clock, executor, spoken, make = _manager(
        tmp_path,
        prediction_state=lambda: {"in_flight": True, "worker_running": True,
                                 "remaining_games": 7, "started_at": clock[0]},
    )
    manager = make()
    manager.tick()
    manager.tick()
    record = corner_notice.read(g)
    # The card stays visible (marked provisional by the dashboard) and carries
    # the prediction's own remaining games; only the spoken promise waits.
    assert record is not None and record["hold"] == "prediction"
    assert record["remaining_games"] == 7
    assert record["corner"]
    assert spoken == []


def test_prediction_hold_never_dispatches_the_due_corner(tmp_path):
    g, clock, executor, spoken, make = _manager(
        tmp_path,
        prediction_state=lambda: {"in_flight": True, "worker_running": True,
                                 "remaining_games": 48, "started_at": clock[0]},
    )
    manager = make()
    manager.tick()
    assert executor.calls == []
    state = json.loads(manager.path.read_text())
    assert state["status"] == "waiting"
    assert state["reason"] == "prediction-hold"
    assert corner_notice.read(g)["hold"] == "prediction"


def test_prediction_resolution_releases_the_due_corner(tmp_path):
    prediction = {"in_flight": True, "worker_running": True,
                  "remaining_games": 1, "started_at": 1_000_000.0}
    g, clock, executor, spoken, make = _manager(
        tmp_path, prediction_state=lambda: dict(prediction)
    )
    manager = make()
    manager.tick()
    assert executor.calls == []
    prediction.update(in_flight=False)
    clock[0] += 1
    manager.tick()
    assert executor.calls and executor.calls[-1]["corner"] == "meriken"


def test_prediction_hold_timeout_falls_back_to_the_normal_boundary(tmp_path):
    prediction = {"in_flight": True, "worker_running": True,
                  "remaining_games": 1, "started_at": 1_000_000.0}
    g, clock, executor, spoken, make = _manager(
        tmp_path, prediction_state=lambda: dict(prediction)
    )
    manager = make()
    manager.tick()
    assert executor.calls == []
    # Default 90 minutes: after the bound the normal dispatch/boundary path
    # runs instead of holding forever.
    clock[0] += 91 * 60
    manager.tick()
    state = json.loads(manager.path.read_text())
    assert state.get("prediction_hold_timeout_at") is not None
    assert executor.calls


def test_post_corner_handoff_announces_pending_and_holds_dispatch(tmp_path):
    g, clock, executor, spoken, make = _manager(
        tmp_path,
        prediction_state=lambda: {"in_flight": False, "worker_running": True},
    )
    manager = make()
    manager.tick()                       # run a slot
    assert executor.calls
    manager.tick()                       # next tick: prediction handoff
    record = corner_notice.read(g)
    assert record is not None and record["hold"] == "prediction-pending"
    assert record["source"] == "auto"
    assert spoken == []
    calls = len(executor.calls)
    manager.tick()                       # still inside the handoff window
    assert len(executor.calls) == calls
    # The next slot is an hour away, so the handoff is not dispatching it; it
    # only gives the prediction time to start before a new clock is promised.
    state = json.loads(manager.path.read_text())
    assert state["reason"] == "not-due"
    # The window expires and a truthful clock is published again.
    clock[0] += 181
    manager.tick()
    assert corner_notice.read(g)["hold"] is None


def test_scheduled_manual_corner_is_announced_when_it_comes_first(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    manager = make()
    manager.tick()                                    # next auto slot: +1h
    manager.queue_manual("nsnake", trigger_at=clock[0] + 1800)
    manager.tick()
    record = corner_notice.read(g)
    assert record is not None
    assert (record["corner"], record["source"]) == ("retro", "manual")
    assert record["due_at"] == int(clock[0] + 1800)


def test_notice_follows_the_first_cooldown_expiry(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path, cooldown=3)
    manager = make()
    manager.tick()
    now = clock[0]
    state = json.loads(manager.path.read_text())
    # Every corner is still inside the 3h rolling cooldown, so the nominal
    # interval slot has no candidate and the dispatcher re-anchors to the
    # first expiry (retro, two hours after its use).
    state["history"] = [
        {"corner": "retro", "at": now - 7200, "source": "completion"},
        {"corner": "meriken", "at": now - 3600, "source": "completion"},
        {"corner": "paper", "at": now - 600, "source": "completion"},
    ]
    state["last_slot_at"] = now - 3600
    state["next_due_at"] = now
    manager.path.write_text(json.dumps(state), encoding="utf-8")

    manager.tick()
    record = corner_notice.read(g)
    assert record is not None
    assert (record["corner"], record["due_at"]) == ("retro", int(now + 3600))

    clock[0] = now + 3600
    manager.tick()
    assert executor.calls[-1]["corner"] == "retro"


def test_notice_is_withdrawn_while_another_game_is_on_air(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    manager = make()
    manager.tick()
    switch = Path(g.state_dir) / "game_switch.json"
    switch.write_text(json.dumps({"phase": "ready", "active": {"game": "hanjuku-hero"}}),
                      encoding="utf-8")
    manager.tick()
    assert corner_notice.read(g) is None

    switch.write_text(json.dumps({"phase": "ready", "active": {"game": "sorengame"}}),
                      encoding="utf-8")
    manager.tick()
    assert corner_notice.read(g) is not None


def test_queue_mode_announces_nothing_without_a_manual_time(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path, mode="queue")
    manager = make()
    manager.tick()
    manager.tick()
    assert corner_notice.read(g) is None
    assert spoken == []


def test_no_notice_when_no_corner_is_eligible(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    manager = make()
    manager.tick()
    manager.tick()
    assert corner_notice.read(g) is not None
    for adapter in manager.adapters.values():
        adapter.available = False
    manager.tick()
    assert corner_notice.read(g) is None


def test_publish_does_not_rewrite_inside_the_refresh_window(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    clock[0] = 1_000_000.0
    first = corner_notice.publish(g, corner="paper", due_at=clock[0] + 600,
                                  source="auto", now=clock[0])
    path = corner_notice.notice_path(g)
    before = path.read_text(encoding="utf-8")
    clock[0] += 1
    again = corner_notice.publish(g, corner="paper", due_at=1_000_600,
                                  source="auto", now=clock[0])
    assert again["decided_at"] == first["decided_at"]
    assert path.read_text(encoding="utf-8") == before
    clock[0] += 120
    refreshed = corner_notice.publish(g, corner="paper", due_at=1_000_600,
                                      source="auto", now=clock[0])
    assert refreshed["updated_at"] == int(clock[0])


def test_invalid_records_are_ignored(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path)
    path = corner_notice.notice_path(g)
    path.parent.mkdir(parents=True, exist_ok=True)
    for payload in (
        {},
        {"schema": 2},
        {"schema": 1, "corner": "paper"},
        {"schema": 1, "corner": "", "label": "x", "when": "18:00",
         "due_at": 1, "source": "auto", "hold": None,
         "decided_at": 1, "updated_at": 1},
        {"schema": 1, "corner": "paper", "label": "x", "when": "18:00",
         "due_at": "soon", "source": "auto", "hold": None,
         "decided_at": 1, "updated_at": 1},
        {"schema": 1, "corner": "paper", "label": "x", "when": "18:00",
         "due_at": 1, "source": "guess", "hold": None,
         "decided_at": 1, "updated_at": 1},
        {"schema": 1, "corner": "paper", "label": "x", "when": "18:00",
         "due_at": 1, "source": "auto", "hold": "weather",
         "decided_at": 1, "updated_at": 1},
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert corner_notice.read(g) is None


def test_labels_and_spoken_time_phrases(tmp_path):
    assert corner_notice.corner_label("ninvaders") == "スペースインベーダー"
    assert corner_notice.corner_label("unknown-corner") == "unknown-corner"
    assert corner_notice.corner_label("communist-law-simulator") == "共産主義法律シミュレーター"

    now = 1_000_000.0
    assert corner_notice.format_when(now + 3600, now) == "23:46"
    assert corner_notice.format_when(now + 86400, now) == "明日 22:46"
    assert corner_notice.format_when(now + 5 * 86400, now).startswith("1/17 ")

    record = {"corner": "paper", "label": "ペーパートレード", "due_at": int(now + 3600)}
    assert corner_notice.speech_text(record, now) == (
        "次のコーナーはペーパートレード、23時46分からの予定です。"
    )
    daily = {"corner": "paper", "label": "ペーパートレード", "due_at": int(now + 86400)}
    assert corner_notice.speech_text(daily, now).endswith("明日22時46分からの予定です。")


def test_prediction_hold_publishes_a_past_due_notice(tmp_path):
    g, clock, executor, spoken, make = _manager(tmp_path, prediction_guard=lambda: True)
    manager = make()
    state = manager.load(clock[0])
    state["last_slot_at"] = clock[0] - 3600
    state["next_due_at"] = clock[0]
    record = manager._sync_notice(state, clock[0], {"retro", "paper", "meriken"})
    assert record is not None
    assert record["hold"] == "prediction"
    # The planned clock is exactly now: a normal notice waits (too close to
    # promise), but a prediction hold must still show what comes next.
    assert record["due_at"] == int(clock[0])
    assert spoken == []


def test_notice_uses_the_real_prediction_round_remaining(tmp_path, monkeypatch):
    import os

    from docich.trading import soren_output

    root = tmp_path / "soren"
    state = root / "tmp" / "state"
    state.mkdir(parents=True)
    (state / "prediction_worker.pid").write_text(str(os.getpid()), encoding="utf-8")
    (state / "current_prediction.json").write_text(json.dumps({
        "prediction_id": "id", "outcome_ids": ["a", "b", "c", "d"],
        "round_version": 2, "max_games": 48, "games_completed": 5,
        "created_at": 1_000_000,
    }), encoding="utf-8")
    monkeypatch.setattr(soren_output, "resolve_soren_root", lambda _g: root)

    g, clock, executor, spoken, make = _manager(tmp_path)
    manager = make()
    manager.tick()
    record = corner_notice.read(g)
    assert record is not None
    assert record["hold"] == "prediction"
    assert record["remaining_games"] == 43
    assert executor.calls == []
    assert spoken == []
