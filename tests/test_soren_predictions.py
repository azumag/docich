"""Offline contracts for the Soren→予想王 handoff reader (#933).

No Twitch call, token, Soren process or production write: the handoff file and
the ledger both live in a temporary directory.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import prediction_leaderboard as lb
from docich import soren_predictions as sp


def row(pid="prediction-1", *, status="RESOLVED", winner="outcome-1", predictors=True):
    outcomes = [
        {"id": "outcome-0", "title": "建国なし"},
        {"id": "outcome-1", "title": "ソ連建国"},
    ]
    if predictors:
        outcomes[0]["top_predictors"] = [{
            "user_id": "u1", "user_login": "alice", "user_name": "Alice",
            "channel_points_used": 100, "channel_points_won": 0}]
        outcomes[1]["top_predictors"] = [{
            "user_id": "u2", "user_login": "bob", "user_name": "Bob",
            "channel_points_used": 50, "channel_points_won": 200}]
    return {"id": pid, "title": "12ゲーム中に建国できる？", "status": status,
            "winning_outcome_id": winner, "ended_at": "2026-10-14T00:00:00Z",
            "outcomes": outcomes}


def canceled(pid="prediction-9", *, reason="no-contest", winner=None, predictors=True):
    """A no-contest refund row as Soren's writer hands it over."""
    if predictors:
        outcomes = [
            {"id": "outcome-0", "title": "建国なし", "top_predictors": [
                {"user_id": "u9", "user_login": "carol", "user_name": "Carol",
                 "channel_points_used": 100}]},
            {"id": "outcome-1", "title": "ソ連建国"},
        ]
    else:
        outcomes = row(pid, predictors=False)["outcomes"]
    record = row(pid, status="CANCELED", winner=winner, predictors=False)
    record["outcomes"] = outcomes
    if reason is not None:
        record["cancel_reason"] = reason
    return record


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "run"
    soren = tmp_path / "soren"
    (soren / "tmp" / "state").mkdir(parents=True)
    state.mkdir(parents=True)
    g = SimpleNamespace(state_dir=state, webui=SimpleNamespace(soren_root=str(soren)))
    handoff = soren / sp.HANDOFF

    def write(*rows, mode="w"):
        with handoff.open(mode, encoding="utf-8") as stream:
            for item in rows:
                stream.write(json.dumps(item) + "\n")

    return SimpleNamespace(g=g, state=state, soren=soren, handoff=handoff, write=write)


def test_settled_rows_are_recorded_once_and_unchanged_files_are_skipped(env):
    env.write(row())

    assert sp.tick(env.g, clock=lambda: 5) == {"status": "recorded", "recorded": 1}

    round_ = lb.load(env.state / lb.FILE)["rounds"]["prediction-1"]
    assert round_["corner"] == "soren" and round_["recorded_at"] == 5
    assert {entry["user_id"]: entry["hit"] for entry in round_["predictors"]} == {
        "u1": False, "u2": True}
    assert sp.cursor_path(env.g).is_file()

    # The same bytes are not re-read, so the ledger is untouched on the next tick.
    before = (env.state / lb.FILE).read_bytes()
    assert sp.tick(env.g, clock=lambda: 5) == {"status": "unchanged"}
    assert (env.state / lb.FILE).read_bytes() == before

    env.write(row("prediction-2"), mode="a")
    assert sp.tick(env.g, clock=lambda: 6) == {"status": "recorded", "recorded": 1}
    assert set(lb.load(env.state / lb.FILE)["rounds"]) == {"prediction-1", "prediction-2"}


def test_missing_handoff_is_idle_and_writes_nothing(env):
    assert sp.tick(env.g) == {"status": "idle", "reason": "no-handoff"}
    assert not (env.state / lb.FILE).exists()
    assert not sp.cursor_path(env.g).exists()


