from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docich.nethack_canary_worker import (
    ARENA,
    CanaryWorkerError,
    _candidate_strategist,
    _read_request,
    _terminal_result,
)


def baseline_request():
    return {
        "schema_version": 1,
        "experiment_id": "exp",
        "episode_id": "000",
        "arm": "baseline",
        "arena": dict(ARENA),
        "player_name": "canary_b_000",
        "max_turns": 1000,
        "seed": 42,
        "controller": {"kind": "baseline_p3b"},
        "requirements": {
            "isolation_mode": "container",
            "production_state_must_remain_untouched": True,
            "wizard_mode": False,
            "explore_mode": False,
        },
    }


def candidate_request():
    raw = baseline_request()
    raw["arm"] = "candidate"
    raw["player_name"] = "canary_c_000"
    raw["controller"] = {
        "kind": "candidate_strategist",
        "manifest_path": "/canary/candidate.json",
        "candidate_id": "cand",
        "candidate_version": "v1",
        "candidate_fingerprint": "f" * 64,
        "command_sha256": "c" * 64,
    }
    return raw


def test_worker_request_requires_fixed_internal_arena_and_normal_play():
    raw = baseline_request()
    parsed = _read_request(json.dumps(raw))
    assert parsed["arena"] == ARENA

    raw["arena"]["save_dir"] = "/var/games/nethack/save"
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))

    raw = baseline_request()
    raw["requirements"]["wizard_mode"] = True
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))


def test_worker_request_rejects_unknown_surface():
    raw = baseline_request()
    raw["production_path"] = "/var/games/nethack"
    with pytest.raises(CanaryWorkerError):
        _read_request(json.dumps(raw))


def test_terminal_result_uses_xlog_facts_and_never_claims_seed_control():
    request = candidate_request()
    record = {
        "name": "canary_c_000",
        "death": "killed by a grid bug",
        "points": "123",
        "turns": "456",
        "maxlvl": "7",
        "achieve": "0x20",
    }
    result = _terminal_result(request, record, exit_reason="terminal_xlog")
    assert result["terminal_status"] == "dead"
    assert result["score"] == 123
    assert result["turns"] == 456
    assert result["max_depth"] == 7
    assert result["got_amulet"] is True
    assert result["seed"] == 42
    assert result["seed_applied"] is False
    assert result["candidate_action_source"] == "candidate_strategist"
    assert result["production_state_touched"] is False


def test_candidate_manifest_identity_is_rechecked_inside_container():
    request = candidate_request()
    manifest = SimpleNamespace(
        candidate_id="cand",
        version="v1",
        fingerprint="f" * 64,
        command_hash="c" * 64,
        command=("python3", "brains/candidate.py"),
        timeout_s=2.0,
        max_request_bytes=32768,
        max_response_bytes=16384,
    )
    with patch("docich.nethack_canary_worker.load_candidate_manifest", return_value=manifest), patch(
        "docich.nethack_canary_worker.CommandStrategist"
    ) as strategist:
        _candidate_strategist(request)
    strategist.assert_called_once()

    request["controller"]["candidate_fingerprint"] = "0" * 64
    with patch("docich.nethack_canary_worker.load_candidate_manifest", return_value=manifest):
        with pytest.raises(CanaryWorkerError):
            _candidate_strategist(request)


def test_baseline_never_constructs_candidate_strategist():
    with patch("docich.nethack_canary_worker.load_candidate_manifest") as loader:
        assert _candidate_strategist(baseline_request()) is None
    loader.assert_not_called()
