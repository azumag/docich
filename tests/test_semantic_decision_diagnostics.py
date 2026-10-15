"""Pure, secret-free diagnostics projection contracts (#882)."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.semantic_decision import diagnostics

NO_FALLBACK = {"fallback_route": None, "fallback_credential": "not_applicable",
               "fallback_account": "not_applicable"}
# A syntactically valid Cloudflare account ID: 32 lowercase hex characters.
CLEF_ACCOUNT = "0123456789abcdef0123456789abcdef"
UNKNOWN = {"backend": "unknown", "route": None, "requested_model": None, "credential": "unknown",
           "account": "unknown", "fallback_route": None, "fallback_credential": "unknown",
           "fallback_account": "unknown"}


def expected(**overrides):
    """The fixed projection for one primary route with no fallback."""
    shape = {"backend": "jev", "route": None, "requested_model": None,
             "credential": "not_applicable", "account": "not_applicable", **NO_FALLBACK}
    shape.update(overrides)
    return shape


def test_unset_backend_reports_heuristic_with_no_route_or_model():
    assert diagnostics.describe({}) == expected(backend="heuristic")


@pytest.mark.parametrize("value", ["", "legacy", "Jev", "JEV", " jev", None, 0, "vercel"])
def test_non_exact_jev_backend_reports_heuristic(value):
    assert diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": value}) == expected(backend="heuristic")


def test_non_dict_env_reports_unknown_not_heuristic():
    for env in (None, [], "jev", 1, object()):
        assert diagnostics.describe(env) == UNKNOWN


def test_jev_direct_default_route_with_credential_present():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "SYNTHETIC_KEY"})
    assert result == expected(route="direct", requested_model="jev-1.13.0", credential="present")
    assert "SYNTHETIC_KEY" not in str(result)


def test_jev_direct_explicit_route_with_credential_absent():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct"})
    assert result == expected(route="direct", requested_model="jev-1.13.0", credential="absent")


def test_jev_vercel_route_reads_its_own_credential_only():
    # The direct-route key being present must not leak into a vercel report,
    # and vice versa: each route only ever inspects its own credential name.
    result = diagnostics.describe({
        "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "vercel",
        "TYPESAFE_API_KEY": "SYNTHETIC_DIRECT_KEY",
    })
    assert result == expected(route="vercel", requested_model="typesafe-ai/jev", credential="absent")

    result = diagnostics.describe({
        "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "vercel",
        "DOCICH_JEV_VERCEL_API_KEY": "SYNTHETIC_VERCEL_KEY",
    })
    assert result == expected(route="vercel", requested_model="typesafe-ai/jev", credential="present")
    assert "SYNTHETIC_VERCEL_KEY" not in str(result)


def test_clef_route_reports_its_own_model_credential_and_account_shape():
    # #2301: an owner must be able to tell from diagnostics alone whether the
    # selected Clef route actually holds both halves of its credential pair.
    result = diagnostics.describe({
        "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "clef",
        "DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID": CLEF_ACCOUNT,
        "DOCICH_JEV_CLOUDFLARE_API_TOKEN": "SYNTHETIC_CLEF_TOKEN",
        # Neither of the other providers' keys may be read for a clef route.
        "TYPESAFE_API_KEY": "SYNTHETIC_DIRECT_KEY",
        "DOCICH_JEV_VERCEL_API_KEY": "SYNTHETIC_VERCEL_KEY",
    })
    assert result == expected(route="clef", requested_model="clef", credential="present", account="valid")
    assert "SYNTHETIC" not in str(result)
    assert CLEF_ACCOUNT not in str(result)


@pytest.mark.parametrize("account,shape", [
    (None, "absent"),
    ("", "absent"),
    (CLEF_ACCOUNT.upper(), "invalid"),
    (CLEF_ACCOUNT[:-1], "invalid"),
    ("0" * 31 + "z", "invalid"),
    (123, "absent"),
])
def test_clef_account_is_reported_by_shape_never_by_value(account, shape):
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "clef",
           "DOCICH_JEV_CLOUDFLARE_API_TOKEN": "SYNTHETIC_CLEF_TOKEN"}
    if account is not None:
        env["DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID"] = account
    result = diagnostics.describe(env)
    assert result["account"] == shape
    assert result["credential"] == "present"
    assert "SYNTHETIC_CLEF_TOKEN" not in str(result)


def test_clef_account_field_matches_the_endpoint_resolver():
    # A "valid" projection must be exactly the shape resolve_endpoint accepts,
    # and every other value must be one it rejects.
    from docich.semantic_decision.routes import resolve_endpoint, resolve_route
    profile = resolve_route("clef")
    assert resolve_endpoint(profile, {"DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID": CLEF_ACCOUNT}).endswith(
        "/accounts/%s/ai/run/@cf/cloudflare/clef" % CLEF_ACCOUNT)
    for bad in ("", CLEF_ACCOUNT.upper(), CLEF_ACCOUNT[:-1]):
        assert diagnostics.describe({
            "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "clef",
            "DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID": bad})["account"] != "valid"
        with pytest.raises(ValueError):
            resolve_endpoint(profile, {"DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID": bad})


def test_clef_as_fallback_reports_its_account_separately():
    result = diagnostics.describe({
        "COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct,clef",
        "TYPESAFE_API_KEY": "SYNTHETIC_DIRECT_KEY",
        "DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID": CLEF_ACCOUNT,
    })
    assert result == {**expected(route="direct", requested_model="jev-1.13.0", credential="present"),
                      "fallback_route": "clef", "fallback_credential": "absent",
                      "fallback_account": "valid"}
    assert "SYNTHETIC" not in str(result)


def test_invalid_route_is_reported_not_guessed_or_defaulted():
    assert diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev",
                                 "DOCICH_JEV_ROUTE": "not-a-route"}) == {**UNKNOWN, "backend": "jev",
                                                                         "route": "invalid"}


@pytest.mark.parametrize("key_value", [123, True, [], {}, ""])
def test_non_string_or_empty_credential_value_is_absent_not_a_crash(key_value):
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": key_value})
    assert result["credential"] == "absent"


def test_output_never_contains_a_credential_env_name_pointing_to_its_value():
    env = {"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "SHOULD_NEVER_APPEAR"}
    result = diagnostics.describe(env)
    assert set(result) == {"backend", "route", "requested_model", "credential", "account",
                           "fallback_route", "fallback_credential", "fallback_account"}
    assert all(value != "SHOULD_NEVER_APPEAR" for value in result.values())


def test_retired_docich_semantic_backend_switch_is_ignored():
    # Only COMMENT_CLASSIFIER_BACKEND gates the live docich classifier.
    assert diagnostics.describe({"DOCICH_SEMANTIC_BACKEND": "jev"})["backend"] == "heuristic"
    assert diagnostics.describe({"DOCICH_SEMANTIC_BACKEND": "", "COMMENT_CLASSIFIER_BACKEND": "jev",
                                 "TYPESAFE_API_KEY": "K"})["backend"] == "jev"


def test_gate_and_route_match_what_the_classifier_reads():
    from docich.comment_classifier import jev
    for env in ({"COMMENT_CLASSIFIER_BACKEND": "jev"},
                {"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "vercel"},
                {"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "clef"}):
        assert diagnostics.describe(env)["route"] == jev.Config.from_env(env).route


def test_route_chain_reports_primary_and_fallback_presence_separately():
    result = diagnostics.describe({"COMMENT_CLASSIFIER_BACKEND": "jev", "DOCICH_JEV_ROUTE": "direct,vercel",
                                   "TYPESAFE_API_KEY": "SYNTHETIC_DIRECT_KEY"})
    assert result == {**expected(route="direct", requested_model="jev-1.13.0", credential="present"),
                      "fallback_route": "vercel", "fallback_credential": "absent",
                      "fallback_account": "not_applicable"}
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
