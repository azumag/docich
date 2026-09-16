from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "scripts/systemd/docich-nethack-canary-smoke.service"
PATH_UNIT = ROOT / "scripts/systemd/docich-nethack-canary-smoke.path"
INSTALLER = ROOT / "ops/container_host/install_nethack_canary_smoke_units.sh"
SMOKE = ROOT / "ops/vm_actions/nethack_canary_production_smoke.py"
VERIFY = ROOT / "ops/vm_actions/nethack_canary_smoke_verify.py"
WAIT = ROOT / "ops/vm_actions/wait_nethack_canary_reviewed_tree.py"
WORKFLOW = ROOT / ".github/workflows/nethack-canary-production-smoke.yml"


def load_verifier():
    spec = importlib.util.spec_from_file_location("nethack_canary_smoke_verify", VERIFY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestNethackCanaryProductionSmoke(unittest.TestCase):
    def test_system_service_scopes_docker_group_to_oneshot(self):
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("User=ubuntu", text)
        self.assertIn("SupplementaryGroups=docker", text)
        self.assertIn("ExecStartPre=/usr/bin/python3 __DOCICH_ROOT__/ops/vm_actions/wait_nethack_canary_reviewed_tree.py", text)
        self.assertIn("ExecStart=/usr/bin/python3 __DOCICH_ROOT__/ops/vm_actions/nethack_canary_production_smoke.py", text)
        self.assertIn("ReadWritePaths=__DOCICH_ROOT__/run-soren-live", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ProtectHome=read-only", text)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", text)
        self.assertIn("CapabilityBoundingSet=", text)
        self.assertNotIn("WantedBy=", text)

    def test_path_unit_only_watches_reviewed_epoch(self):
        text = PATH_UNIT.read_text(encoding="utf-8")
        self.assertIn("PathChanged=__DOCICH_ROOT__/ops/vm_actions/nethack_canary_smoke_epoch", text)
        self.assertIn("Unit=docich-nethack-canary-smoke.service", text)
        self.assertIn("WantedBy=multi-user.target", text)
        self.assertNotIn("PathExists=", text)

    def test_root_installer_never_adds_ubuntu_to_docker_group(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("must run as root", text)
        self.assertIn("ubuntu must not be a permanent docker-group member", text)
        self.assertIn("SupplementaryGroups", SERVICE.read_text(encoding="utf-8"))
        self.assertNotIn("usermod", text)
        self.assertNotIn("gpasswd", text)
        self.assertNotIn("adduser ubuntu docker", text)
        self.assertIn("enable --now docich-nethack-canary-smoke.path", text)
        self.assertNotIn("enable --now docich-nethack-canary-smoke.service", text)

    def test_smoke_uses_reviewed_archive_and_baseline_only(self):
        text = SMOKE.read_text(encoding="utf-8")
        self.assertIn('"git", "archive", "--format=tar", "HEAD"', text)
        self.assertIn('"--network=host"', text)
        self.assertIn("run_container_worker", text)
        self.assertIn("_production_fingerprint", text)
        self.assertIn("_production_live", text)
        self.assertIn('"arm": "baseline"', text)
        self.assertIn('"controller": {"kind": "baseline_p3b"}', text)
        self.assertNotIn("candidate_manifest", text)
        self.assertNotIn("sudo", text)

    def test_waiter_includes_epoch_and_security_paths(self):
        text = WAIT.read_text(encoding="utf-8")
        self.assertIn('"ops/vm_actions/nethack_canary_smoke_epoch"', text)
        self.assertIn('"containers/nethack-canary/Dockerfile"', text)
        self.assertIn('"brains"', text)
        self.assertIn('"diff", "--cached", "--quiet"', text)

    def test_workflow_is_owner_main_protected_and_never_runs_docker_over_exec(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("environment: vm-operations", text)
        self.assertIn("ops/vm_actions/nethack_canary_smoke_epoch", text)
        self.assertIn("nethack_canary_smoke_verify.py", text)
        self.assertIn("exec docich production $SHA", text)
        self.assertNotIn("sudo ", text)
        # Docker belongs to the system-scope oneshot, never the SSH command.
        self.assertNotIn("docker build", text)
        self.assertNotIn("docker run", text)

    def test_verifier_has_bounded_exit_only_contract(self):
        module = load_verifier()
        self.assertEqual(module.EXIT_OK, 0)
        self.assertEqual(module.EXIT_PENDING, 3)
        self.assertEqual(module.EXIT_FAILED, 4)
        self.assertEqual(module.EXIT_UNIT_UNAVAILABLE, 5)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            result = state / "nethack/production-smoke/result.json"
            result.parent.mkdir(parents=True)
            good = {
                "schema_version": 1,
                "status": "success",
                "sha": "a" * 40,
                "epoch": "7",
                "production_untouched": True,
                "runsc_required": True,
                "arm": "baseline",
                "worker_status": "completed",
                "terminal_status": "timeout",
                "policy_effect": "none",
            }
            result.write_text(json.dumps(good), encoding="utf-8")
            fake_global = type("G", (), {"state_dir": state})()
            with patch.object(module, "ROOT", root), patch.object(module, "_system_unit_available", return_value=True), patch.object(module, "load_global", return_value=fake_global):
                self.assertEqual(module.verify("a" * 40, "7"), 0)
                self.assertEqual(module.verify("b" * 40, "7"), 3)
                good["status"] = "error"
                result.write_text(json.dumps(good), encoding="utf-8")
                self.assertEqual(module.verify("a" * 40, "7"), 4)


if __name__ == "__main__":
    unittest.main()
