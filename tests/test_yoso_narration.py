"""Bounded 予想王 announcement contracts (#933 PR-4).

No Twitch, overlay queue, audio worker or production write: both sinks are
injected and the board is a synthetic projection.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import prediction_leaderboard as lb
from docich import yoso_narration as yn
from docich.overlay_queue import OVERLAY_BODY_LIMIT


def board(rows=3, *, rounds=8, viewers=12, min_rounds=3):
    ranking = [
        {"rank": index + 1, "user_name": f"同志名{index}", "hit_rate": 0.8 - index * 0.1,
         "rounds": 10 - index, "hits": 7 - index, "used": 100 + index, "won": 300 + index,
         "lost": 40 + index, "participations": 12 - index, "net": 200 + index}
        for index in range(rows)
    ]
    # The screen lists these same rows per department (`/api/yosou` boards).
    boards = {metric: [dict(row) for row in ranking] for metric in lb.METRICS}
    return {"ok": True, "rounds": rounds, "viewers": viewers, "min_rounds": min_rounds,
            "limit": 10, "ranking": ranking, "boards": boards}


@pytest.fixture
def env(tmp_path):
    soren = tmp_path / "soren"
    soren.mkdir()
    g = SimpleNamespace(state_dir=tmp_path / "run", webui=SimpleNamespace(soren_root=str(soren)))
    return SimpleNamespace(g=g, soren=soren)


def test_narration_text_names_the_top_three_with_the_reviewed_appellation():
    text = yn.narration_text(board(5))

    assert text.startswith("予想的中率の部門です。")
    assert "第1位、同志同志名0、的中率80パーセント、10回中7回的中。" in text
    assert "第3位、同志同志名2" in text
    assert "第4位" not in text
    assert len(text) <= yn.MAX_SPEECH


def test_each_department_reads_its_own_record_in_screen_order():
    data = board(3)
    texts = {metric: yn.narration_text(data, metric=metric) for metric in lb.METRICS}

    assert tuple(texts) == ("hit_rate", "staked", "won", "lost", "plays")
    assert texts["staked"] == (
        "賭けたポイント数の部門です。第1位、同志同志名0、100ポイント賭けました。"
        "第2位、同志同志名1、101ポイント賭けました。第3位、同志同志名2、102ポイント賭けました。")
    assert "300ポイント獲得" in texts["won"] and "得たポイント数の部門です。" in texts["won"]
    assert "40ポイント失いました" in texts["lost"]
    assert "12回。" in texts["plays"] and "賭けた回数の部門です。" in texts["plays"]
    # Same-text suppression in the shared sink must never merge two departments.
    assert len(set(texts.values())) == len(texts)
    assert all(len(text) <= yn.MAX_SPEECH for text in texts.values())


def test_a_department_reads_the_rows_the_screen_lists_for_it():
    data = board(3)
    data["boards"]["won"] = [dict(data["boards"]["won"][2], rank=1)]

    text = yn.narration_text(data, metric="won")

    assert "第1位、同志同志名2、302ポイント獲得。" in text
    assert "同志名0" not in text and "第2位" not in text


def test_narration_text_is_bounded_for_long_names_and_many_rows():
    rows = [{"rank": index + 1, "user_name": "x" * 24, "hit_rate": 1.0, "rounds": 99,
             "hits": 99, "used": 99999, "won": 99999, "lost": 99999,
             "participations": 999, "net": -12345} for index in range(10)]
    data = {"boards": {metric: rows for metric in lb.METRICS}}

    for metric in lb.METRICS:
        text = yn.narration_text(data, metric=metric)
        assert len(text) <= yn.MAX_SPEECH
        assert "第4位" not in text


def test_narration_text_handles_an_empty_department_with_a_distinct_notice():
    assert yn.narration_text(board(0)) == yn.empty_text("hit_rate")
    assert yn.narration_text({}) == yn.empty_text("hit_rate")
    assert yn.narration_text(None) == yn.empty_text("hit_rate")
    notices = {yn.narration_text(None, metric=metric) for metric in lb.METRICS}
    assert len(notices) == len(lb.METRICS)
    assert "失ったポイント数の部門です。" in yn.narration_text(None, metric="lost")


def test_a_pre_boards_payload_still_reads_the_hit_rate_ranking():
    legacy = board(2)
    del legacy["boards"]

    assert "第2位、同志同志名1" in yn.narration_text(legacy)
    assert yn.narration_text(legacy, metric="won") == yn.empty_text("won")


def test_overlay_body_counts_and_ranks_within_the_queue_limit():
    body = yn.overlay_body(board(8))

    lines = body.splitlines()
    assert lines[0] == "確定 8 予想 / 集計対象 12 名（最小参加数 3）"
    assert lines[1].startswith("1位 同志同志名0 80% 10回中7回 純増200")
    assert len(lines) == 6  # counts + top five
    assert len(body) <= OVERLAY_BODY_LIMIT
    assert "ランキング対象の同志はまだいません" in yn.overlay_body(board(0))


def test_intro_is_bounded_and_keeps_its_own_queue_identity():
    assert len(yn.INTRO_TEXT) <= yn.MAX_SPEECH
    assert len(yn.INTRO_OVERLAY_BODY) <= OVERLAY_BODY_LIMIT
    assert yn.INTRO_CONTEXT == "yosou:intro"
    assert yn.OVERLAY_KINDS == ("intro", "ranking")
    assert [kind for kind, _context in yn.SPEECH_ITEMS] == [
        "intro", "hit_rate", "staked", "won", "lost", "plays"]
    assert [context for _kind, context in yn.SPEECH_ITEMS] == [
        "yosou:intro", "yosou:board:hit_rate", "yosou:board:staked",
        "yosou:board:won", "yosou:board:lost", "yosou:board:plays"]
    # `audio-complete` waits on every item; a context missing here would let the
    # corner end before its department is read.
    assert yn.VOICE_CONTEXTS == tuple(context for _kind, context in yn.SPEECH_ITEMS)


def test_announce_publishes_the_intro_and_the_ranking(env):
    events, speeches = [], []
    request_id = "11111111-1111-4111-8111-111111111111"

    result = yn.announce(
        env.g, board(3), request_id=request_id,
        overlay=lambda root, event: events.append((root, event)),
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert result == {"overlay": {"sent": 2, "failed": 0, "skipped": 0},
                      "speech": {"sent": 0, "failed": 0, "skipped": 6},
                      "overlay_events": 2, "speech_items": 6}
    assert speeches == []
    assert [root for root, _event in events] == [env.soren, env.soren]
    intro, ranking = (event for _root, event in events)
    # The shared queue keeps only the newest event per source_id, so the intro
    # and the ranking must not share one.
    assert intro == {
        "ts": 1000, "category": "prediction", "level": "info", "title": yn.INTRO_TITLE,
        "body": yn.INTRO_OVERLAY_BODY, "source_id": f"yosou:intro:{request_id}",
    }
    assert ranking == {
        "ts": 1000, "category": "prediction", "level": "info", "title": "予想王",
        "body": yn.overlay_body(board(3)), "source_id": f"yosou:ranking:{request_id}",
    }

    spoken = yn.announce(
        env.g, board(3), request_id=request_id, audio_enabled=True,
        overlay=lambda root, event: events.append((root, event)),
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert spoken == {"overlay": {"sent": 2, "failed": 0, "skipped": 0},
                      "speech": {"sent": 6, "failed": 0, "skipped": 0},
                      "overlay_events": 2, "speech_items": 6}
    # Intro first, then one item per department in screen order (the audio queue
    # is first-in first-out for these file names).
    assert [text for text, _kwargs in speeches] == [yn.INTRO_TEXT] + [
        yn.narration_text(board(3), metric=metric) for metric in lb.METRICS]
    assert [kwargs for _text, kwargs in speeches] == [
        {"context": "yosou:intro", "speaker": ""},
        {"context": "yosou:board:hit_rate", "speaker": ""},
        {"context": "yosou:board:staked", "speaker": ""},
        {"context": "yosou:board:won", "speaker": ""},
        {"context": "yosou:board:lost", "speaker": ""},
        {"context": "yosou:board:plays", "speaker": ""},
    ]


def test_announce_never_raises_on_a_sink_failure(env):
    def boom(*_args, **_kwargs):
        raise RuntimeError("SECRET-SINK-BODY")

    failed = yn.announce(env.g, board(3), request_id="r1", audio_enabled=True,
                         overlay=boom, speech=boom, now=lambda: 5.0)

    assert failed == {"overlay": {"sent": 0, "failed": 2, "skipped": 0},
                      "speech": {"sent": 0, "failed": 6, "skipped": 0},
                      "overlay_events": 2, "speech_items": 6}


def test_announcement_reads_the_state_dir_ledger(tmp_path, monkeypatch):
    from docich import prediction_leaderboard as lb

    state = tmp_path / "run"
    state.mkdir()
    ledger = lb.new_ledger()
    assert lb.record(ledger, {
        "id": "p1", "title": "t", "status": "RESOLVED", "winning_outcome_id": "o1",
        "ended_at": "2026-10-14T00:00:00Z",
        "outcomes": [
            {"id": "o1", "title": "a", "users": 1, "channel_points": 10,
             "top_predictors": [{"user_id": "u1", "user_login": "alice", "user_name": "Alice",
                                 "channel_points_used": 10, "channel_points_won": 20}]},
            {"id": "o2", "title": "b", "users": 1, "channel_points": 10, "top_predictors": None},
        ]}, corner="hanjuku") is True
    lb.save(state / lb.FILE, ledger)
    g = SimpleNamespace(state_dir=state, webui=SimpleNamespace(soren_root=str(tmp_path)))
    seen = []
    monkeypatch.setattr(yn, "append_event", lambda root, event: seen.append(event))
    monkeypatch.setattr(yn, "enqueue_audio_text", lambda *a, **k: None)

    yn.announcement(g, request_id="r2", now=lambda: 7.0)

    assert [event["source_id"] for event in seen] == ["yosou:intro:r2", "yosou:ranking:r2"]
    assert {event["category"] for event in seen} == {"prediction"}
    assert seen[0]["body"] == yn.INTRO_OVERLAY_BODY
    # min_rounds=3 keeps a one-shot viewer off the board, so the notice is used.
    assert "ランキング対象の同志はまだいません" in seen[1]["body"]


def test_voice_queue_dir_defaults_below_the_soren_root(env, monkeypatch):
    monkeypatch.delenv("COMMENT_QUEUE_DIR", raising=False)

    assert yn.voice_queue_dir(env.g) == env.soren / "tmp" / ".comment_queue"


def test_voice_items_in_flight_counts_only_this_corners_markers(env, monkeypatch):
    queue = env.soren / "tmp" / ".comment_queue"
    queue.mkdir(parents=True)
    monkeypatch.setenv("COMMENT_QUEUE_DIR", str(queue))

    # A missing marker is "nothing left to read", including a content-deduplicated
    # enqueue that never wrote a file.
    assert yn.voice_items_in_flight(env.g) == 0

    queued = queue / "comment_announce_1_2_3_yosou:intro.txt"
    queued.write_text("x")
    assert yn.voice_items_in_flight(env.g) == 1

    # The worker renames the item while playing it; that is still in flight.
    playing = queue / "comment_announce_1_2_3_yosou:intro.playing"
    queued.rename(playing)
    assert yn.voice_items_in_flight(env.g) == 1

    (queue / "comment_announce_4_5_6_yosou:board:won.playing").write_text("y")
    # Other sources never extend this corner.
    (queue / "comment_announce_9_9_9_chat.txt").write_text("z")
    (queue / "comment_announce_9_9_9_weather_corner.playing").write_text("w")
    assert yn.voice_items_in_flight(env.g) == 2

    playing.unlink()
    (queue / "comment_announce_4_5_6_yosou:board:won.playing").unlink()
    assert yn.voice_items_in_flight(env.g) == 0

    # An unreadable/missing queue directory is "nothing to wait for".
    (queue / "comment_announce_9_9_9_chat.txt").unlink()
    (queue / "comment_announce_9_9_9_weather_corner.playing").unlink()
    queue.rmdir()
    assert yn.voice_items_in_flight(env.g) == 0
