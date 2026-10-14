"""Bounded 同志カード大賞 announcement contracts (#934 PR-5).

No Twitch, overlay queue, audio worker or production write: both sinks are
injected and the ranking is a synthetic design-§6 payload. The owner approval
gate (``ANNOUNCEMENT_ENABLED``) must hold both sinks shut by default.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import doushi_narration as dn
from docich.overlay_queue import OVERLAY_BODY_LIMIT
from doushi_payload_fixtures import make_payload, stored_body


def payload(rows=3):
    data = make_payload()
    if rows == 3:
        return data
    return {key: (value[:rows] if isinstance(value, list) else value)
            for key, value in data.items()}


@pytest.fixture
def env(tmp_path):
    soren = tmp_path / "soren"
    soren.mkdir()
    g = SimpleNamespace(state_dir=tmp_path / "run", webui=SimpleNamespace(soren_root=str(soren)))
    return SimpleNamespace(g=g, soren=soren)


def test_narration_text_covers_all_four_pages_with_the_reviewed_appellation():
    text = dn.narration_text(payload(5))

    assert text.startswith("同志カード大賞の発表です。")
    for opener in dn.PAGE_SPEECH_OPENERS:
        assert opener in text
    assert "第1位、同志alice0、50枚。" in text
    assert "第1位、同志alice0、5000ポイント使用。" in text
    assert "第1位、同志alice0、40種類。" in text
    assert "第1位、カード0、30枚、20人が獲得。" in text
    assert "第4位" not in text  # speech stays on the top three
    assert text.endswith(dn.FOOTER_TEXT)
    assert len(text) <= dn.MAX_SPEECH


def test_narration_text_is_bounded_for_long_names_and_many_rows():
    long_rows = [{"rank": index + 1, "username": "x" * 40, "redemption_count": 99}
                 for index in range(10)]
    data = payload()
    data["by_count"] = long_rows

    text = dn.narration_text(data)

    assert len(text) <= dn.MAX_SPEECH
    assert text.endswith(dn.FOOTER_TEXT)
    assert "第4位" not in text


def test_narration_text_handles_an_empty_ranking():
    empty = {key: ([] if isinstance(value, list) else value)
             for key, value in payload().items()}
    assert dn.narration_text(empty) == dn.EMPTY_TEXT
    assert dn.narration_text({}) == dn.EMPTY_TEXT
    assert dn.narration_text(None) == dn.EMPTY_TEXT


def test_overlay_body_labels_the_period_and_bounds_each_page():
    body = dn.overlay_body(payload(8))

    lines = body.splitlines()
    assert lines[0] == "集計期間 全期間 / 全 128 種類"
    assert lines[1] == "■ 排出枚数"
    assert lines[2].startswith("1位 同志alice0 50枚")
    assert "1位 カード0 30枚（20人が獲得）" in body
    assert body.count("■ ") == 4
    assert len(body) <= OVERLAY_BODY_LIMIT

    narrowed = dn.overlay_body({**payload(), "period": "7d"})
    assert narrowed.startswith("集計期間 直近7日 / 全 128 種類")


def test_overlay_body_reports_an_empty_board_without_rows():
    body = dn.overlay_body({**payload(), "period": "30d",
                            **{key: [] for key in
                               ("by_count", "by_points", "by_unique", "popular_cards")}})

    assert body == "集計期間 直近30日 / 全 128 種類\nランキング対象の同志はまだいません"
    assert dn.overlay_body(None).endswith("ランキング対象の同志はまだいません")


def test_announcement_is_held_shut_until_the_owner_approves(env, monkeypatch):
    events, speeches = [], []

    result = dn.announce(
        env.g, payload(3), request_id="11111111-1111-4111-8111-111111111111",
        audio_enabled=True,
        overlay=lambda root, event: events.append((root, event)),
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert dn.ANNOUNCEMENT_ENABLED is False
    assert result == {"overlay": "skipped", "speech": "skipped"}
    assert events == [] and speeches == []


def test_approving_the_flag_publishes_exactly_one_overlay_event_and_opts_into_speech(
        env, monkeypatch):
    events, speeches = [], []
    monkeypatch.setattr(dn, "ANNOUNCEMENT_ENABLED", True)

    result = dn.announce(
        env.g, payload(3), request_id="11111111-1111-4111-8111-111111111111",
        overlay=lambda root, event: events.append((root, event)),
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert result == {"overlay": "sent", "speech": "skipped"}
    assert speeches == []
    (root, event), = events
    assert root == env.soren
    assert event == {
        "ts": 1000, "category": "prediction", "level": "info", "title": "同志カード大賞",
        "body": dn.overlay_body(payload(3)),
        "source_id": "doushi:11111111-1111-4111-8111-111111111111",
    }

    spoken = dn.announce(
        env.g, payload(3), request_id="11111111-1111-4111-8111-111111111111", audio_enabled=True,
        overlay=lambda root, event: None,
        speech=lambda g, text, **kwargs: speeches.append((text, kwargs)),
        now=lambda: 1000.0)

    assert spoken == {"overlay": "sent", "speech": "sent"}
    (text, kwargs), = speeches
    assert text == dn.narration_text(payload(3))
    assert kwargs == {"context": "doushi:ranking", "speaker": ""}


def test_announce_never_raises_on_a_sink_failure(env, monkeypatch):
    monkeypatch.setattr(dn, "ANNOUNCEMENT_ENABLED", True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("SECRET-SINK-BODY")

    failed = dn.announce(env.g, payload(3), request_id="r1", audio_enabled=True,
                         overlay=boom, speech=boom, now=lambda: 5.0)

    assert failed == {"overlay": "failed", "speech": "failed"}


def test_announcement_reads_only_the_transient_runtime_payload(tmp_path, monkeypatch):
    state = tmp_path / "run"
    runtime_id = "g4-a1b2c3d4"
    runtime_dir = state / "runtimes" / runtime_id
    runtime_dir.mkdir(parents=True)
    (runtime_dir / dn.PAYLOAD_FILENAME).write_bytes(stored_body())
    (state / "doushi_corner.json").write_text(json.dumps({
        "status": "announcing",
        "doushi_runtime_identity": {
            "game": "doushi-view", "runtime_id": runtime_id,
            "generation": 4, "lease_id": "11111111-1111-4111-8111-111111111111",
        },
    }), encoding="utf-8")
    g = SimpleNamespace(state_dir=state, webui=SimpleNamespace(soren_root=str(tmp_path)))
    seen = []
    monkeypatch.setattr(dn, "append_event", lambda root, event: seen.append(event))
    monkeypatch.setattr(dn, "enqueue_audio_text", lambda *a, **k: None)

    assert dn._load_payload(g)["card_type_total"] == 128
    dn.announcement(g, request_id="r2", now=lambda: 7.0)
    assert seen == []  # still owner-gated

    monkeypatch.setattr(dn, "ANNOUNCEMENT_ENABLED", True)
    dn.announcement(g, request_id="r2", now=lambda: 7.0)
    assert len(seen) == 1 and seen[0]["category"] == "prediction"
    assert "128 種類" in seen[0]["body"]


@pytest.mark.parametrize("damage", ["missing-state", "missing-runtime", "missing-file",
                                    "broken-json", "no-identity", "unwrapped"])
def test_announcement_never_falls_back_to_a_previous_ranking(tmp_path, monkeypatch, damage):
    state = tmp_path / "run"
    runtime_id = "g4-a1b2c3d4"
    runtime_dir = state / "runtimes" / runtime_id
    runtime_dir.mkdir(parents=True)
    identity = {"game": "doushi-view", "runtime_id": runtime_id,
                "generation": 4, "lease_id": "11111111-1111-4111-8111-111111111111"}
    (runtime_dir / dn.PAYLOAD_FILENAME).write_bytes(stored_body())
    if damage == "missing-file":
        (runtime_dir / dn.PAYLOAD_FILENAME).unlink()
    elif damage == "broken-json":
        (runtime_dir / dn.PAYLOAD_FILENAME).write_text("{broken", encoding="utf-8")
    elif damage == "unwrapped":
        (runtime_dir / dn.PAYLOAD_FILENAME).write_bytes(
            json.dumps({"by_count": []}).encode("utf-8"))
    elif damage == "missing-runtime":
        identity = {**identity, "runtime_id": "g5-other"}
    if damage == "no-identity":
        (state / "doushi_corner.json").write_text(json.dumps({}), encoding="utf-8")
    elif damage != "missing-state":
        (state / "doushi_corner.json").write_text(
            json.dumps({"doushi_runtime_identity": identity}), encoding="utf-8")
    g = SimpleNamespace(state_dir=state, webui=SimpleNamespace(soren_root=str(tmp_path)))
    monkeypatch.setattr(dn, "ANNOUNCEMENT_ENABLED", True)
    seen = []
    monkeypatch.setattr(dn, "append_event", lambda root, event: seen.append(event))

    assert dn._load_payload(g) is None
    assert dn.announcement(g, request_id="r3", now=lambda: 1.0) == {
        "overlay": "skipped", "speech": "skipped"}
    assert seen == []


def test_the_payload_file_never_carries_the_endpoint(tmp_path):
    body = Path(tmp_path / dn.PAYLOAD_FILENAME)
    body.write_bytes(stored_body())

    assert "http" not in body.read_text(encoding="utf-8")
