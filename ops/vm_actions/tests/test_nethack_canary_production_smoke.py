from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "ops" / "vm_actions" / "nethack_canary_production_smoke.py"
SPEC = importlib.util.spec_from_file_location("nethack_canary_production_smoke", MODULE_PATH)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


class RequestTests(unittest.TestCase):
    def test_strict_request(self):
        req = smoke.parse_request_payload(
            {"schema_version": 1, "request_id": "a" * 32, "sha": "b" * 40}
        )
        self.assertEqual(req.request_id, "a" * 32)
        with self.assertRaises(ValueError):
            smoke.parse_request_payload(
                {"schema_version": 1, "request_id": "a" * 32, "sha": "b" * 40, "extra": True}
            )
        with self.assertRaises(ValueError):
            smoke.parse_request_payload(
                {"schema_version": 1, "request_id": "../bad", "sha": "b" * 40}
            )

    def test_consume_requires_0600_and_unlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(
                json.dumps({"schema_version": 1, "request_id": "a" * 32, "sha": "b" * 40}),
                encoding="utf-8",
            )
            path.chmod(0o600)
            req = smoke.consume_request(path)
            self.assertEqual(req.sha, "b" * 40)
            self.assertFalse(path.exists())

    def test_consume_rejects_group_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(
                json.dumps({"schema_version": 1, "request_id": "a" * 32, "sha": "b" * 40}),
                encoding="utf-8",
            )
            path.chmod(0o640)
            with self.assertRaises(ValueError):
                smoke.consume_request(path)


class ScopeTests(unittest.TestCase):
    def test_scoped_docker_group_is_required_but_persistent_membership_is_forbidden(self):
        smoke.verify_docker_scope(
            user="ubuntu",
            primary_gid=1000,
            docker_gid=999,
            persistent_members=(),
            effective_gids=(1000, 999),
        )
        with self.assertRaisesRegex(smoke.SmokeError, "persistent_docker_membership"):
            smoke.verify_docker_scope(
                user="ubuntu",
                primary_gid=1000,
                docker_gid=999,
                persistent_members=("ubuntu",),
                effective_gids=(1000, 999),
            )
        with self.assertRaisesRegex(smoke.SmokeError, "docker_scope_missing"):
            smoke.verify_docker_scope(
                user="ubuntu",
                primary_gid=1000,
                docker_gid=999,
                persistent_members=(),
                effective_gids=(1000,),
            )

    def test_reviewed_build_context_rejects_tracked_or_untracked_drift(self):
        clean = SimpleNamespace(returncode=0, stdout="")
        dirty = SimpleNamespace(returncode=0, stdout="?? src/rogue.py\n")
        with mock.patch.object(smoke.subprocess, "run", return_value=clean):
            smoke._assert_reviewed_build_context(Path("/home/ubuntu/docich"))
        with mock.patch.object(smoke.subprocess, "run", return_value=dirty):
            with self.assertRaisesRegex(smoke.SmokeError, "reviewed_checkout_drift"):
                smoke._assert_reviewed_build_context(Path("/home/ubuntu/docich"))

    def test_canary_path_must_be_disjoint(self):
        settings = mock.Mock()
        settings.save_dir = Path("/var/games/nethack/save")
        settings.xlogfile = Path("/var/games/nethack/xlogfile")
        settings.dump_dir = Path("/var/games/nethack/dumps")
        smoke._assert_disjoint(Path("/home/ubuntu/docich/run-soren-live/nethack/canary/x"), settings)
        with self.assertRaisesRegex(smoke.SmokeError, "canary_path_overlap"):
            smoke._assert_disjoint(Path("/var/games/nethack/save/canary"), settings)


class ResultTests(unittest.TestCase):
    def base(self):
        return {
            "worker_status": "completed",
            "arm": "baseline",
            "isolation_mode": "container",
            "production_state_touched": False,
            "candidate_action_source": "baseline_p3b",
            "terminal_status": "dead",
            "exit_reason": "terminal_xlog",
        }

    def test_terminal_and_policy_stall_categories(self):
        self.assertEqual(smoke.classify_worker_result(self.base()), "terminal")
        data = self.base()
        data["terminal_status"] = "timeout"
        data["exit_reason"] = "policy_stall:adjacent_creature"
        self.assertEqual(smoke.classify_worker_result(data), "policy_stall")
        data["exit_reason"] = "max_turns"
        self.assertEqual(smoke.classify_worker_result(data), "turn_limit")

    def test_outer_timeout_is_failure(self):
        with self.assertRaisesRegex(smoke.SmokeError, "worker_timeout"):
            smoke.classify_worker_result({"worker_status": "timeout"})


class StaticContractTests(unittest.TestCase):
    def test_systemd_scope_and_hardening(self):
        service = (ROOT / "scripts/systemd/docich-nethack-canary-smoke.service").read_text()
        path = (ROOT / "scripts/systemd/docich-nethack-canary-smoke.path").read_text()
        self.assertIn("User=ubuntu", service)
        self.assertIn("SupplementaryGroups=docker", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", service)
        self.assertIn("MemoryMax=2G", service)
        self.assertIn("TasksMax=128", service)
        self.assertIn("PathExists=/home/ubuntu/.local/state/docich/nethack-canary-smoke/request.json", path)

    def test_installer_never_persists_docker_membership(self):
        text = (ROOT / "ops/vm_actions/install_nethack_canary_smoke.sh").read_text()
        self.assertIn("verify_container_host.sh", text)
        self.assertIn("systemctl enable --now", text)
        self.assertNotIn("usermod", text)
        self.assertNotIn("gpasswd", text)
        self.assertNotIn("adduser", text)

    def test_requester_has_no_docker_command(self):
        text = (ROOT / "ops/vm_actions/request_nethack_canary_smoke.sh").read_text()
        self.assertNotIn("docker ", text)
        self.assertIn("docich-nethack-canary-smoke.path", text)
        self.assertIn('codes = {"terminal": 0, "policy_stall": 10, "turn_limit": 11, "other_timeout": 12}', text)

    def test_workflow_is_manual_owner_gated_and_uses_production_gateway(self):
        text = (ROOT / ".github/workflows/nethack-canary-production-smoke.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertNotIn("docker build", text)
        self.assertNotIn("SupplementaryGroups=docker", text)


if __name__ == "__main__":
    unittest.main()
