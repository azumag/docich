import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner import PaperCornerManager


class Result:
    status = "succeeded"
    detail = None
    error_code = None


class Coordinator:
    def __init__(self):
        self.active = "sorengame"
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


def _manager(
    tmp_path,
    *,
    script_agents="fixture:agent",
    overlay=None,
    speech=None,
    clock=lambda: 1000.0,
    sleep=lambda seconds: None,
):
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
    mgr = PaperCornerManager(
        g,
        clock=clock,
        sleep=sleep,
        coordinator=coord,
        overlay=overlay or (lambda g, payload: None),
        speech=speech or (lambda g, text, **kwargs: None),
    )
    mgr._active_game = lambda: coord.active
    # Never touch the stream/radio in unit tests.
    mgr._stream_paper = lambda: None
    mgr._stream_game = lambda game: None
    return mgr, coord


def _starting_state():
    return {
        "status": "starting",
        "date": "2026-09-20",
        "previous_game": "sorengame",
        "reports": {},
    }


def test_ai_segments_are_spoken_as_generated_then_exhausted(tmp_path, monkeypatch):
    from docich.trading import corner_script

    spoken = []
    mgr, _coord = _manager(
        tmp_path,
        overlay=lambda g, payload: None,
        speech=lambda g, text, **kwargs: spoken.append(text),
    )
    queue = [
        {"status": "item", "topic": "相場", "text": "一つ目のネタです。"},
        {"status": "item", "topic": "ニュース", "text": "二つ目のネタです。"},
        {"status": "done"},
    ]
    monkeypatch.setattr(
        corner_script, "generate_next_narration", lambda *a, **k: queue.pop(0)
    )

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["end_reason"] == "exhausted"
    assert saved["reports"]["ai:1"]["text"] == "一つ目のネタです。"
    assert saved["reports"]["ai:2"]["text"] == "二つ目のネタです。"
    assert not any(key.startswith("fallback:") for key in saved["reports"])
    assert "一つ目のネタです。" in spoken and "二つ目のネタです。" in spoken
    # Covered topics are handed back so the narrator can avoid repeats.
    assert saved["covered_topics"] == ["相場", "ニュース"]


def test_generation_failure_reads_finite_fallback_then_marks_degraded(tmp_path, monkeypatch):
    from docich.trading import corner_script

    mgr, _coord = _manager(tmp_path)
    calls = {"n": 0}

    def failing(*args, **kwargs):
        calls["n"] += 1
        return {"status": "failed", "reason": "AiTextError:timeout"}

    monkeypatch.setattr(corner_script, "generate_next_narration", failing)

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert calls["n"] == 2, "a transient failure must be retried a bounded number of times"
    assert saved["end_reason"] == "generation-failed"
    assert saved["degraded"] is True
    assert "timeout" in saved["end_detail"]
    delivered = {key for key in saved["reports"] if key.startswith("fallback:")}
    assert delivered == {f"fallback:{index}" for index in range(1, 9)}


def test_ai_disabled_reads_finite_fallback_and_ends_exhausted(tmp_path):
    mgr, _coord = _manager(tmp_path, script_agents="")

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["end_reason"] == "exhausted"
    assert "degraded" not in saved
    delivered = {key for key in saved["reports"] if key.startswith("fallback:")}
    assert delivered == {f"fallback:{index}" for index in range(1, 9)}


def test_no_explicit_narration_interval_between_segments(tmp_path, monkeypatch):
    from docich.trading import corner_script

    sleeps = []
    mgr, _coord = _manager(tmp_path, sleep=lambda seconds: sleeps.append(seconds))
    queue = [{"status": "item", "topic": "a", "text": "A"},
             {"status": "item", "topic": "b", "text": "B"},
             {"status": "done"}]
    monkeypatch.setattr(
        corner_script, "generate_next_narration", lambda *a, **k: queue.pop(0)
    )

    assert mgr._run_locked(_starting_state()) == "completed"
    assert sleeps == [], "segments must be spoken as generated, with no fixed interval"
