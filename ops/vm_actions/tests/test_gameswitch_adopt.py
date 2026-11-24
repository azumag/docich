"""Contracts for the fixed owner-only GameSwitch adoption operator (#2088).

``recover --adopt-failed-candidate`` publishes a still-running failed candidate
as ``active`` without stopping it. This suite pins the three layers that matter:

1. the authorizer's fixed env contract (owner/protected main/SHA/confirm) and
   every output the workflow consumes,
2. the VM helper's adopt-specific guards (the exact adoptable failed shape,
   the immutable rollback-failure receipt, the declared-mismatch refusal) and
   its postcondition proof that exit 0 really adopted the *same* runtime,
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
AUTH = ROOT / "ops/vm_actions/authorize_gameswitch_adopt.py"
HELPER = ROOT / "ops/vm_actions/gameswitch_adopt.py"
VM_SCRIPT = ROOT / "ops/vm_actions/gameswitch_adopt.sh"
WF = ROOT / ".github/workflows/gameswitch-adopt.yml"
CI = ROOT / ".github/workflows/vm-operations-ci.yml"
RECOVER_HELPER = ROOT / "ops/vm_actions/gameswitch_recover.py"

SPEC = importlib.util.spec_from_file_location("gameswitch_adopt", HELPER)
HELPER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER_MODULE)

RECOVER_SPEC = importlib.util.spec_from_file_location("gameswitch_recover_pin", RECOVER_HELPER)
RECOVER_MODULE = importlib.util.module_from_spec(RECOVER_SPEC)
RECOVER_SPEC.loader.exec_module(RECOVER_MODULE)

WORKFLOW_REF = "azumag/docich/.github/workflows/gameswitch-adopt.yml@refs/heads/main"
PINNED_CHECKOUT = "d23441a48e516b6c34aea4fa41551a30e30af803"
REQUEST_ID = "0f5d2a1c-3b4e-4a5d-8f6a-1b2c3d4e5f60"


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
        "INPUT_OPERATION": "adopt_failed_candidate",
        "INPUT_PHASE": "failed",
        "INPUT_CLEANUP_PENDING": "true",
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
        result = run_auth()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"operation": "adopt_failed_candidate", "target": "production", "ref": "main",
             "phase": "failed", "cleanup_pending": "true"},
        )

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
            self.assertEqual(decision["operation"], "adopt_failed_candidate")
            self.assertEqual(decision["phase"], "failed")

    def test_only_the_fixed_failed_phase_is_accepted(self):
        for phase in ("recovery_required", "idle", "ready", "draining", "stopping", "unknown", ""):
            with self.subTest(phase=phase):
                self.assertNotEqual(run_auth(INPUT_PHASE=phase).returncode, 0)

    def test_only_declared_cleanup_pending_true_is_accepted(self):
        for value in ("", "false", "yes", "no", "1", "0", "TRUE", "FALSE"):
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
            "INPUT_OPERATION": "recover_gameswitch",
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


def failed_seat(**overrides):
    seat = runtime(seat_generation=3, game="sorengame", adapter="soren")
    seat["cleanup_role"] = "failed_candidate"
    seat.update(overrides)
    return seat


def previous_seat(seat_generation=2, game="nethack", adapter="retroarch",
                  runtime_id="g2-abcdef", lease_id=None, **overrides):
    return runtime(seat_generation=seat_generation, game=game, adapter=adapter,
                   runtime_id=runtime_id, lease_id=lease_id, **overrides)


def failed_result(**overrides):
    result = {
        "request_id": REQUEST_ID,
        "operation": "switch",
        "status": "failed",
        "error_code": "rollback_failed",
        "from_game": "nethack",
        "to_game": "sorengame",
        "generation": 3,
        "cleanup_pending": True,
    }
    result.update(overrides)
    return result


def canonical(**overrides):
    state = {
        "schema_version": 2,
        "revision": 5,
        "phase": "failed",
        "operation": None,
        "request_id": None,
        "deadline_at": None,
        "next_generation": 4,
        "active": None,
        "candidate": None,
        "previous": previous_seat(),
        "retiring": [failed_seat()],
        "last_result": failed_result(),
        "last_error": {"error_code": "rollback_failed", "detail": "rollback失敗"},
        "updated_at": "2026-10-16T21:00:00Z",
    }
    state.update(overrides)
    return state


def adopted(**overrides):
    """The canonical state a successful adoption must leave behind."""

    active = {key: value for key, value in failed_seat().items() if key != "cleanup_role"}
    state = canonical(
        revision=6,
        phase="ready",
        active=active,
        previous=None,
        retiring=[],
        last_result={
            "request_id": "", "operation": "recover", "status": "succeeded",
            "from_game": "nethack", "to_game": "sorengame", "generation": 3,
            "cleanup_pending": None,
        },
    )
    state.update(overrides)
    return state


def write_state(root, state):
    path = root / HELPER_MODULE.STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def write_receipt(root, request_id=REQUEST_ID, receipt_overrides=None, result_overrides=None):
    """Write the durable receipt exactly as the canonical writer does (#2670).

    The terminal facts (error_code/to_game/generation/request_id) live under
    ``result``; only the receipt identity and status are top-level.
    """

    result = {
        "request_id": request_id,
        "operation": "switch",
        "status": "failed",
        "error_code": "rollback_failed",
        "from_game": "nethack",
        "to_game": "sorengame",
        "generation": 3,
    }
    result.update(result_overrides or {})
    receipt = {
        "schema_version": 1,
        "request_id": request_id,
        "operation": "switch",
        "target": "sorengame",
        "generation": 3,
        "status": "failed",
        "result": result,
    }
    receipt.update(receipt_overrides or {})
    path = root / HELPER_MODULE.REQUESTS_REL / f"{request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")


def write_runtimes(root, *seats):
    for seat in seats:
        (root / HELPER_MODULE.RUNTIMES_REL / str(seat["runtime_id"])).mkdir(parents=True, exist_ok=True)


class HelperShapeTests(unittest.TestCase):
    def test_adoptable_failed_shape_is_accepted(self):
        self.assertEqual(HELPER_MODULE.failed_candidate(canonical())["runtime_id"], "g3-abcdef")

    def test_adoption_is_limited_to_the_failed_phase(self):
        for phase in ("recovery_required", "ready", "idle", "draining", "stopping"):
            with self.subTest(phase=phase):
                state = canonical(phase=phase)
                if phase in {"recovery_required"}:
                    state["active"] = None
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.failed_candidate(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_a_live_request_or_seat_is_unknown_drift(self):
        states = (
            canonical(request_id=REQUEST_ID, operation="recover"),
            canonical(active=runtime(), next_generation=5),
            canonical(candidate=runtime(seat_generation=4), next_generation=5),
        )
        for state in states:
            with self.subTest(state=state.get("phase")):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.failed_candidate(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_exactly_one_failed_candidate_must_be_retiring(self):
        states = (
            canonical(retiring=[]),
            canonical(retiring=[failed_seat(), failed_seat(seat_generation=4)]),
            canonical(retiring=[runtime(seat_generation=3)]),
            canonical(retiring=[{key: value for key, value in failed_seat().items()
                                 if key != "cleanup_role"}]),
        )
        for state in states:
            with self.subTest(state=state["retiring"]):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.failed_candidate(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_an_already_retiring_candidate_is_unknown_drift(self):
        state = canonical(retiring=[failed_seat(retirement={"request_id": REQUEST_ID})])
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.failed_candidate(state)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_the_failed_result_must_be_this_candidates_rollback_failure(self):
        states = (
            canonical(last_result=failed_result(status="succeeded")),
            canonical(last_result=failed_result(error_code="timeout")),
            canonical(last_result=failed_result(to_game="othegame")),
            canonical(last_result=failed_result(generation=2)),
            canonical(last_result={key: value for key, value in failed_result().items()
                                   if key != "request_id"}),
            canonical(last_result=None),
        )
        for state in states:
            with self.subTest(last_result=state["last_result"]):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.failed_candidate(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_the_previous_game_must_be_a_distinct_older_runtime(self):
        states = (
            canonical(previous=failed_seat(generation=2, runtime_id="g2-abcdef")),
            canonical(previous=previous_seat(game="sorengame", adapter="retroarch")),
            canonical(previous=previous_seat(seat_generation=3, runtime_id="g3-abcdef")),
        )
        for state in states:
            with self.subTest(previous=state["previous"]):
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                    HELPER_MODULE.failed_candidate(state)
                self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_UNKNOWN_DRIFT)

    def test_shape_guards_agree_with_the_reviewed_recover_helper(self):
        """Both operators share one fixed-form contract; drift must not split."""
        drift = (
            {"schema_version": 1},
            {"phase": "teleporting"},
            {"next_generation": 0},
        )
        for override in drift:
            with self.subTest(override=override):
                state = canonical(**override)
                with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as adopt_ctx:
                    HELPER_MODULE.validate_shape(state)
                with self.assertRaises(RECOVER_MODULE.FixedRecoverExit) as recover_ctx:
                    RECOVER_MODULE.validate_shape(state)
                self.assertEqual(adopt_ctx.exception.code, recover_ctx.exception.code)


class HelperDeclaredTests(unittest.TestCase):
    def test_matching_declaration_passes(self):
        HELPER_MODULE.declared_matches(canonical(), "failed", True)

    def test_phase_mismatch_is_declared_mismatch(self):
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.declared_matches(canonical(), "recovery_required", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_DECLARED_MISMATCH)

    def test_cleanup_pending_mismatch_is_declared_mismatch(self):
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.declared_matches(canonical(), "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_DECLARED_MISMATCH)

    def test_a_result_flagging_pending_cleanup_must_be_declared(self):
        state = canonical(retiring=[])
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.declared_matches(state, "failed", False)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_DECLARED_MISMATCH)


class HelperOwnershipTests(unittest.TestCase):
    def test_missing_seated_runtime_directory_is_ownership_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_receipt(root)
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.verify_ownership(root, canonical())
            self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_OWNERSHIP_MISSING)

    def test_present_seated_runtime_directories_prove_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_receipt(root)
            write_runtimes(root, failed_seat(), previous_seat())
            HELPER_MODULE.verify_ownership(root, canonical())

    def test_the_immutable_failed_receipt_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_runtimes(root, failed_seat(), previous_seat())
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.verify_ownership(root, canonical())
            self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_OWNERSHIP_MISSING)

    def test_the_live_nested_result_receipt_proves_ownership(self):
        """Regression #2670: the real receipt stores its facts under result."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_receipt(root)
            write_runtimes(root, failed_seat(), previous_seat())
            receipt = json.loads(
                (root / HELPER_MODULE.REQUESTS_REL / f"{REQUEST_ID}.json").read_text(
                    encoding="utf-8"))
            self.assertEqual(receipt["status"], "failed")
            self.assertNotIn("error_code", receipt)
            self.assertNotIn("to_game", receipt)
            HELPER_MODULE.verify_ownership(root, canonical())

    def test_a_foreign_or_contradicting_receipt_is_ownership_missing(self):
        cases = (
            {"receipt_overrides": {"request_id": "11111111-2222-3333-4444-555555555555"}},
            {"receipt_overrides": {"status": "succeeded"}},
            {"receipt_overrides": {"result": None}},
            {"result_overrides": {"status": "rolled_back"}},
            {"result_overrides": {"request_id": "11111111-2222-3333-4444-555555555555"}},
            {"result_overrides": {"error_code": "timeout"}},
            {"result_overrides": {"to_game": "othegame"}},
            {"result_overrides": {"generation": 2}},
        )
        for override in cases:
            with self.subTest(override=override):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    write_receipt(root, **override)
                    write_runtimes(root, failed_seat(), previous_seat())
                    with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                        HELPER_MODULE.verify_ownership(root, canonical())
                    self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_OWNERSHIP_MISSING)


