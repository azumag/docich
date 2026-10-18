"""Offline lifecycle checks for the participatory AI探偵ミステリー corner (#684).

The manager runs one deterministic case per slot with injected fake Twitch
clients: the truth stays engine-owned, the reveal always lands, and a
prediction is always settled or refunded.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_global
from docich.corner_adapters import CornerExecutionError, MysteryCornerAdapter
from docich.corner_catalog import Corner, CornerCatalogError, load_catalog
from docich.corner_rotation import STOP_REQUEST_DIR
from docich.game_switch import GameSwitchCoordinator, GameSwitchStore, ReadinessTimeoutError, StepTimeouts
from docich import mystery_case
from docich import mystery_narration
from docich.mystery_corner import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    MysteryCornerError,
    MysteryCornerManager,
)
from docich.mystery_view import PAYLOAD_FILENAME, VIEW_NAME, read_payload


class FakeRuntimeAdapter:
    agent_enabled = False

    def __init__(self, spec, *, boundary=False):
        self.spec = spec
        self.name = "program" if spec.game == VIEW_NAME else "cli"
        self.requires_round_boundary = boundary
        self.round_boundary_timeout_s = None
        self.live = False
        self.events = []
        self.boundary_entered = threading.Event()
        self.boundary_release = threading.Event()
        if not boundary:
            self.request_round_boundary = None

    def preflight(self, deadline, cancel):
        self.events.append("preflight")

    def materialize_runtime(self, deadline, cancel):
        self.live = True
        self.events.append("materialize")

    def readiness(self, deadline, cancel):
        self.events.append("readiness")

    def alive(self, deadline, cancel):
        return self.live

    def cleanup_runtime(self, deadline, cancel):
        self.events.append("cleanup")
        self.live = False

    def start_agent(self, deadline, cancel):
        return None

    def stop_agent(self, deadline, cancel):
        self.events.append("stop_agent")

    def request_round_boundary(self, request_id, deadline, cancel):
        self.events.append("boundary_wait")
        self.boundary_entered.set()
        while not self.boundary_release.wait(0.01):
            if cancel.is_set() or time.monotonic() >= deadline:
                raise ReadinessTimeoutError("synthetic boundary timeout")
        self.events.append("boundary_ack")

    def cancel_round_boundary(self, request_id, deadline, cancel):
        self.boundary_release.set()
        return True


class FakeFactory:
    def __init__(self):
        self.adapters = {}

    def __call__(self, spec):
        if spec.runtime_id not in self.adapters:
            self.adapters[spec.runtime_id] = FakeRuntimeAdapter(spec)
        return self.adapters[spec.runtime_id]


class FakePollClient:
    """A poll row that is already terminal at create time."""

    def __init__(self, votes=None, status="COMPLETED"):
        self.votes = votes
        self.status = status
        self.created = []
        self.row = None

    def create(self, title, choices, duration):
        self.created.append((title, tuple(choices), duration))
        self.row = {
            "id": f"poll{len(self.created)}", "status": self.status, "title": title,
            "choices": [{"id": f"c{i}", "title": label,
                         "votes": (self.votes[i] if self.votes and i < len(self.votes) else 0)}
                        for i, label in enumerate(choices)],
        }
        return [self.row]

    def list(self, poll_id=None):
        row = self.row
        if poll_id and row and row.get("id") == poll_id:
            return [row]
        return [row] if row else []


class FakePredictionClient:
    """Create → ACTIVE; end("LOCKED") simulates the crowd's bets landing."""

    def __init__(self, *, bets=(("o0", 100), ("o1", 500)), lock_bets=True):
        self.bets = dict(bets)
        self.lock_bets = lock_bets
        self.rows = {}
        self.calls = []

    def create(self, title, outcomes, window):
        self.calls.append(("create", title, tuple(outcomes), window))
        row = {"id": "pred1", "status": "ACTIVE", "title": title,
               "outcomes": [{"id": f"o{i}", "title": label, "users": 0, "channel_points": 0}
                            for i, label in enumerate(outcomes)]}
        self.rows["pred1"] = row
        return [row]

    def list(self, prediction_id=None):
        row = self.rows.get(prediction_id or "pred1")
        return [row] if row else []

    def end(self, prediction_id, status, outcome_id=None):
        self.calls.append(("end", prediction_id, status, outcome_id))
        row = self.rows[prediction_id]
        if status == "LOCKED":
            row["status"] = "LOCKED"
            if self.lock_bets:
                for outcome in row["outcomes"]:
                    points = self.bets.get(outcome["id"], 0)
                    outcome["channel_points"] = points
                    outcome["users"] = 1 if points else 0
        else:
            row["status"] = status
            # Real Twitch keeps outcomes[].channel_points as the amounts bet;
            # the resolution only records the winning outcome id.
            if status == "RESOLVED":
                row["winning_outcome_id"] = outcome_id
        return [row]


