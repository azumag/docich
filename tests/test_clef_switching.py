"""Keyless, synthetic Clef transport and reversible owner-only route switches."""
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.semantic_decision import transport as transport
from docich.semantic_decision.routes import parse_route_chain, resolve_endpoint, resolve_route
from docich.semantic_decision.validator import dumps, validate_wire_response

spec = importlib.util.spec_from_file_location("clef_route_switch", ROOT / "ops/vm_actions/configure_jev_route.py")
switch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(switch)
ACCOUNT_KEY, TOKEN_KEY = switch.CLEF_KEYS
ACCOUNT = "a" * 32
JEV = "SYNTHETIC_JEV_KEY"
CLEF = "SYNTHETIC_CLEF_KEY"
BOTH = {"TYPESAFE_API_KEY": JEV, ACCOUNT_KEY: ACCOUNT, TOKEN_KEY: CLEF}


def values(path):
    result = {}
    for line in path.read_text().splitlines():
        match = switch.ASSIGNMENT.match(line)
        if match:
            value = shlex.split(line[match.end():], comments=True)
            result[match.group(1)] = value[0] if value else ""
    return result


@pytest.fixture
def envfile(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# Keep existing settings\nCOMMENT_CLASSIFIER_BACKEND=jev\n"
                    "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=1500\n"
                    "COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE=0.70\n"
                    f"TYPESAFE_API_KEY={JEV}\nUNRELATED=unchanged\nDOCICH_JEV_ROUTE=direct\n")
    path.chmod(0o600)
    return path


def run_switch(path, route, *, supplied=None, replacement=None):
    old = values(path)
    def restart(root, *, verify):
        assert root == path.parent
        verify(values(path) if replacement is None else replacement)
        return 1111, 2222
    with patch.dict(os.environ, supplied or {}, clear=True), \
            patch.object(sys, "argv", ["configure_jev_route.py", "--soren-root", str(path.parent), "--route", route]), \
            patch.object(switch._classifier, "_read_worker_pid", return_value=1111), \
            patch.object(switch._classifier, "_process_env", return_value=old), \
            patch.object(switch, "restart_chat_worker_verified", side_effect=restart) as restarted:
        assert switch.main() == 0
    restarted.assert_called_once()


def test_direct_clef_direct_clef_roundtrip_needs_credentials_only_once(envfile, capsys):
    original = values(envfile)
    run_switch(envfile, "clef", supplied={ACCOUNT_KEY: ACCOUNT, TOKEN_KEY: CLEF})
    for route in ("direct", "clef", "direct", "clef"):
        run_switch(envfile, route)
        actual = values(envfile)
        assert actual["DOCICH_JEV_ROUTE"] == route
        assert all(actual[key] == value for key, value in BOTH.items())
        for key, value in original.items():
            if key != "DOCICH_JEV_ROUTE":
                assert actual[key] == value
    output = capsys.readouterr().out
    assert JEV not in output and CLEF not in output and ACCOUNT not in output
    assert len(output.splitlines()) == 5
    assert stat.S_IMODE(envfile.stat().st_mode) == 0o600
    backup_dir = envfile.parent / ".codex_deploy/comment-classifier-jev"
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert len(list(backup_dir.iterdir())) == 5
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in backup_dir.iterdir())


@pytest.mark.parametrize("route", ["direct", "clef"])
def test_missing_target_credentials_refuses_before_any_write_or_restart(envfile, route):
    if route == "direct":
        envfile.write_text(envfile.read_text().replace(f"TYPESAFE_API_KEY={JEV}\n", ""))
    before = envfile.read_bytes()
    with pytest.raises(switch.ConfigureError), patch.object(switch, "_write_atomic") as write:
        run_switch(envfile, route)
    assert envfile.read_bytes() == before
    write.assert_not_called()


def test_clef_requires_retaining_existing_jev_rollback_credential(envfile):
    envfile.write_text(envfile.read_text().replace(f"TYPESAFE_API_KEY={JEV}\n", ""))
    before = envfile.read_bytes()
    with pytest.raises(switch.ConfigureError, match="clef_switch_requires_existing_typesafe_api_key"):
        run_switch(envfile, "clef", supplied={ACCOUNT_KEY: ACCOUNT, TOKEN_KEY: CLEF})
    assert envfile.read_bytes() == before


