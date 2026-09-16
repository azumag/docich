from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
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

    def test_reviewed_build_context_is_exported_from_commit_not_worktree(self):
        if shutil.which("git") is None:
            self.skipTest("git is required")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "brains").mkdir()
            docker_dir = repo / "containers" / "nethack-canary"
            docker_dir.mkdir(parents=True)
            (repo / "src" / "tracked.py").write_text("tracked\n", encoding="utf-8")
            (repo / "brains" / "tracked.py").write_text("brain\n", encoding="utf-8")
            (docker_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            env = dict(os.environ)
            env.update(
                {
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com",
                }
            )
            subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "reviewed"], check=True, env=env)
            sha = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            # The production checkout legitimately carries untracked runtime
            # state; it must never leak into the image build context.
            (repo / "src" / "untracked_runtime.py").write_text("runtime\n", encoding="utf-8")
            (repo / "brains" / "untracked_bot.py").write_text("bot\n", encoding="utf-8")
            dest = Path(tmp) / "context"
            smoke._export_reviewed_build_context(repo, sha, dest)
            self.assertTrue((dest / "src" / "tracked.py").is_file())
            self.assertTrue((dest / "brains" / "tracked.py").is_file())
            self.assertTrue((dest / "containers" / "nethack-canary" / "Dockerfile").is_file())
            self.assertFalse((dest / "src" / "untracked_runtime.py").exists())
            self.assertFalse((dest / "brains" / "untracked_bot.py").exists())

    def test_reviewed_build_context_export_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with self.assertRaisesRegex(smoke.SmokeError, "reviewed_checkout_drift"):
                smoke._export_reviewed_build_context(missing, "a" * 40, Path(tmp) / "context")

    def test_build_image_uses_host_network_for_build_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = Path(tmp) / "context"
            (context / "containers" / "nethack-canary").mkdir(parents=True)
            setup = Path(tmp) / "setup"
            captured: dict[str, list[str]] = {}

            def fake_run(argv, **kwargs):
                captured["argv"] = list(argv)
                Path(argv[argv.index("--iidfile") + 1]).write_text(
                    "sha256:" + "a" * 64 + "\n", encoding="ascii"
                )
                return mock.Mock(returncode=0)

            with mock.patch.object(smoke.subprocess, "run", side_effect=fake_run):
                image = smoke._build_image(context, setup, docker="/usr/bin/docker")
            self.assertEqual(image, "sha256:" + "a" * 64)
            self.assertIn("--network=host", captured["argv"])

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
