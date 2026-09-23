import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.config import load_global
from docich.paper_corner import SPEECH_DRAIN_STABLE_POLLS, PaperCornerManager


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


def _write_current_source(tmp_path, content, label, phase="playing"):
    source = tmp_path / "soren/tmp/.say_queue/current_source"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"owner|{phase}|{content}|1000|{label}\n", encoding="utf-8")


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
    # Covered topics are handed back with their opening sentence so the
    # narrator can recognise a repeat even under a new label.
    assert saved["covered_topics"] == ["相場：一つ目のネタです。", "ニュース：二つ目のネタです。"]


def test_covered_topics_keep_every_spoken_segment(tmp_path, monkeypatch):
    """A long corner must not forget its early topics (2026-09-23: the list was
    cut to the latest 24 and a 58-segment corner re-told them)."""
    from docich.trading import corner_script

    mgr, _coord = _manager(
        tmp_path, overlay=lambda g, payload: None, speech=lambda g, text, **kwargs: None,
    )
    queue = [
        {"status": "item", "topic": f"話題{index}", "text": f"{index}番目のネタです。"}
        for index in range(1, 31)
    ] + [{"status": "done"}]
    seen = []

    def fake_next(*args, **kwargs):
        seen.append(list(kwargs.get("covered") or []))
        return queue.pop(0)

    monkeypatch.setattr(corner_script, "generate_next_narration", fake_next)

    assert mgr._run_locked(_starting_state()) == "completed"

    saved = json.loads(mgr.path.read_text())
    assert len(saved["covered_topics"]) == 30
    assert saved["covered_topics"][0] == "話題1：1番目のネタです。"
    # The final generation call still saw the very first topic.
    assert seen[-1][0] == "話題1：1番目のネタです。" and len(seen[-1]) == 30


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

    assert sleeps == [2.0] * SPEECH_DRAIN_STABLE_POLLS, (
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

    assert mgr._run_locked(_starting_state()) == "completed"

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