@pytest.mark.parametrize("supplied", [{ACCOUNT_KEY: "b" * 32}, {TOKEN_KEY: "OTHER_SYNTHETIC_TOKEN"},
                                      {ACCOUNT_KEY: "", TOKEN_KEY: ""}])
def test_partial_explicit_pair_never_combines_with_existing_credentials(envfile, supplied):
    switch.configure_clef_env(envfile, ACCOUNT, CLEF)
    before = envfile.read_bytes()
    with pytest.raises(switch.ConfigureError):
        run_switch(envfile, "clef", supplied=supplied)
    assert envfile.read_bytes() == before


@pytest.mark.parametrize("account,token", [("../path", CLEF), ("g" * 32, CLEF),
                                           ("a" * 31, CLEF), (ACCOUNT, "bad\nkey"),
                                           (ACCOUNT, ""), (ACCOUNT, "x" * 4097)])
def test_invalid_clef_settings_never_mutate_environment(envfile, account, token):
    before = envfile.read_bytes()
    with pytest.raises(switch.ConfigureError):
        switch.configure_clef_env(envfile, account, token)
    assert envfile.read_bytes() == before


def test_switch_secret_quoting_cannot_execute_shell(envfile, tmp_path):
    marker = tmp_path / "must_not_exist"
    token = f"$(touch${{IFS}}{marker})'\";SAFE"
    switch.configure_clef_env(envfile, ACCOUNT, token)
    result = subprocess.run(["/bin/bash", "-c", '. "$1"; printf "%s" "$DOCICH_JEV_CLOUDFLARE_API_TOKEN"',
                             "test", str(envfile)], capture_output=True, text=True, check=True, env={})
    assert result.stdout == token
    assert not marker.exists()
    assert values(envfile)["TYPESAFE_API_KEY"] == JEV


def test_disable_route_keeps_credentials_available_for_both_providers(envfile):
    switch.configure_clef_env(envfile, ACCOUNT, CLEF)
    switch.disable_env(envfile)
    actual = values(envfile)
    assert "DOCICH_JEV_ROUTE" not in actual
    assert all(actual[key] == value for key, value in BOTH.items())
    switch.verify_jev_route(None, retained_credentials=BOTH)(actual)


@pytest.mark.parametrize("lost_key", ["TYPESAFE_API_KEY", ACCOUNT_KEY, TOKEN_KEY])
def test_replacement_cannot_report_success_if_a_retained_credential_disappears(envfile, lost_key, capsys):
    switch.configure_clef_env(envfile, ACCOUNT, CLEF)
    replacement = {**values(envfile), "DOCICH_JEV_ROUTE": "direct"}
    replacement.pop(lost_key)
    with pytest.raises(switch.ConfigureError, match="retained_credential_mismatch"):
        run_switch(envfile, "direct", replacement=replacement)
    assert capsys.readouterr().out == ""


def test_existing_vercel_configuration_contract_unchanged(envfile):
    switch.configure_vercel_env(envfile, "SYNTHETIC_VERCEL_KEY")
    actual = values(envfile)
    assert actual["DOCICH_JEV_ROUTE"] == "direct,vercel"
    switch.verify_jev_route("vercel")(actual)
    switch.configure_direct_env(envfile)
    assert "DOCICH_JEV_VERCEL_API_KEY" not in values(envfile)
    assert switch.MANAGED_KEYS == ("DOCICH_JEV_ROUTE", "DOCICH_JEV_VERCEL_API_KEY")


def example(route):
    model = resolve_route(route).requested_model
    request = {"model": model, "state": "Synthetic input", "questions": {
        "route": {"type": "choice", "instructions": "Choose one.", "criteria": {"yes": "Yes", "no": "No"}}}}
    answer = {"model": model, "answers": {"route": {"type": "choice", "choice": "yes",
        "confidence": 0.9, "probabilities": {"yes": 0.95, "no": 0.05}}},
        "usage": {"input_tokens": 20, "output_tokens": 0}}
    return request, answer