class HelperCliTests(unittest.TestCase):
    ADOPT_TEMPLATE = (
        'cp "{root}/after.json" "{root}/' + HELPER_MODULE.STATE_REL + '"\n'
        "exit 0"
    )

    def _root(self, launcher_body="exit 0"):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "bin").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        (root / HELPER_MODULE.CONFIG_REL).write_text("", encoding="utf-8")
        launcher = root / HELPER_MODULE.LAUNCHER_REL
        launcher.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$0\" \"$@\" > \"" + str(root) + "/argv.txt\"\n"
            f"{launcher_body.format(root=root)}\n",
            encoding="utf-8")
        launcher.chmod(0o755)
        write_state(root, canonical())
        write_receipt(root)
        write_runtimes(root, failed_seat(), previous_seat())
        self.addCleanup(tmp.cleanup)
        return root

    def _adopting_root(self, after_state):
        root = self._root(self.ADOPT_TEMPLATE)
        (root / "after.json").write_text(json.dumps(after_state), encoding="utf-8")
        return root

    def test_canonical_cli_is_called_once_with_the_adoption_flag(self):
        root = self._root()
        self.assertEqual(HELPER_MODULE.run_canonical_adopt(root), 0)
        argv = (root / "argv.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv, [
            str(root / HELPER_MODULE.LAUNCHER_REL), "--config",
            str(root / HELPER_MODULE.CONFIG_REL), "recover",
            "--adopt-failed-candidate",
            "--timeout", str(HELPER_MODULE.CLI_ADOPT_TIMEOUT_S),
        ])
        self.assertLessEqual(HELPER_MODULE.CLI_ADOPT_TIMEOUT_S, 600)

    def test_missing_launcher_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
                HELPER_MODULE.run_canonical_adopt(Path(tmp))
            self.assertEqual(ctx.exception.code, 25)

    def test_a_nonzero_cli_exit_is_an_adopt_refusal(self):
        root = self._root("exit 2")
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.adopt(root, "failed", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_ADOPT_REFUSED)

    def test_success_requires_the_same_runtime_to_become_active(self):
        root = self._adopting_root(adopted())
        self.assertEqual(HELPER_MODULE.adopt(root, "failed", True), 0)

    def test_success_without_any_adoption_is_unproven(self):
        root = self._root("exit 0")
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.adopt(root, "failed", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_ADOPT_UNPROVEN)

    def test_a_different_runtime_is_unproven(self):
        other = runtime(seat_generation=4, runtime_id="g4-abcdef")
        root = self._adopting_root(adopted(active=other, next_generation=5))
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.adopt(root, "failed", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_ADOPT_UNPROVEN)

    def test_cleanup_residue_is_unproven(self):
        root = self._adopting_root(adopted(retiring=[previous_seat()]))
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.adopt(root, "failed", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_ADOPT_UNPROVEN)

    def test_a_non_success_result_is_unproven(self):
        root = self._adopting_root(adopted(last_result=failed_result()))
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.adopt(root, "failed", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_ADOPT_UNPROVEN)

    def test_a_success_exit_with_an_invalid_state_is_invalid_result(self):
        root = self._adopting_root(canonical(phase="teleporting"))
        with self.assertRaises(HELPER_MODULE.FixedRecoverExit) as ctx:
            HELPER_MODULE.adopt(root, "failed", True)
        self.assertEqual(ctx.exception.code, HELPER_MODULE.EX_INVALID_RESULT)


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
            for argv in (["recovery_required", "true"], ["failed", "false"], ["failed", "maybe"]):
                with self.subTest(argv=argv):
                    self.assertEqual(self._run(argv, Path(tmp)).returncode, 64)

    def test_foreign_root_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            result = self._run(["failed", "true"], Path(other), root=Path(tmp))
            self.assertEqual(result.returncode, 3, result.stderr)

    def test_declared_mismatch_exits_80_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_state(root, canonical(phase="recovery_required"))
            result = self._run(["failed", "true"], root)
            self.assertEqual(result.returncode, 80, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_ownership_missing_exits_82_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_state(root, canonical())
            result = self._run(["failed", "true"], root)
            self.assertEqual(result.returncode, 82, result.stderr)
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
        self.assertIn("options: [adopt_failed_candidate]", self.text)
        self.assertIn("options: [failed]", self.text)
        self.assertIn("options: [true]", self.text)

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
        self.assertIn("control/ops/vm_actions/gameswitch_adopt.sh", self.text)
        self.assertIn("exec docich production $SHA", self.text)
        self.assertIn("status docich production $SHA", self.text)
        self.assertIn("bash -n control/ops/vm_actions/gameswitch_adopt.sh", self.text)
        self.assertIn("python3 -m py_compile control/ops/vm_actions/gameswitch_adopt.py", self.text)

    def test_workflow_passes_the_declaration_and_the_confirmation(self):
        self.assertIn("GS_ADOPT_PHASE=%s", self.text)
        self.assertIn("GS_ADOPT_CLEANUP_PENDING=%s", self.text)
        self.assertIn("INPUT_CONFIRM: ${{ inputs.confirm }}", self.text)
        self.assertIn("steps.auth.outputs.phase", self.text)
        self.assertIn("steps.auth.outputs.cleanup_pending", self.text)

    def test_workflow_maps_every_fixed_helper_exit_code(self):
        for code in ("64", "71", "72", "80", "81", "82", "83"):
            self.assertIn(f"{code})", self.text, code)
        self.assertIn("adopt_unproven", self.text)
        self.assertIn("adopt_refused", self.text)

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
        self.assertIn("GS_ADOPT_PHASE", self.text)
        self.assertIn("GS_ADOPT_CLEANUP_PENDING", self.text)
        self.assertIn("failed)", self.text)
        self.assertIn("true)", self.text)

    def test_script_runs_the_reviewed_helper_with_a_bounded_deadline(self):
        self.assertIn("timeout --kill-after=5s 480s", self.text)
        self.assertIn("python3 ops/vm_actions/gameswitch_adopt.py", self.text)


class CiContractTests(unittest.TestCase):
    def test_ci_compiles_and_lints_the_new_fixed_surface(self):
        text = CI.read_text(encoding="utf-8")
        for path in (
            "ops/vm_actions/authorize_gameswitch_adopt.py",
            "ops/vm_actions/gameswitch_adopt.py",
            "ops/vm_actions/gameswitch_adopt.sh",
            ".github/workflows/gameswitch-adopt.yml",
        ):
            self.assertIn(path, text, path)


if __name__ == "__main__":
    unittest.main()
