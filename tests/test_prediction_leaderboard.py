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


def canceled_row(pid, picks=None, *, ended="2026-10-01T00:00:00Z",
                 title="半熟英雄：どこまで到達できるか？"):
    """A no-contest CANCELED Helix row (``winning_outcome_id`` is null)."""
    outcomes = []
    for oid, otitle in (("o1", "新記録"), ("o2", "惜しい")):
        rows = (picks or {}).get(oid, [])
        outcomes.append({"id": oid, "title": otitle, "users": len(rows),
                         "channel_points": sum(r["channel_points_used"] for r in rows),
                         "top_predictors": rows or None})
    return {"id": pid, "title": title, "status": "CANCELED", "winning_outcome_id": None,
            "ended_at": ended, "outcomes": outcomes}


def canceled_predictor(user_id, login, *, outcome="o1", used=100):
    return {"user_id": user_id, "user_login": login, "user_name": login.title(),
            "channel_points_used": used}


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
        lb.record({"schema": 3, "rounds": {}}, row("p1"), corner="hanjuku")
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
    assert lb.stats(ledger) == {"rounds": 3, "viewers": 2, "canceled": 0}


def test_load_missing_is_empty_and_save_round_trips(tmp_path):
    path = tmp_path / "prediction_leaderboard.json"
    assert lb.load(path) == lb.new_ledger()
    ledger = lb.new_ledger()
    lb.record(ledger, with_top("p1", {"o1": [predictor("u1", "alice")]}),
              corner="hanjuku", now=7)
    lb.save(path, ledger)
    assert lb.load(path) == ledger
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 2


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


def test_record_resolved_serializes_writers_and_is_idempotent(tmp_path):
    import threading

    remotes = [with_top(f"p{i:03d}", {"o1": [predictor(f"u{i}", f"login{i}")]}) for i in range(40)]
    chunks = [remotes[i::8] for i in range(8)]
    errors = []

    def write(rows):
        try:
            for remote in rows:
                assert lb.record_resolved(tmp_path, remote, corner="hanjuku", now=7) is True
        except Exception as exc:  # surfaced in the main thread
            errors.append(exc)

    workers = [threading.Thread(target=write, args=(chunk,)) for chunk in chunks]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10.0)

    assert errors == []
    ledger = lb.load(tmp_path / lb.FILE)
    assert len(ledger["rounds"]) == 40
    assert all(round_["corner"] == "hanjuku" and round_["recorded_at"] == 7
               for round_ in ledger["rounds"].values())
    assert (tmp_path / "locks" / lb.LOCK_FILE).is_file()
    # A retry of the same prediction (another owner tick) is a no-op.
    assert lb.record_resolved(tmp_path, remotes[0], corner="hanjuku") is False
    assert len(lb.load(tmp_path / lb.FILE)["rounds"]) == 40


