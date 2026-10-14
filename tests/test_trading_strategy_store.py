from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from docich.trading.strategies import StrategyPolicy  # noqa: E402
from docich.trading.strategy_store import (  # noqa: E402
    StrategyStoreError,
    load_strategy_policy,
    policy_from_mapping,
    policy_to_payload,
    save_strategy_policy,
    strategy_policy_path,
)

D = Decimal


def test_round_trip_persists_and_loads(tmp_path):
    policy = StrategyPolicy(
        momentum_lookback=8,
        momentum_threshold_bps=D("250"),
        mean_reversion_lookback=12,
        mean_reversion_z=D("-2.0"),
        max_notional_fraction=D("0.2"),
    )
    path = save_strategy_policy(tmp_path, policy)
    assert path == strategy_policy_path(tmp_path)
    assert load_strategy_policy(tmp_path) == policy
    assert path.stat().st_mode & 0o777 == 0o600
    # only the five allowlisted keys are written
    assert set(json.loads(path.read_text(encoding="utf-8"))) == set(policy_to_payload(policy))


def test_missing_and_invalid_fall_back_to_default(tmp_path):
    assert load_strategy_policy(tmp_path) == StrategyPolicy()

    (tmp_path / "strategy_policy.json").write_text("{not json", encoding="utf-8")
    assert load_strategy_policy(tmp_path) == StrategyPolicy()

    (tmp_path / "strategy_policy.json").write_text(
        json.dumps({"momentum_lookback": 0}), encoding="utf-8"
    )
    assert load_strategy_policy(tmp_path) == StrategyPolicy()

    bad = policy_to_payload(StrategyPolicy())
    bad["max_notional_fraction"] = "2"
    (tmp_path / "strategy_policy.json").write_text(json.dumps(bad), encoding="utf-8")
    assert load_strategy_policy(tmp_path) == StrategyPolicy()


def test_custom_fallback_is_used(tmp_path):
    custom = StrategyPolicy(momentum_lookback=4, momentum_threshold_bps=D("50"))
    assert load_strategy_policy(tmp_path, fallback=custom) == custom


def test_policy_from_mapping_rejects_bad_shapes(tmp_path):
    with pytest.raises(StrategyStoreError):
        policy_from_mapping({"momentum_lookback": 6})

    invalid = {
        "momentum_lookback": 6,
        "momentum_threshold_bps": "300",
        "mean_reversion_lookback": 10,
        "mean_reversion_z": "1.5",  # must be negative
        "max_notional_fraction": "0.15",
    }
    with pytest.raises(StrategyStoreError):
        policy_from_mapping(invalid)


def test_policy_from_mapping_accepts_numeric_strings():
    policy = policy_from_mapping(
        {
            "momentum_lookback": "6",
            "momentum_threshold_bps": "300",
            "mean_reversion_lookback": 10,
            "mean_reversion_z": "-1.5",
            "max_notional_fraction": "0.15",
        }
    )
    assert policy == StrategyPolicy()