def _setup(tmp_path, *, clock=None, duration=1):
    state = tmp_path / "run"
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir="{state}"\n'
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n'
        f'corners=[{{id="mystery",adapter="mystery",game="mystery-view",'
        f'enabled=true,duration_minutes={duration},audio_enabled=true}}]\n'
    )
    g = load_global(tmp_path, config)
    now = [time.time() if clock is None else clock]
    factory = FakeFactory()
    store = GameSwitchStore(g.state_dir)
    switch = GameSwitchCoordinator(
        store,
        factory,
        default_timeout_s=4.0,
        quiesce_verify_timeout_s=0.3,
        poll_interval_s=0.01,
        round_reacquire_timeout_s=0.3,
        step_timeouts=StepTimeouts(
            preflight_s=1.0,
            round_boundary_s=3.0,
            stop_agent_s=1.0,
            start_s=1.0,
            agent_start_s=1.0,
            cleanup_s=1.0,
            probe_s=0.3,
        ),
    )
    assert switch.start("robots").status == "succeeded"
    return g, now, factory, store, switch


def _corner(duration=1, **overrides):
    return Corner("mystery", "mystery", VIEW_NAME, duration_minutes=duration, **overrides)


def _adapter(g, corner, switch, now, sleep, *, poll_factory=None, prediction_factory=None):
    adapter = MysteryCornerAdapter(g, corner)
    manager = adapter.manager
    manager.coordinator = switch
    manager.clock = lambda: now[0]
    manager.sleep = sleep
    manager.poll_s = 1.0
    manager.poll_wait_poll_s = 1.0
    if poll_factory is not None:
        manager.poll_client_factory = lambda _g: (poll_factory, None)
    else:
        manager.poll_client_factory = lambda _g: (None, "disabled")
    if prediction_factory is not None:
        manager.prediction_client_factory = lambda _g: (prediction_factory, None)
    else:
        manager.prediction_client_factory = lambda _g: (None, "disabled")
    return adapter


@pytest.fixture()
def quiet_narration(monkeypatch):
    calls = []

    def fake_publish(g, *, kind, title, overlay_body, speech_text, request_id,
                     audio_enabled, **_kwargs):
        calls.append({"kind": kind, "speech": speech_text, "overlay": overlay_body})
        return {"overlay": {"sent": 1, "failed": 0, "skipped": 0},
                "speech": {"sent": 0, "failed": 0, "skipped": 1}}

    monkeypatch.setattr(mystery_narration, "publish", fake_publish)
    return calls


def test_production_catalog_enables_mystery_for_the_automatic_rotation():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    catalog = load_catalog(g)
    row = next(c for c in catalog if c.id == "mystery")
    assert (row.adapter, row.game) == ("mystery", VIEW_NAME)
    # 2026-10-18 オーナー承認 (#684): 30分枠・読み上げあり・自動抽選対象。
    assert row.enabled is True and row.manual_only is False
    assert row.live_eligible is False and row.paused is False
    assert row.duration_minutes == 30 and row.audio_enabled is True
    assert row.fetch_on_start is False and row.target_matches is None


def test_mystery_catalog_row_requires_the_reviewed_identity_and_bounds(tmp_path):
    config = tmp_path / "config.toml"

    def load(row):
        config.write_text(f"[corner_rotation]\ncorners=[{row}]\n")
        return load_catalog(load_global(tmp_path, config))

    row = load('{id="mystery",adapter="mystery",game="mystery-view",enabled=false}')
    assert len(row) == 1 and row[0].duration_minutes is None
    assert row[0].audio_enabled is False
    assert load('{id="mystery",adapter="mystery",game="mystery-view",duration_minutes=30}'
                )[0].duration_minutes == 30
    assert load('{id="mystery",adapter="mystery",game="mystery-view",audio_enabled=true}'
                )[0].audio_enabled is True

    for bad in (
        '{id="mystery-card",adapter="mystery",game="mystery-view"}',
        '{id="mystery",adapter="mystery",game="mystery"}',
    ):
        with pytest.raises(CornerCatalogError, match="mystery"):
            load(bad)
    with pytest.raises(CornerCatalogError, match="fetch_on_start"):
        load('{id="mystery",adapter="mystery",game="mystery-view",fetch_on_start=true}')
    with pytest.raises(CornerCatalogError, match="audio_enabled"):
        load('{id="mystery",adapter="mystery",game="mystery-view",audio_enabled="yes"}')
    for duration in ("0", "46", "true", "1.5"):
        with pytest.raises(CornerCatalogError, match="1-45"):
            load(f'{{id="mystery",adapter="mystery",game="mystery-view",duration_minutes={duration}}}')


