import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner_fast import (
    FOLLOWUP_NARRATION_INTERVAL_S,
    MAIN_NARRATION_INTERVAL_S,
    FastPaperCornerManager,
)


class Result:
    status = "succeeded"
    detail = None
    error_code = None


class Coordinator:
    def start(self, game):
        return Result()

    def switch(self, game):
        return Result()

    def stop(self):
        return Result()


def _manager(
    tmp_path,
    *,
    clock=lambda: 1000.0,
    sleep=lambda seconds: None,
    script_spawn=None,
    stream_paper=None,
):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'''[paths]\nstate_dir = "run"\n[trading]\npaper_worker_enabled = true\nnotifications_enabled = true\nnotification_speech_enabled = true\n[webui]\nsoren_root = "{tmp_path}/soren"\n[paper_corner]\nenabled = true\nstart_hour = 22\nduration_minutes = 30\nscript_agents = "fixture:agent"\n''',
        encoding="utf-8",
    )
    g = load_global(tmp_path, cfg)
    return FastPaperCornerManager(
        g,
        clock=clock,
        sleep=sleep,
        coordinator=Coordinator(),
        overlay=lambda g, payload: None,
        speech=lambda g, text, **kwargs: None,
        script_spawn=script_spawn,
        stream_paper=stream_paper,
    )


def test_main_segments_are_front_loaded_then_followups_slow_down():
    assert MAIN_NARRATION_INTERVAL_S == 75
    assert FOLLOWUP_NARRATION_INTERVAL_S == 90
    assert FastPaperCornerManager._due_seconds(1) == 75
    assert FastPaperCornerManager._due_seconds(8) == 600
    assert FastPaperCornerManager._due_seconds(9) == 690
    assert FastPaperCornerManager._due_seconds(10) == 780


def test_script_prepare_uses_local_fallback_and_detaches_real_ai(tmp_path, monkeypatch):
    calls = []
    spawned = []

    from docich.trading import corner_script

    def fake_generate(g, *, trading_dir, agents, timeout, now):
        calls.append(agents)
        return {
            "source": "fallback",
            "reason": "no-agents",
            "segments": {
                "corner": "相場です。",
                "news": "ニュースです。",
                "chart": "チャートです。",
                "strategy": "戦略です。",
                "result": "結果です。",
                "fills": "約定です。",
                "review": "レビューです。",
                "improve": "改善です。",
            },
        }

    monkeypatch.setattr(corner_script, "generate_corner_script", fake_generate)
    mgr = _manager(
        tmp_path,
        script_spawn=lambda argv, log_path: spawned.append((argv, log_path)) or 12345,
    )
    state = {"date": "2026-09-16", "reports": {}}

    mgr._announce_script(state)

    assert calls == [""]
    assert state["script_source"] == "fallback-initial"
    assert len(state["script_segments"]) == 8
    assert state["script_job"]["status"] == "running"
    assert state["script_job"]["pid"] == 12345
    assert len(spawned) == 1
    assert "docich.paper_corner_fast" in spawned[0][0]
    assert "script-worker" in spawned[0][0]


def test_start_becomes_active_before_script_enrichment(tmp_path):
    now = [1000.0]
    observed = []

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    mgr = _manager(tmp_path, clock=clock, sleep=sleep, script_spawn=lambda argv, log: 99)
    mgr.minutes = 1

    def probe_script(state):
        observed.append((state.get("status"), state.get("started_at"), state.get("ends_at")))
        state["script_segments"] = {str(i): f"segment-{i}" for i in range(1, 9)}
        mgr.save(state)

    mgr._announce_script = probe_script
    state = {
        "status": "starting",
        "date": "2026-09-16",
        "previous_game": None,
        "reports": {},
    }

    assert mgr._run_locked(state) == "completed"
    assert observed == [("active", 1000.0, 1060.0)]
    saved = json.loads(mgr.path.read_text(encoding="utf-8"))
    assert saved["status"] == "completed"


def test_fast_path_announces_paper_category_after_view_commit(tmp_path):
    now = [1000.0]
    announced = []
    mgr = _manager(
        tmp_path,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        stream_paper=lambda: announced.append("paper"),
    )
    mgr.minutes = 1
    mgr._announce_script = lambda state: None

    assert mgr._run_locked({
        "status": "starting",
        "date": "2026-09-16",
        "previous_game": None,
        "reports": {},
    }) == "completed"
    assert announced == ["paper"]


def test_prewarm_installs_fallback_and_spawns_worker_once(tmp_path):
    spawned = []
    mgr = _manager(tmp_path, script_spawn=lambda argv, log: spawned.append(argv) or 999)
    state = {'status': 'waiting', 'date': '2026-09-17', 'requested_at': 1000.0}
    mgr._prewarm_script(state)
    assert state.get('script_segments')
    assert len(state['script_segments']) == 8
    assert state['script_job']['status'] == 'running'
    count = len(spawned)
    assert count == 1
    mgr._prewarm_script(state)
    assert len(spawned) == count


def test_prewarm_ignores_non_waiting_states(tmp_path):
    spawned = []
    mgr = _manager(tmp_path, script_spawn=lambda argv, log: spawned.append(argv) or 999)
    state = {'status': 'active', 'date': '2026-09-17'}
    mgr._prewarm_script(state)
    assert spawned == []
    assert 'script_segments' not in state
