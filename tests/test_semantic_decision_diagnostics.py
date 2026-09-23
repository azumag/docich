"""Pure, secret-free diagnostics projection contracts (#882)."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.semantic_decision import diagnostics

NO_FALLBACK = {"fallback_route": None, "fallback_credential": "not_applicable"}


def test_unset_backend_reports_heuristic_with_no_route_or_model():
    assert diagnostics.describe({}) == {
        "backend": "heuristic", "route": None, "requested_model": None, "credential": "not_applicable",
        "fallback_route": None, "fallback_credential": "not_applicable",
    }


@pytest.mark.parametrize("value", ["", "legacy", "Jev", "JEV", " jev", None, 0, "vercel"])
def test_non_exact_jev_backend_reports_heuristic(value):
    assert diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": value}) == {
        "backend": "heuristic", "route": None, "requested_model": None, "credential": "not_applicable",
        "fallback_route": None, "fallback_credential": "not_applicable",
    }


def test_non_dict_env_reports_unknown_not_heuristic():
    for env in (None, [], "jev", 1, object()):
        assert diagnostics.describe(env) == {
            "backend": "unknown", "route": None, "requested_model": None, "credential": "unknown",
            "fallback_route": None, "fallback_credential": "unknown",
        }


def test_jev_direct_default_route_with_credential_present():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "SYNTHETIC_KEY"})
    assert result == {"backend": "jev", "route": "direct",
                      "requested_model": "jev-1.13.0", "credential": "present", **NO_FALLBACK}
    assert "SYNTHETIC_KEY" not in str(result)


def test_jev_direct_explicit_route_with_credential_absent():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct"})
    assert result == {"backend": "jev", "route": "direct",
                      "requested_model": "jev-1.13.0", "credential": "absent", **NO_FALLBACK}


def test_jev_vercel_route_reads_its_own_credential_only():
    # The direct-route key being present must not leak into a vercel report,
    # and vice versa: each route only ever inspects its own credential name.
    result = diagnostics.describe({
        "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "vercel",
        "TYPESAFE_API_KEY": "SYNTHETIC_DIRECT_KEY",
    })
    assert result == {"backend": "jev", "route": "vercel",
                      "requested_model": "typesafe-ai/jev", "credential": "absent", **NO_FALLBACK}

    result = diagnostics.describe({
        "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "vercel",
        "DOCICH_JEV_VERCEL_API_KEY": "SYNTHETIC_VERCEL_KEY",
    })
    assert result == {"backend": "jev", "route": "vercel",
                      "requested_model": "typesafe-ai/jev", "credential": "present", **NO_FALLBACK}
    assert "SYNTHETIC_VERCEL_KEY" not in str(result)


def test_invalid_route_is_reported_not_guessed_or_defaulted():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "not-a-route"})
    assert result == {"backend": "jev", "route": "invalid", "requested_model": None, "credential": "unknown",
                      "fallback_route": None, "fallback_credential": "unknown"}


@pytest.mark.parametrize("key_value", [123, True, [], {}, ""])
def test_non_string_or_empty_credential_value_is_absent_not_a_crash(key_value):
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": key_value})
    assert result["credential"] == "absent"


def test_output_never_contains_a_credential_env_name_pointing_to_its_value():
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "SHOULD_NEVER_APPEAR"}
    result = diagnostics.describe(env)
    assert set(result) == {"backend", "route", "requested_model", "credential",
                           "fallback_route", "fallback_credential"}
    assert all(value != "SHOULD_NEVER_APPEAR" for value in result.values())


def test_retired_docich_semantic_backend_switch_is_ignored():
    # Only COMMENT_CLASSIFIER_BACKEND gates the live docich classifier.
    assert diagnostics.describe({"DOCICH_SEMANTIC_BACKEND": "jev"})["backend"] == "heuristic"
    assert diagnostics.describe({"DOCICH_SEMANTIC_BACKEND": "", "COMMENT_CLASSIFIER_BACKEND": "jev",
                                 "TYPESAFE_API_KEY": "K"})["backend"] == "jev"


def test_gate_and_route_match_what_the_classifier_reads():
    from docich.comment_classifier import jev
    for env in ({"COMMENT_CLASSIFIER_BACKEND": "jev"},
                {"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "vercel"}):
        assert diagnostics.describe(env)["route"] == jev.Config.from_env(env).route


def test_route_chain_reports_primary_and_fallback_presence_separately():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct,vercel",
                                   "TYPESAFE_API_KEY": "SYNTHETIC_DIRECT_KEY"})
    assert result == {"backend": "jev", "route": "direct", "requested_model": "jev-1.13.0",
                      "credential": "present", "fallback_route": "vercel", "fallback_credential": "absent"}
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct,vercel",
                                   "DOCICH_JEV_VERCEL_API_KEY": "SYNTHETIC_VERCEL_KEY"})
    assert (result["credential"], result["fallback_credential"]) == ("absent", "present")
    assert "SYNTHETIC" not in str(result)


@pytest.mark.parametrize("chain", ["direct,direct", "direct, vercel", "direct,vercel,direct"])
def test_malformed_chain_is_invalid_not_guessed(chain):
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": chain})
    assert result["route"] == "invalid" and result["fallback_route"] is None


def test_chain_matches_what_the_classifier_reads():
    from docich.comment_classifier import jev
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct,vercel"}
    result = diagnostics.describe(env)
    assert (result["route"], result["fallback_route"]) == jev.Config.from_env(env).chain
