import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_gameswitch_recover.py"
HELPER = ROOT / "ops/vm_actions/gameswitch_recover.py"
WRAPPER = ROOT / "ops/vm_actions/gameswitch_recover.sh"
WF = ROOT / ".github/workflows/gameswitch-recover.yml"

WORKFLOW_REF = "azumag/docich/.github/workflows/gameswitch-recover.yml@refs/heads/main"

spec = importlib.util.spec_from_file_location("gameswitch_recover", HELPER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def run_auth(**overrides):
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
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "INPUT_OPERATION": "recover_gameswitch",
        "INPUT_PHASE": "recovery_required",
        "INPUT_CLEANUP_PENDING": "false",
        "INPUT_CONFIRM": "production",
        "GITHUB_OUTPUT": "",
    }
    for key, value in overrides.items():
        if value == "":
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ["python3", str(AUTH)], env=env, capture_output=True, text=True
    )


class GameswitchRecoverAuthorizeTests(unittest.TestCase):
    def test_valid_recovery_required_phase_succeeds(self):
        result = run_auth(INPUT_PHASE="recovery_required", INPUT_CLEANUP_PENDING="false")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["operation"], "recover_gameswitch")
        self.assertEqual(data["phase"], "recovery_required")

    def test_valid_failed_phase_succeeds(self):
        result = run_auth(INPUT_PHASE="failed", INPUT_CLEANUP_PENDING="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["phase"], "failed")
        self.assertEqual(data["cleanup_pending"], "true")

    def test_unknown_phase_is_rejected(self):
        for phase in ("idle", "ready", "validating", "stopping", "draining", "unknown", ""):
            with self.subTest(phase=phase):
                result = run_auth(INPUT_PHASE=phase)
                self.assertNotEqual(result.returncode, 0)

    def test_unknown_cleanup_pending_is_rejected(self):
        for value in ("", "yes", "no", "1", "0", "TRUE", "FALSE"):
            with self.subTest(cleanup_pending=value):
                result = run_auth(INPUT_CLEANUP_PENDING=value)
                self.assertNotEqual(result.returncode, 0)

    def test_production_confirmation_is_required(self):
        result = run_auth(INPUT_CONFIRM="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("confirmation required", result.stderr)
        result = run_auth(INPUT_CONFIRM="preview")
        self.assertNotEqual(result.returncode, 0)

    def test_non_owner_actor_is_rejected(self):
        result = run_auth(GITHUB_ACTOR="other")
        self.assertNotEqual(result.returncode, 0)

    def test_non_owner_actor_id_is_rejected(self):
        result = run_auth(GITHUB_ACTOR_ID="42")
        self.assertNotEqual(result.returncode, 0)

    def test_unprotected_ref_is_rejected(self):
        result = run_auth(GITHUB_REF_PROTECTED="false")
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_workflow_ref_is_rejected(self):
        result = run_auth(
            GITHUB_WORKFLOW_REF="azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_operation_is_rejected(self):
        result = run_auth(INPUT_OPERATION="exec")
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_sha_is_rejected(self):
        result = run_auth(GITHUB_SHA="notasha")
        self.assertNotEqual(result.returncode, 0)

    def test_non_dispatch_event_is_rejected(self):
        result = run_auth(GITHUB_EVENT_NAME="push")
        self.assertNotEqual(result.returncode, 0)

    def test_public_repository_is_permitted_for_fixed_operation(self):
        """The fixed operation is permitted even when the repository is public."""
        result = run_auth()
        self.assertEqual(result.returncode, 0, result.stderr)


def canonical_state(phase="failed", **overrides):
    state = {
        "schema_version": 2,
        "revision": 7,
        "phase": phase,
        "operation": None,
        "request_id": None,
        "deadline_at": None,
        "next_generation": 3,
        "active": None,
        "candidate": None,
        "previous": None,
        "retiring": [],
        "last_result": None,
        "last_error": {"error_code": "switch_failed", "detail": "x"} if phase in {"failed", "recovery_required"} else None,
        "updated_at": "2026-10-17T00:00:00+00:00",
    }
    state.update(overrides)
    return state


def runtime(**overrides):
    generation = overrides.get("generation", 2)
    base = {
        "game": "sorengame",
        "adapter": overrides.get("adapter", "sorengame"),
        "generation": generation,
        "runtime_id": overrides.get("runtime_id", f"g{generation}-{'a' * 8}"),
        "lease_id": None,
        "game_window": f"game-g{generation}",
        "agent_window": f"agent-g{generation}",
        "adapter_session": f"docich-game-g{generation}",
        "started_at": "2026-10-17T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class GameswitchFixedHelperTests(unittest.TestCase):
    def chdir_fixture(self):
        """One docich root per test; cwd is the trust anchor.

        Registered cleanups run in reverse: rmtree is registered *after*
        the cwd restore so deletion never races the trust anchor.
        """

        root = Path(tempfile.mkdtemp(prefix="gameswitch-recover-"))
        self.addCleanup(os.chdir, os.getcwd())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.chdir(root)
        env = mock.patch.dict(os.environ, {"DOCICH_ROOT": os.path.realpath(root)})
        env.start()
        self.addCleanup(env.stop)
        return root

    def assert_exit(self, code, callable_):
        with self.assertRaises(module.FixedRecoverExit) as caught:
            callable_()
        self.assertEqual(caught.exception.code, code)

    def validate(self, state):
        return module.validate_shape(state)

    def test_minimal_failed_state_is_valid(self):
        self.assertEqual(self.validate(canonical_state())["phase"], "failed")
        self.assertEqual(self.validate(canonical_state(phase="recovery_required"))["phase"],
                         "recovery_required")
        ready = self.validate(canonical_state(phase="ready", active=runtime()))
        self.assertEqual(ready["phase"], "ready")
        self.assertEqual(self.validate(canonical_state(phase="idle"))["phase"], "idle")

    def test_unknown_schema_or_phase_is_drift(self):
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(schema_version=1)),
        )
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(phase="made_up_phase")),
        )
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(operation="exec", request_id="x")),
        )

    def test_non_boolean_cleanup_pending_is_drift(self):
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(last_result={"cleanup_pending": "true"})),
        )

    def test_seat_and_generation_drift_is_rejected(self):
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(active=runtime(runtime_id=f"g3-{'a' * 8}"))),
        )
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(active=runtime(game_window="game-g9"))),
        )
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(
                active=runtime(), retiring=[runtime(runtime_id=f"g2-{'b' * 8}")])),
        )
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(next_generation=2, active=runtime())),
        )

    def test_request_id_contract_is_enforced(self):
        self.assert_exit(
            module.EX_UNKNOWN_DRIFT,
            lambda: self.validate(canonical_state(operation="recover")),
        )
        good = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(
            self.validate(canonical_state(operation="recover", request_id=good))["request_id"],
            good,
        )

    def test_declared_phase_must_match_canonical(self):
        self.assert_exit(
            module.EX_DECLARED_MISMATCH,
            lambda: module.declared_matches(canonical_state(), "recovery_required", False),
        )
        self.assert_exit(
            module.EX_DECLARED_MISMATCH,
            lambda: module.declared_matches(canonical_state(), "failed", True),
        )
        module.declared_matches(canonical_state(), "failed", False)
        module.declared_matches(
            canonical_state(retiring=[runtime()]), "failed", True)
        module.declared_matches(
            canonical_state(last_result={"cleanup_pending": True}), "failed", True)

    def test_ownership_needs_receipt_and_runtime_dirs(self):
        root = self.chdir_fixture()
        switch = root / "run-soren-live"
        switch.mkdir()
        (switch / "game_switch.json").write_text(json.dumps(canonical_state()))
        receipt_request = "00000000-0000-0000-0000-000000000000"
        state = canonical_state(operation="recover", request_id=receipt_request)
        (switch / "game_switch.json").write_text(json.dumps(state))
        self.assert_exit(
            module.EX_OWNERSHIP_MISSING,
            lambda: module.verify_ownership(root, state),
        )
        requests = switch / "game-switch/requests"
        requests.mkdir(parents=True)
        (requests / f"{receipt_request}.json").write_text(
            json.dumps({"request_id": receipt_request, "result": None}))
        module.verify_ownership(root, state)
        (requests / f"{receipt_request}.json").unlink()
        (requests / f"{receipt_request}.json").write_text(json.dumps({"owner": "elsewhere"}))
        self.assert_exit(
            module.EX_OWNERSHIP_MISSING,
            lambda: module.verify_ownership(root, state),
        )
        state = canonical_state(active=runtime())
        (switch / "game_switch.json").write_text(json.dumps(state))
        self.assert_exit(
            module.EX_OWNERSHIP_MISSING,
            lambda: module.verify_ownership(root, state),
        )
        runtimes = switch / "runtimes"
        runtimes.mkdir()
        (runtimes / runtime()["runtime_id"]).mkdir()
        module.verify_ownership(root, state)

    def test_fixed_cli_recover_runs_once_in_a_bounded_argv(self):
        root = self.chdir_fixture()
        switch = root / "run-soren-live"
        switch.mkdir(parents=True)
        (switch / "game_switch.json").write_text(json.dumps(canonical_state()))
        bin_dir = root / "bin"
        config_dir = root / "config"
        bin_dir.mkdir(); config_dir.mkdir()
        (bin_dir / "docich").write_text("#!/bin/sh\n")
        os.chmod(bin_dir / "docich", 0o755)
        (config_dir / "docich.soren-live.toml").write_text("")
        with mock.patch.object(module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            self.assertEqual(module.recover(root, "failed", False), 0)
            (argv,), kwargs = run.call_args
        self.assertEqual(
            argv,
            [str(bin_dir / "docich"), "--config", str(config_dir / "docich.soren-live.toml"),
             "recover", "--timeout", str(module.CLI_RECOVER_TIMEOUT_S)],
        )
        self.assertLess(kwargs["timeout"], module.CLI_RECOVER_TIMEOUT_S + 600)
        with mock.patch.object(module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 2)
            self.assert_exit(
                module.EX_RECOVERY_REJECTED,
                lambda: module.recover(root, "failed", False),
            )
        with mock.patch.object(module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1)
            self.assert_exit(
                module.EX_RECOVERY_PENDING,
                lambda: module.recover(root, "failed", False),
            )

    def test_missing_reviewed_launcher_or_config_refuses(self):
        root = self.chdir_fixture()
        switch = root / "run-soren-live"
        switch.mkdir(parents=True)
        (switch / "game_switch.json").write_text(json.dumps(canonical_state()))
        self.assert_exit(25, lambda: module.run_canonical_recover(root))
        (root / "bin").mkdir()
        self.assert_exit(25, lambda: module.run_canonical_recover(root))

    def test_recover_only_runs_from_its_own_root_with_fixed_usage(self):
        self.addCleanup(os.chdir, os.getcwd())
        home = tempfile.mkdtemp(prefix="gameswitch-recover-alt-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        os.chdir(home)
        self.assert_exit(module.EX_BAD_ROOT, lambda: module.main(["failed", "true"]))
        root = self.chdir_fixture()
        self.assert_exit(module.EX_BAD_USAGE, lambda: module.main([]))
        self.assert_exit(module.EX_BAD_USAGE, lambda: module.main(["failed", "true", "extra"]))
        (root / "run-soren-live").mkdir()
        (root / "run-soren-live/game_switch.json").write_text(
            json.dumps(canonical_state(phase="failed")))
        self.assert_exit(
            module.EX_INVALID_REQUEST,
            lambda: module.main(["draining", "true"]),
        )
        self.assert_exit(
            module.EX_INVALID_REQUEST,
            lambda: module.main(["failed", "yes"]),
        )

    def test_wrapper_is_a_fixed_guard_with_no_arguments(self):
        content = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("GS_RECOVER_PHASE", content)
        self.assertIn("timeout --kill-after=5s", content)
        self.assertNotIn("$1", content)
        # The gateway exec caps the piped payload including the fixed
        # preamble; the reviewed script stays far below the cap.
        self.assertLessEqual(WRAPPER.stat().st_size + 128, 16384)


class GameswitchRecoverWorkflowTests(unittest.TestCase):
    def test_workflow_exists_and_has_dispatch_trigger(self):
        self.assertTrue(WF.exists())
        content = WF.read_text()
        assert "workflow_dispatch" in content
        assert "gameswitch-recover" in content
        assert "authorize_gameswitch_recover.py" in content
        assert "timeout-minutes" in content

    def test_workflow_owner_protected_main_and_environment_gates(self):
        content = WF.read_text()
        self.assertIn("github.actor_id == 9018513", content)
        self.assertIn("github.triggering_actor == 'azumag'", content)
        self.assertIn("github.ref == 'refs/heads/main'", content)
        self.assertIn("github.ref_protected == true", content)
        self.assertIn("environment: vm-operations", content)

    def test_workflow_keeps_the_immutable_sha_and_production_contract(self):
        content = WF.read_text()
        self.assertIn("git -C control rev-parse HEAD", content)
        self.assertIn("ls-remote --exit-code origin refs/heads/main", content)
        self.assertIn("status docich production $SHA", content)

    def test_workflow_pipes_only_the_fixed_reviewed_script(self):
        content = WF.read_text()
        self.assertIn("control/ops/vm_actions/gameswitch_recover.sh", content)
        self.assertIn("exec docich production $SHA", content)
        self.assertNotIn("VM_COMMAND", content)
        self.assertNotIn("inputs.command", content)
        # The piped payload carries only the two authorized fixed values.
        self.assertIn("GS_RECOVER_PHASE=%s", content)
        self.assertIn("GS_RECOVER_CLEANUP_PENDING=%s", content)
        self.assertIn("$GS_PHASE", content)
        self.assertIn("$GS_PENDING", content)
        self.assertIn("timeout --kill-after", content)

    def test_workflow_does_not_touch_shared_delivery_or_audio(self):
        content = WF.read_text()
        # No unit management and no arbitrary command surface: the only
        # production entry point is the fixed recover helper, whose comment
        # documents that the shared units are out of scope.
        self.assertNotIn("systemctl", content)
        self.assertIn("Shared display/audio/stream units", content)
        self.assertNotIn("kill ", content)


if __name__ == "__main__":
    unittest.main()
