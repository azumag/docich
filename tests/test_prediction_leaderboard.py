"""Offline 予想王 leaderboard contract: no Twitch, tokens or production writes."""
from __future__ import annotations

import json

import pytest

from docich import prediction_leaderboard as lb


def row(pid, *, status="RESOLVED", winner="o1", ended="2026-10-01T00:00:00Z",
        outcomes=None, title="半熟英雄：どこまで到達できるか？"):
    return {"id": pid, "title": title, "status": status, "winning_outcome_id": winner,
            "ended_at": ended, "outcomes": outcomes if outcomes is not None else [
                {"id": "o1", "title": "新記録", "users": 5, "channel_points": 500,
                 "top_predictors": []},
                {"id": "o2", "title": "惜しい", "users": 3, "channel_points": 300,
                 "top_predictors": []},
            ]}


def predictor(user_id, login, *, outcome="o1", used=100, won=200):
    return {"user_id": user_id, "user_login": login, "user_name": login.title(),
            "channel_points_used": used, "channel_points_won": won}


def with_top(pid, picks, *, winner="o1", ended="2026-10-01T00:00:00Z"):
    """``picks`` maps outcome -> list of predictor dicts."""
    outcomes = []
    for oid, title in (("o1", "新記録"), ("o2", "惜しい")):
        rows = picks.get(oid, [])
        outcomes.append({"id": oid, "title": title, "users": len(rows),
                         "channel_points": sum(r["channel_points_used"] for r in rows),
                         "top_predictors": rows or None})
    return row(pid, winner=winner, ended=ended, outcomes=outcomes)


def test_unresolved_row_is_not_recorded():
    active = row("p1", status="ACTIVE", winner=None,
                 outcomes=[{"id": "o1", "title": "A"}, {"id": "o2", "title": "B"}])
    assert lb.extract(active) == (None, [])
    ledger = lb.new_ledger()
    assert lb.record(ledger, active, corner="hanjuku") is False
    assert ledger["rounds"] == {}


def test_extract_marks_winner_and_points():
    remote = with_top("p1", {"o1": [predictor("u1", "alice")],
                             "o2": [predictor("u2", "bob", used=50, won=0)]})
    round_, predictors = lb.extract(remote)
    assert round_["id"] == "p1"
    assert round_["winning_outcome_id"] == "o1"
    by_user = {p["user_id"]: p for p in predictors}
    assert by_user["u1"]["hit"] is True and by_user["u1"]["won"] == 200
    assert by_user["u2"]["hit"] is False and by_user["u2"]["outcome_id"] == "o2"
    assert [o["id"] for o in round_["outcomes"]] == ["o1", "o2"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(title=5),
    lambda r: r.update(outcomes="oops"),
    lambda r: r.update(outcomes=r["outcomes"][:1]),
    lambda r: r.update(winning_outcome_id="missing"),
    lambda r: r["outcomes"][0].update(id=""),
    lambda r: r["outcomes"][0].update(top_predictors="nope"),
    lambda r: r["outcomes"][0].update(top_predictors=[{"user_id": "u"}]),
    lambda r: r["outcomes"][0]["top_predictors"][0].update(channel_points_won=-1),
    lambda r: r["outcomes"][0].update(
        top_predictors=[predictor("u1", "alice"), predictor("u1", "alice")]),
])
def test_extract_rejects_malformed_resolved_rows(mutate):
    remote = with_top("p1", {"o1": [predictor("u1", "alice")]})
    mutate(remote)
    with pytest.raises(lb.LeaderboardError):
        lb.extract(remote)


def test_extract_rejects_non_dict_row():
    with pytest.raises(lb.LeaderboardError):
        lb.extract(["not", "a", "row"])


def test_record_is_idempotent_per_prediction_id():
    ledger = lb.new_ledger()
    remote = with_top("p1", {"o1": [predictor("u1", "alice")]})
    assert lb.record(ledger, remote, corner="hanjuku", now=100) is True
    assert ledger["rounds"]["p1"]["corner"] == "hanjuku"
    assert ledger["rounds"]["p1"]["recorded_at"] == 100
    # A retried settlement tick must not double count the same round.
    assert lb.record(ledger, remote, corner="hanjuku") is False
    assert len(ledger["rounds"]) == 1


def test_record_requires_corner_and_valid_now():
    ledger = lb.new_ledger()
    remote = with_top("p1", {"o1": [predictor("u1", "alice")]})
    with pytest.raises(lb.LeaderboardError):
        lb.record(ledger, remote, corner="")
    with pytest.raises(lb.LeaderboardError):
        lb.record(ledger, remote, corner="hanjuku", now=True)
    assert ledger["rounds"] == {}


def test_record_rejects_invalid_ledger():
    with pytest.raises(lb.LeaderboardError):
        lb.record({"schema": 2, "rounds": {}}, row("p1"), corner="hanjuku")
    with pytest.raises(lb.LeaderboardError):
        lb.record({"schema": True, "rounds": {}}, row("p1"), corner="hanjuku")


