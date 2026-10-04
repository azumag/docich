import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_hanjuku_recover_query.py"
SCRIPT = ROOT / "ops/vm_actions/recover_corner_rotation.sh"
WORKFLOW = ROOT / ".github/workflows/hanjuku-recover-query.yml"
WORKFLOW_REF = (
    "azumag/docich/.github/workflows/hanjuku-recover-query.yml@refs/heads/main"
)


def auth_env(**overrides):
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
        "ISSUE_NUMBER": "1657",
        "COMMENT_AUTHOR": "azumag",
        "COMMENT_AUTHOR_ID": "9018513",
        "COMMENT_BODY": "/hanjuku-recover production",
    }
    env.update(overrides)
    return env


def test_recovery_query_authorizes_only_exact_owner_command():
    result = subprocess.run(
        ["python3", str(AUTH)], env=auth_env(), text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "operation": "recover-failed",
        "target": "production",
        "ref": "main",
    }
    rejected = (
        {"COMMENT_BODY": "/hanjuku-recover production "},
        {"COMMENT_BODY": "/hanjuku-start production"},
        {"COMMENT_AUTHOR_ID": "42"},
        {"GITHUB_ACTOR_ID": "42"},
        {"GITHUB_TRIGGERING_ACTOR": "other"},
        {"GITHUB_REF_PROTECTED": "false"},
        {"GITHUB_SHA": "not-a-sha"},
        {"ISSUE_NUMBER": "1681"},
        {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
    )
    for overrides in rejected:
        denied = subprocess.run(
            ["python3", str(AUTH)],
            env=auth_env(**overrides),
            text=True,
            capture_output=True,
        )
        assert denied.returncode != 0, overrides


def _fake_recovery_tree(tmp_path, retro_status="succeeded"):
    root = tmp_path / "docich"
    (root / "bin").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "docich.soren-live.toml").write_text("", encoding="utf-8")
    log = tmp_path / "calls.log"
    launcher = root / "bin" / "docich"
    launcher.write_text(
        """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[-2:] == ["retro-corner", "recover-failed"]:
    print(json.dumps({"status": os.environ["RETRO_STATUS"]}))
    raise SystemExit(0)
if sys.argv[-2:] == ["corner-rotation", "recover"]:
    raise SystemExit(0)
raise SystemExit(9)
""",
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as out:
    out.write(json.dumps(["systemctl", *sys.argv[1:]]) + "\\n")
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    config_home = tmp_path / "xdg"
    unit_dir = config_home / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "docich-corner-rotation.service").write_text(
        "[Service]\n", encoding="utf-8"
    )
    env = {
        **os.environ,
        "DOCICH_PROD_ROOT": str(root),
        "XDG_CONFIG_HOME": str(config_home),
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "CALL_LOG": str(log),
        "RETRO_STATUS": retro_status,
    }
    return env, log


def test_recovery_script_terminalizes_adapter_before_rotation_and_restart(tmp_path):
    env, log = _fake_recovery_tree(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls[0] == ["systemctl", "--user", "show", "docich-corner-rotation.service"]
    assert calls[1][-2:] == ["retro-corner", "recover-failed"]
    assert calls[2][-2:] == ["corner-rotation", "recover"]
    assert calls[3] == [
        "systemctl", "--user", "--no-block", "restart",
        "docich-corner-rotation.service",
    ]


def test_recovery_script_refuses_nonterminal_adapter_without_rotation_restart(tmp_path):
    env, log = _fake_recovery_tree(tmp_path, retro_status="queued")
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 70
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls[1][-2:] == ["retro-corner", "recover-failed"]
    assert not any(call[-2:] == ["corner-rotation", "recover"] for call in calls)
    assert not any("restart" in call for call in calls)


def test_recovery_query_workflow_is_fixed_and_withholds_vm_output():
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "github.event.issue.number == 1657",
        "github.event.comment.body == '/hanjuku-recover production'",
        "github.actor_id == 9018513",
        "github.event.comment.user.id == 9018513",
        "github.ref_protected",
        "Require production to equal event SHA",
        "control/ops/vm_actions/authorize_hanjuku_recover_query.py",
        "control/ops/vm_actions/recover_corner_rotation.sh",
        "exec docich production $SHA",
        'data.get("output") == "withheld"',
        "group: retro-corner-operator-${{ github.repository }}",
    )
    for marker in required:
        assert marker in text
    forbidden = (
        "inputs.command",
        "pull_request_target",
        "event.issue.body",
        "workflow_dispatch",
        "cat $work/recover.stdout",
        "cat $work/recover.stderr",
    )
    for marker in forbidden:
        assert marker not in text


def test_recovery_script_noop_is_idempotent_second_stage_retry(tmp_path):
    env, log = _fake_recovery_tree(tmp_path, retro_status="noop")
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls[1][-2:] == ["retro-corner", "recover-failed"]
    assert calls[2][-2:] == ["corner-rotation", "recover"]
    assert calls[3][-3:] == [
        "--no-block", "restart", "docich-corner-rotation.service",
    ]
