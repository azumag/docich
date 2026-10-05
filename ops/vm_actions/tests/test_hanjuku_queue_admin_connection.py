"""Owner-only connection carries public run handles, never private context."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "ops/vm_actions"
WF = ROOT / ".github/workflows/corner-rotation-operator.yml"


def load(name):
    spec = importlib.util.spec_from_file_location(name, OPS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_step():
    lines = WF.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith('- name: Prepare or execute only'))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('      - name:')), len(lines))
    step = lines[start:end]
    run = next(i for i, line in enumerate(step) if line.strip() == 'run: |')
    return '\n'.join(step[:run]), '\n'.join(line[10:] for line in step[run + 1:])


class QueueAdminInputTests(unittest.TestCase):
    def setUp(self):
        self.module = load("hanjuku_queue_admin_input")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.event = self.root / "event.json"

    def event_env(self, **inputs):
        self.event.write_text(json.dumps({"inputs": inputs}))
        return {"GITHUB_EVENT_PATH": str(self.event)}

    def test_only_public_handle_and_explicit_acknowledgement_are_read(self):
        env = self.event_env(plan_handle="37276117224-1", unknown_resources="acknowledged",
                             fingerprint="PRIVATE_HASH", secret="PRIVATE_SECRET")
        self.assertEqual(self.module.read_plan(env), ("37276117224-1", "acknowledged"))
        self.assertEqual(self.module.read_plan(self.event_env()), ("", "not-acknowledged"))

    def test_invalid_types_paths_shell_and_secret_fingerprint_are_rejected_without_echo(self):
        for handle in (True, 1, [], {}, "a" * 64, "../100-1", "100-1;id", "0-1"):
            with self.subTest(handle=handle):
                env = self.event_env(plan_handle=handle)
                with self.assertRaisesRegex(ValueError, "queue admin input unavailable"):
                    self.module.read_plan(env)
                result = subprocess.run([sys.executable, str(OPS / "hanjuku_queue_admin_input.py"), "handle"],
                                        env={**os.environ, **env}, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        for ack in (True, 1, None, "true", "PRIVATE_SECRET", []):
            with self.subTest(ack=ack), self.assertRaises(ValueError):
                self.module.read_plan(self.event_env(plan_handle="100-1", unknown_resources=ack))

    def test_duplicate_or_oversized_event_is_refused(self):
        for raw in ('{"inputs":{"plan_handle":"100-1","plan_handle":"200-1"}}', 'x' * (1024 * 1024 + 1)):
            self.event.write_text(raw)
            with self.assertRaises(ValueError):
                self.module.read_plan({"GITHUB_EVENT_PATH": str(self.event)})


class QueueAdminAuthorizeTests(unittest.TestCase):
    def run_auth(self, operation, inputs=None, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text(json.dumps({"inputs": inputs or {}}))
            output = root / "output"
            env = {"GITHUB_REPOSITORY": "azumag/docich", "GITHUB_REPOSITORY_ID": "1327276249",
                "GITHUB_REPOSITORY_OWNER": "azumag", "GITHUB_REPOSITORY_OWNER_ID": "9018513",
                "GITHUB_ACTOR": "azumag", "GITHUB_ACTOR_ID": "9018513", "GITHUB_TRIGGERING_ACTOR": "azumag",
                "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true", "GITHUB_DEFAULT_BRANCH": "main",
                "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "a" * 40,
                "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
                "INPUT_OPERATION": operation, "INPUT_CONFIRM": "production", **overrides}
            result = subprocess.run([sys.executable, str(OPS / "authorize_corner_rotation.py")],
                                    env=env, capture_output=True, text=True)
            outputs = output.read_text() if output.exists() else ""
            return result, outputs

    def test_fixed_operations_require_owner_canonical_protected_main_and_explicit_ack(self):
        for operation in ("check-admin-cancel-retro-queues", "admin-cancel-retro-queues"):
            values = {} if operation.startswith("check-") else {"plan_handle": "100-1", "unknown_resources": "acknowledged"}
            result, outputs = self.run_auth(operation, values)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("100-1", outputs)
            for changed in ({"GITHUB_ACTOR_ID": "42"}, {"GITHUB_REPOSITORY_ID": "42"},
                {"GITHUB_TRIGGERING_ACTOR": "other"}, {"GITHUB_REF": "refs/heads/other"},
                {"GITHUB_REF_PROTECTED": "false"}, {"GITHUB_EVENT_NAME": "pull_request"},
                {"INPUT_CONFIRM": ""}, {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/main"}):
                with self.subTest(operation=operation, changed=changed):
                    self.assertNotEqual(self.run_auth(operation, values, **changed)[0].returncode, 0)
        for values in ({}, {"plan_handle": "100-1"}, {"plan_handle": "a" * 64, "unknown_resources": "acknowledged"},
                       {"plan_handle": "100-1", "unknown_resources": True},
                       {"plan_handle": "100-1", "unknown_resources": "acknowledged", "expected_reservation": "b" * 64}):
            self.assertNotEqual(self.run_auth("admin-cancel-retro-queues", values)[0].returncode, 0)
        self.assertNotEqual(self.run_auth("check-admin-cancel-retro-queues", {"plan_handle": "100-1"})[0].returncode, 0)


class QueueAdminScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "src/docich").mkdir(parents=True)
        (self.root / "src/docich/hanjuku_queue_admin_plan.py").touch()
        (self.root / "config").mkdir()
        (self.root / "config/docich.soren-live.toml").touch()
        python = self.root / "python3"
        python.write_text(f'#!{sys.executable}\nimport json,os,sys\nif sys.argv[1:3]==["-I","-c"]: sys.exit(int(os.getenv("VERSION_PROBE_EXIT","0")))\nprint(json.dumps(sys.argv[1:]))\n')
        python.chmod(0o755)
        git = self.root / "git"
        git.write_text('#!/bin/bash\ncase "$*" in *rev-parse*) echo "${VM_HEAD:-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}" ;; *status*) printf "%s" "${VM_DIRTY:-}" ;; esac\n')
        git.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
            "DOCICH_PROD_ROOT": str(self.root), "QUEUE_ADMIN_SHA": "a" * 40,
            "QUEUE_ADMIN_MODE": "check", "QUEUE_ADMIN_HANDLE": "", "QUEUE_ADMIN_ACK": "not-acknowledged",
            "QUEUE_ADMIN_REPOSITORY": "azumag/docich", "QUEUE_ADMIN_REPOSITORY_ID": "1327276249",
            "QUEUE_ADMIN_ACTOR_ID": "9018513", "QUEUE_ADMIN_WORKFLOW_REF": "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main",
            "QUEUE_ADMIN_REF": "refs/heads/main", "QUEUE_ADMIN_RUN_ID": "100", "QUEUE_ADMIN_RUN_ATTEMPT": "1"}

    def call(self, args=(), **changes):
        return subprocess.run(["bash", str(OPS / "admin_cancel_hanjuku_queues.sh"), *args],
                              env={**self.env, **changes}, capture_output=True, text=True)

    def test_fixed_argv_never_contains_fingerprint_and_uses_isolated_module(self):
        check = self.call()
        self.assertEqual(check.returncode, 0, check.stderr)
        argv = json.loads(check.stdout)
        self.assertEqual(argv[:5], ["-B", "-P", "-m", "docich.hanjuku_queue_admin_plan", "check"])
        self.assertNotIn("--expected", argv)
        self.assertNotIn("--acknowledge-unknown-resources", argv)
        result = self.call(QUEUE_ADMIN_MODE="execute", QUEUE_ADMIN_HANDLE="100-1", QUEUE_ADMIN_ACK="acknowledged")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[-3:], ["--plan-handle", "100-1", "--acknowledge-unknown-resources"])

    def test_invalid_scope_extra_args_version_sha_and_dirty_state_refuse_before_module(self):
        for changed in ({"QUEUE_ADMIN_SHA": "bad"}, {"QUEUE_ADMIN_ACTOR_ID": "42"}, {"QUEUE_ADMIN_RUN_ID": "100;id"},
            {"QUEUE_ADMIN_MODE": "execute", "QUEUE_ADMIN_HANDLE": "a" * 64, "QUEUE_ADMIN_ACK": "acknowledged"},
            {"QUEUE_ADMIN_MODE": "execute", "QUEUE_ADMIN_HANDLE": "100-1", "QUEUE_ADMIN_ACK": "not-acknowledged"},
            {"QUEUE_ADMIN_MODE": "check", "QUEUE_ADMIN_HANDLE": "100-1"},
            {"QUEUE_ADMIN_WORKFLOW_REF": "other"}):
            with self.subTest(changed=changed):
                result = self.call(**changed)
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.call(["unexpected"]).returncode, 64)
        for changed in ({"VM_HEAD": "b" * 40}, {"VM_DIRTY": " M tracked.py"}, {"VERSION_PROBE_EXIT": "25"}):
            result = self.call(**changed)
            self.assertEqual(result.returncode, 25)
            self.assertEqual(result.stdout, "")


class QueueAdminPublicResultTests(unittest.TestCase):
    def test_public_result_never_outputs_private_fields_or_failure_reasons(self):
        module = load("hanjuku_queue_admin_result")
        for rc in (0, 1, 25, 64, 255):
            raw = json.dumps({"status": "executed", "sha": "a" * 40, "exit_code": rc,
                              "output": "withheld", "fingerprint": "PRIVATE_SECRET", "reason": "PRIVATE_STATUS"})
            result, code = module.public_result(raw, "check", "a" * 40, rc)
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertEqual(code, 0 if rc == 0 else 1)
        for raw in ("PRIVATE_SECRET", "{}", "[]", "null", '{"exit_code":0,"exit_code":1}'):
            self.assertEqual(module.public_result(raw, "check", "a" * 40, 0)[1], 1)

    def test_transport_sha_bool_and_non_withheld_output_are_rejected(self):
        module = load("hanjuku_queue_admin_result")
        good = {"status": "executed", "sha": "a" * 40, "exit_code": 0, "output": "withheld"}
        for changes, transport in (({"sha": "b" * 40}, 0), ({"exit_code": True}, 0),
            ({"output": "PRIVATE_SECRET"}, 0), ({}, 1), ({"exit_code": 0.0}, 0)):
            self.assertEqual(module.public_result(json.dumps({**good, **changes}), "execute", "a" * 40, transport)[1], 1)


class QueueAdminWorkflowTests(unittest.TestCase):
    def test_connection_has_no_fingerprint_dataflow_or_capability_expansion(self):
        header, body = workflow_step()
        self.assertIn('git -C control -c core.hooksPath=/dev/null ls-remote origin refs/heads/main', body)
        self.assertIn('[[ "$remote_main" == "$SHA" ]]', body)
        self.assertIn('2>/dev/null', body)
        self.assertIn('exec docich production $SHA', body)
        for forbidden in ('inputs.plan_handle', 'inputs.unknown_resources', 'fingerprint', 'GITHUB_OUTPUT',
                          'GITHUB_ENV', 'artifact', 'set -x', 'cat "$gateway_result"', 'admin_release', 'start-hanjuku'):
            self.assertNotIn(forbidden, header + body)
        self.assertIn('hanjuku_queue_admin_input.py handle', body)
        self.assertIn('hanjuku_queue_admin_input.py acknowledgement', body)
        self.assertIn('hanjuku_queue_admin_result.py', body)
        self.assertNotIn('QUEUE_ADMIN', (OPS / 'gateway.py').read_text())
        subprocess.run(['bash', '-n'], input=body, text=True, check=True)

    def test_workflow_step_returns_only_generic_public_outcome_and_refuses_main_drift(self):
        _, body = workflow_step()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'control').symlink_to(ROOT, target_is_directory=True)
            binaries = root / 'bin'
            binaries.mkdir()
            (binaries / 'python3').symlink_to(sys.executable)
            ssh = binaries / 'ssh'
            ssh.write_text('#!/bin/bash\ncat >/dev/null\nprintf "%s" "$GATEWAY_RESULT"\nexit "${GATEWAY_RC:-0}"\n')
            ssh.chmod(0o755)
            git = binaries / 'git'
            git.write_text('#!/bin/bash\nprintf "%s\\trefs/heads/main\\n" "${REMOTE_MAIN:-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}"\n')
            git.chmod(0o755)
            event = root / 'event.json'
            env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'], 'GITHUB_EVENT_PATH': str(event),
                'SHA': 'a' * 40, 'REPOSITORY': 'azumag/docich', 'REPOSITORY_ID': '1327276249', 'ACTOR_ID': '9018513',
                'WORKFLOW_REF': 'azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main',
                'REF': 'refs/heads/main', 'RUN_ID': '100', 'RUN_ATTEMPT': '1', 'RUNNER_TEMP': str(root),
                'VM_SSH_HOST': 'example.invalid', 'VM_SSH_USER': 'owner', 'port': '22'}
            for operation in ('check-admin-cancel-retro-queues', 'admin-cancel-retro-queues'):
                inputs = {} if operation.startswith('check-') else {'plan_handle': '99-1', 'unknown_resources': 'acknowledged'}
                event.write_text(json.dumps({'inputs': inputs}))
                for rc in (0, 1):
                    with self.subTest(operation=operation, rc=rc):
                        gateway = {'status': 'executed', 'sha': 'a' * 40, 'output': 'withheld', 'exit_code': rc,
                                   'fingerprint': 'PRIVATE_FINGERPRINT', 'reason': 'PRIVATE_REASON'}
                        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', body], cwd=root, env={**env,
                            'OPERATION': operation, 'GATEWAY_RESULT': json.dumps(gateway), 'GATEWAY_RC': str(rc)},
                            capture_output=True, text=True)
                        self.assertEqual(result.returncode, rc)
                        public = json.loads(result.stdout)
                        self.assertEqual(set(public), {'status', 'operation'})
                        self.assertNotIn('PRIVATE', result.stdout + result.stderr)
                result = subprocess.run(['bash', '-euo', 'pipefail', '-c', body], cwd=root, env={**env,
                    'OPERATION': operation, 'REMOTE_MAIN': 'b' * 40, 'GATEWAY_RESULT': '{}'}, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
