"""Bounded 予想王 announcement contracts (#933 PR-4).

No Twitch, overlay queue, audio worker or production write: both sinks are
injected and the board is a synthetic projection.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import yoso_narration as yn
from docich.overlay_queue import OVERLAY_BODY_LIMIT


def board(rows=3, *, rounds=8, viewers=12, min_rounds=3):
    ranking = [
        {"rank": index + 1, "user_name": f"同志名{index}", "hit_rate": 0.8 - index * 0.1,
         "rounds": 10 - index, "hits": 7 - index, "used": 100, "won": 300 + index,
         "net": 200 + index}
        for index in range(rows)
    ]
    return {"ok": True, "rounds": rounds, "viewers": viewers, "min_rounds": min_rounds,
            "limit": 10, "ranking": ranking}


@pytest.fixture
def env(tmp_path):
    soren = tmp_path / "soren"
    soren.mkdir()
    g = SimpleNamespace(state_dir=tmp_path / "run", webui=SimpleNamespace(soren_root=str(soren)))
    return SimpleNamespace(g=g, soren=soren)


def test_narration_text_names_the_top_three_with_the_reviewed_appellation():
    text = yn.narration_text(board(5))

    assert text.startswith("予想王の発表です。")
    assert "第1位、同志同志名0、的中率80パーセント、10回中7回的中、純増200ポイント。" in text
    assert "第3位、同志同志名2" in text
    assert "第4位" not in text
    assert text.endswith(yn.FOOTER_TEXT)
    assert len(text) <= yn.MAX_SPEECH


def test_narration_text_is_bounded_for_long_names_and_many_rows():
    rows = [{"rank": index + 1, "user_name": "x" * 24, "hit_rate": 1.0, "rounds": 99,
             "hits": 99, "net": -12345} for index in range(10)]

    text = yn.narration_text(board(10))

    assert len(text) <= yn.MAX_SPEECH
    assert "第4位" not in text


def test_narration_text_handles_an_empty_ranking():
    assert yn.narration_text(board(0)) == yn.EMPTY_TEXT
    assert yn.narration_text({}) == yn.EMPTY_TEXT
    assert yn.narration_text(None) == yn.EMPTY_TEXT


def test_overlay_body_counts_and_ranks_within_the_queue_limit():
    body = yn.overlay_body(board(8))

    lines = body.splitlines()
    assert lines[0] == "確定 8 予想 / 集計対象 12 名（最小参加数 3）"
    assert lines[1].startswith("1位 同志同志名0 80% 10回中7回 純増200")
    assert len(lines) == 6  # counts + top five
    assert len(body) <= OVERLAY_BODY_LIMIT
    assert "ランキング対象の同志はまだいません" in yn.overlay_body(board(0))


def test_announce_publishes_one_overlay_event_and_opts_into_speech(env):
    events, speeches = [], []

    result = yn.announce(
        env.g, board(3), request_id="11111111-1111-4111-8111-111111111111",
        overlay=lambda root, event: events.append((root, event)),
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert result == {"overlay": "sent", "speech": "skipped"}
    assert speeches == []
    (root, event), = events
    assert root == env.soren
    assert event == {
        "ts": 1000, "category": "prediction", "level": "info", "title": "予想王",
        "body": yn.overlay_body(board(3)),
        "source_id": "yosou:11111111-1111-4111-8111-111111111111",
    }

    spoken = yn.announce(
        env.g, board(3), request_id="11111111-1111-4111-8111-111111111111", audio_enabled=True,
        overlay=lambda root, event: None,
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert spoken == {"overlay": "sent", "speech": "sent"}
    (text, kwargs), = speeches
    assert text == yn.narration_text(board(3))
    assert kwargs == {"context": "yosou:ranking", "speaker": ""}


def test_announce_never_raises_on_a_sink_failure(env):
    def boom(*_args, **_kwargs):
        raise RuntimeError("SECRET-SINK-BODY")

    failed = yn.announce(env.g, board(3), request_id="r1", audio_enabled=True,
                         overlay=boom, speech=boom, now=lambda: 5.0)

    assert failed == {"overlay": "failed", "speech": "failed"}


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

    assert len(seen) == 1 and seen[0]["category"] == "prediction"
    # min_rounds=3 keeps a one-shot viewer off the board, so the notice is used.
    assert "ランキング対象の同志はまだいません" in seen[0]["body"]
