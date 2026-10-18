"""Integration tests for the ported trpg-core participation logic (#684).

The vote tally, winner tie-break and run ledger here are the generic logic
merged from the trpg offline core (PR #2481) by the unification ruling;
these tests carry over that core's contract.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.mystery_participation import (
    LEDGER_SCHEMA,
    MAX_BYTES,
    MAX_HISTORY_ENTRIES,
    MAX_PARTICIPANTS,
    MAX_USER_ID,
    OUTCOMES,
    ParticipationError,
    choice_key,
    load_ledger,
    new_ledger,
    participants_from_prediction,
    record_run,
    record_vote,
    save_ledger,
    tally_votes,
    trim_ledger,
    winner,
)


# --- vote tally (trpg core: idempotent, last vote wins) ---------------------


def test_record_vote_is_idempotent_and_last_vote_wins():
    votes, order = {}, {}
    record_vote(votes, order, "alice", 0)
    record_vote(votes, order, "alice", 2)
    # The re-vote moved alice out of bucket 0 into bucket 2.
    assert votes == {choice_key(2): ["alice"]}
    assert order == {"alice": choice_key(2)}
    record_vote(votes, order, "alice", 2)  # same vote twice = one vote
    assert votes[choice_key(2)] == ["alice"]
    assert tally_votes(votes, choice_count=3) == [0, 0, 1]


def test_record_vote_rejects_invalid_input():
    votes, order = {}, {}
    with pytest.raises(ParticipationError):
        record_vote(votes, order, "", 0)
    with pytest.raises(ParticipationError):
        record_vote(votes, order, "x" * (MAX_USER_ID + 1), 0)
    with pytest.raises(ParticipationError):
        record_vote(votes, order, "bob", -1)
    with pytest.raises(ParticipationError):
        record_vote(votes, order, "bob", True)
    with pytest.raises(ParticipationError):
        record_vote(votes, order, None, 0)
    assert votes == {} and order == {}


def test_record_vote_bounds_participants():
    votes, order = {}, {}
    for i in range(MAX_PARTICIPANTS):
        record_vote(votes, order, f"u{i}", i % 3)
    with pytest.raises(ParticipationError):
        record_vote(votes, order, "overflow-user", 0)


def test_record_vote_rejection_leaves_state_byte_identical():
    """Over-limit rejection must not mutate anything (the trpg core's
    post-write check let an ignored newcomer's vote linger and even score —
    owner-verified 2026-10-18; this port checks before any write)."""
    votes, order = {}, {}
    for i in range(MAX_PARTICIPANTS):
        record_vote(votes, order, f"u{i}", i % 3)
    before = (json.dumps(votes, sort_keys=True), json.dumps(order, sort_keys=True))
    with pytest.raises(ParticipationError):
        record_vote(votes, order, "overflow-user", 2)
    after = (json.dumps(votes, sort_keys=True), json.dumps(order, sort_keys=True))
    assert before == after


def test_existing_participant_can_resubmit_at_the_limit():
    votes, order = {}, {}
    for i in range(MAX_PARTICIPANTS):
        record_vote(votes, order, f"u{i}", i % 3)
    # An existing participant at the limit may move their vote (last wins).
    record_vote(votes, order, "u0", 2)
    assert order["u0"] == choice_key(2)
    assert "u0" not in votes.get(choice_key(0), [])
    assert "u0" in votes[choice_key(2)]
    # Their move is counted once.
    assert votes[choice_key(2)].count("u0") == 1


def test_winner_is_plurality_with_lowest_index_tiebreak():
    assert winner([3, 5, 5]) == 1  # tie → lowest index
    assert winner([0, 0, 0]) is None  # no votes → solo detective takes over
    assert winner([]) is None
    assert winner([2]) == 0
    with pytest.raises(ParticipationError):
        winner([1, -1])
    with pytest.raises(ParticipationError):
        winner([1, True])


def test_tally_votes_validates_buckets():
    votes = {choice_key(1): ["a", "b"], "garbage": ["c"]}
    with pytest.raises(ParticipationError):
        tally_votes(votes, choice_count=3)
    good = {choice_key(0): ["a"], choice_key(2): ["b", "c"]}
    assert tally_votes(good, choice_count=3) == [1, 0, 2]
    with pytest.raises(ParticipationError):
        tally_votes({}, choice_count=0)


def test_participants_from_prediction_is_fail_open_and_bounded():
    row = {"outcomes": [
        {"title": "A", "top_predictors": [
            {"user_login": "alice", "channel_points": 100},
            {"user_login": "bob", "channel_points": 10},
        ]},
        {"title": "B", "top_predictors": [
            {"user_name": "carol", "channel_points": 500},
            "garbage",
            {"user_login": "", "channel_points": 1},
        ]},
        {"title": "C", "top_predictors": "not-a-list"},
    ]}
    votes, order = participants_from_prediction(row)
    assert order == {"alice": choice_key(0), "bob": choice_key(0),
                     "carol": choice_key(1)}
    # carol is the crowd's pick by users (1 vs 2? no: A has 2 users).
    assert tally_votes(votes, choice_count=3) == [2, 1, 0]
    assert winner([2, 1, 0]) == 0
    assert participants_from_prediction(None) == ({}, {})
    assert participants_from_prediction({"outcomes": []}) == ({}, {})


# --- run ledger (trpg core: bounded, trimmed, atomic) ------------------------


def _entry(**overrides):
    base = {
        "seed": 12345, "commitment": "a" * 64, "culprit_index": 2,
        "started_at": 1000, "ended_at": 2000, "outcome": "completed",
        "participants": 7, "votes_total": 42, "polls": 2,
        "prediction": {"status": "RESOLVED", "resolved": True, "hit": True},
        "end_reason": "case-completed", "error_code": None,
    }
    base.update(overrides)
    return base


def test_record_run_validates_and_appends_bounded_fields():
    ledger = new_ledger()
    assert record_run(ledger, _entry()) is True
    assert len(ledger["entries"]) == 1
    recorded = ledger["entries"][0]
    assert recorded["outcome"] == "completed" and recorded["seed"] == 12345
    assert recorded["prediction"]["hit"] is True
    with pytest.raises(ParticipationError):
        record_run(ledger, _entry(outcome="strange"))
    with pytest.raises(ParticipationError):
        record_run(ledger, _entry(seed=-1))
    with pytest.raises(ParticipationError):
        record_run(ledger, _entry(end_reason="x" * 500))
    with pytest.raises(ParticipationError):
        record_run(new_ledger(), "not-a-dict")
    assert OUTCOMES == frozenset({"completed", "failed", "timeout", "aborted"})


def test_ledger_trims_by_count_and_size():
    ledger = new_ledger()
    for i in range(MAX_HISTORY_ENTRIES + 10):
        record_run(ledger, _entry(seed=i, started_at=i, ended_at=i))
    assert len(ledger["entries"]) == MAX_HISTORY_ENTRIES
    # Oldest entries were dropped first.
    assert ledger["entries"][0]["seed"] == 10

    big = new_ledger()
    filler = "y" * 4096
    for i in range(60):
        entry = _entry(seed=i)
        entry["end_reason"] = filler[:64]
        # Sneak extra bulk through the prediction dict (unbounded sub-dict is
        # the caller's data; the trim pass bounds the total serialized size).
        entry["prediction"] = {"bulk": filler}
        record_run(big, entry)
        assert len(json.dumps(big, ensure_ascii=False).encode()) <= MAX_BYTES
    assert len(big["entries"]) < 60


def test_ledger_roundtrip_is_atomic_and_schema_checked(tmp_path):
    path = tmp_path / "mystery_history.json"
    assert load_ledger(path) == new_ledger()  # absent → fresh
    ledger = new_ledger()
    record_run(ledger, _entry(seed=7))
    save_ledger(path, ledger)
    loaded = load_ledger(path)
    assert loaded["schema"] == LEDGER_SCHEMA
    assert loaded["entries"][0]["seed"] == 7

    path.write_text('{"schema": 99, "entries": [1]}', encoding="utf-8")
    assert load_ledger(path) == new_ledger()  # wrong schema → fresh
    path.write_text("not json", encoding="utf-8")
    assert load_ledger(path) == new_ledger()
    path.write_text('{"schema": 1, "entries": "nope"}', encoding="utf-8")
    assert load_ledger(path)["entries"] == []

    path.write_text("x" * (MAX_BYTES + 1), encoding="utf-8")
    oversized = new_ledger()
    record_run(oversized, _entry())
    with pytest.raises(ParticipationError):
        save_ledger(path, oversized | {"entries": [{"bulk": "z" * (MAX_BYTES + 1)}]})


def test_trim_ledger_ignores_malformed_entries_field():
    ledger = {"schema": LEDGER_SCHEMA, "entries": "not-a-list"}
    trim_ledger(ledger)  # no crash, no change
    assert ledger["entries"] == "not-a-list"
