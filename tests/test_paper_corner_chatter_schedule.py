import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner import NARRATION_AI_RETRIES, SPEECH_DRAIN_STABLE_POLLS, PaperCornerManager
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
        "narration_schema": 2,
        "reports": {},
    }


def _write_current_source(tmp_path, content, label, phase="playing"):
    source = tmp_path / "soren/tmp/.say_queue/current_source"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"owner|{phase}|{content}|1000|{label}\n", encoding="utf-8")


def test_ai_segments_are_spoken_in_fixed_order(tmp_path, monkeypatch):
    from docich.trading import corner_script

    spoken = []
    mgr, _coord = _manager(
        tmp_path,
        overlay=lambda g, payload: None,
        speech=lambda g, text, **kwargs: spoken.append(text),
    )
    queue = [{"status": "item", "topic": key, "text": f"{key}のネタです。"}
             for key in SEGMENT_KEYS]
    targets = []
    def fake_next(*args, **kwargs):
        targets.append(kwargs["target_key"])
        return queue.pop(0)
    monkeypatch.setattr(
        corner_script, "generate_next_narration", fake_next
    )

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["end_reason"] == "eight-slots-drained"
    assert targets == list(SEGMENT_KEYS)
    assert [saved["reports"][f"script:{i}"]["text"] for i in range(1, 9)] == [
        f"{key}のネタです。" for key in SEGMENT_KEYS]
    assert all(saved["reports"][f"script:{i}"]["drained"] for i in range(1, 9))
    assert all(f"{key}のネタです。" in spoken for key in SEGMENT_KEYS)
    assert saved["covered_topics"][0] == "corner：cornerのネタです。"


def test_covered_topics_keep_every_spoken_slot(tmp_path, monkeypatch):
    """Each later slot sees all prior spoken topics."""
    from docich.trading import corner_script

    mgr, _coord = _manager(
        tmp_path, overlay=lambda g, payload: None, speech=lambda g, text, **kwargs: None,
    )
    queue = [{"status": "item", "topic": f"話題{index}", "text": f"{index}番目のネタです。"}
             for index in range(1, 9)]
    seen = []

    def fake_next(*args, **kwargs):
        seen.append(list(kwargs.get("covered") or []))
        return queue.pop(0)

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert len(saved["covered_topics"]) == 8
    assert saved["covered_topics"][0] == "話題1：1番目のネタです。"
    assert seen[-1][0] == "話題1：1番目のネタです。" and len(seen[-1]) == 7


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
    assert calls["n"] == 8 * NARRATION_AI_RETRIES
    assert saved["end_reason"] == "eight-slots-drained"
    assert saved["degraded"] is True
    assert "timeout" in saved["end_detail"]
    assert set(saved["generation_failures"]) == set(SEGMENT_KEYS)
    assert all(saved["reports"][f"script:{index}"]["source"] == "fallback"
               for index in range(1, 9))


def test_ai_disabled_reads_all_eight_fallback_slots(tmp_path):
    mgr, _coord = _manager(tmp_path, script_agents="")

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert saved["end_reason"] == "eight-slots-drained"
    assert "degraded" not in saved
    assert [saved["reports"][f"script:{index}"]["slot"] for index in range(1, 9)] == list(SEGMENT_KEYS)


def test_each_slot_waits_for_audio_monitor_before_next(tmp_path, monkeypatch):
    from docich.trading import corner_script

    sleeps = []
    mgr, _coord = _manager(tmp_path, sleep=lambda seconds: sleeps.append(seconds))
    queue = [{"status": "item", "topic": key, "text": key} for key in SEGMENT_KEYS]
    monkeypatch.setattr(
        corner_script, "generate_next_narration", lambda *a, **k: queue.pop(0)
    )

    assert mgr._run_locked(_starting_state()) == "completed"
    assert sleeps == [2.0] * (9 * (SPEECH_DRAIN_STABLE_POLLS - 1))