def test_mystery_adapter_rejects_an_unreviewed_identity_and_uses_the_catalog_duration(tmp_path):
    g, _now, _factory, _store, switch = _setup(tmp_path, duration=5)

    adapter = MysteryCornerAdapter(g, _corner(duration=5))
    assert adapter.manager.duration_minutes == 5
    assert adapter.manager.eligible() is True
    assert adapter.manager.resources_released() is True
    assert adapter.state_path.name == "mystery_corner.json"

    default = MysteryCornerAdapter(g, _corner(duration=None))
    assert default.manager.duration_minutes == DEFAULT_DURATION_MINUTES

    with pytest.raises(CornerExecutionError, match="identity"):
        MysteryCornerAdapter(g, Corner("mystery", "mystery", "other-view"))
    with pytest.raises(CornerExecutionError, match="identity"):
        MysteryCornerAdapter(g, _corner(live_eligible=True))
    with pytest.raises(MysteryCornerError, match="duration"):
        MysteryCornerManager(g, duration_minutes=MAX_DURATION_MINUTES + 1, coordinator=switch)


def test_mystery_corner_runs_a_solo_case_then_always_reveals_and_restores(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())

    assert adapter.manager.run_rotation(request_id) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "case-completed"
    assert state["previous_runtime_identity"]["game"] == "robots"
    assert state["mystery_runtime_identity"]["game"] == VIEW_NAME
    assert state["ends_at"] - state["started_at"] == 60
    assert state["restored_runtime_identity"]["game"] == "robots"
    canonical, _ = store.canonical.load()
    assert canonical["phase"] == "ready" and canonical["active"]["game"] == "robots"
    assert canonical["active"]["runtime_id"] == state["restored_runtime_identity"]["runtime_id"]

    # The case session ran end to end with zero participation: seeded solo
    # picks, no poll/prediction, and the reveal published with verification.
    session = state["session"]
    case = mystery_case.generate(session["seed"])
    assert session["revealed"] is True
    assert len(session["revealed_rooms"]) == 1
    assert len(session["revealed_suspects"]) == 1
    assert session["prediction"]["status"] == "skipped"
    payload = read_payload(Path(g.state_dir) / PAYLOAD_FILENAME)
    assert payload is not None and payload["case"]["reveal"]["seed"] == case.seed
    assert payload["case"]["reveal"]["culprit"]["name"] == case.culprit.name
    kinds = [item["kind"] for item in quiet_narration]
    assert kinds == ["intro", "investigate", "interrogate", "deduction", "reveal"]
    assert case.culprit.name in quiet_narration[-1]["overlay"]


def test_mystery_corner_polls_drive_the_investigation(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path, duration=30)
    poll = FakePollClient(votes=[5, 1, 0, 0])

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(duration=30), switch, now, sleep, poll_factory=poll)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    session = state["session"]
    case = mystery_case.generate(session["seed"])
    actions = mystery_case.poll_actions(case, "investigate")
    # votes=[5,1,0,0] → first-max index 0.
    assert session["revealed_rooms"] == [actions[0][0]]
    assert poll.created and poll.created[0][2] <= 1800
    assert state["ends_at"] - state["started_at"] == 30 * 60


def test_mystery_corner_zero_votes_falls_back_to_the_seeded_detective(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path, duration=30)
    poll = FakePollClient(votes=[0, 0, 0, 0])

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(duration=30), switch, now, sleep, poll_factory=poll)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    session = state["session"]
    case = mystery_case.generate(session["seed"])
    rng = random.Random(f"{case.seed}:investigate:solo")
    expected = mystery_case.ai_detective_choice(case, "investigate", rng)
    assert session["revealed_rooms"] == [expected]


