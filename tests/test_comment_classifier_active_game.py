"""#1243 PR-1: active-game hint contract, fixed vocabulary only.

No live API, no screenshots, no raw game state. Synthetic model replies test
routing/contracts, not Japanese model accuracy.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich import comment_classifier as cc  # noqa: E402
from docich.comment_classifier import active_game, heuristic, jev  # noqa: E402
from docich.semantic_decision import transport as core  # noqa: E402
from docich.semantic_decision.routes import resolve_route  # noqa: E402
from docich.semantic_decision.validator import dumps, validate_response  # noqa: E402

KEYS = {"TYPESAFE_API_KEY": "test-key"}
HINT_ENV = {**KEYS, "COMMENT_ACTIVE_GAME_ENABLED": "1",
            "COMMENT_ACTIVE_GAME_KIND": "sorengame",
            "COMMENT_ACTIVE_GAME_INTERACTION": "board"}
HINT = {"game_active": True, "active_game_kind": "sorengame",
        "interaction_kind": "board"}


@pytest.fixture(autouse=True)
def no_live_process(monkeypatch):
    blocked = Mock(side_effect=AssertionError("no live API process in these tests"))
    monkeypatch.setattr(core, "_bounded_process", blocked)
    return blocked


def rows(*texts):
    return [{"index": i, "user": "viewer", "comment": text,
             "category": "chitchat", "is_english": False}
            for i, text in enumerate(texts or ("右じゃない？",), 1)]


def reply(request, choices=None, confidence=None):
    choices, confidence = choices or {}, confidence or {}
    answers = {}
    for key, question in request["questions"].items():
        choice = choices.get(key, "game_question" if key.startswith("c") else "required")
        labels = question["criteria"]
        answers[key] = {"type": "choice", "choice": choice,
                        "confidence": confidence.get(key, 0.9),
                        "probabilities": {label: 0.9 if label == choice else 0.1 / (len(labels) - 1)
                                          for label in labels}}
    return {"model": request["model"], "answers": answers,
            "usage": {"input_tokens": 1000, "output_tokens": 100}}


def run(batch, tmp_path, *, choices=None, env=None, game_hint=None, status=None):
    def respond(request, cfg, env):
        if status:
            return {"status": status}
        data = validate_response(reply(request, choices), request, resolve_route(cfg.route))
        return {"status": "ok", "data": data}
    mocked = Mock(side_effect=respond)
    output, event = jev.classify(batch, jev.Config(), env or KEYS, tmp_path,
                                 transport=mocked, game_hint=game_hint)
    return output, event, mocked


# --- heuristic regression cases (#1243) -------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ("右じゃない？", "game_question"),
    ("左のほうが良くない？", "strategy_advice"),
    ("そこに置くんじゃない？", "strategy_advice"),
    ("今の何？", "game_question"),
    ("そこ違う", "game_status"),
])
def test_short_game_utterance_resolves_with_hint(text, expected):
    assert heuristic.classify("viewer", text, game_hint=HINT) == expected


@pytest.mark.parametrize("text", [
    "明日の天気は？",
    "右派って何？",
    "こんにちは",
    "このゲームのルールを教えて",
])
def test_non_game_topics_stay_non_game_with_hint(text):
    assert heuristic.classify("viewer", text, game_hint=HINT) in {
        "general_question", "chitchat"}


def test_body_only_without_hint_is_unchanged():
    assert heuristic.classify("viewer", "右じゃない？") == "general_question"
    assert heuristic.classify("viewer", "そこに置くんじゃない？") == "general_question"
    assert heuristic.classify("viewer", "そこ違う") == "chitchat"


def test_stale_or_unknown_kind_degrades_to_body_only():
    assert active_game.settings({"COMMENT_ACTIVE_GAME_ENABLED": "1",
                                 "COMMENT_ACTIVE_GAME_KIND": "mystery-game"})[0] is False
    assert active_game.hint("mystery-game") is None
    assert heuristic.classify("viewer", "右じゃない？",
                              game_hint=active_game.hint("mystery-game")) == "general_question"


def test_flag_off_ignores_kind_tuning():
    enabled, kind, interaction = active_game.settings(
        {"COMMENT_ACTIVE_GAME_ENABLED": "0",
         "COMMENT_ACTIVE_GAME_KIND": "sorengame"})
    assert (enabled, kind) == (False, None)
    assert heuristic.classify("viewer", "右じゃない？", game_hint=None) == "general_question"


def test_invalid_flag_is_invalid_config():
    with pytest.raises(ValueError, match="invalid_config"):
        active_game.settings({"COMMENT_ACTIVE_GAME_ENABLED": "yes"})


def test_explicit_game_categories_are_never_overridden():
    assert heuristic.classify("viewer", "このゲームの戦略を教えて？",
                              game_hint=HINT) == "game_question"
    assert heuristic.classify("viewer", "スコアがすごい",
                              game_hint=HINT) == "game_status"


def test_notifications_keep_protection_with_hint():
    assert heuristic.classify("wizebot", "raid happened",
                              game_hint=HINT) == "raid"


# --- fixed-vocabulary contract ----------------------------------------------

def test_hint_carries_no_raw_state():
    assert set(HINT) == {"game_active", "active_game_kind", "interaction_kind"}
    text = active_game.sentence(HINT)
    assert "sorengame" in text and "screenshot" not in text.lower()
    assert "score" not in text.lower()


@pytest.mark.parametrize("bad", [
    {"game_active": True, "active_game_kind": "sorengame",
     "interaction_kind": "board", "raw_state": {"score": 1}},
    {"game_active": True, "active_game_kind": "sorengame",
     "interaction_kind": "board", "screenshot_path": "/tmp/latest.png"},
    {"game_active": True, "active_game_kind": "mystery-game",
     "interaction_kind": "board"},
    {"game_active": False, "active_game_kind": "sorengame",
     "interaction_kind": "board"},
    {"active_game_kind": "sorengame", "interaction_kind": "board"},
    "sorengame",
])
def test_check_hint_rejects_anything_but_fixed_vocab(bad):
    with pytest.raises(ValueError, match="invalid_config"):
        active_game.check_hint(bad)


# --- Jev request contract ----------------------------------------------------

def test_request_without_hint_is_legacy_identical():
    batch = rows("右じゃない？")
    request = jev.build_request(batch, jev.Config().model)
    assert "active game session" not in request["questions"]["c1"]["instructions"]
    assert set(request["questions"]) == {"c1"}


def test_request_with_hint_adds_fixed_sentence_only():
    batch = rows("右じゃない？")
    plain = jev.build_request(batch, jev.Config().model)
    hinted = jev.build_request(batch, jev.Config().model, game_hint=HINT)
    assert set(hinted["questions"]) == {"c1"}
    assert (hinted["questions"]["c1"]["instructions"]
            == plain["questions"]["c1"]["instructions"] + " " + active_game.sentence(HINT))
    assert hinted["state"] == plain["state"]
    assert len(dumps(hinted).encode()) <= jev.MAX_REQUEST_BYTES


def test_request_with_hint_and_screen_coexist():
    batch = rows("右じゃない？")
    request = jev.build_request(batch, jev.Config().model,
                                screen_enabled=True, game_hint=HINT)
    assert set(request["questions"]) == {"c1", "s1"}
    assert "active game session" in request["questions"]["c1"]["instructions"]


def test_full_batch_with_hint_stays_within_budget():
    batch = rows(*["右じゃない？"] * 8)
    request = jev.build_request(batch, jev.Config().model,
                                screen_enabled=True, game_hint=HINT)
    assert len(dumps(request).encode()) <= jev.MAX_REQUEST_BYTES


# --- Jev classify/event ------------------------------------------------------

def test_classify_with_hint_records_descriptor_and_applied(tmp_path):
    output, event, mocked = run(rows("右じゃない？"), tmp_path, game_hint=HINT)
    assert output[0]["category"] == "game_question"
    assert event["game_hint"] == {"active": True, "kind": "sorengame",
                                  "interaction": "board"}
    assert event["rows"][0]["game_hint_applied"] is True
    sent = mocked.call_args[0][0]
    assert "active game session" in sent["questions"]["c1"]["instructions"]


def test_classify_without_hint_has_no_hint_keys(tmp_path):
    output, event, mocked = run(rows("右じゃない？"), tmp_path)
    assert event["game_hint"] == {"active": False}
    assert "game_hint_applied" not in event["rows"][0]
    sent = mocked.call_args[0][0]
    assert "active game session" not in sent["questions"]["c1"]["instructions"]


def test_failure_keeps_heuristic_and_marks_hint_sent(tmp_path):
    batch = rows("右じゃない？")
    output, event, _ = run(batch, tmp_path, game_hint=HINT, status="timeout")
    assert output[0]["category"] == "chitchat"
    assert event["rows"][0]["game_hint_applied"] is True
    assert event["game_hint"]["active"] is True


def test_run_jev_reads_hint_from_env(tmp_path):
    batch = heuristic.baseline(["viewer: 右じゃない？"])
    output, event = jev.run_jev(batch, env={**HINT_ENV, "COMMENT_CLASSIFIER_BACKEND": "jev",
                                            "COMMENT_CLASSIFIER_JEV_STATE_DIR": str(tmp_path / "s"),
                                            "COMMENT_CLASSIFIER_JEV_METRICS_DIR": str(tmp_path / "m"),
                                            "COMMENT_CLASSIFIER_JEV_LOG_ENABLED": "0"},
                                heuristic_ms=0.1, started=0.0,
                                transport=Mock(return_value={"status": "timeout"}))
    assert event["game_hint"] == {"active": True, "kind": "sorengame",
                                  "interaction": "board"}
    assert event["rows"][0]["game_hint_applied"] is True


def test_run_jev_unknown_kind_degrades_to_body_only(tmp_path):
    env = {**KEYS, "COMMENT_ACTIVE_GAME_ENABLED": "1",
           "COMMENT_ACTIVE_GAME_KIND": "mystery-game",
           "COMMENT_CLASSIFIER_JEV_STATE_DIR": str(tmp_path / "s"),
           "COMMENT_CLASSIFIER_JEV_METRICS_DIR": str(tmp_path / "m"),
           "COMMENT_CLASSIFIER_JEV_LOG_ENABLED": "0"}
    batch = heuristic.baseline(["viewer: 右じゃない？"])
    output, event = jev.run_jev(batch, env=env, heuristic_ms=0.1, started=0.0,
                                transport=Mock(return_value={"status": "timeout"}))
    assert event["game_hint"] == {"active": False}
    assert output[0]["category"] == "general_question"


def test_classify_file_applies_hint_to_baseline(tmp_path):
    source = tmp_path / "batch.txt"
    source.write_text("viewer: 右じゃない？\n", encoding="utf-8")
    rows_plain, _ = cc.classify_file(source, env=dict(KEYS))
    rows_hinted, _ = cc.classify_file(source, env=dict(HINT_ENV))
    assert rows_plain[0]["category"] == "general_question"
    assert rows_hinted[0]["category"] == "game_question"
    assert set(rows_hinted[0]) == set(rows_plain[0])
