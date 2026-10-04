import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_corner_rotation.py"
LEGACY_AUTH = ROOT / "ops/vm_actions/authorize_retro_corner.py"
SCRIPT = ROOT / "ops/vm_actions/restart_corner_rotation.sh"
RECOVER_SCRIPT = ROOT / "ops/vm_actions/recover_corner_rotation.sh"
RECOVER_RUNTIME_SCRIPT = ROOT / "ops/vm_actions/recover_hanjuku_runtime.sh"
WF = ROOT / ".github/workflows/corner-rotation-operator.yml"
LEGACY_WF = ROOT / ".github/workflows/retro-corner-operator.yml"

CANONICAL_REF = (
    "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main"
)
LEGACY_REF = (
    "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/main"
)


class CornerRotationAuthorizeTests(unittest.TestCase):
    def test_admin_script_excludes_untracked_cwd_package(self):
        import os
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for package in (root / "src/docich", root / "docich"):
                package.mkdir(parents=True)
                (package / "__init__.py").touch()
            (root / "src/docich/hanjuku_manual_admin_release.py").write_text('print("TRUSTED_TRACKED_MODULE")\n')
            (root / "docich/hanjuku_manual_admin_release.py").write_text('print("UNTRACKED_CWD_SHADOW")\n')
            (root / "config").mkdir()
            (root / "config/docich.soren-live.toml").touch()
            binaries = root / "bin"
            binaries.mkdir()
            (binaries / "python3").symlink_to(sys.executable)
            git = binaries / "git"
            git.write_text('#!/bin/bash\ncase "$*" in *rev-parse*) printf "%s\\n" "$ADMIN_RELEASE_SHA" ;; *status*) : ;; esac\n')
            git.chmod(0o755)
            env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                   "DOCICH_PROD_ROOT": str(root), "ADMIN_RELEASE_SHA": "b" * 40,
                   "ADMIN_RELEASE_EXPECTED": "a" * 64, "ADMIN_RELEASE_MODE": "release"}
            result = subprocess.run(["bash", str(ROOT / "ops/vm_actions/admin_release_hanjuku_manual.sh")],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "TRUSTED_TRACKED_MODULE\n")

    def test_administrative_release_requires_canonical_owner_and_exact_target(self):
        for operation in ("check-admin-release-hanjuku", "admin-release-hanjuku"):
            self.assertEqual(self.run_auth(INPUT_OPERATION=operation, INPUT_EXPECTED_RESERVATION="a" * 64).returncode, 0)
            for changes in (
                {"INPUT_EXPECTED_RESERVATION": ""},
                {"INPUT_EXPECTED_RESERVATION": "a" * 64 + ";id"},
                {"INPUT_EXPECTED_RESERVATION": "a" * 64, "GITHUB_WORKFLOW_REF": LEGACY_REF},
                {"INPUT_EXPECTED_RESERVATION": "a" * 64, "INPUT_CONFIRM": ""},
                {"INPUT_EXPECTED_RESERVATION": "a" * 64, "GITHUB_ACTOR_ID": "42"},
                {"INPUT_EXPECTED_RESERVATION": "a" * 64, "GITHUB_REF_PROTECTED": "false"},
            ):
                with self.subTest(operation=operation, changes=changes):
                    self.assertNotEqual(self.run_auth(INPUT_OPERATION=operation, **changes).returncode, 0)
            self.assertNotEqual(self.run_auth(INPUT_OPERATION=operation + ";id", INPUT_EXPECTED_RESERVATION="a" * 64).returncode, 0)

    def test_admin_script_uses_only_fixed_argv(self):
        import os
        import sys
        import tempfile
        script = ROOT / "ops/vm_actions/admin_release_hanjuku_manual.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/docich").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "src/docich/hanjuku_manual_admin_release.py").touch()
            (root / "config/docich.soren-live.toml").touch()
            python = root / "python3"
            python.write_text(f'#!{sys.executable}\nimport json,os,sys\nif sys.argv[1:3] == ["-I", "-c"]: sys.exit(int(os.environ.get("VERSION_PROBE_EXIT", "0")))\nprint(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            git = root / "git"
            git.write_text('#!/bin/bash\ncase "$*" in *rev-parse*) printf "%s\\n" "${VM_HEAD:-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb}" ;; *status*) printf "%s" "${VM_DIRTY:-}" ;; esac\n')
            git.chmod(0o755)
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"], "DOCICH_PROD_ROOT": str(root), "ADMIN_RELEASE_SHA": "b" * 40}
            for mode in ("check", "release"):
                result = subprocess.run(["bash", str(script)], env={**env, "ADMIN_RELEASE_MODE": mode,
                    "ADMIN_RELEASE_EXPECTED": "a" * 64}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["-B", "-P", "-m", "docich.hanjuku_manual_admin_release", mode, "--expected", "a" * 64])
            for mode, expected, args in (("", "a" * 64, []), ("release;id", "a" * 64, []),
                                         ("release", "", []), ("check", "a" * 64 + ";id", []),
                                         ("release", "a" * 64, ["other-game"])):
                result = subprocess.run(["bash", str(script), *args], env={**env, "ADMIN_RELEASE_MODE": mode,
                    "ADMIN_RELEASE_EXPECTED": expected}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, "")
            for changed in ({"VM_HEAD": "c" * 40}, {"VM_DIRTY": " M tracked.py"}):
                result = subprocess.run(["bash", str(script)], env={**env, "ADMIN_RELEASE_MODE": "release",
                    "ADMIN_RELEASE_EXPECTED": "a" * 64, **changed}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 25)
                self.assertEqual(result.stdout, "")
            unsupported = subprocess.run(["bash", str(script)], env={**env,
                "ADMIN_RELEASE_MODE": "release", "ADMIN_RELEASE_EXPECTED": "a" * 64,
                "VERSION_PROBE_EXIT": "25"}, capture_output=True, text=True)
            self.assertEqual(unsupported.returncode, 25)
            self.assertEqual(unsupported.stdout, "")

    def test_cancel_script_has_only_fixed_argv_and_rejects_injection(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src/docich").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "src/docich/hanjuku_manual_cancel.py").touch()
            (root / "config/docich.soren-live.toml").touch()
            python = root / "python3"
            # Use the interpreter by absolute path to avoid the fake binary recursing.
            import sys
            python.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            script = ROOT / "ops/vm_actions/cancel_hanjuku_manual.sh"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"], "DOCICH_PROD_ROOT": str(root)}
            for mode, expected, suffix in (("check", "", ["check"]), ("apply", "a" * 64, ["apply", "--expected", "a" * 64])):
                result = subprocess.run(["bash", str(script)], env={**env, "CANCEL_MODE": mode, "CANCEL_EXPECTED": expected}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["-B", "-m", "docich.hanjuku_manual_cancel", *suffix])
            for changes in ({"CANCEL_MODE": "apply", "CANCEL_EXPECTED": "a" * 64 + ";id"}, {"CANCEL_MODE": "check;id"}):
                result = subprocess.run(["bash", str(script)], env={**env, **changes}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, "")
    def test_cancel_requires_canonical_owner_and_exact_fingerprint(self):
        self.assertEqual(self.run_auth(INPUT_OPERATION="check-cancel-hanjuku").returncode, 0)
        self.assertEqual(self.run_auth(INPUT_OPERATION="cancel-hanjuku", INPUT_EXPECTED_RESERVATION="a" * 64).returncode, 0)
        for changes in (
            {"INPUT_EXPECTED_RESERVATION": ""},
            {"INPUT_EXPECTED_RESERVATION": "a" * 64 + ";id"},
            {"INPUT_EXPECTED_RESERVATION": "a" * 64, "GITHUB_WORKFLOW_REF": LEGACY_REF},
            {"INPUT_EXPECTED_RESERVATION": "a" * 64, "INPUT_CONFIRM": ""},
        ):
            self.assertNotEqual(self.run_auth(INPUT_OPERATION="cancel-hanjuku", **changes).returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_OPERATION="check-cancel-hanjuku", INPUT_EXPECTED_RESERVATION="a" * 64).returncode, 0)
    def run_auth(self, auth=AUTH, **overrides):
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
            "GITHUB_WORKFLOW_REF": CANONICAL_REF,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "a" * 40,
            "INPUT_OPERATION": "restart-service",
            "INPUT_CONFIRM": "production",
        }
        env.update(overrides)
        return subprocess.run(["python3", str(auth)], capture_output=True, text=True, env=env)

    def test_owner_dispatch_allows_only_fixed_operations(self):
        result = self.run_auth()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"operation": "restart-service", "target": "production", "ref": "main"},
        )
        recovered = self.run_auth(INPUT_OPERATION="recover-failed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            json.loads(recovered.stdout),
            {"operation": "recover-failed", "target": "production", "ref": "main"},
        )
        rolled_back = self.run_auth(INPUT_OPERATION="rollback-timer")
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(
            json.loads(rolled_back.stdout),
            {"operation": "rollback-timer", "target": "production", "ref": "main"},
        )
        self.assertEqual(self.run_auth(INPUT_OPERATION="start-hanjuku").returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_OPERATION="start-hanjuku",GITHUB_WORKFLOW_REF=LEGACY_REF).returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_OPERATION="start-hanjuku;id").returncode, 0)
        recovered_runtime = self.run_auth(INPUT_OPERATION="recover-runtime")
        self.assertEqual(recovered_runtime.returncode, 0, recovered_runtime.stderr)
        self.assertEqual(
            json.loads(recovered_runtime.stdout),
            {"operation": "recover-runtime", "target": "production", "ref": "main"},
        )
        self.assertNotEqual(self.run_auth(INPUT_OPERATION="recover-runtime",GITHUB_WORKFLOW_REF=LEGACY_REF).returncode, 0)
        self.assertNotEqual(self.run_auth(INPUT_OPERATION="recover-runtime;id").returncode, 0)
        for operation in ("status", "restart", "exec", "restart-service;id", "", "recover-failed;id", "rollback-timer;id", "recover-runtime;id"):
            with self.subTest(operation=operation):
                self.assertNotEqual(self.run_auth(INPUT_OPERATION=operation).returncode, 0)

    def test_both_reviewed_workflow_paths_are_accepted_by_both_scripts(self):
        for auth in (AUTH, LEGACY_AUTH):
            for workflow_ref in (CANONICAL_REF, LEGACY_REF):
                with self.subTest(auth=auth.name, workflow_ref=workflow_ref):
                    result = self.run_auth(auth, GITHUB_WORKFLOW_REF=workflow_ref)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_owner_confirmation_ref_event_or_workflow_fails_closed(self):
        cases = (
            {"GITHUB_ACTOR": "other", "GITHUB_ACTOR_ID": "42"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_DEFAULT_BRANCH": "release"},
            {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
            {
                "GITHUB_WORKFLOW_REF": (
                    "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/feature"
                )
            },
            {"GITHUB_WORKFLOW_REF": ""},
            {"GITHUB_EVENT_NAME": "push"},
            {"INPUT_CONFIRM": ""},
            {"GITHUB_SHA": "not-a-sha"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_auth(**overrides).returncode, 0)
                self.assertNotEqual(
                    self.run_auth(LEGACY_AUTH, **overrides).returncode, 0
                )


class CornerRotationOperatorPolicyTests(unittest.TestCase):
    def test_restart_script_resolves_the_reviewed_unit_and_never_restarts_shared(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for required in (
            'canonical="docich-corner-rotation.service"',
            'legacy="docich-retro-corner.service"',
            'fifo_timer="docich-game-switch-fifo.timer"',
            "docich-game-switch-fifo.service",
            "ambiguous corner rotation unit state",
            "no corner rotation service unit found",
            "systemctl --user daemon-reload",
            'systemctl --user enable --now "$fifo_timer"',
            'systemctl --user show "$unit"',
            'systemctl --user --no-block restart "$unit"',
            "refusing to write a unit through a symlink",
        ):
            self.assertIn(required, text)
        self.assertNotIn("$@", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("docich.service", text)
        self.assertNotIn("systemctl --user restart", text)

    def test_recover_script_resolves_the_latch_before_restarting_the_reviewed_unit(self):
        text = RECOVER_SCRIPT.read_text(encoding="utf-8")
        for required in (
            'canonical="docich-corner-rotation.service"',
            'legacy="docich-retro-corner.service"',
            'launcher="$DOCICH_PROD_ROOT/bin/docich"',
            'config="$DOCICH_PROD_ROOT/config/docich.soren-live.toml"',
            '"$launcher" --config "$config" corner-rotation recover',
            "reviewed docich launcher or config missing; refusing to recover",
            "ambiguous corner rotation unit state",
            "no corner rotation service unit found",
            'systemctl --user show "$unit"',
            'systemctl --user --no-block restart "$unit"',
        ):
            self.assertIn(required, text)
        # fail-closed ordering: the durable latch is resolved first, and a
        # refusal stops before the unit is touched (#986).
        self.assertLess(
            text.index('corner-rotation recover'),
            text.index('systemctl --user --no-block restart "$unit"'),
        )
        self.assertNotIn("$@", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("docich.service", text)
        self.assertNotIn("systemctl --user restart", text)
        self.assertNotIn("rm -", text)
        self.assertNotIn("corner-rotation tick", text)

    def test_recover_runtime_script_is_fixed_and_runs_the_reviewed_cli(self):
        text = RECOVER_RUNTIME_SCRIPT.read_text(encoding="utf-8")
        for required in (
            'launcher="$DOCICH_PROD_ROOT/bin/docich"',
            'config="$DOCICH_PROD_ROOT/config/docich.soren-live.toml"',
            'exec "$launcher" --config "$config" retro-corner recover-runtime',
            "reviewed docich launcher or config missing; refusing to recover",
            "recover-runtime accepts no arguments",
        ):
            self.assertIn(required, text)
        self.assertNotIn("$@", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("docich.service", text)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("rm -", text)
        self.assertNotIn("corner-rotation tick", text)

    def test_workflow_is_fixed_and_never_exposes_arbitrary_command_input(self):
        text = WF.read_text(encoding="utf-8")
        for required in (
            "options: [restart-service, recover-failed, rollback-timer, start-hanjuku, recover-runtime, check-cancel-hanjuku, cancel-hanjuku, check-admin-release-hanjuku, admin-release-hanjuku]",
            "github.actor_id == 9018513",
            "github.triggering_actor == 'azumag'",
            "github.ref_protected == true",
            "environment: vm-operations",
            "Require production to equal current protected main",
            "control/ops/vm_actions/authorize_corner_rotation.py",
            "control/ops/vm_actions/restart_corner_rotation.sh",
            "control/ops/vm_actions/recover_corner_rotation.sh",
            "control/ops/vm_actions/rollback_corner_rotation_timer.sh",
            "control/ops/vm_actions/start_hanjuku_corner.sh",
            "control/ops/vm_actions/recover_hanjuku_runtime.sh",
            "control/ops/vm_actions/admin_release_hanjuku_manual.sh",
            "Recover only the failed corner rotation slot",
            "Restart only the corner rotation service",
            "Roll back only the corner rotation timer",
            "Recover a dead Hanjuku runtime through the reviewed CLI",
            "if: steps.auth.outputs.operation == 'recover-failed'",
            "if: steps.auth.outputs.operation == 'restart-service'",
            "if: steps.auth.outputs.operation == 'rollback-timer'",
            "if: steps.auth.outputs.operation == 'recover-runtime'",
            "StrictHostKeyChecking=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "exec docich production $SHA",
        ):
            self.assertIn(required, text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("event.issue.body", text)
        self.assertNotIn("pull_request_target", text)

    def test_admin_workflow_propagates_private_helper_refusal(self):
        import os
        import tempfile
        import textwrap
        text = WF.read_text(encoding="utf-8")
        step = text.split("      - name: Check or administratively release", 1)[1].split("      - name:", 1)[0]
        shell = "set -euo pipefail\n" + textwrap.dedent(step.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "control/ops/vm_actions/admin_release_hanjuku_manual.sh"
            helper.parent.mkdir(parents=True)
            helper.write_text("# fixed helper fixture\n")
            ssh = root / "ssh"
            ssh.write_text('#!/bin/bash\ncat >/dev/null\nprintf "%s\\n" "$GATEWAY_RESULT"\n')
            ssh.chmod(0o755)
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "EXPECTED": "a" * 64, "VM_SSH_USER": "operator", "VM_SSH_HOST": "fixture.invalid",
                "SHA": "b" * 40, "RUNNER_TEMP": str(root), "port": "22"}
            for operation in ("check-admin-release-hanjuku", "admin-release-hanjuku"):
                for gateway in ({"status": "executed", "exit_code": 0},
                                {"status": "executed", "exit_code": 1},
                                {"status": "executed", "exit_code": False},
                                {"status": "rejected", "exit_code": 0},
                                {"status": "executed", "exit_code": 0, "sha": "c" * 40}):
                    gateway.setdefault("sha", "b" * 40)
                    result = subprocess.run(["bash", "-c", shell], cwd=root, env={**env,
                        "OPERATION": operation, "GATEWAY_RESULT": json.dumps(gateway)}, capture_output=True, text=True)
                    ok = gateway["status"] == "executed" and gateway["sha"] == env["SHA"] and type(gateway["exit_code"]) is int and gateway["exit_code"] == 0
                    with self.subTest(operation=operation, gateway=gateway):
                        self.assertEqual(result.returncode == 0, ok, result.stderr)
                        if ok:
                            public = json.loads(result.stdout)
                            self.assertEqual(public["status"], "admin-eligible" if operation.startswith("check-") else "admin-released")
                            self.assertIs(public["cancellation_authority"], False)
                        else:
                            self.assertEqual(result.stdout, "")

    def test_new_and_legacy_workflows_serialize_on_the_same_concurrency_group(self):
        group = "group: retro-corner-operator-${{ github.repository }}"
        self.assertIn(group, WF.read_text(encoding="utf-8"))
        self.assertIn(group, LEGACY_WF.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


def test_hanjuku_operator_rejects_extra_arguments_before_any_launch():
    script=ROOT/'ops/vm_actions/start_hanjuku_corner.sh'
    result=subprocess.run(['bash',str(script),'arbitrary-command'],capture_output=True,text=True)
    assert result.returncode==64
    assert 'accepts no arguments' in result.stderr


def test_recover_runtime_operator_rejects_extra_arguments_before_any_launch():
    script=ROOT/'ops/vm_actions/recover_hanjuku_runtime.sh'
    result=subprocess.run(['bash',str(script),'arbitrary-command'],capture_output=True,text=True)
    assert result.returncode==64
    assert 'accepts no arguments' in result.stderr


def test_fixed_recover_runtime_script_runs_only_the_reviewed_cli(tmp_path):
    import os
    root=tmp_path/'docich'
    (root/'bin').mkdir(parents=True)
    (root/'config').mkdir()
    (root/'config'/'docich.soren-live.toml').write_text('')
    tool=root/'bin'/'docich'
    tool.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    tool.chmod(0o755)
    result=subprocess.run(['bash',str(ROOT/'ops/vm_actions/recover_hanjuku_runtime.sh')],
        env={**os.environ,'DOCICH_PROD_ROOT':str(root)},
        capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    args=json.loads(result.stdout.splitlines()[0])
    assert args==['--config',str(root/'config'/'docich.soren-live.toml'),'retro-corner','recover-runtime']


def test_hanjuku_start_reuses_common_reservation_without_overriding_policy(monkeypatch):
    import sys
    from dataclasses import replace
    from unittest.mock import Mock
    sys.path.insert(0,str(ROOT/'src'))
    from docich import hanjuku_corner
    from docich.config import load_global
    from docich.retro_corner import load_retro_corner_config
    config=load_global(ROOT,ROOT/'config/docich.soren-live.toml')
    expected=replace(load_retro_corner_config(config),games=['hanjuku-hero'])
    constructor=Mock()
    monkeypatch.setattr(hanjuku_corner,'RetroCornerManager',constructor)
    result=hanjuku_corner.start()
    constructor.assert_called_once()
    assert constructor.call_args.kwargs['config']==expected
    assert result is constructor.return_value.start.return_value
    constructor.return_value.start.assert_called_once_with()
    constructor.return_value._start_direct.assert_not_called()


def test_hanjuku_entry_rejects_unbounded_arguments_before_start(monkeypatch):
    import sys
    from unittest.mock import Mock
    import pytest
    sys.path.insert(0,str(ROOT/'src'))
    from docich import hanjuku_corner
    start=Mock()
    monkeypatch.setattr(hanjuku_corner,'start',start)
    with pytest.raises(SystemExit):hanjuku_corner.main(['--game','another-game'])
    start.assert_not_called()


def test_fixed_hanjuku_script_launches_only_owned_service(tmp_path):
    import os
    tool=tmp_path/'systemd-run'
    tool.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    tool.chmod(0o755)
    result=subprocess.run(['bash',str(ROOT/'ops/vm_actions/start_hanjuku_corner.sh')],
        env={**os.environ,'PATH':str(tmp_path)+os.pathsep+os.environ['PATH']},
        capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    args=json.loads(result.stdout.splitlines()[0])
    assert '--unit=docich-hanjuku-corner' in args
    assert '--collect' in args and '--property=Type=exec' in args
    assert '--working-directory=/home/ubuntu/docich' in args
    assert args[-2:]==['-m','docich.hanjuku_corner']
    assert not any(x in args for x in ('restart','stop','kill','--shell'))
