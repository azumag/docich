"""Daily improvement loop and promotion judgement for free PAPER strategies (synthetic)."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy import improve  # noqa: E402
from docich.trading.free_strategy.contract import Artifact  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402

IMAGE = "sha256:" + "a" * 64
DAY = 86400.0
# 2026-11-10 00:00 JST
START = 1794236400.0


def _artifact(number: int) -> Artifact:
    return Artifact.create(
        source=("def decide(context): return "
                f"{{'schema_version':1,'target_positions':[],'state':{{'n':{number}}},'reason':'wait'}}"),
        image=IMAGE, name=f"cand-{number}", family=f"family-{number}",
        thesis=f"thesis {number}", symbols=["BTC/JPY"])


def _create(store: LabStore, number: int, *, now: float = START + 3600) -> str:
    artifact = _artifact(number)
    store.register(artifact)
    return store.create(artifact.digest, now=now, capital="10000", days=30)


def _equity(store: LabStore, identity: str, values: list[str]) -> None:
    """One end-of-day observation per day, starting the day after creation."""
    with store.transaction():
        for index, value in enumerate(values):
            at = START + (index + 1) * DAY + 20 * 3600
            store.db.execute("INSERT INTO equity_observations VALUES (?,?,?)", (identity, at, value))


def _growth(rate: float, days: int) -> list[str]:
    value, out = 10000.0, []
    for _ in range(days):
        value *= 1 + rate
        out.append(f"{value:.4f}")
    return out


def test_equity_day_returns_use_whole_days_after_creation(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    try:
        identity = _create(store, 1)
        _equity(store, identity, ["10100", "10201", "10201"])
        blocks = improve.equity_day_returns(store, identity, capital=improve.Decimal("10000"),
                                            created_at=START + 3600, now=START + 10 * DAY)
        assert [day for day, _ in blocks] == ["2026-11-11", "2026-11-12", "2026-11-13"]
        assert [round(value, 4) for _, value in blocks] == [0.01, 0.01, 0.0]
    finally:
        store.close()


def test_a_strong_candidate_is_eligible_and_a_weak_one_is_not(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    try:
        strong = _create(store, 1)
        weak = _create(store, 2)
        _equity(store, strong, _growth(0.02, 7))
        _equity(store, weak, _growth(0.001, 7))
        report = improve.judge_promotion(store, now=START + 12 * DAY)
        assert report["candidate"]["experiment"] == strong
        reasons = {item["experiment"]: item["reason"] for item in report["experiments"]}
        assert reasons == {strong: None, weak: "rule_not_met"}
    finally:
        store.close()


def test_the_challenger_must_beat_the_live_free_strategy(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    try:
        strong = _create(store, 1)
        _equity(store, strong, _growth(0.02, 7))
        live = {f"2026-11-{day}": 0.05 for day in range(11, 19)}
        report = improve.judge_promotion(store, now=START + 12 * DAY, live_returns=live)
        assert report["candidate"] is None
        assert report["experiments"][0]["reason"] == "not_better_than_live"
        artifact = report["experiments"][0]["artifact"]
        report = improve.judge_promotion(store, now=START + 12 * DAY, live_artifact=artifact)
        assert report["experiments"][0]["reason"] == "already_live"
    finally:
        store.close()


def test_quarantined_candidates_are_never_eligible(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    try:
        identity = _create(store, 1)
        _equity(store, identity, _growth(0.02, 7))
        with store.transaction():
            store.db.execute("UPDATE experiments SET phase='quarantined' WHERE id=?", (identity,))
        assert improve.judge_promotion(store, now=START + 12 * DAY)["candidate"] is None
    finally:
        store.close()


def test_risk_stopped_candidates_are_retired(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    try:
        identity = _create(store, 1)
        with store.transaction():
            store.db.execute("UPDATE experiments SET phase='paused', last_error='drawdown_limit' WHERE id=?",
                             (identity,))
        assert improve.retire_risk_stopped(store, now=START + DAY) == 1
        assert store.experiment(identity)["end_at"] == START + DAY
    finally:
        store.close()


def test_run_improve_generates_with_prior_results_and_writes_the_report(tmp_path):
    lab = tmp_path / "free-strategies"
    store = LabStore(lab / "lab.sqlite3")
    try:
        old = _create(store, 1)
        with store.transaction():
            store.db.execute("INSERT INTO evaluations VALUES (?,?,?)", (old, START, json.dumps(
                {"net_pnl_jpy": "-120", "max_drawdown_fraction": "0.03", "fill_count": 4}).encode()))
    finally:
        store.close()
    prompts = []

    def text_fn(prompt):
        prompts.append(prompt)
        return json.dumps({"source": "def decide(context): return {'schema_version':1,"
                                     "'target_positions':[],'state':{},'reason':'x'}",
                           "name": "next", "family": "breakout", "thesis": "learn from cand-1"})

    result = improve.run_improve(tmp_path, image=IMAGE, symbols=["BTC/JPY"],
                                 now_fn=lambda: START + 2 * DAY, text_fn=text_fn)
    assert result["status"] == "registered"
    assert '"net_pnl_jpy":"-120"' in prompts[0] and "cand-1" in prompts[0]
    assert improve.read_report(tmp_path)["rule"] == improve.RULE_ID


def test_run_improve_respects_the_stop_file_and_capacity(tmp_path):
    lab = tmp_path / "free-strategies"
    lab.mkdir()
    (lab / improve.DISABLED_FILENAME).write_text("")
    assert improve.run_improve(tmp_path, image=IMAGE, symbols=["BTC/JPY"],
                               text_fn=lambda prompt: "")["reason"] == "disabled"
    (lab / improve.DISABLED_FILENAME).unlink()
    store = LabStore(lab / "lab.sqlite3")
    try:
        _create(store, 1)
        _create(store, 2)
    finally:
        store.close()

    def never(prompt):
        raise AssertionError("no provider call when every slot is used")

    result = improve.run_improve(tmp_path, image=IMAGE, symbols=["BTC/JPY"],
                                 now_fn=lambda: START + DAY, text_fn=never)
    assert result["reason"] == "experiment_capacity"
    assert improve.read_report(tmp_path) is not None
