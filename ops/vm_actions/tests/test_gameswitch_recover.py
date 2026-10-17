"""Contracts for the fixed owner-only GameSwitch recover operator (#1960).

The previous revision passed CI while the workflow could not run at all, so
this suite pins the three layers that matter:

1. the authorizer's fixed env contract (owner/protected main/SHA/confirm) and
   every output the workflow consumes,
2. the VM helper's guards (fixed shape, declared mismatch, ownership proof)
   and its fixed exit codes,
3. the workflow's pinned supply chain and its single fixed execution path.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_gameswitch_recover.py"
HELPER = ROOT / "ops/vm_actions/gameswitch_recover.py"
VM_SCRIPT = ROOT / "ops/vm_actions/gameswitch_recover.sh"
WF = ROOT / ".github/workflows/gameswitch-recover.yml"

SPEC = importlib.util.spec_from_file_location("gameswitch_recover", HELPER)
HELPER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER_MODULE)

WORKFLOW_REF = "azumag/docich/.github/workflows/gameswitch-recover.yml@refs/heads/main"
PINNED_CHECKOUT = "d23441a48e516b6c34aea4fa41551a30e30af803"


# ---------------------------------------------------------------- authorizer


def base_env():
    return {
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
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "INPUT_OPERATION": "recover_gameswitch",
        "INPUT_PHASE": "recovery_required",
        "INPUT_CLEANUP_PENDING": "false",
        "INPUT_CONFIRM": "production",
    }


def run_auth(output_path=None, **overrides):
    env = base_env()
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    if output_path is not None:
        env["GITHUB_OUTPUT"] = str(output_path)
    return subprocess.run(
        [sys.executable, str(AUTH)], env=env, capture_output=True, text=True
    )


class AuthorizeContractTests(unittest.TestCase):
    def test_valid_declaration_succeeds(self):
        result = run_auth(INPUT_PHASE="recovery_required", INPUT_CLEANUP_PENDING="false")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(
            data,
            {"operation": "recover_gameswitch", "target": "production", "ref": "main",
             "phase": "recovery_required", "cleanup_pending": "false"},
        )

    def test_failed_phase_with_cleanup_pending_succeeds(self):
        result = run_auth(INPUT_PHASE="failed", INPUT_CLEANUP_PENDING="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["phase"], "failed")

    def test_every_declared_output_is_written(self):
        """The workflow consumes these outputs; an empty one must fail here."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "github_output"
            out.write_text("", encoding="utf-8")
            result = run_auth(output_path=out)
            self.assertEqual(result.returncode, 0, result.stderr)
            keys = {}
            for line in out.read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition("=")
                keys[name] = value
            for name in ("operation", "target", "ref", "phase", "cleanup_pending", "decision"):
                self.assertIn(name, keys)
                self.assertTrue(keys[name], name)
            decision = json.loads(keys["decision"])
            self.assertEqual(decision["operation"], "recover_gameswitch")
            self.assertEqual(decision["phase"], "recovery_required")

    def test_unknown_phase_is_rejected(self):
        for phase in ("idle", "ready", "draining", "stopping", "unknown", ""):
            with self.subTest(phase=phase):
                self.assertNotEqual(run_auth(INPUT_PHASE=phase).returncode, 0)

    def test_unknown_cleanup_pending_is_rejected(self):
        for value in ("", "yes", "no", "1", "0", "TRUE", "FALSE"):
            with self.subTest(cleanup_pending=value):
                self.assertNotEqual(run_auth(INPUT_CLEANUP_PENDING=value).returncode, 0)

    def test_missing_production_confirmation_is_rejected(self):
        result = run_auth(INPUT_CONFIRM=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("confirmation", result.stderr)

    def test_wrong_production_confirmation_is_rejected(self):
        for value in ("", "staging", "PRODUCTION", "production ", "true"):
            with self.subTest(confirm=value):
                self.assertNotEqual(run_auth(INPUT_CONFIRM=value).returncode, 0)

    def test_owner_protected_main_contract_is_enforced(self):
        cases = {
            "GITHUB_ACTOR": "other",
            "GITHUB_ACTOR_ID": "42",
            "GITHUB_TRIGGERING_ACTOR": "other",
            "GITHUB_REPOSITORY": "other/repo",
            "GITHUB_REPOSITORY_ID": "1",
            "GITHUB_REPOSITORY_OWNER": "other",
            "GITHUB_REPOSITORY_OWNER_ID": "1",
            "GITHUB_DEFAULT_BRANCH": "topic",
            "GITHUB_REF": "refs/heads/topic",
            "GITHUB_REF_PROTECTED": "false",
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_SHA": "notasha",
            "INPUT_OPERATION": "exec",
        }
        for key, value in cases.items():
            with self.subTest(**{key: value}):
                self.assertNotEqual(run_auth(**{key: value}).returncode, 0)

    def test_public_repository_is_permitted_for_the_fixed_operation(self):
        source = AUTH.read_text(encoding="utf-8")
        for forbidden in ("visibility", "is_private", "GITHUB_REPOSITORY_PRIVATE"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(run_auth().returncode, 0)


# --------------------------------------------------------------------- helper


def runtime(seat_generation=3, runtime_id=None, **overrides):
    seat = {
        "game": "sorengame",
        "adapter": "soren",
        "generation": seat_generation,
        "runtime_id": runtime_id or f"g{seat_generation}-abcdef",
        "lease_id": "lease-1",
        "game_window": f"game-g{seat_generation}",
        "agent_window": f"agent-g{seat_generation}",
        "adapter_session": f"docich-game-g{seat_generation}",
        "started_at": "2026-10-16T20:00:00Z",
    }
    seat.update(overrides)
    return seat


def canonical(**overrides):
    state = {
        "schema_version": 2,
        "revision": 0,
        "phase": "failed",
        "operation": None,
        "request_id": None,
        "deadline_at": None,
        "next_generation": 2,
        "active": None,
        "candidate": None,
        "previous": None,
        "retiring": [],
        "last_result": None,
        "last_error": None,
        "updated_at": "2026-10-16T20:00:00Z",
    }
    state.update(overrides)
    return state


def write_state(root, state):
    path = root / HELPER_MODULE.STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


class HelperShapeTests(unittest.TestCase):
    def test_minimal_failed_state_is_accepted(self):
        self.assertEqual(HELPER_MODULE.validate_shape(canonical())["phase"], "failed")

    def test_missing_required_key_is_unknown_drift(self):
        state = canonical()
        del state["revision"]
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.validate_shape(state)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_unknown_schema_or_phase_is_unknown_drift(self):
        for state in (canonical(schema_version=1), canonical(phase="teleporting")):
            with self.subTest(state=state):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.validate_shape(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_operation_and_request_id_must_move_together(self):
        state = canonical(operation="switch")
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.validate_shape(state)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_seat_invariants_are_enforced(self):
        idle_with_seat = canonical(phase="idle", active=runtime())
        ready_without_active = canonical(phase="ready")
        recovery_without_error = canonical(phase="recovery_required")
        for state in (idle_with_seat, ready_without_active, recovery_without_error):
            with self.subTest(phase=state["phase"]):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.validate_shape(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_malformed_seat_is_unknown_drift(self):
        for broken in (runtime(runtime_id="runtime-3"), runtime(game_window="game-g9"),
                       runtime(agent_window="agent-g9"), runtime(adapter_session="docich-game"),
                       runtime(lease_id=7), runtime(generation=0)):
            with self.subTest(broken=broken):
                state = canonical(phase="ready", active=broken, next_generation=9)
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.validate_shape(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_duplicate_generations_and_stale_next_generation_are_refused(self):
        duplicate = canonical(phase="draining", active=runtime(), previous=runtime(),
                              next_generation=4)
        stale = canonical(phase="ready", active=runtime(), next_generation=3)
        for state in (duplicate, stale):
            with self.subTest(state=state):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.validate_shape(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)


class HelperDeclaredTests(unittest.TestCase):
    def test_matching_declaration_passes(self):
        HELPER_MODULE.declared_matches(canonical(), "failed", False)

    def test_phase_mismatch_is_declared_mismatch(self):
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.declared_matches(canonical(phase="recovery_required"), "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_DECLARED_MISMATCH)

    def test_cleanup_pending_mismatch_is_declared_mismatch(self):
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.declared_matches(canonical(retiring=[runtime()]), "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_DECLARED_MISMATCH)
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.declared_matches(
                canonical(last_result={"cleanup_pending": True}), "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_DECLARED_MISMATCH)

    def test_declared_cleanup_pending_true_matches_retiring(self):
        HELPER_MODULE.declared_matches(canonical(retiring=[runtime()]), "failed", True)


class HelperOwnershipTests(unittest.TestCase):
    def test_missing_runtime_directory_is_ownership_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = canonical(phase="ready", active=runtime(), next_generation=4)
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.verify_ownership(root, state)
            self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_OWNERSHIP_MISSING)

    def test_present_runtime_directory_proves_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seat = runtime()
            (root / HELPER_MODULE.RUNTIMES_REL / seat["runtime_id"]).mkdir(parents=True)
            HELPER_MODULE.verify_ownership(
                root, canonical(phase="ready", active=seat, next_generation=4))

    def test_missing_or_foreign_receipt_is_ownership_missing(self):
        request_id = "0f5d2a1c-3b4e-4a5d-8f6a-1b2c3d4e5f60"
        state = canonical(operation="switch", request_id=request_id,
                          deadline_at="2026-10-16T21:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipts = root / HELPER_MODULE.REQUESTS_REL
            receipts.mkdir(parents=True)
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.verify_ownership(root, state)
            self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_OWNERSHIP_MISSING)
            (receipts / f"{request_id}.json").write_text(
                json.dumps({"request_id": "11111111-2222-3333-4444-555555555555"}),
                encoding="utf-8")
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.verify_ownership(root, state)
            self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_OWNERSHIP_MISSING)
            (receipts / f"{request_id}.json").write_text(
                json.dumps({"request_id": request_id}), encoding="utf-8")
            HELPER_MODULE.verify_ownership(root, state)


class HelperCliTests(unittest.TestCase):
    def _root_with_launcher(self, exit_code=0):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "bin").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        (root / HELPER_MODULE.CONFIG_REL).write_text("", encoding="utf-8")
        launcher = root / HELPER_MODULE.LAUNCHER_REL
        launcher.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$0\" \"$@\" > \"" + str(root) + "/argv.txt\"\n"
            f"exit {exit_code}\n",
            encoding="utf-8")
        launcher.chmod(0o755)
        write_state(root, canonical())
        self.addCleanup(tmp.cleanup)
        return root

    def test_canonical_cli_is_called_once_with_a_bounded_timeout(self):
        root = self._root_with_launcher()
        self.assertEqual(HELPER_MODULE.run_canonical_recover(root), 0)
        argv = (root / "argv.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv, [
            str(root / HELPER_MODULE.LAUNCHER_REL), "--config",
            str(root / HELPER_MODULE.CONFIG_REL), "recover",
            "--timeout", str(HELPER_MODULE.CLI_RECOVER_TIMEOUT_S),
        ])
        self.assertLessEqual(HELPER_MODULE.CLI_RECOVER_TIMEOUT_S, 600)

    def test_missing_launcher_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.run_canonical_recover(Path(tmp))
            self.assertEqual(ctx.exception.code, 25)

    def test_cli_exit_codes_are_mapped_to_fixed_reasons(self):
        pending = self._root_with_launcher(exit_code=1)
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.recover(pending, "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_RECOVERY_PENDING)
        rejected = self._root_with_launcher(exit_code=9)
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.recover(rejected, "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_RECOVERY_REJECTED)

    def test_successful_recovery_revalidates_the_state(self):
        root = self._root_with_launcher()
        self.assertEqual(HELPER_MODULE.recover(root, "failed", False), 0)


class HelperProcessTests(unittest.TestCase):
    """The fixed exit codes are the workflow's only rejection vocabulary."""

    def _run(self, argv, cwd, root=None):
        # Resolve so macOS /tmp symlinks cannot make Path.cwd() differ from
        # DOCICH_ROOT and trip the fixed production-root guard instead.
        cwd_path = Path(cwd).resolve()
        root_path = Path(root).resolve() if root is not None else cwd_path
        env = dict(os.environ)
        env["DOCICH_ROOT"] = str(root_path)
        return subprocess.run(
            [sys.executable, str(HELPER), *argv], cwd=str(cwd_path), env=env,
            capture_output=True, text=True,
        )

    def test_bad_usage_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run([], Path(tmp)).returncode, 2)

    def test_out_of_form_declaration_exits_64(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(["teleporting", "false"], Path(tmp))
            self.assertEqual(result.returncode, 64, result.stderr)

    def test_foreign_root_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            result = self._run(["failed", "false"], Path(other), root=Path(tmp))
            self.assertEqual(result.returncode, 3, result.stderr)

    def test_declared_mismatch_exits_80_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_state(root, canonical(phase="failed"))
            result = self._run(["recovery_required", "false"], root)
            self.assertEqual(result.returncode, 80, result.stderr)
            self.assertNotIn("Traceback", result.stderr)


# ------------------------------------------------------------------ workflow


PINNED_ACTION_RE = re.compile(r"^\s*uses:\s*(\S+)", re.M)
CHECKOUT_RE = re.compile(r"^\s*uses:\s*actions/checkout@(\S+)", re.M)


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.text = WF.read_text(encoding="utf-8")

    def test_dispatch_inputs_are_the_fixed_set(self):
        inputs = re.findall(r"^      ([a-z_]+):$", self.text, re.M)
        self.assertEqual(sorted(inputs),
                         ["cleanup_pending", "confirm", "operation", "phase"])
        self.assertIn("options: [recover_gameswitch]", self.text)
        self.assertIn("options: [failed, recovery_required]", self.text)
        self.assertIn("options: [true, false]", self.text)

    def test_every_action_is_pinned_to_a_full_sha(self):
        uses = PINNED_ACTION_RE.findall(self.text)
        self.assertTrue(uses)
        for reference in uses:
            name, _, version = reference.partition("@")
            self.assertRegex(version, r"^[0-9a-f]{40}$", name)
        self.assertEqual(CHECKOUT_RE.findall(self.text), [PINNED_CHECKOUT])
        self.assertIn("persist-credentials: false", self.text)

    def test_execution_is_bounded_and_serialized(self):
        self.assertIn("timeout-minutes: 20", self.text)
        self.assertIn("concurrency:", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_owner_and_protected_main_guard_is_present(self):
        self.assertIn("github.actor_id == 9018513", self.text)
        self.assertIn("github.triggering_actor == 'azumag'", self.text)
        self.assertIn("github.ref == 'refs/heads/main'", self.text)
        self.assertIn("github.ref_protected == true", self.text)
        self.assertIn("environment: vm-operations", self.text)

    def test_workflow_runs_the_fixed_vm_script_through_the_gateway(self):
        self.assertIn("control/ops/vm_actions/gameswitch_recover.sh", self.text)
        self.assertIn("exec docich production $SHA", self.text)
        self.assertIn("status docich production $SHA", self.text)
        self.assertIn("bash -n control/ops/vm_actions/gameswitch_recover.sh", self.text)

    def test_workflow_passes_the_declaration_and_the_confirmation(self):
        self.assertIn("GS_RECOVER_PHASE=%s", self.text)
        self.assertIn("GS_RECOVER_CLEANUP_PENDING=%s", self.text)
        self.assertIn("INPUT_CONFIRM: ${{ inputs.confirm }}", self.text)
        self.assertIn("steps.auth.outputs.phase", self.text)
        self.assertIn("steps.auth.outputs.cleanup_pending", self.text)

    def test_workflow_maps_every_fixed_helper_exit_code(self):
        for code in ("64", "70", "71", "72", "80", "81", "82"):
            self.assertIn(f"{code})", self.text, code)

    def test_workflow_has_no_stub_execution_or_empty_decision_chain(self):
        self.assertNotIn("The actual recovery command is executed", self.text)
        self.assertNotIn("needs.authorize.outputs.decision", self.text)
        self.assertNotIn("checkout@v4", self.text)


class VmScriptContractTests(unittest.TestCase):
    def setUp(self):
        self.text = VM_SCRIPT.read_text(encoding="utf-8")

    def test_script_is_fixed_and_argument_free(self):
        self.assertIn("set -euo pipefail", self.text)
        self.assertIn('[[ "$#" -eq 0 ]] || exit 2', self.text)
        self.assertNotIn("eval", self.text)
        self.assertNotIn("$1", self.text)

    def test_script_pins_its_own_production_root(self):
        self.assertIn('[[ "$(pwd -P)" == /home/ubuntu/docich ]] || exit 3', self.text)

    def test_script_validates_the_declared_parameters(self):
        self.assertIn("GS_RECOVER_PHASE", self.text)
        self.assertIn("GS_RECOVER_CLEANUP_PENDING", self.text)
        self.assertIn("failed|recovery_required", self.text)
        self.assertIn("true|false", self.text)

    def test_script_runs_the_reviewed_helper_with_a_bounded_deadline(self):
        self.assertIn("timeout --kill-after=5s 480s", self.text)
        self.assertIn("python3 ops/vm_actions/gameswitch_recover.py", self.text)


if __name__ == "__main__":
    unittest.main()