def test_untrusted_handoff_shapes_are_refused(env):
    env.handoff.write_text("{not json\n", encoding="utf-8")
    assert sp.tick(env.g) == {"status": "error", "reason": "handoff-invalid"}

    env.write(row(status="ACTIVE"))
    assert sp.tick(env.g) == {"status": "error", "reason": "handoff-invalid"}

    env.write(row())
    env.handoff.write_text("x" * (sp.MAX_BYTES + 1), encoding="utf-8")
    assert sp.tick(env.g) == {"status": "error", "reason": "handoff-oversized"}

    env.handoff.unlink()
    (env.soren / "elsewhere.json").write_text(json.dumps(row()) + "\n", encoding="utf-8")
    env.handoff.symlink_to(env.soren / "elsewhere.json")
    assert sp.tick(env.g) == {"status": "error", "reason": "handoff-unavailable"}

    assert not (env.state / lb.FILE).exists()


def test_broken_ledger_keeps_the_handoff_retryable(env):
    env.write(row())
    path = env.state / lb.FILE
    path.write_text("{broken", encoding="utf-8")

    assert sp.tick(env.g) == {"status": "error", "reason": "leaderboard"}
    assert path.read_text(encoding="utf-8") == "{broken"
    assert not sp.cursor_path(env.g).exists()

    path.write_text(json.dumps(lb.new_ledger()), encoding="utf-8")
    assert sp.tick(env.g) == {"status": "recorded", "recorded": 1}
    assert len(lb.load(path)["rounds"]) == 1


def test_a_no_contest_cancel_is_recorded_as_participation_only(env):
    env.write(canceled())

    assert sp.tick(env.g, clock=lambda: 7) == {"status": "recorded", "recorded": 1}

    ledger = lb.load(env.state / lb.FILE)
    round_ = ledger["rounds"]["prediction-9"]
    assert round_["status"] == "CANCELED"
    assert round_["cancel_reason"] == "no-contest"
    assert round_["corner"] == "soren" and round_["recorded_at"] == 7
    # 返金は元本返却。的中にも外れにも数えず、損益にも二重計上しない。
    assert [(e["user_id"], e["hit"], e["used"], e["won"], e["refunded"])
            for e in round_["predictors"]] == [("u9", None, 100, 0, 100)]
    assert lb.stats(ledger) == {"rounds": 1, "viewers": 1, "canceled": 1}
    assert lb.ranking(ledger, min_rounds=1) == []

    # 同じバイトは再読しないので、再起動・再試行でも二重計上しない。
    before = (env.state / lb.FILE).read_bytes()
    assert sp.tick(env.g, clock=lambda: 7) == {"status": "unchanged"}
    assert (env.state / lb.FILE).read_bytes() == before


def test_resolved_and_canceled_rounds_share_one_handoff(env):
    env.write(row(), canceled())

    assert sp.tick(env.g, clock=lambda: 8) == {"status": "recorded", "recorded": 2}

    ledger = lb.load(env.state / lb.FILE)
    assert set(ledger["rounds"]) == {"prediction-1", "prediction-9"}
    boards = lb.boards(ledger, min_rounds=1)
    # 賭けたポイント・賭けた回数にはキャンセル分も入るが、的中率の分母には入らない。
    staked = {e["user_id"]: e for e in boards["staked"]}
    assert staked["u9"]["used"] == 100 and staked["u9"]["participations"] == 1
    hit = {e["user_id"]: e for e in boards["hit_rate"]}
    assert "u9" not in hit
    assert {key: entry["rounds"] for key, entry in hit.items()} == {"u1": 1, "u2": 1}


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(cancel_reason="refund"),
    lambda r: r.update(cancel_reason=None),
    lambda r: r.pop("cancel_reason"),
    lambda r: r.update(status="LOCKED"),
    lambda r: r.update(winning_outcome_id="outcome-1"),
])
def test_a_canceled_row_without_the_fixed_reason_is_refused_whole(env, mutate):
    env.write(mutate(canceled()))

    assert sp.tick(env.g) == {"status": "error", "reason": "handoff-invalid"}
    assert not (env.state / lb.FILE).exists()
    assert not sp.cursor_path(env.g).exists()


def test_a_resolved_row_may_not_carry_a_refund_reason(env):
    forged = row()
    forged["cancel_reason"] = "no-contest"
    env.write(forged)

    assert sp.tick(env.g) == {"status": "error", "reason": "handoff-invalid"}
    assert not (env.state / lb.FILE).exists()


