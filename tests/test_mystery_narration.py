"""Offline narration tests: the LLM can only retell engine facts (#684)."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import mystery_narration
from docich.mystery_case import generate
from docich.mystery_narration import (
    MAX_SPEECH,
    deduction_text,
    generate_text,
    investigate_text,
    intro_text,
    narration_agents,
    prediction_result_text,
    publish,
    reveal_text,
    sanitize,
)


def _g(tmp_path, toml=""):
    config = tmp_path / "docich.toml"
    config.write_text(toml, encoding="utf-8")
    return SimpleNamespace(state_dir=tmp_path / "run", config_path=config)


def test_narration_agents_read_the_configured_chain(tmp_path):
    assert narration_agents(_g(tmp_path)) == ""
    chain = "opencode:mimo-v2.6-flash-free,opencode-go:mimo-v2.6-flash"
    assert narration_agents(
        _g(tmp_path, f'[mystery_corner]\nnarration_agents = "{chain}"\n')) == chain
    assert narration_agents(
        _g(tmp_path, '[mystery_corner]\nnarration_agents = 42\n')) == ""


def test_fallback_texts_are_bounded_and_fact_only():
    case = generate(21)
    intro = intro_text(case)
    assert intro == intro_text(generate(21))  # deterministic
    assert len(intro) <= MAX_SPEECH and case.place in intro and case.victim in intro
    room = case.rooms[0]
    clue = next(c for c in case.clues if c.room_id == room.id)
    text = investigate_text(room.name, clue.text)
    assert len(text) <= MAX_SPEECH and room.name in text and clue.text in text
    assert "見つかりませんでした" in investigate_text(room.name, None)
    suspect = case.suspects[0]
    alibi_text = f"{suspect.name}を尋問しました。"
    spoken = mystery_narration.interrogate_text(suspect.name, suspect.role)
    assert len(spoken) <= MAX_SPEECH and suspect.name in spoken
    assert len(deduction_text()) <= MAX_SPEECH
    reveal = reveal_text(case.culprit.name, case.method, case.motive)
    assert len(reveal) <= MAX_SPEECH and case.culprit.name in reveal
    assert len(alibi_text) <= MAX_SPEECH


def test_reveal_parts_explain_the_whole_case_within_one_utterance():
    """#2757 B: the reveal is spoken as verdict → story → motive → reasoning."""
    from docich.mystery_case import contradiction_text
    from docich.mystery_narration import (
        deduction_summary_text,
        reveal_motive_text,
        reveal_reasoning_text,
        reveal_story_text,
    )

    for seed in (7, 21, 99, 2711):
        case = generate(seed)
        parts = {
            "verdict": reveal_text(case.culprit.name, case.method, case.motive),
            "story": reveal_story_text(case),
            "motive": reveal_motive_text(case),
            "reasoning": reveal_reasoning_text(case),
        }
        for name, text in parts.items():
            assert 0 < len(text) <= MAX_SPEECH, name
            assert text.endswith("。"), name
        # The story names the when/where/what and the method's execution.
        assert case.time_label in parts["story"]
        assert case.method_how in parts["story"]
        assert case.method_evidence in parts["story"]
        assert case.victim in parts["story"]
        # The motive links the culprit to the victim and its evidence.
        assert case.motive in parts["motive"]
        assert case.motive_evidence in parts["motive"]
        assert case.culprit.name in parts["motive"]
        # The reasoning states the broken alibi and clears the rest.
        assert contradiction_text(case)[:20] in parts["reasoning"]
        assert "両立しない" in parts["reasoning"]
        assert f"残る{len(case.suspects) - 1}人" in parts["reasoning"]
        # The deduction phase organises the material instead of a stock line.
        summary = deduction_summary_text(case)
        assert 0 < len(summary) <= MAX_SPEECH
        assert f"{len(case.rooms)}室" in summary
        assert f"{len(case.suspects)}人" in summary
        assert summary != deduction_text()


def test_prediction_result_text_distinguishes_resolve_cancel_and_hit():
    assert "返金" in prediction_result_text(resolved=False, hit=None, culprit_name="X")
    assert "的中" in prediction_result_text(resolved=True, hit=True, culprit_name="X")
    assert "外れ" in prediction_result_text(resolved=True, hit=False, culprit_name="X")


def test_sanitize_rejects_unsafe_or_non_japanese_output():
    long_jp = ("こちらは生成された日本語の読み上げ文です。事件の事実の範囲内で、"
               "視聴者に向けてていねいにお伝えします。")
    assert sanitize(long_jp)
    # Too short / no sentence / unsafe marker / too much ASCII / too long.
    assert sanitize("短い") is None
    assert sanitize("あ" * 50) is None
    assert sanitize("This is an English answer that is long enough. Really. OK.") is None
    assert sanitize("```json\n日本語の長い文章がここにあります。句点もあります。```") is None
    assert sanitize("X" * 400 + "。") is None


def test_generate_text_falls_back_closed(monkeypatch, tmp_path):
    g = _g(tmp_path)
    assert generate_text(g, "", "事実", "固定文") == ("固定文", "fallback")

    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("docich.ai_generate.run_prompt", boom)
    assert generate_text(g, "opencode:x", "事実", "固定文") == ("固定文", "fallback")


def test_generate_text_accepts_sanitized_model_output(monkeypatch, tmp_path):
    g = _g(tmp_path)

    class Result:
        text = ("こちらが生成した日本語の読み上げ文です。事件の確定した事実の範囲内で、"
                "視聴者に向けてていねいにお伝えします。")

    monkeypatch.setattr("docich.ai_generate.run_prompt", lambda *a, **k: Result())
    spoken, kind = generate_text(g, "opencode:x", "事実", "固定文")
    assert kind == "ai" and "生成した" in spoken

    Result.text = "English only output without any Japanese characters at all. OK."
    assert generate_text(g, "opencode:x", "事実", "固定文") == ("固定文", "fallback")


def test_publish_counts_sinks_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(mystery_narration, "resolve_soren_root", lambda _g: tmp_path)
    g = _g(tmp_path)
    sent = []

    def overlay_ok(root, event):
        sent.append(("overlay", event["source_id"]))

    def speech_ok(_g, text, *, context, speaker=""):
        sent.append(("speech", context))

    def overlay_boom(_root, _event):
        raise RuntimeError("queue down")

    result = publish(g, kind="intro", title="t", overlay_body="b", speech_text="s",
                     request_id="req-1", audio_enabled=True,
                     overlay=overlay_ok, speech=speech_ok, now=lambda: 1)
    assert result["overlay"]["sent"] == 1 and result["speech"]["sent"] == 1
    assert sent == [("overlay", "mystery:intro:req-1"), ("speech", "mystery:intro")]

    result = publish(g, kind="reveal", title="t", overlay_body="b", speech_text="s",
                     request_id="req-2", audio_enabled=False,
                     overlay=overlay_boom, speech=speech_ok, now=lambda: 1)
    assert result["overlay"]["failed"] == 1
    assert result["speech"]["skipped"] == 1