def test_record_resolved_never_overwrites_a_broken_ledger(tmp_path):
    path = tmp_path / lb.FILE
    path.write_text(json.dumps({"schema": 3, "rounds": {}}), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(lb.LeaderboardError):
        lb.record_resolved(tmp_path, with_top("p1", {"o1": [predictor("u1", "alice")]}),
                           corner="hanjuku")

    assert path.read_bytes() == before


def test_record_resolved_ignores_unsettled_rows(tmp_path):
    active = row("p1", status="ACTIVE", winner=None,
                 outcomes=[{"id": "o1", "title": "A"}, {"id": "o2", "title": "B"}])
    assert lb.record_resolved(tmp_path, active, corner="hanjuku") is False
    assert not (tmp_path / lb.FILE).exists()


def test_canceled_round_keeps_participation_and_refund_without_grading():
    remote = canceled_row("p1", {"o1": [canceled_predictor("u1", "alice", used=100)]})
    round_, predictors = lb.extract(remote)
    assert round_["status"] == "CANCELED"
    assert round_["winning_outcome_id"] is None
    assert predictors == [{
        "user_id": "u1", "user_login": "alice", "user_name": "Alice",
        "outcome_id": "o1", "used": 100, "won": 0, "refunded": 100, "hit": None}]

    ledger = lb.new_ledger()
    assert lb.record(ledger, remote, corner="hanjuku", cancel_reason="no-contest", now=5)
    stored = ledger["rounds"]["p1"]
    assert stored["status"] == "CANCELED" and stored["cancel_reason"] == "no-contest"
    # A retried observation tick must not count the refunded round twice.
    assert lb.record(ledger, remote, corner="hanjuku", cancel_reason="no-contest") is False


def test_canceled_row_without_predictors_is_still_evidence():
    ledger = lb.new_ledger()
    assert lb.record(ledger, canceled_row("p1"), corner="hanjuku", cancel_reason="no-contest")
    assert ledger["rounds"]["p1"]["predictors"] == []
    assert lb.stats(ledger) == {"rounds": 1, "viewers": 0, "canceled": 1}


def test_canceled_row_ignores_an_echoed_won_amount():
    remote = canceled_row("p1", {"o1": [canceled_predictor("u1", "alice")]})
    remote["outcomes"][0]["top_predictors"][0]["channel_points_won"] = 200
    round_, predictors = lb.extract(remote)
    assert predictors[0]["won"] == 0 and predictors[0]["refunded"] == 100
    remote["outcomes"][0]["top_predictors"][0]["channel_points_won"] = None
    assert lb.extract(remote)[1][0]["won"] == 0


def test_canceled_round_requires_a_known_reason():
    ledger = lb.new_ledger()
    remote = canceled_row("p1")
    for reason in (None, "", "refund", 1):
        with pytest.raises(lb.LeaderboardError):
            lb.record(ledger, remote, corner="hanjuku", cancel_reason=reason)
    with pytest.raises(lb.LeaderboardError):
        lb.record(ledger, with_top("p2", {"o1": [predictor("u1", "alice")]}),
                  corner="hanjuku", cancel_reason="no-contest")
    assert ledger["rounds"] == {}


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(winning_outcome_id="o1"),
    lambda r: r["outcomes"][0]["top_predictors"][0].update(channel_points_won=-1),
    lambda r: r["outcomes"][0]["top_predictors"][0].update(channel_points_won="many"),
])
def test_extract_rejects_malformed_canceled_rows(mutate):
    remote = canceled_row("p1", {"o1": [canceled_predictor("u1", "alice")]})
    mutate(remote)
    with pytest.raises(lb.LeaderboardError):
        lb.extract(remote)


def test_save_refuses_a_canceled_round_without_a_reason(tmp_path):
    ledger = lb.new_ledger()
    ledger["rounds"]["p1"] = {
        "id": "p1", "title": "t", "ended_at": "", "status": "CANCELED",
        "winning_outcome_id": None, "outcomes": [], "predictors": []}
    with pytest.raises(lb.LeaderboardError):
        lb.save(tmp_path / lb.FILE, ledger)
    assert not (tmp_path / lb.FILE).exists()


def test_record_settled_records_a_canceled_round_once(tmp_path):
    remote = canceled_row("p1", {"o1": [canceled_predictor("u1", "alice")]})
    assert lb.record_settled(tmp_path, remote, corner="hanjuku", now=9,
                             cancel_reason="no-contest") is True
    assert lb.record_settled(tmp_path, remote, corner="hanjuku", now=9,
                             cancel_reason="no-contest") is False
    assert lb.load(tmp_path / lb.FILE)["rounds"]["p1"]["cancel_reason"] == "no-contest"


def test_a_full_refund_is_neither_a_gain_nor_a_loss():
    """100 staked and fully refunded: 1 participation, 0 gain, 0 loss."""
    ledger = lb.new_ledger()
    lb.record(ledger, with_top("p1", {"o1": [predictor("alice", "alice", used=100, won=300)]}),
              corner="hanjuku")
    lb.record(ledger, canceled_row("p2", {"o1": [canceled_predictor("alice", "alice", used=100)]}),
              corner="hanjuku", cancel_reason="no-contest")
    # Only the graded round counts for the hit-rate board.
    assert [(e["user_id"], e["rounds"], e["hits"], e["used"], e["won"])
            for e in lb.ranking(ledger, min_rounds=1)] == [("alice", 1, 1, 100, 300)]
    alice = {e["user_id"]: e for e in lb.boards(ledger, min_rounds=1)["staked"]}["alice"]
    assert alice["participations"] == 2
    assert alice["used"] == 200 and alice["refunded"] == 100
    assert alice["won"] == 300 and alice["lost"] == 0
    assert alice["net"] == 300 - 200 + 100