def test_a_canceled_row_without_predictors_is_still_evidence(env):
    env.write(canceled(predictors=False))

    assert sp.tick(env.g, clock=lambda: 9) == {"status": "recorded", "recorded": 1}

    ledger = lb.load(env.state / lb.FILE)
    assert ledger["rounds"]["prediction-9"]["predictors"] == []
    assert lb.stats(ledger) == {"rounds": 1, "viewers": 0, "canceled": 1}


def test_broken_ledger_keeps_the_canceled_handoff_retryable(env):
    env.write(canceled())
    path = env.state / lb.FILE
    path.write_text("{broken", encoding="utf-8")

    assert sp.tick(env.g) == {"status": "error", "reason": "leaderboard"}
    assert path.read_text(encoding="utf-8") == "{broken"

    path.write_text(json.dumps(lb.new_ledger()), encoding="utf-8")
    assert sp.tick(env.g, clock=lambda: 10) == {"status": "recorded", "recorded": 1}
    assert lb.stats(lb.load(path))["canceled"] == 1


def test_tick_never_raises_on_an_unusable_soren_root(tmp_path):
    state = tmp_path / "run"
    state.mkdir()
    broken = SimpleNamespace(state_dir=state, webui=SimpleNamespace(soren_root="\x00soren"))

    result = sp.tick(broken)

    assert result["status"] == "error" and result["reason"] == "handoff-unavailable"
    assert not (state / lb.FILE).exists()


def test_rotation_timer_runs_the_soren_reader_beside_the_hanjuku_tick():
    source = (Path(__file__).resolve().parents[1] / "src/docich/corner_rotation.py").read_text(
        encoding="utf-8")

    assert "prediction_tick(self.g)" in source
    assert "soren_prediction_tick(self.g)" in source
    assert source.index("prediction_tick(self.g)") < source.index("soren_prediction_tick(self.g)")


@pytest.mark.parametrize("damage", [
    "negative-used", "invalid-login", "non-list-predictors",
    "duplicate-predictor", "invalid-outcome-users",
])
def test_malformed_settled_row_refuses_whole_handoff_before_recording(env, monkeypatch, damage):
    bad = row("bad")
    predictors = bad["outcomes"][1]["top_predictors"]
    if damage == "negative-used":
        predictors[0]["channel_points_used"] = -1
    elif damage == "invalid-login":
        predictors[0]["user_login"] = None
    elif damage == "non-list-predictors":
        bad["outcomes"][1]["top_predictors"] = "broken"
    elif damage == "duplicate-predictor":
        predictors.append(dict(predictors[0]))
    else:
        bad["outcomes"][0]["users"] = True
    env.write(row("before"), bad, row("after"))

    def unexpected_record(*args, **kwargs):
        pytest.fail("a malformed handoff must be validated before any ledger work")

    monkeypatch.setattr(sp, "record_settled", unexpected_record)
    for _ in range(2):
        assert sp.tick(env.g) == {"status": "error", "reason": "handoff-invalid"}
        assert not (env.state / lb.FILE).exists()
        assert not sp.cursor_path(env.g).exists()


def test_malformed_recorded_id_still_refuses_whole_handoff_without_changing_state(env, monkeypatch):
    env.write(row("existing"))
    assert sp.tick(env.g, clock=lambda: 5) == {"status": "recorded", "recorded": 1}
    ledger_before = (env.state / lb.FILE).read_bytes()
    cursor_before = sp.cursor_path(env.g).read_bytes()
    bad = row("existing")
    bad["outcomes"][1]["top_predictors"][0]["user_login"] = None
    env.write(row("before"), bad, row("after"))

    def unexpected_record(*args, **kwargs):
        pytest.fail("even an already-recorded id must be validated before ledger work")

    monkeypatch.setattr(sp, "record_settled", unexpected_record)
    for _ in range(2):
        assert sp.tick(env.g) == {"status": "error", "reason": "handoff-invalid"}
        assert (env.state / lb.FILE).read_bytes() == ledger_before
        assert sp.cursor_path(env.g).read_bytes() == cursor_before
