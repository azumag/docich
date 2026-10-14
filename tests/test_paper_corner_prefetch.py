import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner import PaperCornerManager
from docich.trading.corner_script import SEGMENT_KEYS


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


def _manager(tmp_path, *, script_agents="fixture:agent", clock=lambda: 1000.0,
             sleep=lambda seconds: None):
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
        overlay=lambda g, payload: None,
        speech=lambda g, text, **kwargs: None,
    )
    mgr._active_game = lambda: coord.active
    mgr._stream_paper = lambda: None
    mgr._stream_game = lambda game: None
    return mgr, coord


def _starting_state():
    return {
        "status": "starting",
        "date": "2026-09-29",
        "previous_game": "sorengame",
        "narration_schema": 2,
        "reports": {},
    }


def test_next_slot_generation_runs_while_current_speech_is_playing(tmp_path, monkeypatch):
    """The prefetch must overlap generation with the previous audio drain."""
    from docich.trading import corner_script

    mgr, _coord = _manager(tmp_path)
    events = []
    waiting = threading.Event()
    polls = {"n": 0}

    def pending():
        polls["n"] += 1
        waiting.set()
        # Keep reporting playback until the news generation has actually run,
        # so the assertion cannot pass because the wait finished first.
        return "gen:news" not in events and polls["n"] < 10000

    mgr._pending_speech = pending

    def fake_next(*args, **kwargs):
        slot = kwargs["target_key"]
        if slot == "news":
            assert waiting.wait(5.0), "the speech wait never started"
        events.append(f"gen:{slot}")
        return {"status": "item", "topic": slot, "text": f"{slot}のネタです。"}

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    assert mgr._run_locked(_starting_state()) == "completed"

    assert events[0] == "gen:corner"
    assert "gen:news" in events
    assert polls["n"] < 10000


def test_prefetch_generates_each_slot_exactly_once(tmp_path, monkeypatch):
    from docich.trading import corner_script

    mgr, _coord = _manager(tmp_path)
    calls = []

    def fake_next(*args, **kwargs):
        calls.append(kwargs["target_key"])
        return {"status": "item", "topic": kwargs["target_key"],
                "text": f"{kwargs['target_key']}本文"}

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    assert mgr._run_locked(_starting_state()) == "completed"

    assert calls == list(SEGMENT_KEYS)
    saved = json.loads(mgr.path.read_text())
    assert all(saved["reports"][f"script:{index}"]["source"] == "ai"
               for index in range(1, 9))


def test_prefetch_starts_after_announce_and_before_the_wait(tmp_path):
    """Pin the loop ordering that makes generation/speech overlap possible."""
    mgr, _coord = _manager(tmp_path)
    order = []

    def fake_item(index):
        return {"key": f"script:{index}", "text": f"本文{index}",
                "topic": f"話題{index}", "source": "fallback"}

    mgr._next_narration_item = lambda state, index: fake_item(index)
    mgr._start_prefetch = lambda state, index, report: order.append(("prefetch", index))
    mgr._wait_for_speech = lambda state: order.append(("wait",)) or True

    assert mgr._run_locked(_starting_state()) == "completed"

    assert order[:2] == [("prefetch", 2), ("wait",)]
    assert order.count(("wait",)) == 9
    # The loop asks for the slot after each announced segment; the real
    # _start_prefetch declines the out-of-range index 9 (see the unit test).
    assert [entry for entry in order if entry[0] == "prefetch"] == [
        ("prefetch", index) for index in range(2, 10)]


def test_start_prefetch_declines_when_not_useful(tmp_path):
    mgr, _coord = _manager(tmp_path)
    assert mgr._start_prefetch({}, 9, None) is None
    assert mgr._start_prefetch({"reports": {"script:2": {"text": "既存"}}}, 2, None) is None

    disabled, _coord = _manager(tmp_path, script_agents="")
    assert disabled._start_prefetch({}, 2, None) is None


def test_narration_generation_keeps_the_gate_out_of_process_env(tmp_path, monkeypatch):
    """The prefetch grants the real-AI gate in a private env copy only."""
    from docich.trading import corner_script

    monkeypatch.delenv("DOCICH_ALLOW_REAL_AI", raising=False)
    mgr, _coord = _manager(tmp_path)
    seen = {}

    def fake_next(*args, **kwargs):
        seen["env_gate"] = dict(kwargs.get("env") or {}).get("DOCICH_ALLOW_REAL_AI")
        seen["process_gate"] = os.environ.get("DOCICH_ALLOW_REAL_AI")
        return {"status": "item", "topic": "話題", "text": "本文です。"}

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    item = mgr._generate_narration_text(1, [], "fallback")

    assert item["source"] == "ai"
    assert seen["env_gate"] == "1"
    assert seen["process_gate"] is None
    assert "DOCICH_ALLOW_REAL_AI" not in os.environ


def test_stale_prefetch_blocks_new_ai_until_it_ends(tmp_path, monkeypatch):
    """A timed-out worker must not overlap the next AI call or break its gate."""
    from docich.trading import corner_script

    now = [1000.0]
    mgr, _coord = _manager(
        tmp_path,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    release = threading.Event()
    calls = []

    def fake_next(*args, **kwargs):
        slot = kwargs["target_key"]
        calls.append(slot)
        if slot == "news":
            release.wait(10.0)
        return {"status": "item", "topic": slot, "text": f"{slot}本文"}

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["reports"]["script:2"]["source"] == "fallback"
    assert saved["generation_failures"]["news"] == "prefetch-timeout"
    # No other AI call overlapped the abandoned worker.
    assert calls == ["corner", "news"]
    assert all(saved["generation_failures"][key] == "prefetch-busy"
               for key in SEGMENT_KEYS[2:])

    # Releasing the worker lets it exit; prefetching then resumes and the
    # real-AI gate still reaches the provider through the explicit env.
    stale = mgr._stale_prefetch
    release.set()
    stale["thread"].join(5)
    assert not stale["thread"].is_alive()

    seen_gate = {}
    original = fake_next

    def gated_next(*args, **kwargs):
        seen_gate["env"] = dict(kwargs.get("env") or {}).get("DOCICH_ALLOW_REAL_AI")
        return original(*args, **kwargs)

    monkeypatch.setattr(corner_script, "generate_next_narration", gated_next)
    state = {"reports": {}}
    handle = mgr._start_prefetch(state, 3, None)
    assert handle is not None
    item = mgr._consume_prefetch(state, handle)
    assert item["source"] == "ai"
    assert seen_gate["env"] == "1"


def test_prefetch_timeout_uses_fallback_without_another_ai_attempt(tmp_path, monkeypatch):
    from docich.trading import corner_script

    now = [1000.0]
    mgr, _coord = _manager(
        tmp_path,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    calls = []
    release = threading.Event()

    def fake_next(*args, **kwargs):
        slot = kwargs["target_key"]
        calls.append(slot)
        if slot == "news":
            # Ignores its own provider timeout: the corner must abandon the
            # worker at its bounded deadline and not start another AI attempt.
            release.wait(10.0)
        return {"status": "item", "topic": slot, "text": f"{slot}本文"}

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["reports"]["script:2"]["source"] == "fallback"
    assert saved["generation_failures"]["news"] == "prefetch-timeout"
    assert calls.count("news") == 1
