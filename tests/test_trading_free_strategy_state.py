from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy.contract import Artifact, StrategyError  # noqa: E402
from docich.trading.free_strategy.store import LabStore  # noqa: E402

IMAGE = "sha256:" + "a" * 64


def _artifact() -> Artifact:
    return Artifact.create(
        source="def decide(context): return {'schema_version':1,'target_positions':[],'state':{},'reason':'wait'}",
        image=IMAGE,
        name="state-test",
        family="state",
        thesis="independent paper experiments",
        symbols=["BTC/JPY"],
    )


def _store(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    artifact = _artifact()
    store.register(artifact)
    return store, artifact


def test_same_artifact_can_start_new_independent_experiment_after_review(tmp_path):
    store, artifact = _store(tmp_path)
    try:
        first = store.create(artifact.digest, now=1000.0, days=1)
        with pytest.raises(StrategyError, match="artifact_experiment_active"):
            store.create(artifact.digest, now=1001.0, days=1)
        with store.transaction():
            store.db.execute("UPDATE experiments SET phase='review_due' WHERE id=?", (first,))
        second = store.create(artifact.digest, now=2000.0, days=1)
        assert second != first
        assert store.experiment(first)["phase"] == "review_due"
        assert store.experiment(second)["phase"] == "research"
        assert store.experiment(first)["account"] is not store.experiment(second)["account"]
        assert store.db.execute(
            "SELECT COUNT(*) FROM experiments WHERE artifact=?", (artifact.digest,)
        ).fetchone()[0] == 2
    finally:
        store.close()


def test_paused_or_quarantined_artifact_cannot_spawn_overlapping_copy(tmp_path):
    store, artifact = _store(tmp_path)
    try:
        identity = store.create(artifact.digest, now=1000.0, days=1)
        store.pause(identity)
        with pytest.raises(StrategyError, match="artifact_experiment_active"):
            store.create(artifact.digest, now=1001.0, days=1)

        with store.transaction():
            store.db.execute("UPDATE experiments SET phase='quarantined' WHERE id=?", (identity,))
        with pytest.raises(StrategyError, match="artifact_experiment_active"):
            store.create(artifact.digest, now=1002.0, days=1)
    finally:
        store.close()


@pytest.mark.parametrize("phase", ["paused", "review_due", "quarantined"])
def test_pause_does_not_rewrite_non_running_terminal_or_protected_states(tmp_path, phase):
    store, artifact = _store(tmp_path)
    try:
        identity = store.create(artifact.digest, now=1000.0, days=1)
        with store.transaction():
            store.db.execute("UPDATE experiments SET phase=? WHERE id=?", (phase, identity))
        before = store.experiment(identity)
        with pytest.raises(StrategyError, match="pause_not_allowed"):
            store.pause(identity)
        after = store.experiment(identity)
        assert after["phase"] == phase
        assert after["revision"] == before["revision"]
    finally:
        store.close()


def test_manual_pause_and_resume_only_change_running_paper_experiment(tmp_path):
    store, artifact = _store(tmp_path)
    try:
        identity = store.create(artifact.digest, now=1000.0, days=1)
        store.pause(identity)
        paused = store.experiment(identity)
        assert paused["phase"] == "paused"
        assert paused["revision"] == 1
        store.resume(identity, now=1001.0)
        resumed = store.experiment(identity)
        assert resumed["phase"] == "paper_validating"
        assert resumed["revision"] == 2
    finally:
        store.close()
