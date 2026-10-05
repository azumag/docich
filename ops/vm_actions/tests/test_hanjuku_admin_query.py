import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_hanjuku_admin_query.py"
WORKFLOW = ROOT / ".github/workflows/hanjuku-admin-query.yml"
WORKFLOW_REF = "azumag/docich/.github/workflows/hanjuku-admin-query.yml@refs/heads/main"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_hanjuku_admin_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def auth_env(body="/hanjuku-admin-check production", **overrides):
    env = {
        "GITHUB_REPOSITORY": "azumag/docich",
        "GITHUB_REPOSITORY_ID": "1327276249",
        "GITHUB_REPOSITORY_OWNER": "azumag",
        "GITHUB_REPOSITORY_OWNER_ID": "9018513",
        "GITHUB_ACTOR": "azumag",
        "GITHUB_ACTOR_ID": "9018513",
        "GITHUB_TRIGGERING_ACTOR": "azumag",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_DEFAULT_BRANCH": "main",
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "GITHUB_EVENT_NAME": "issue_comment",
        "GITHUB_EVENT_ACTION": "created",
        "GITHUB_SHA": "a" * 40,
        "ISSUE_NUMBER": "1752",
        "COMMENT_AUTHOR": "azumag",
        "COMMENT_AUTHOR_ID": "9018513",
        "COMMENT_BODY": body,
    }
    env.update(overrides)
    return env


@pytest.mark.parametrize("body,mode", [
    ("/hanjuku-admin-check production", "check"),
    ("/hanjuku-admin-release production", "release"),
])
def test_authorize_exact_owner_commands(body, mode):
    module = load_auth()
    assert module.authorize(auth_env(body)) == {
        "mode": mode, "target": "production", "ref": "main"}


@pytest.mark.parametrize("overrides", [
    {"GITHUB_REPOSITORY": "azumag/other"},
    {"GITHUB_REPOSITORY_ID": "1"},
    {"GITHUB_REPOSITORY_OWNER": "other"},
    {"GITHUB_REPOSITORY_OWNER_ID": "1"},
    {"GITHUB_ACTOR": "other"},
    {"GITHUB_ACTOR_ID": "1"},
    {"GITHUB_TRIGGERING_ACTOR": "other"},
    {"GITHUB_REF": "refs/heads/topic"},
    {"GITHUB_REF_PROTECTED": "false"},
    {"GITHUB_DEFAULT_BRANCH": "preview"},
    {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
    {"GITHUB_EVENT_NAME": "workflow_dispatch"},
    {"GITHUB_EVENT_ACTION": "edited"},
    {"GITHUB_SHA": "not-a-sha"},
    {"ISSUE_NUMBER": "1657"},
    {"COMMENT_AUTHOR": "other"},
    {"COMMENT_AUTHOR_ID": "1"},
])
def test_identity_event_and_issue_mismatches_are_rejected(overrides):
    module = load_auth()
    with pytest.raises(ValueError, match="authorization_denied"):
        module.authorize(auth_env(**overrides))


@pytest.mark.parametrize("body", [
    "",
    "/hanjuku-admin-check",
    "/hanjuku-admin-check production ",
    " /hanjuku-admin-check production",
    "/hanjuku-admin-check production\n",
    "/hanjuku-admin-release production ",
    "/hanjuku-admin-release production; id",
    "/hanjuku-admin-release staging",
    "/hanjuku-start production",
])
def test_only_exact_fixed_commands_are_accepted(body):
    module = load_auth()
    with pytest.raises(ValueError, match="authorization_denied"):
        module.authorize(auth_env(body))


@pytest.mark.parametrize("body,expected", [
    ("/hanjuku-admin-check production", "mode=check\ntarget=production\nref=main\n"),
    ("/hanjuku-admin-release production", "mode=release\ntarget=production\nref=main\n"),
])
def test_cli_writes_only_fixed_outputs(body, expected):
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "output"
        env = dict(os.environ, **auth_env(body), GITHUB_OUTPUT=str(output))
        result = subprocess.run(["python3", str(AUTH)], env=env, text=True,
                                capture_output=True, check=False)
        assert result.returncode == 0, result.stderr
        assert output.read_text(encoding="utf-8") == expected
        parsed = json.loads(result.stdout)
        assert parsed["mode"] in {"check", "release"}
        assert set(parsed) == {"mode", "target", "ref"}


def test_workflow_is_fixed_serialized_and_never_accepts_fingerprint_from_comment():
    text = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "github.event.issue.number == 1752",
        "github.event.comment.body == '/hanjuku-admin-check production'",
        "github.event.comment.body == '/hanjuku-admin-release production'",
        "group: retro-corner-operator-${{ github.repository }}",
        "Require the event to remain current main",
        "Require production to equal event SHA",
        '"diagnostics docich production $SHA"',
        "manual_pending_fingerprint",
        'manual_q.get("status") == "error"',
        'scheduled_q.get("status") == "error"',
        "control/ops/vm_actions/admin_release_hanjuku_manual.sh",
        "control/src/docich/hanjuku_admin_result.py",
        '"exec docich production $SHA"',
        "gh api --method POST repos/azumag/docich/issues/1752/comments",
    ):
        assert marker in text
    for forbidden in (
        "workflow_dispatch:",
        "inputs.",
        "event.issue.body",
        "COMMENT_BODY:-",
        "cat "$work/expected" >> "$GITHUB_OUTPUT"",
        "echo "$diagnostics_json"",
        "printf '%s\\n' "$diagnostics_json"",
    ):
        assert forbidden not in text


def test_workflow_replies_without_reservation_fingerprint_or_raw_gateway_output():
    text = WORKFLOW.read_text(encoding="utf-8")
    reply = text[text.index("- name: Return fixed result metadata"):]
    assert "manual_pending_fingerprint" not in reply
    assert "EXPECTED" not in reply
    assert "gateway_result" not in reply
    assert 'f"Reason: {result}"' in reply
    assert "PRIVATE" not in reply


def test_workflow_scopes_dynamic_fingerprint_to_reviewed_stuck_shape():
    text = WORKFLOW.read_text(encoding="utf-8")
    evidence = text[text.index("- name: Read only the fixed stuck-reservation fingerprint"):
                    text.index("- name: Check or release only that exact reservation")]
    for marker in (
        'rotation.get("status") == "waiting"',
        'rotation.get("reason") == "manual-request-needs-resume-or-recovery"',
        'rotation.get("manual_pending_corner") == "hanjuku-hero"',
        'rotation.get("manual_pending_owner") == "none"',
        'rotation.get("queued_manual") is True',
        'receipt.get("present") is False',
        'manual_q.get("status") == "error"',
        'scheduled_q.get("status") == "error"',
        'game_switch.get("active_game") != "hanjuku-hero"',
        'fifo.get("queued_count") == 0',
        're.fullmatch(r"[0-9a-f]{64}", fingerprint or "")',
    ):
        assert marker in evidence
    assert "GITHUB_OUTPUT" not in evidence
    assert "expected" in evidence


def test_admin_step_never_uses_comment_text_or_arbitrary_command():
    text = WORKFLOW.read_text(encoding="utf-8")
    admin = text[text.index("- name: Check or release only that exact reservation"):
                 text.index("- name: Return fixed result metadata")]
    assert "github.event.comment.body" not in admin
    assert "ADMIN_RELEASE_EXPECTED" in admin
    assert "ADMIN_RELEASE_MODE" in admin
    assert "ADMIN_RELEASE_SHA" in admin
    assert "eval " not in admin
    assert "bash -c" not in admin
