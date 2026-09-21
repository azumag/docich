import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner_fast import FastPaperCornerManager


class Result:
    status = "succeeded"
    detail = None
    error_code = None


class Coordinator:
    def __init__(self, active="sorengame"):
        self.active = active
        self.calls = []

    def start(self, game):
        self.calls.append(("start", game))
        self.active = game
        return Result()

    def switch(self, game):
        self.calls.append(("switch", game))
        self.active = game
        return Result()

    def stop(self):
        self.calls.append(("stop", None))
        self.active = None
        return Result()


def _manager(tmp_path, *, script_agents="", clock=lambda: 1000.0, sleep=lambda s: None):
    cfg = tmp_path / "config.toml"
    agents_line = f'script_agents = "{script_agents}"\n' if script_agents else ""
    cfg.write_text(
        '[paths]\nstate_dir = "run"\n'
        "[trading]\npaper_worker_enabled = true\nnotifications_enabled = true\n"
        "notification_speech_enabled = true\n"
        f'[webui]\nsoren_root = "{tmp_path}/soren"\n'
        "[paper_corner]\nenabled = true\nstart_hour = 22\n" + agents_line,
        encoding="utf-8",
    )
    g = load_global(tmp_path, cfg)
    coord = Coordinator()
    mgr = FastPaperCornerManager(
        g,
        clock=clock,
        sleep=sleep,
        coordinator=coord,
        overlay=lambda g, payload: None,
        speech=lambda g, text, **kwargs: None,
    )
    mgr._active_game = lambda: coord.active
    mgr._stream_paper = lambda: None
    mgr._stream_game = lambda game: None
    return mgr, coord


def test_prewarm_installs_the_finite_fallback_once(tmp_path):
    mgr, _coord = _manager(tmp_path)
    state = {"status": "waiting", "date": "2026-09-17", "requested_at": 1000.0}

    mgr._prewarm_script(state)

    assert set(state["fallback_segments"]) == {str(index) for index in range(1, 9)}
    assert state["fallback_segments"]["1"]
    saved = json.loads(mgr.path.read_text())
    assert set(saved["fallback_segments"]) == {str(index) for index in range(1, 9)}

    # Idempotent: a second prewarm must not overwrite the installed set.
    state["fallback_segments"]["1"] = "sentinel"
    mgr._prewarm_script(state)
    assert state["fallback_segments"]["1"] == "sentinel"


def test_prewarm_ignores_non_waiting_states(tmp_path):
    mgr, _coord = _manager(tmp_path)
    state = {"status": "active", "date": "2026-09-17"}

    mgr._prewarm_script(state)

    assert "fallback_segments" not in state


def test_fast_manager_runs_content_driven_narration(tmp_path, monkeypatch):
    from docich.trading import corner_script

    mgr, coord = _manager(tmp_path, script_agents="fixture:agent")
    queue = [
        {"status": "item", "topic": "相場", "text": "最初のネタです。"},
        {"status": "done"},
    ]
    monkeypatch.setattr(
        corner_script, "generate_next_narration", lambda *a, **k: queue.pop(0)
    )

    assert mgr._run_locked({
        "status": "starting",
        "date": "2026-09-17",
        "previous_game": "sorengame",
        "reports": {},
    }) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["end_reason"] == "exhausted"
    assert saved["reports"]["ai:1"]["text"] == "最初のネタです。"
    assert coord.calls[0] == ("switch", "paper-view")
