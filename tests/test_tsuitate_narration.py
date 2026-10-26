"""衝立将棋 narration: the corner introduction and the bounded 感想戦.

Both sinks are injected and the review facts are synthetic: no overlay queue, no
audio worker, no network and no production write happens here.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import tsuitate_narration as tn
from docich.overlay_queue import OVERLAY_BODY_LIMIT

SUMMARY = {
    "gameId": "fixtureGame01",
    "color": "sente",
    "outcome": "loss",
    "reason": "foul_limit",
    "moveCount": 50,
    "ownMoves": 25,
    "opponentMoves": 25,
    "own": {"moves": 25, "totalMs": 9169, "maxMs": 1561, "medianMs": 309},
    "opponent": {"moves": 25, "totalMs": 65641, "maxMs": 3418, "medianMs": 3001},
    "fouls": 10,
    "opponentFouls": 0,
    "foulAttempts": 10,
    "ratingBefore": 1005,
    "ratingAfter": 1003,
    "opponentName": "kakuren_bot",
    "lastMove": {"usi": "5g5h", "by": "gote", "ms": 3059},
    "promotions": 8,
    "drops": 13,
}

TERMINAL = {
    "gameId": "fixtureGame01", "outcome": "loss", "reason": "foul_limit",
    "endedAt": "2026-10-14T20:38:05.153Z", "moveNumber": 50,
    "resultConfidence": "verified", "durationMs": 76410,
}


@pytest.fixture
def env(tmp_path):
    soren = tmp_path / "soren"
    soren.mkdir()
    g = SimpleNamespace(
        state_dir=tmp_path / "run", config_path=tmp_path / "docich.toml",
        webui=SimpleNamespace(soren_root=str(soren)),
    )
    return SimpleNamespace(g=g, soren=soren)


def test_intro_explains_the_corner_and_the_game(env):
    text = tn.INTRO_TEXT

    assert "AI衝立将棋" in text
    assert "相手の駒が見えない" in text
    assert "反則" in text and "10回" in text
    assert "300秒" in text and "3秒" in text
    assert "感想戦" in text
    assert len(text) <= tn.MAX_SPEECH
    assert len(tn.INTRO_OVERLAY_BODY) <= OVERLAY_BODY_LIMIT


def test_facts_text_carries_only_reviewed_facts():
    facts = tn.facts_text(SUMMARY, TERMINAL)

    assert "終局理由: 反則負け" in facts
    assert "総手数: 50手" in facts
    assert "AIの手番: 先手" in facts
    assert "AIの反則: 10回 / 相手の反則: 0回" in facts
    assert "合計9秒" in facts and "最長2秒" in facts
    assert "1005から1003" in facts
    assert "対戦相手: kakuren_bot" in facts
    # The raw move list, the board and the model prompt never appear.
    for forbidden in ("usi", "5g5h", "sfen", "SFEN", "P*5b"):
        assert forbidden not in facts


def test_facts_text_survives_a_missing_replay():
    facts = tn.facts_text(None, TERMINAL)

    assert "終局理由: 反則負け" in facts and "総手数: 50手" in facts
    assert "対戦相手" not in facts


def test_prompt_uses_the_facts_and_forbids_invention():
    prompt = tn.build_prompt(SUMMARY, TERMINAL)

    assert "衝立将棋" in prompt
    assert "終局理由: 反則負け" in prompt
    assert "推測して書かない" in prompt
    assert "駒の種類・捕獲・盤面" in prompt
    assert "5g5h" not in prompt


@pytest.mark.parametrize("text", [
    "AIは反則を重ねてしまいましたが、持ち時間の使い方は落ち着いていました。次は反則を減らして勝ちを狙います。",
    "相手の駒が見えない中で、よく粘った一局でした。最後は反則が重なりましたが、次につながる内容でした。",
])
def test_sanitize_accepts_one_spoken_paragraph(text):
    assert tn.sanitize(text) == text


@pytest.mark.parametrize("text", [
    None, 7, "", "短い。",
    "あ" * (tn.MAX_SPEECH + 1),
    "This is an assistant answer with thinking and reasoning and a URL https://example.com and more text to pass.",
    "<p>HTMLは読み上げない</p>" + "あ" * 80,
])
def test_sanitize_rejects_unusable_output(text):
    assert tn.sanitize(text) is None


def test_sanitize_requires_a_sentence_break():
    assert tn.sanitize("あ" * 80) is None


def test_sanitize_dedups_repeated_sentences_in_model_output():
    # The same idea stated twice is spoken once (#2737); what remains must
    # still stand on its own as one bounded paragraph.
    repeated = ("相手の駒が見えない中で最後まで粘り、反則を減らす課題が残りました。"
                "相手の駒が見えない中で最後まで粘り、反則を減らす課題が残りました。"
                "次の対局もぜひご覧ください。")

    assert tn.sanitize(repeated) == ("相手の駒が見えない中で最後まで粘り、反則を減らす課題が残りました。"
                                     "次の対局もぜひご覧ください。")


def test_sanitize_rejects_output_that_is_only_repetition():
    assert tn.sanitize("同じ文の繰り返しです。" * 6) is None


def test_status_facts_project_only_verified_public_facts():
    facts = tn.status_facts(SUMMARY)

    assert facts == {"rating": "1005→1003", "clock": "AI計9秒 / 相手計66秒",
                     "fouls": "AI10回 / 相手0回", "shapes": "成り8回 / 打ち13回"}

    # Without the replay summary there is nothing to add to the result line.
    assert tn.status_facts(None) is None
    assert tn.status_facts({"own": None}) == {
        "clock": "AI計0秒 / 相手計0秒"}


def test_fallback_text_is_bounded_and_names_the_reason():
    text = tn.fallback_text(SUMMARY, TERMINAL)

    assert text.startswith("感想戦です。")
    assert "50手" in text and "反則負け" in text
    assert "kakuren_bot" in text
    assert len(text) <= tn.MAX_SPEECH

    result_only = tn.fallback_text(None, TERMINAL)
    assert "公開棋譜を確認できなかった" in result_only
    assert len(result_only) <= tn.MAX_SPEECH


def test_review_text_prefers_the_model_and_falls_back(env):
    good = "AIは反則を重ねてしまいましたが、持ち時間の使い方は落ち着いていました。次は反則を減らして勝ちを狙います。"
    text, kind, failure = tn.review_text(env.g, SUMMARY, TERMINAL, agents="fixture:chain",
                                         generate_fn=lambda *_: good)
    assert (text, kind, failure) == (good, "ai", "none")

    # Unsafe or missing model output falls back to the fixed text.
    for bad in ("thinking...", "", "短い。"):
        text, kind, failure = tn.review_text(env.g, SUMMARY, TERMINAL, agents="fixture:chain",
                                             generate_fn=lambda *_args, _bad=bad: _bad)
        assert kind == "fallback" and text == tn.fallback_text(SUMMARY, TERMINAL)
        assert failure == "unsafe"

    def boom(*_args):
        raise RuntimeError("model down")

    text, kind, failure = tn.review_text(env.g, SUMMARY, TERMINAL, agents="fixture:chain",
                                         generate_fn=boom)
    assert (kind, failure) == ("fallback", "exception")

    # No configured chain means no billed call at all.
    text, kind, failure = tn.review_text(env.g, SUMMARY, TERMINAL, agents="")
    assert (kind, failure) == ("fallback", "none")


def test_model_failure_maps_the_dispatcher_kinds():
    assert tn._model_failure("gate_giveup") == "gate"
    assert tn._model_failure("timeout") == "timeout"
    assert tn._model_failure("rate_limit") == "rate_limit"
    assert tn._model_failure("provider_failed") == "provider"
    assert tn._model_failure(None) == "provider"
    assert tn.MODEL_FAILURES == ("none", "unsafe", "gate", "timeout", "rate_limit",
                                 "provider", "exception")


def test_review_agents_reads_only_the_tsuitate_section(env):
    env.g.config_path.write_text(
        "[tsuitate_corner]\nreview_agents = \"opencode-go:fixture-free\"\n"
        "[external_video_corner]\nclosing_agents = \"never-read\"\n",
        encoding="utf-8",
    )
    assert tn.review_agents(env.g) == "opencode-go:fixture-free"

    env.g.config_path.write_text("[tsuitate_corner]\nreview_agents = 7\n", encoding="utf-8")
    assert tn.review_agents(env.g) == ""
    env.g.config_path.write_text("not toml", encoding="utf-8")
    assert tn.review_agents(env.g) == ""
    env.g.config_path = env.g.config_path.parent / "missing.toml"
    assert tn.review_agents(env.g) == ""


def test_announce_intro_publishes_both_sinks_once(env):
    overlay, speech = [], []
    result = tn.announce_intro(
        env.g, request_id="req-1", audio_enabled=True,
        overlay=lambda root, event: overlay.append(event),
        speech=lambda g, text, *, context, speaker: speech.append((text, context)),
        now=lambda: 1234.0,
    )

    assert result["overlay"] == {"sent": 1, "failed": 0, "skipped": 0}
    assert result["speech"] == {"sent": 1, "failed": 0, "skipped": 0}
    assert overlay[0]["category"] == "prediction"
    assert overlay[0]["source_id"] == "tsuitate:intro:req-1"
    assert overlay[0]["title"] == tn.INTRO_TITLE
    assert speech == [(tn.INTRO_TEXT, tn.INTRO_CONTEXT)]


def test_announce_review_is_skipped_when_speech_is_off(env):
    overlay, speech = [], []
    result = tn.announce_review(
        env.g, "感想戦の本文です。", request_id="req-2", body="結果の行\n感想戦の本文です。",
        audio_enabled=False,
        overlay=lambda root, event: overlay.append(event),
        speech=lambda *args, **kwargs: speech.append(args),
        now=lambda: 1234.0,
    )

    assert result["overlay"]["sent"] == 1
    assert result["speech"] == {"sent": 0, "failed": 0, "skipped": 1}
    assert speech == []
    assert overlay[0]["source_id"] == "tsuitate:review:req-2"
    assert overlay[0]["body"] == "結果の行\n感想戦の本文です。"


def test_announcement_sink_failures_never_raise(env):
    def boom(*_args, **_kwargs):
        raise RuntimeError("queue down")

    result = tn.announce_intro(env.g, request_id="req-3", audio_enabled=True,
                               overlay=boom, speech=boom)

    assert result["overlay"] == {"sent": 0, "failed": 1, "skipped": 0}
    assert result["speech"] == {"sent": 0, "failed": 1, "skipped": 0}


def test_overlay_body_is_bounded_and_prefixed_with_the_result():
    body = tn.overlay_body("あ" * tn.MAX_SPEECH, TERMINAL)

    assert body.startswith("AIの負け / 反則負け / 50手")
    assert len(body) <= OVERLAY_BODY_LIMIT


def test_review_label_is_a_dispatch_legal_identifier():
    """The shared dispatcher only accepts COMMENT/RADIO labels.

    A label that fails `validate_label` would make every 感想戦 fall back to the
    fixed text, so the contract is pinned here.
    """
    from docich.llm.policy import validate_label

    assert validate_label(tn.REVIEW_LABEL) == tn.REVIEW_LABEL
    assert tn.REVIEW_LABEL.startswith("RADIO")


# --- 感想戦前のオーディオ境界待ち -----------------------------------------

def _queue(tmp_path, *, queued=0, playing=0):
    directory = tmp_path / "queue"
    directory.mkdir(exist_ok=True)
    for index in range(queued):
        (directory / f"comment_{index}.txt").write_text("x", encoding="utf-8")
    for index in range(playing):
        (directory / f"comment_p{index}.playing").write_text("x", encoding="utf-8")
    return directory


def test_boundary_wait_returns_quiet_immediately(env, tmp_path):
    directory = _queue(tmp_path)
    calls = []
    outcome = tn.wait_for_audio_boundary(
        env.g, queue_dir=directory, sleep=calls.append,
        monotonic=lambda: 0.0)
    assert outcome == "quiet"
    assert calls == []  # already quiet: no polling at all


def test_boundary_wait_polls_until_the_queue_drains(env, tmp_path):
    directory = _queue(tmp_path, queued=1, playing=1)
    clock = {"now": 0.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds
        if len(sleeps) == 2:  # the worker finishes after two polls
            for entry in directory.iterdir():
                entry.unlink()

    outcome = tn.wait_for_audio_boundary(
        env.g, queue_dir=directory, sleep=sleep, monotonic=lambda: clock["now"],
        poll_s=1.0, timeout_s=10.0)
    assert outcome == "quiet"
    assert len(sleeps) == 2


def test_boundary_wait_is_bounded_and_fails_open(env, tmp_path):
    directory = _queue(tmp_path, playing=1)
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    outcome = tn.wait_for_audio_boundary(
        env.g, queue_dir=directory, sleep=sleep, monotonic=lambda: clock["now"],
        poll_s=1.0, timeout_s=5.0)
    assert outcome == "timeout"
    assert clock["now"] >= 5.0


def test_boundary_wait_treats_a_missing_or_unresolvable_queue_as_quiet(env, tmp_path):
    # A missing queue is quiet, exactly like the corner-entry boundary.
    assert tn.wait_for_audio_boundary(
        env.g, queue_dir=tmp_path / "missing", monotonic=lambda: 0.0) == "quiet"

    # An unresolvable Soren root is reported, never raised.
    class _NoRoot:
        state_dir = tmp_path

    assert tn.wait_for_audio_boundary(
        _NoRoot(), queue_dir=None, monotonic=lambda: 0.0) == "unavailable"


def test_boundary_vocabulary_is_fixed():
    assert tn.BOUNDARY_OUTCOMES == ("quiet", "timeout", "unavailable", "skipped")
    assert tn.REVIEW_BOUNDARY_WAIT_S > 0