def test_corner_waits_for_speech_to_finish_before_restoring(tmp_path):
    speaking = tmp_path / "soren" / "tmp" / "state" / "speaking.json"
    speaking.parent.mkdir(parents=True)
    speaking.write_text("{}", encoding="utf-8")
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        speaking.unlink(missing_ok=True)

    mgr, _coord = _manager(tmp_path, script_agents="", sleep=sleep)

    assert mgr._run_locked(_starting_state()) == "completed"

    assert len(sleeps) >= SPEECH_DRAIN_STABLE_POLLS and all(s == 2.0 for s in sleeps), (
        "the corner must wait for the audio queue to drain"
    )
    saved = json.loads(mgr.path.read_text())
    assert "speech_drain_timeout" not in saved


def test_corner_bounds_the_speech_wait(tmp_path):
    speaking = tmp_path / "soren" / "tmp" / "state" / "speaking.json"
    speaking.parent.mkdir(parents=True)
    speaking.write_text("{}", encoding="utf-8")
    now = [1000.0]

    mgr, _coord = _manager(
        tmp_path,
        script_agents="",
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert mgr._run_locked(_starting_state()) == "pending"

    saved = json.loads(mgr.path.read_text())
    assert saved.get("speech_drain_timeout") is True


def test_unrelated_comment_and_say_queue_do_not_block_paper_restore(tmp_path):
    comment_queue = tmp_path / "soren/tmp/.comment_queue"
    say_queue = tmp_path / "soren/tmp/.say_queue"
    comment_queue.mkdir(parents=True)
    say_queue.mkdir(parents=True)
    (comment_queue / "comment_announce_1_unrelated.txt").write_text("chat", encoding="utf-8")
    (say_queue / "content_unrelated.txt").write_text("radio", encoding="utf-8")

    mgr, _coord = _manager(tmp_path, script_agents="")

    assert mgr._pending_speech() is False


def test_paper_queue_and_current_source_block_restore(tmp_path):
    comment_queue = tmp_path / "soren/tmp/.comment_queue"
    comment_queue.mkdir(parents=True)
    paper_name = "comment_announce_1_" + ("a" * 64) + "_crypto_paper.playing"
    (comment_queue / paper_name).write_text("PAPER", encoding="utf-8")
    mgr, _coord = _manager(tmp_path, script_agents="")

    assert mgr._pending_speech() is True

    (comment_queue / paper_name).unlink()
    _write_current_source(
        tmp_path,
        "tmp/.comment_queue/comment_announce_1_" + ("b" * 64) + "_crypto_paper.playing",
        "crypto_paper",
    )
    assert mgr._pending_speech() is True


def test_speaking_without_source_metadata_remains_fail_safe(tmp_path):
    speaking = tmp_path / "soren/tmp/state/speaking.json"
    speaking.parent.mkdir(parents=True)
    speaking.write_text("{}", encoding="utf-8")
    mgr, _coord = _manager(tmp_path, script_agents="")

    assert mgr._pending_speech() is True

    _write_current_source(tmp_path, "tmp/.comment_queue/comment_announce_2_other.playing", "comment")
    assert mgr._pending_speech() is False

    speaking.unlink()
    (tmp_path / "soren/tmp/.say_queue/current_source").write_text("malformed\n", encoding="utf-8")
    assert mgr._pending_speech() is True

    _write_current_source(
        tmp_path,
        "tmp/.comment_queue/comment_announce_3_crypto_paper.txt",
        "comment",
    )
    assert mgr._pending_speech() is True


def test_handoff_to_unrelated_audio_reaches_stable_empty(tmp_path):
    paper = tmp_path / "soren/tmp/.comment_queue" / (
        "comment_announce_1_" + ("c" * 64) + "_crypto_paper.txt"
    )
    paper.parent.mkdir(parents=True)
    paper.write_text("PAPER", encoding="utf-8")
    speaking = tmp_path / "soren/tmp/state/speaking.json"
    speaking.parent.mkdir(parents=True)
    speaking.write_text("{}", encoding="utf-8")
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        paper.unlink(missing_ok=True)
        _write_current_source(tmp_path, "tmp/.comment_queue/comment_announce_2_other.playing", "comment")

    mgr, _coord = _manager(tmp_path, script_agents="", sleep=sleep)

    assert mgr._wait_for_speech({"status": "active"}) is True
    assert sleeps == [2.0] * SPEECH_DRAIN_STABLE_POLLS
