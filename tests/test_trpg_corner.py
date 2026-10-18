"""Offline TRPG / murder-mystery corner core contract (Issue #684).

No Twitch, game switch, VM or production writes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docich import trpg_corner as tc


def make_scenario(**kw):
    base = {
        "id": "murder-001",
        "title": "洋館の殺人",
        "genre": "murder_mystery",
        "duration_minutes": 8,
        "scenes": [
            {"id": "s1", "text": "シーン1", "choices": ["Aへ行く", "Bへ行く"],
             "next": {"1": "s2a", "2": "s2b"}},
            {"id": "s2a", "text": "シーン2A", "choices": ["調べる", "戻る"],
             "next": {"1": None, "2": "s1"}},
            {"id": "s2b", "text": "シーン2B", "choices": ["話す", "逃げる"],
             "next": {"1": None, "2": None}},
        ],
        "climax": {"suspects": ["A", "B", "C"], "answer": "B", "trick": "毒", "window_seconds": 120},
    }
    base.update(kw)
    return base


def test_validate_scenario_ok():
    s = tc.validate_scenario(make_scenario())
    assert s["id"] == "murder-001"
    assert s["first_scene"] == "s1"
    assert len(s["scenes"]) == 3


def test_validate_scenario_rejects_bad():
    # missing id
    with pytest.raises(tc.TrpgCornerError):
        tc.validate_scenario({"title": "x", "scenes": [{"id": "s1", "text": "t", "choices": ["a", "b"]}]})
    # too few choices
    with pytest.raises(tc.TrpgCornerError):
        tc.validate_scenario(make_scenario(scenes=[{"id": "s1", "text": "t", "choices": ["a"]}, {"id": "s2", "text": "t2", "choices": ["a", "b"]}, {"id": "s3", "text": "t3", "choices": ["a", "b"]}] ))
    # duplicate scene id
    with pytest.raises(tc.TrpgCornerError):
        tc.validate_scenario(make_scenario(scenes=[
            {"id": "s1", "text": "t", "choices": ["a", "b"]},
            {"id": "s1", "text": "t2", "choices": ["a", "b"]},
        ]))
    # unknown next
    with pytest.raises(tc.TrpgCornerError):
        tc.validate_scenario(make_scenario(scenes=[
            {"id": "s1", "text": "t", "choices": ["a", "b"], "next": {"1": "nope"}},
            {"id": "s2", "text": "t2", "choices": ["a", "b"]},
        ]))
    # climax answer not in suspects
    with pytest.raises(tc.TrpgCornerError):
        tc.validate_scenario(make_scenario(climax={"suspects": ["A", "B"], "answer": "Z", "trick": "x"}))
    # duplicate suspect
    with pytest.raises(tc.TrpgCornerError):
        tc.validate_scenario(make_scenario(climax={"suspects": ["A", "A"], "answer": "A", "trick": "x"}))


def test_state_machine_happy_path():
    st = tc.new_state()
    assert st["phase"] == "IDLE"
    scen = make_scenario()
    tc.start(st, scen)
    assert st["phase"] == "SCENE"
    assert st["scene_id"] == "s1"
    tc.open_choice(st)
    assert st["phase"] == "CHOICE"
    tc.record_vote(st, "u1", 1)
    tc.record_vote(st, "u2", 2)
    tc.record_vote(st, "u1", 2)  # last wins
    assert st["vote_order"]["u1"] == "2"
    assert "u2" in st["votes"].get("2", [])
    tc.close_choice(st)
    assert st["phase"] == "RESOLVE"
    assert st["tally"]["2"] == 2  # u1+u2
    assert st["tally"]["1"] == 0
    # advance to climax (terminal branch s2b has next None)
    tc.advance_scene(st)  # winning choice 2 -> s2b
    assert st["phase"] == "SCENE"
    assert st["scene_id"] == "s2b"
    tc.open_choice(st)
    tc.record_vote(st, "u3", 1)
    tc.close_choice(st)
    # advance: winning 1 -> None -> climax (climax present)
    tc.advance_scene(st)
    assert st["phase"] == "CLIMAX"
    tc.record_accusation(st, "u3", "B", "毒")
    tc.record_accusation(st, "u4", "A", "銃")
    tc.close_climax(st)
    assert st["phase"] == "REVEAL"
    assert st["climax_result"]["answer"] == "B"
    assert st["climax_result"]["correct_count"] == 1
    assert "u3" in st["climax_result"]["correct_users"]
    tc.finish(st)
    assert st["phase"] == "END"
    assert st["outcome"] == "completed"


def test_invalid_transitions():
    st = tc.new_state()
    with pytest.raises(tc.TrpgCornerError):
        tc.open_choice(st)  # not started
    tc.start(st, make_scenario())
    with pytest.raises(tc.TrpgCornerError):
        tc.record_vote(st, "u1", 1)  # SCENE not CHOICE
    with pytest.raises(tc.TrpgCornerError):
        tc.close_choice(st)
    tc.open_choice(st)
    with pytest.raises(tc.TrpgCornerError):
        tc.advance_scene(st)  # CHOICE not RESOLVE
    # invalid choice index
    with pytest.raises(tc.TrpgCornerError):
        tc.record_vote(st, "u1", 0)
    with pytest.raises(tc.TrpgCornerError):
        tc.record_vote(st, "u1", 99)
    # invalid transition IDLE->END
    st2 = tc.new_state()
    with pytest.raises(tc.TrpgCornerError):
        tc._transition(st2, "END")


def test_fail_and_timeout_and_reset():
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.fail(st, "boom")
    assert st["phase"] == "FAILED"
    assert st["failure_reason"] == "boom"
    tc.reset(st)
    assert st["phase"] == "IDLE"
    # timeout path
    st2 = tc.new_state()
    tc.start(st2, make_scenario())
    tc.timeout(st2)
    assert st2["phase"] == "TIMEOUT"
    tc.reset(st2)


def test_parse_vote_and_accusation():
    assert tc.parse_vote("!1") == 1
    assert tc.parse_vote("3") == 3
    assert tc.parse_vote("!vote 2") == 2
    assert tc.parse_vote("hello") is None
    assert tc.parse_vote("!5") is None
    r = tc.parse_accusation("!accuse B 毒を盛った")
    assert r == ("B", "毒を盛った")
    assert tc.parse_accusation("hello") is None
    assert tc.parse_accusation("!accuse") is None


@pytest.mark.parametrize("text, expected", [
    ("!1", 1), ("3", 3), ("!vote 2", 2), ("!v 4", 4),
    (" !VOTE ０２ ", 2), ("!٢", 2), ("!01", 1),
])
def test_parse_vote_preserves_supported_forms(text, expected):
    assert tc.parse_vote(text) == expected


@pytest.mark.parametrize("text", [
    "!²", "!vote ²", "!v ²", "!②", "!1²",
    pytest.param("!" + "1" * 5000, id="oversized-integer"),
    "!0", "!5", "!-1", "!+1", "!1_0", "!1 2", "", None,
])
def test_parse_vote_rejects_invalid_input(text):
    assert tc.parse_vote(text) is None


@pytest.mark.parametrize("text, expected_trick", [
    ("!accuse B", ""), ("!accuse B   ", ""), ("!accuse B 毒を盛った", "毒を盛った"),
])
def test_parsed_accusation_can_be_recorded(text, expected_trick):
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.close_choice(st)
    tc.open_climax(st)
    parsed = tc.parse_accusation(text)
    assert parsed == ("B", expected_trick)
    tc.record_accusation(st, "u1", *parsed)
    assert st["accusations"]["u1"]["trick"] == expected_trick
    tc.close_climax(st)
    assert st["climax_result"]["correct_users"] == ["u1"]
    assert st["scores"] == {"u1": 10}


@pytest.mark.parametrize("trick_args, expected_trick", [
    ((), ""), (("",), ""), ((None,), ""),
    (("毒" * tc.MAX_TRICK_TEXT,), "毒" * tc.MAX_TRICK_TEXT),
])
def test_record_accusation_optional_trick(trick_args, expected_trick):
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.close_choice(st)
    tc.open_climax(st)
    tc.record_accusation(st, "u1", "A", "前の推理")
    tc.record_accusation(st, "u1", "B", *trick_args)
    assert len(st["accusations"]) == 1
    assert st["accusations"]["u1"]["suspect"] == "B"
    assert st["accusations"]["u1"]["trick"] == expected_trick


@pytest.mark.parametrize("trick", [False, 0, [], {}, "毒" * (tc.MAX_TRICK_TEXT + 1)])
def test_record_accusation_rejects_invalid_trick(trick):
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.close_choice(st)
    tc.open_climax(st)
    with pytest.raises(tc.TrpgCornerError, match="invalid trick"):
        tc.record_accusation(st, "u1", "B", trick)
    assert st["accusations"] == {}


def test_ledger_record_and_trim():
    ledger = tc.new_ledger()
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.close_choice(st)
    tc.advance_scene(st)  # -> climax? s1 winning none -> climax (climax present)
    # Actually winning none -> advance_scene with None -> climax present -> CLIMAX
    assert st["phase"] == "CLIMAX"
    tc.close_climax(st)
    tc.finish(st)
    assert tc.record_run(ledger, st) is True
    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["outcome"] == "completed"
    # record from non-terminal returns False
    st2 = tc.new_state()
    tc.start(st2, make_scenario())
    assert tc.record_run(ledger, st2) is False
    # trim by count
    for i in range(tc.MAX_HISTORY_ENTRIES + 5):
        e = dict(ledger["entries"][0])
        ledger["entries"].append(e)
    tc._trim_ledger(ledger)
    assert len(ledger["entries"]) <= tc.MAX_HISTORY_ENTRIES


def test_persistence_roundtrip(tmp_path):
    p_state = tmp_path / "state.json"
    p_ledger = tmp_path / "ledger.json"
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.save_state(p_state, st)
    loaded = tc.load_state(p_state)
    assert loaded["phase"] == "SCENE"
    assert loaded["scenario_id"] == "murder-001"
    ledger = tc.new_ledger()
    # record not terminal -> False; make terminal
    tc.open_choice(st)
    tc.close_choice(st)
    tc.advance_scene(st)
    # advance to climax then finish
    if st["phase"] == "CLIMAX":
        tc.close_climax(st)
        tc.finish(st)
    tc.record_run(ledger, st)
    tc.save_ledger(p_ledger, ledger)
    loaded_l = tc.load_ledger(p_ledger)
    assert len(loaded_l["entries"]) == 1
    # append jsonl
    jp = tmp_path / "hist.jsonl"
    tc.append_history_jsonl(jp, loaded_l["entries"][0])
    assert jp.read_text().strip() != ""
    # lock helper
    res = tc.with_lock(tmp_path, lambda: 42)
    assert res == 42


def test_missing_state_file_returns_new(tmp_path):
    assert tc.load_state(tmp_path / "nope.json")["phase"] == "IDLE"
    assert tc.load_ledger(tmp_path / "nope.json")["entries"] == []


def test_climax_result_correctness():
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.close_choice(st)
    # winning none -> advance_scene -> climax (climax present)
    tc.advance_scene(st)
    assert st["phase"] == "CLIMAX"
    tc.record_accusation(st, "u1", "B", "毒")
    tc.record_accusation(st, "u2", "A", "銃")
    tc.record_accusation(st, "u1", "C", " overriding")  # last wins for u1
    assert st["accusations"]["u1"]["suspect"] == "C"
    tc.close_climax(st)
    assert st["climax_result"]["correct_count"] == 0  # C and A both wrong
    assert st["climax_result"]["total_accusations"] == 2


def test_vote_tally_tie_lowest():
    st = tc.new_state()
    tc.start(st, make_scenario())
    tc.open_choice(st)
    tc.record_vote(st, "u1", 1)
    tc.record_vote(st, "u2", 2)
    tc.close_choice(st)
    # tie 1-1 -> lowest index 1
    assert tc.winning_choice(st) == 1
