from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich.nethack_action_spec import REVIEWED_EFFECTS, load_action_catalog
from docich.nethack_canary_tactics import SUPPORTED_EFFECTS
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
    return build_proposal_request(signal, specs, allowed_effects=SUPPORTED_EFFECTS)


def propose_kwargs():
    return {"allowed_effects": SUPPORTED_EFFECTS}


def test_build_request_carries_signal_catalog_and_allowlist():
    request = proposal_request()
    assert request["schema_version"] == 1
    assert request["failure"]["stall_intent"] == "seek_food"
    assert set(request["allowed_effects"]) == set(SUPPORTED_EFFECTS)
    assert set(request["allowed_effects"]) == set(REVIEWED_EFFECTS)
    assert "keys" not in request["allowed_new_action_effects"]
    assert set(request["allowed_new_action_effects"]) == set(SUPPORTED_EFFECTS) - {"keys"}
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
    proposed = proposer_returning(payload).propose(proposal_request(), **propose_kwargs())
    assert {spec.id for spec in proposed} == {spec.id for spec in specs}


def test_proposer_accepts_a_new_id_with_a_fixed_reviewed_effect():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"].append(
        {
            "id": "attack_adjacent_backup",
            "effect": "attack_direction",
            "risk_class": "combat",
            "preconditions": ["prompt:none", "player_visible", "adjacent_attackable"],
            "key_pattern": ["{direction}"],
            "postconditions": ["screen_changed"],
        }
    )
    payload = json.dumps(raw).encode("utf-8")
    proposed = proposer_returning(payload).propose(proposal_request(), **propose_kwargs())
    assert "attack_adjacent_backup" in {spec.id for spec in proposed}


def test_proposer_rejects_new_id_with_generic_literal_keys_effect():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"].append(
        {
            "id": "quit_game",
            "effect": "keys",
            "risk_class": "movement",
            "preconditions": ["prompt:none", "player_visible"],
            "key_pattern": ["Q"],
            "postconditions": ["always"],
        }
    )
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="non-extensible generic effect 'keys'"):
        proposer_returning(payload).propose(proposal_request(), **propose_kwargs())


def test_proposer_rejects_a_new_id_with_an_unreviewed_effect():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"].append(
        {
            "id": "teleport",
            "effect": "zap_wand",
            "risk_class": "movement",
            "preconditions": ["player_visible"],
            "key_pattern": ["{direction}"],
            "postconditions": ["always"],
        }
    )
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError):
        proposer_returning(payload).propose(proposal_request(), **propose_kwargs())


def test_proposer_rejects_an_incomplete_action_set():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"] = raw["actions"][:-1]
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="dropped reviewed actions"):
        proposer_returning(payload).propose(proposal_request(), **propose_kwargs())


def test_proposer_rejects_key_pattern_changes():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"][0]["key_pattern"] = ["z"]
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="fixed key_pattern"):
        proposer_returning(payload).propose(proposal_request(), **propose_kwargs())


def test_proposer_rejects_effect_changes():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    raw["actions"][0]["effect"] = "attack_direction"
    raw["actions"][0]["key_pattern"] = ["{direction}"]
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="fixed effect"):
        proposer_returning(payload).propose(proposal_request(), **propose_kwargs())


def test_proposer_rejects_risk_class_changes():
    specs = load_action_catalog(CATALOG)
    raw = catalog_to_dict(specs)
    current = raw["actions"][0]["risk_class"]
    raw["actions"][0]["risk_class"] = "rest" if current != "rest" else "movement"
    payload = json.dumps(raw).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="fixed risk_class"):
        proposer_returning(payload).propose(proposal_request(), **propose_kwargs())


def test_proposer_rejects_a_request_without_reviewed_baseline():
    specs = load_action_catalog(CATALOG)
    payload = json.dumps(catalog_to_dict(specs)).encode("utf-8")
    with pytest.raises(CatalogProposalError, match="valid baseline catalog"):
        proposer_returning(payload).propose({}, **propose_kwargs())


def test_proposer_fails_closed_on_nonzero_or_bad_json():
    request = proposal_request()
    with pytest.raises(CatalogProposalError):
        proposer_returning(b"", returncode=2).propose(request, **propose_kwargs())
    with pytest.raises(CatalogProposalError):
        proposer_returning(b"not json").propose(request, **propose_kwargs())


def test_proposer_timeout_is_bounded():
    with pytest.raises(ValueError):
        CommandCatalogProposer(command=("proposer",), timeout_s=1000.0)


def test_proposer_legacy_allowlist_still_restricts_ids():
    # Backward compatibility: callers that still pass allowed_action_ids get
    # the P6f id-set boundary on top of the effect vocabulary boundary.
    specs = load_action_catalog(CATALOG)
    payload = json.dumps(catalog_to_dict(specs)).encode("utf-8")
    with pytest.raises(CatalogProposalError):
        proposer_returning(payload).propose(
            proposal_request(),
            allowed_effects=SUPPORTED_EFFECTS,
            allowed_action_ids=frozenset({"rest"}),
        )