def test_lost_points_are_the_stake_of_a_graded_miss_only():
    ledger = lb.new_ledger()
    lb.record(ledger, with_top("p1", {"o1": [predictor("alice", "alice", used=10, won=40)],
                                      "o2": [predictor("bob", "bob", used=30, won=0)]}),
              corner="hanjuku")
    lb.record(ledger, with_top("p2", {"o1": [predictor("bob", "bob", used=20, won=60)]}),
              corner="hanjuku")
    lb.record(ledger, canceled_row("p3", {"o1": [canceled_predictor("bob", "bob", used=70)]}),
              corner="hanjuku", cancel_reason="no-contest")
    boards = lb.boards(ledger, min_rounds=1)
    bob = {e["user_id"]: e for e in boards["lost"]}["bob"]
    assert bob["lost"] == 30 and bob["won"] == 60 and bob["refunded"] == 70
    # A no-contest cancel on its own puts a viewer on the play/stake boards only.
    lb.record(ledger, canceled_row("p4", {"o1": [canceled_predictor("carol", "carol")]}),
              corner="hanjuku", cancel_reason="no-contest")
    boards = lb.boards(ledger, min_rounds=1)
    assert "carol" not in {e["user_id"] for e in boards["lost"]}
    assert "carol" not in {e["user_id"] for e in boards["hit_rate"]}
    assert "carol" in {e["user_id"] for e in boards["plays"]}


def test_boards_are_independent_and_deterministically_ordered():
    ledger = lb.new_ledger()
    for index in range(3):
        lb.record(ledger, with_top(f"g{index}",
                                   {"o1": [predictor("graded", "graded", used=10, won=20)]}),
                  corner="hanjuku")
    lb.record(ledger, canceled_row("c1", {"o1": [canceled_predictor("casual", "casual", used=5)]}),
              corner="hanjuku", cancel_reason="no-contest")
    assert set(lb.boards(ledger)) == set(lb.METRICS)
    graded = lb.boards(ledger, min_rounds=3, min_participations=1)
    assert {e["user_id"] for e in graded["hit_rate"]} == {"graded"}
    assert [e["user_id"] for e in graded["plays"]] == ["graded", "casual"]
    # min_rounds must not filter a board that never grades a round.
    strict = lb.boards(ledger, min_rounds=99, min_participations=1)
    assert strict["hit_rate"] == [] and len(strict["staked"]) == 2
    assert lb.boards(ledger, min_rounds=1, min_participations=5)["plays"] == []
    with pytest.raises(lb.LeaderboardError):
        lb.boards(ledger, min_participations=0)


def test_load_upgrades_a_schema_1_ledger(tmp_path):
    path = tmp_path / lb.FILE
    path.write_text(json.dumps({"schema": 1, "rounds": {"p1": {
        "id": "p1", "title": "t", "ended_at": "2026-10-01T00:00:00Z",
        "winning_outcome_id": "o1",
        "outcomes": [{"id": "o1", "title": "A", "users": 1, "points": 100}],
        "predictors": [{"user_id": "u1", "user_login": "alice", "user_name": "Alice",
                        "outcome_id": "o1", "used": 100, "won": 200, "hit": True}],
        "corner": "hanjuku", "recorded_at": 7}}}), encoding="utf-8")
    ledger = lb.load(path)
    assert ledger["schema"] == 2
    assert ledger["rounds"]["p1"]["status"] == "RESOLVED"
    assert ledger["rounds"]["p1"]["predictors"][0]["refunded"] == 0
    assert lb.ranking(ledger, min_rounds=1)[0]["hit_rate"] == 1.0


def test_load_refuses_a_legacy_round_that_is_not_graded(tmp_path):
    path = tmp_path / lb.FILE
    path.write_text(json.dumps({"schema": 1, "rounds": {"p1": {"id": "p1", "predictors": "x"}}}),
                    encoding="utf-8")
    with pytest.raises(lb.LeaderboardError):
        lb.load(path)