class Response(io.BytesIO):
    status = 200


@pytest.mark.parametrize("route", ["direct", "clef", "vercel"])
def test_fixed_endpoint_models_and_credentials_follow_selected_route_only(route):
    request, answer = example(route)
    profile = resolve_route(route)
    wire = {"success": True, "errors": [], "result": answer} if route == "clef" else answer
    env = {**BOTH, "DOCICH_JEV_VERCEL_API_KEY": "SYNTHETIC_VERCEL_KEY", "DOCICH_JEV_ROUTE": route,
           "HTTPS_PROXY": "https://not-used.example", "DOCICH_JEV_ENDPOINT": "https://not-used.example"}
    child_env = {}
    def run(argv, *, data, timeout, env):
        child_env.update(env)
        return dumps(transport._http_worker(json.loads(data), argv[-2], timeout, env)).encode()
    with patch.object(transport, "_bounded_process", side_effect=run) as spawn, \
            patch.object(transport.urllib.request, "build_opener") as factory:
        factory.return_value.open.return_value = Response(dumps(wire).encode())
        result = transport.request_once(request, env=env)
        sent = factory.return_value.open.call_args.args[0]
    assert result["status"] == "ok" and result["data"] == answer
    assert sent.full_url == resolve_endpoint(profile, env)
    assert sent.get_header("Authorization") == "Bearer " + env[profile.credential_env]
    assert child_env == {profile.credential_env: env[profile.credential_env], "LANG": "C.UTF-8",
                         **({ACCOUNT_KEY: ACCOUNT} if route == "clef" else {})}
    assert result["meta"]["requested_model"] == result["meta"]["resolved_model"] == profile.requested_model
    assert result["meta"]["route"] == route
    assert JEV not in dumps(result) and CLEF not in dumps(result)
    spawn.assert_called_once()


@pytest.mark.parametrize("account", [None, "", "x" * 32, "../path", "a" * 32 + "\n"])
def test_invalid_account_stops_before_child(account):
    request, _ = example("clef")
    with patch.object(transport, "_bounded_process") as spawn:
        assert transport.request_once(request, route="clef", env={**BOTH, ACCOUNT_KEY: account})["status"] == "invalid_config"
    spawn.assert_not_called()


@pytest.mark.parametrize("bad", [{"success": False}, {"success": 1}, {"errors": ["private"]},
                                  {"errors": None}, {"result": None}])
def test_failed_cloudflare_envelope_rejected(bad):
    request, answer = example("clef")
    wire = {"success": True, "errors": [], "result": answer, **bad}
    with pytest.raises(ValueError):
        validate_wire_response(wire, request, resolve_route("clef"))


@pytest.mark.parametrize("mutation", ["model", "confidence", "probabilities", "usage"])
def test_strict_answer_validation_is_not_weakened(mutation):
    request, answer = example("clef")
    if mutation == "model":
        answer["model"] = "jev-1.13.0"
    elif mutation == "confidence":
        answer["answers"]["route"]["confidence"] = True
    elif mutation == "probabilities":
        answer["answers"]["route"]["probabilities"]["yes"] = 0.7
    else:
        answer["usage"]["input_tokens"] = -1
    with pytest.raises(ValueError):
        validate_wire_response({"success": True, "errors": [], "result": answer}, request, resolve_route("clef"))


def test_no_implicit_provider_fallback_on_timeout_or_missing_key():
    assert parse_route_chain("clef") == ("clef",)
    assert parse_route_chain("direct") == ("direct",)
    request, _ = example("clef")
    with patch.object(transport, "_bounded_process", side_effect=subprocess.TimeoutExpired("test", 1)) as spawn:
        assert transport.request_once(request, route="clef", env=BOTH)["status"] == "timeout"
    spawn.assert_called_once()
    with patch.object(transport, "_bounded_process") as spawn:
        assert transport.request_once(request, route="clef", env={ACCOUNT_KEY: ACCOUNT, "TYPESAFE_API_KEY": JEV})["status"] == "missing_key"
    spawn.assert_not_called()