def _three_round_ledger():
    ledger = lb.new_ledger()
    rounds = {
        "p1": {"o1": [predictor("alice", "alice"), predictor("bob", "bob")]},
        "p2": {"o1": [predictor("alice", "alice"), predictor("bob", "bob")]},
        # Winner is o1 again; alice misses in p3 while bob keeps the hit.
        # alice -> 3 rounds, 2 hits; bob -> 3 rounds, 3 hits.
        "p3": {"o1": [predictor("bob", "bob")], "o2": [predictor("alice", "alice", won=0)]},
    }
    for pid, picks in rounds.items():
        assert lb.record(ledger, with_top(pid, picks), corner="hanjuku")
    return ledger


def test_ranking_orders_by_hit_rate_then_filters_min_rounds():
    ledger = _three_round_ledger()
    ranked = lb.ranking(ledger, min_rounds=3, limit=10)
    assert [e["user_id"] for e in ranked] == ["bob", "alice"]
    assert ranked[0]["hits"] == 3 and ranked[0]["hit_rate"] == 1.0
    assert ranked[1]["hits"] == 2 and ranked[1]["hit_rate"] == pytest.approx(2 / 3)
    assert ranked[0]["net"] == ranked[0]["won"] - ranked[0]["used"]
    # A viewer with fewer than min_rounds appearances is off the board.
    assert lb.ranking(ledger, min_rounds=4) == []
    assert lb.ranking(ledger, min_rounds=1, limit=1)[0]["user_id"] == "bob"


def test_ranking_tiebreak_net_then_login():
    ledger = lb.new_ledger()
    # Same hit rate; carol wins more points, dave/dan tie and break on login.
    for pid, picks in (
        ("p1", {"o1": [predictor("carol", "carol", used=10, won=100),
                       predictor("dave", "dave", used=10, won=10),
                       predictor("dan", "dan", used=10, won=10)]}),
        ("p2", {"o1": [predictor("carol", "carol", used=10, won=100),
                       predictor("dave", "dave", used=10, won=10),
                       predictor("dan", "dan", used=10, won=10)]}),
    ):
        lb.record(ledger, with_top(pid, picks), corner="hanjuku")
    ranked = lb.ranking(ledger, min_rounds=2)
    assert [e["user_id"] for e in ranked] == ["carol", "dan", "dave"]


def test_ranking_uses_latest_name_and_rejects_bad_bounds():
    ledger = lb.new_ledger()
    lb.record(ledger, with_top("p1", {"o1": [predictor("u1", "old")]}), corner="hanjuku")
    lb.record(ledger, with_top("p2", {"o1": [predictor("u1", "new")]}), corner="hanjuku")
    assert lb.ranking(ledger, min_rounds=2)[0]["user_login"] == "new"
    with pytest.raises(lb.LeaderboardError):
        lb.ranking(ledger, min_rounds=0)
    with pytest.raises(lb.LeaderboardError):
        lb.ranking(ledger, limit=0)


def test_stats_counts_rounds_and_distinct_viewers_without_names():
    ledger = _three_round_ledger()
    assert lb.stats(ledger) == {"rounds": 3, "viewers": 2}


def test_load_missing_is_empty_and_save_round_trips(tmp_path):
    path = tmp_path / "prediction_leaderboard.json"
    assert lb.load(path) == lb.new_ledger()
    ledger = lb.new_ledger()
    lb.record(ledger, with_top("p1", {"o1": [predictor("u1", "alice")]}),
              corner="hanjuku", now=7)
    lb.save(path, ledger)
    assert lb.load(path) == ledger
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1


def test_load_rejects_unbounded_and_corrupt_files(tmp_path):
    path = tmp_path / "prediction_leaderboard.json"
    path.write_text("x" * (lb.MAX_BYTES + 1), encoding="utf-8")
    with pytest.raises(lb.LeaderboardError):
        lb.load(path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(lb.LeaderboardError):
        lb.load(path)
    path.write_text(json.dumps({"schema": 1, "rounds": []}), encoding="utf-8")
    with pytest.raises(lb.LeaderboardError):
        lb.load(path)


def test_prune_keeps_the_newest_rounds():
    ledger = lb.new_ledger()
    for i in range(lb.MAX_ROUNDS + 5):
        remote = with_top(f"p{i:05d}", {"o1": [predictor("u1", "alice")]},
                          ended=f"2026-01-{1 + i % 28:02d}T00:00:00Z")
        lb.record(ledger, remote, corner="hanjuku")
    assert len(ledger["rounds"]) == lb.MAX_ROUNDS
    # Removed are the five oldest by (ended_at, id): the lowest ids of Jan 1..5.
    for pid in ("p00000", "p00028", "p00056", "p00084", "p00112"):
        assert pid not in ledger["rounds"]
    assert "p00001" in ledger["rounds"]
