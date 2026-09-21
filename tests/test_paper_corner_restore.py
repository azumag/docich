import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import paper_corner_restore
from docich.paper_corner import PaperCornerError


class _Result:
    def __init__(self, status="rolled_back", detail=None, error_code=None):
        self.status = status
        self.detail = detail
        self.error_code = error_code


class _Coordinator:
    def __init__(self, result=None):
        self.result = result or _Result()
        self.calls = []

    def recover(self, *, timeout_s):
        self.calls.append(timeout_s)
        return self.result


class _Manager:
    def __init__(self, *, status="failed", result=None, canonical=None, state_dir=None):
        self.clock = lambda: dt.datetime(2026, 9, 19, 23, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()
        self.tz = ZoneInfo("Asia/Tokyo")
        self.state = {
            "status": status,
            "date": "2026-09-19",
            "previous_game": "sorengame",
        }
        self.g = SimpleNamespace(state_dir=state_dir or Path("/nonexistent-paper-state"))
        self.coordinator = _Coordinator(result)
        self.store = SimpleNamespace(
            canonical=SimpleNamespace(load=lambda: (canonical or {
                "phase": "failed",
                "previous": {"game": "paper-view"},
            }, False))
        )
        self.active = None

    def _read_state(self):
        return self.state

    def _active_game(self):
        if self.active is None and self.coordinator.calls:
            self.active = "paper-view"
        return self.active


def test_failed_paper_view_is_recovered_before_restore():
    manager = _Manager()

    result = paper_corner_restore._recover_failed_paper_view(manager)

    assert result.status == "rolled_back"
    assert manager.coordinator.calls == [paper_corner_restore.RECOVERY_TIMEOUT_S]
    assert manager._active_game() == "paper-view"


def test_unrelated_or_stale_state_is_left_for_existing_fail_safe():
    stale = _Manager()
    stale.state["date"] = "2026-09-18"
    assert paper_corner_restore._recover_failed_paper_view(stale) is None
    assert stale.coordinator.calls == []

    unrelated = _Manager(canonical={
        "phase": "failed",
        "previous": {"game": "sorengame"},
    })
    assert paper_corner_restore._recover_failed_paper_view(unrelated) is None
    assert unrelated.coordinator.calls == []


def test_failed_canonical_recovery_stays_fail_closed():
    manager = _Manager(result=_Result(status="failed", error_code="recovery_required"))

    with pytest.raises(PaperCornerError, match="canonical PAPER view recovery failed"):
        paper_corner_restore._recover_failed_paper_view(manager)


def test_restore_runs_canonical_recovery_before_manager_stop(monkeypatch, tmp_path):
    manager = _Manager()
    events = []

    def recover(*, timeout_s):
        events.append("recover")
        manager.coordinator.calls.append(timeout_s)
        return _Result()

    manager.coordinator.recover = recover

    def stop():
        events.append("stop")
        manager.active = "sorengame"
        return "completed"

    manager.stop = stop
    monkeypatch.setattr(paper_corner_restore, "load_global", lambda *_args: object())
    monkeypatch.setattr(paper_corner_restore, "_stop_scheduled_service", lambda **_kwargs: None)
    monkeypatch.setattr(paper_corner_restore, "FastPaperCornerManager", lambda _g: manager)

    result = paper_corner_restore.restore(tmp_path / "config.toml")

    assert result == {"status": "restored", "result": "completed"}
    assert events == ["recover", "stop"]


def test_failed_manual_corner_state_is_recovered_before_restore(tmp_path, monkeypatch):
    # The scheduled state is stale (yesterday); today's operator/manual run is
    # the stranded one. Its previous game is the only safe restore target.
    manager = _Manager(state_dir=tmp_path)
    manager.state["date"] = "2026-09-18"
    (tmp_path / paper_corner_restore.MANUAL_STATE_FILE).write_text(
        json.dumps({"status": "failed", "date": "2026-09-19", "previous_game": "sorengame"}),
        encoding="utf-8",
    )

    result = paper_corner_restore._recover_failed_paper_view(manager)

    assert result.status == "rolled_back"
    assert manager.coordinator.calls == [paper_corner_restore.RECOVERY_TIMEOUT_S]


def test_abandon_fallback_clears_program_view_and_starts_recorded_game(tmp_path, monkeypatch):
    manager = _Manager(state_dir=tmp_path)
    manager.state["date"] = "2026-09-18"
    (tmp_path / paper_corner_restore.MANUAL_STATE_FILE).write_text(
        json.dumps({"status": "failed", "date": "2026-09-19", "previous_game": "sorengame"}),
        encoding="utf-8",
    )
    calls = []

    class Coordinator:
        def recover(self, *, timeout_s=None, abandon_program_view=False):
            calls.append(("recover", abandon_program_view))
            return _Result(status="succeeded")

        def start(self, game):
            calls.append(("start", game))
            return _Result(status="succeeded")

    manager.coordinator = Coordinator()

    result = paper_corner_restore._recover_failed_paper_view(manager, abandon=True)

    assert result.status == "succeeded"
    assert calls == [("recover", True), ("start", "sorengame")]
    g = SimpleNamespace(state_dir=tmp_path)
    manager = _Manager(state_dir=tmp_path)
    manager.state["date"] = "2026-09-18"
    (tmp_path / paper_corner_restore.MANUAL_STATE_FILE).write_text(
        json.dumps({"status": "failed", "date": "2026-09-19", "previous_game": "sorengame"}),
        encoding="utf-8",
    )
    manager.stop = lambda: "not-active"

    events = []

    def recover(*, timeout_s):
        events.append("recover")
        manager.active = "paper-view"
        return _Result()

    manager.coordinator.recover = recover
    manager._active_game = lambda: manager.active

    manual = SimpleNamespace(
        stop=lambda: events.append("manual-stop") or setattr(manager, "active", "sorengame") or "completed"
    )

    monkeypatch.setattr(paper_corner_restore, "load_global", lambda *_args: g)
    monkeypatch.setattr(paper_corner_restore, "_stop_scheduled_service", lambda **_kwargs: None)
    monkeypatch.setattr(paper_corner_restore, "FastPaperCornerManager", lambda _g: manager)
    monkeypatch.setattr(paper_corner_restore, "ManualPaperCornerManager", lambda _g: manual)

    result = paper_corner_restore.restore(tmp_path / "config.toml")

    assert result == {"status": "restored", "result": "completed"}
    assert events == ["recover", "manual-stop"]
