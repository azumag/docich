from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.free_strategy.contract import Artifact, StrategyError  # noqa: E402
from docich.trading.free_strategy.generation import generate  # noqa: E402
from docich.trading.free_strategy.store import (  # noqa: E402
    LabStore,
    MAX_OBSERVED_EXPERIMENTS,
)

IMAGE = "sha256:" + "a" * 64


def _artifact(number: int) -> Artifact:
    return Artifact.create(
        source=(
            "def decide(context): return "
            f"{{'schema_version':1,'target_positions':[],'state':{{'n':{number}}},'reason':'wait'}}"
        ),
        image=IMAGE,
        name=f"capacity-{number}",
        family="capacity",
        thesis=f"independent observation slot {number}",
        symbols=["BTC/JPY"],
    )


def _register_create(store: LabStore, number: int, *, now: float) -> str:
    artifact = _artifact(number)
    store.register(artifact)
    return store.create(artifact.digest, now=now, capital="10000", days=1)


def test_paused_experiment_still_consumes_observation_capacity(tmp_path):
    store = LabStore(tmp_path / "lab.sqlite3")
    try:
        first = _register_create(store, 1, now=1000.0)
        store.pause(first)
        second = _register_create(store, 2, now=1001.0)

        assert MAX_OBSERVED_EXPERIMENTS == 2
        assert store.observed_ids() == [first, second]
        assert store.active_ids() == [second]

        third = _artifact(3)
        store.register(third)
        with pytest.raises(StrategyError, match="experiment_capacity"):
            store.create(third.digest, now=1002.0, capital="10000", days=1)

        # Completing the paused observation frees exactly one research slot.
        with store.transaction():
            store.db.execute("UPDATE experiments SET phase='review_due' WHERE id=?", (first,))
        third_id = store.create(third.digest, now=1003.0, capital="10000", days=1)
        assert store.observed_ids() == [second, third_id]
    finally:
        store.close()


def test_generation_does_not_call_provider_when_paused_plus_active_fill_slots(tmp_path):
    trading_dir = tmp_path / "trading"
    store = LabStore(trading_dir / "free-strategies" / "lab.sqlite3")
    try:
        first = _register_create(store, 1, now=1000.0)
        store.pause(first)
        _register_create(store, 2, now=1001.0)
    finally:
        store.close()

    calls: list[str] = []

    def provider(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({
            "source": "def decide(context): return {'schema_version':1,'target_positions':[],'state':{},'reason':'wait'}",
            "name": "must-not-be-requested",
            "family": "capacity",
            "thesis": "provider should not be called",
        })

    result = generate(
        trading_dir,
        image=IMAGE,
        symbols=["BTC/JPY"],
        brief="capacity test",
        now_fn=lambda: 2000.0,
        text_fn=provider,
    )
    assert result == {"status": "skipped", "reason": "experiment_capacity"}
    assert calls == []