def test_mystery_corner_settles_a_contested_prediction_with_the_engine_answer(
        tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path, duration=30)
    prediction = FakePredictionClient()

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(duration=30), switch, now, sleep,
                       prediction_factory=prediction)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    session = state["session"]
    case = mystery_case.generate(session["seed"])
    assert session["prediction"]["resolved"] is True
    # The fake crowd bets o0=100 / o1=500 → the crowd's pick is outcome o1.
    assert session["prediction"]["hit"] is (case.culprit_index == 1)
    end_calls = [call for call in prediction.calls if call[0] == "end"]
    assert ("end", "pred1", "LOCKED", None) in end_calls
    resolved = [call for call in end_calls if call[2] == "RESOLVED"]
    assert len(resolved) == 1
    payload = read_payload(Path(g.state_dir) / PAYLOAD_FILENAME)
    assert payload["case"]["reveal"]["culprit"]["name"] == case.culprit.name


def test_mystery_corner_refunds_an_uncontested_prediction(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path, duration=30)
    prediction = FakePredictionClient(bets=(("o0", 400),))

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(duration=30), switch, now, sleep,
                       prediction_factory=prediction)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    session = state["session"]
    # One outcome with bets → no contest → CANCELED + refund (2026-10-15 方針).
    assert session["prediction"]["resolved"] is False
    assert ("end", "pred1", "CANCELED", None) in prediction.calls
    assert not any(call[2] == "RESOLVED" for call in prediction.calls)


def test_mystery_corner_manual_stop_still_reveals_and_restores(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path, duration=30)
    prediction = FakePredictionClient()
    calls = {"sleep": 0}

    def sleep(seconds):
        now[0] += seconds
        calls["sleep"] += 1
        if calls["sleep"] == 3:
            path = Path(g.state_dir) / STOP_REQUEST_DIR / "mystery_corner.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"state_file": "mystery_corner.json", "requested_at": now[0]}))

    adapter = _adapter(g, _corner(duration=30), switch, now, sleep,
                       prediction_factory=prediction)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "completed" and state["end_reason"] == "manual"
    assert state["session"]["revealed"] is True
    canonical, _ = store.canonical.load()
    assert canonical["active"]["game"] == "robots"
    assert not (Path(g.state_dir) / STOP_REQUEST_DIR / "mystery_corner.json").exists()


def test_mystery_corner_stop_before_start_needs_no_game_switch(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(), switch, now, sleep)
    path = Path(g.state_dir) / STOP_REQUEST_DIR / "mystery_corner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state_file": "mystery_corner.json", "requested_at": now[0]}))

    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    assert state["status"] == "interrupted"


def test_mystery_corner_never_restores_over_an_operator_moved_runtime(tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path)

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(), switch, now, sleep)
    request_id = str(uuid.uuid4())
    state = adapter.manager._new_state(request_id)
    adapter.manager._save(state)
    assert adapter.manager._dispatch_start(state) is None
    view_identity = state["mystery_runtime_identity"]

    # An operator moves the shared slot away from the view while it is active.
    moved = switch.switch("nsnake", payload={"expected_source": view_identity})
    assert moved.status == "succeeded"

    assert adapter.manager.run_rotation(request_id) == "completed"

    saved = json.loads(adapter.state_path.read_text())
    assert saved["status"] == "interrupted"
    assert saved["end_reason"] == "operator-moved-during-mystery"
    final, _ = store.canonical.load()
    assert final["active"]["game"] == "nsnake"


def test_mystery_session_persists_only_the_seed_and_publishes_a_verified_reveal(
        tmp_path, quiet_narration):
    g, now, _factory, store, switch = _setup(tmp_path, duration=30)

    def sleep(seconds):
        now[0] += seconds

    adapter = _adapter(g, _corner(duration=30), switch, now, sleep)
    assert adapter.manager.run_rotation(str(uuid.uuid4())) == "completed"

    state = json.loads(adapter.state_path.read_text())
    session = state["session"]
    case = mystery_case.generate(session["seed"])
    # The state holds only the seed and progress; the broadcast payload is the
    # commitment-bearing public board, and the final reveal carries its seed
    # so anyone can recompute the case with the public engine.
    assert session.get("seed") == case.seed
    reveal_payload = read_payload(Path(g.state_dir) / PAYLOAD_FILENAME)
    assert mystery_case.verify_reveal(reveal_payload["case"]["reveal"], case)
