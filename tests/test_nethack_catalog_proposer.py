from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich.nethack_action_spec import load_action_catalog
from docich.nethack_canary_tactics import SUPPORTED_ACTION_IDS
from docich.nethack_catalog_proposer import (
    CatalogProposalError,
    CommandCatalogProposer,
    FailureSignal,
    build_proposal_request,
    catalog_to_dict,
    failure_signal_from_outcomes,
)
from docich.nethack_promotion_gate import EpisodeOutcome

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "nethack-canary-actions.json"


def outcome(*, seed=1, depth=3, turns=100, exit_reason="max_turns"):
    return EpisodeOutcome(
        seed=seed,
        arm="baseline",
        status="completed",
        terminal_status="timeout",
        turns=turns,
        max_depth=depth,
        score=None,
        exit_reason=exit_reason,
    )


def proposer_returning(payload: bytes, returncode: int = 0) -> CommandCatalogProposer:
    return CommandCatalogProposer(
        command=("proposer",),
        runner=lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=payload),
    )


def proposal_request():
    specs = load_action_catalog(CATALOG)
    signal = FailureSignal("policy_stall:seek_food", "seek_food", (), "x", 10, 1)
    return build_proposal_request(signal, specs, allowed_action_ids=SUPPORTED_ACTION_IDS)


def test_build_request_carries_signal_catalog_and_allowlist():
    request = proposal_request()
    assert request["schema_version"] == 1
    assert request["failure"]["stall_intent"] == "seek_food"
    assert set(request["allowed_actions"]) == set(SUPPORTED_ACTION_IDS)
    assert request["catalog"]["schema_version"] == 1
    assert request["constraints"]


def test_failure_signal_prefers_a_stall_over_fitness():
    outcomes = [
        outcome(seed=1, depth=9, exit_reason="wall_timeout"),
        outcome(seed=2, depth=1, exit_reason="policy_stall:seek_food"),
    ]
    signal = failure_signal_from_outcomes(outcomes)
    assert signal.stall_intent == "seek_food"
    assert signal.exit_reason == "policy_stall:seek_food"


def test_proposer_accepts_a_valid_reviewed_catalog():
    specs = load_action_catalog(CATALOG)
    payload = json.dumps(catalog_to_dict(specs)).encode("utf-8")
    proposed = proposer_returning(payload).propose(
        proposal_request(), allowed_action_ids=SUPPORTED_ACTION_IDS
    )
    assert {spec.id for spec in proposed} == set(SUPPORTED_ACTION_IDS)


def test_proposer_rejects_an_unknown_action_id():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"].append(
        {
            "id": "teleport",
            "risk_class": "movement",
            "preconditions": ["player_visible"],
            "key_pattern": ["{direction}"],
            "postconditions": ["always"],
        }
    )
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError):
        proposer_returning(payload).propose(
            proposal_request(), allowed_action_ids=SUPPORTED_ACTION_IDS
        )


def test_proposer_rejects_an_incomplete_action_set():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"] = raw["actions"][:-1]
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="full reviewed action set"):
        proposer_returning(payload).propose(
            proposal_request(), allowed_action_ids=SUPPORTED_ACTION_IDS
        )


def test_proposer_rejects_key_pattern_changes():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"][0]["key_pattern"] = ["z"]
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="fixed key_pattern"):
        proposer_returning(payload).propose(
            proposal_request(), allowed_action_ids=SUPPORTED_ACTION_IDS
        )


def test_proposer_rejects_risk_class_changes():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    current = raw["actions"][0]["risk_class"]
    raw["actions"][0]["risk_class"] = "rest" if current != "rest" else "movement"
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="fixed risk_class"):
        proposer_returning(payload).propose(
            proposal_request(), allowed_action_ids=SUPPORTED_ACTION_IDS
        )


def test_proposer_rejects_a_request_without_reviewed_baseline():
    specs = load_action_catalog(CATALOG)
    payload = json.dumps(catalog_to_dict(specs)).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="valid baseline catalog"):
        proposer_returning(payload).propose({}, allowed_action_ids=SUPPORTED_ACTION_IDS)


def test_proposer_fails_closed_on_nonzero_or_bad_json():
    request = proposal_request()
    with pytest.raises(CatalogProposalError):
        proposer_returning(b"", returncode=2).propose(
            request, allowed_action_ids=SUPPORTED_ACTION_IDS
        )
    with pytest.raises(CatalogProposalError):
        proposer_returning(b"not json").propose(
            request, allowed_action_ids=SUPPORTED_ACTION_IDS
        )


def test_proposer_timeout_is_bounded():
    with pytest.raises(ValueError):
        CommandCatalogProposer(command=("proposer",), timeout_s=1000.0)
