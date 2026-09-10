import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops" / "vm_actions" / "authorize.py"
GATEWAY = ROOT / "ops" / "vm_actions" / "gateway.py"
WF = ROOT / ".github" / "workflows" / "vm-operations.yml"


def load_gateway():
    spec = importlib.util.spec_from_file_location("vm_gateway", str(GATEWAY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiagnosticsGatewayTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vmops-diag-gw-"))
        self.state = self.base / "state"
        self.state.mkdir()
        self.doc = self.base / "docich"
        (self.doc / "ops" / "vm_actions").mkdir(parents=True)
        (self.doc / "src" / "docich").mkdir(parents=True)
        self.soren = self.base / "soren"
        (self.soren / "tmp" / "state").mkdir(parents=True)
        for rel in (
            "ops/vm_actions/collect_diagnostics.py",
            "ops/vm_actions/runtime_registry.py",
            "src/docich/runtime_backend.py",
            "src/docich/__init__.py",
        ):
            src = ROOT / rel
            if src.is_file():
                shutil.copy(src, self.doc / rel)
        subprocess.run(["git", "init", "-q", str(self.doc)], check=True)
        subprocess.run(["git", "-C", str(self.doc), "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.doc), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(self.doc), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.doc), "commit", "-qm", "v1"], check=True)
        self.config = self.base / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "state": str(self.state),
                    "repos": {
                        "docich": {
                            "production": str(self.doc),
                            "mode": "git",
                            "projections": {"games/soviet_now": str(self.soren)},
                        }
                    },
                }
            )
        )

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def call(self, cmd, payload=b""):
        env = os.environ.copy()
        env["SSH_ORIGINAL_COMMAND"] = cmd
        env["VMOPS_TESTING"] = "1"
        return subprocess.run(
            ["python3", str(GATEWAY), str(self.config)], input=payload, capture_output=True, env=env
        )

    def snapshot(self, root):
        found = {}
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and not path.is_symlink() and ".git/" not in str(path):
                stat = path.stat()
                found[str(path)] = (stat.st_mtime_ns, stat.st_size)
        return found

    def test_production_diagnostics_returns_sanitized_json(self):
        before = self.snapshot(self.soren)
        proc = self.call(f"diagnostics docich production {'b' * 40}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        data = json.loads(proc.stdout.decode())
        self.assertEqual(data["status"], "diagnosed")
        diag = data["diagnostics"]
        self.assertIn(diag["status"], ("ok", "warn", "critical"))
        for section in ("meta", "workers", "queues", "ai", "improvement", "corners"):
            self.assertIn(section, diag)
        self.assertEqual(diag["workers"]["expected"] >= 10, True)
        self.assertEqual(self.snapshot(self.soren), before)

    def test_preview_diagnostics_is_rejected(self):
        proc = self.call(f"diagnostics docich preview {'b' * 40}")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("VM operation rejected", proc.stderr.decode())

    def test_unknown_operation_with_suffix_is_rejected(self):
        proc = self.call(f"diagnostics_extra docich production {'b' * 40}")
        self.assertNotEqual(proc.returncode, 0)
        proc = self.call(f"diagnostics docich production {'b' * 40} extra")
        self.assertNotEqual(proc.returncode, 0)

    def test_stdin_payload_cannot_change_diagnostics(self):
        clean = self.call(f"diagnostics docich production {'b' * 40}")
        evil = self.call(
            f"diagnostics docich production {'b' * 40}", payload=b"; cat /etc/passwd; echo pwned"
        )
        self.assertEqual(clean.returncode, 0)
        self.assertEqual(evil.returncode, 0)
        clean_data = json.loads(clean.stdout.decode())
        evil_data = json.loads(evil.stdout.decode())
        for data in (clean_data, evil_data):
            data["diagnostics"]["meta"].pop("generated_at", None)
        self.assertEqual(clean_data, evil_data)

    def test_modified_collector_is_refused(self):
        with open(self.doc / "ops" / "vm_actions" / "collect_diagnostics.py", "a") as handle:
            handle.write("\n# operator edit\n")
        proc = self.call(f"diagnostics docich production {'b' * 40}")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("VM operation rejected", proc.stderr.decode())

    def test_missing_collector_is_refused(self):
        (self.doc / "ops" / "vm_actions" / "collect_diagnostics.py").unlink()
        proc = self.call(f"diagnostics docich production {'b' * 40}")
        self.assertNotEqual(proc.returncode, 0)

    def test_secrets_never_reach_actions_output(self):
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        import time

        event = {
            "ts": int(time.time()) - 10,
            "day": "20260101",
            "event": "fail",
            "label": "RADIO",
            "agent": "opencode-go:deepseek-v4-flash",
            "rc": "1",
            "resolved_model": "",
            "error": "boom token=SUPERSECRET999 password=hunter2",
        }
        with open(stats / "20260101.jsonl", "w", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        proc = self.call(f"diagnostics docich production {'b' * 40}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertNotIn("SUPERSECRET999", proc.stdout.decode())
        self.assertNotIn("hunter2", proc.stdout.decode())

    def test_huge_runtime_state_stays_bounded(self):
        stats = self.soren / "tmp" / "state" / "ai_stats"
        stats.mkdir(parents=True, exist_ok=True)
        import time

        with open(stats / "20260101.jsonl", "w", encoding="utf-8") as handle:
            for _ in range(30000):
                handle.write(
                    json.dumps(
                        {
                            "ts": int(time.time()) - 10,
                            "day": "20260101",
                            "event": "fail",
                            "label": "RADIO",
                            "agent": "x:y",
                            "rc": "1",
                            "resolved_model": "",
                            "error": "z" * 3000,
                        }
                    )
                    + "\n"
                )
        proc = self.call(f"diagnostics docich production {'b' * 40}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertLessEqual(len(proc.stdout), 65536 + 4096)


class DiagnosticsSanitizerTests(unittest.TestCase):
    def test_redacts_secret_keys_and_caps_sizes(self):
        gateway = load_gateway()
        data = {
            "status": "ok",
            "nested": {"api_key": "SHOULD_NOT_APPEAR", "ok_value": "fine"},
            "long": "v" * 1000,
            "big": list(range(500)),
            "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}},
        }
        clean = gateway._sanitize_diagnostics({k: v for k, v in data.items() if k != "deep"})
        text = json.dumps(clean)
        self.assertNotIn("SHOULD_NOT_APPEAR", text)
        self.assertEqual(clean["nested"]["api_key"], "***")
        self.assertEqual(clean["nested"]["ok_value"], "fine")
        self.assertLessEqual(len(clean["long"]), 500)
        self.assertLessEqual(len(clean["big"]), 100)
        with self.assertRaises(ValueError):
            gateway._sanitize_diagnostics(data)
        with self.assertRaises(ValueError):
            gateway._sanitize_diagnostics({"status": "ok", "bad": object()})


class DiagnosticsAuthorizeTests(unittest.TestCase):
    def run_auth(self, **overrides):
        env = {
            "GITHUB_REPOSITORY": "azumag/docich",
            "GITHUB_REPOSITORY_ID": "1327276249",
            "GITHUB_REPOSITORY_OWNER": "azumag",
            "GITHUB_REPOSITORY_OWNER_ID": "9018513",
            "GITHUB_REPOSITORY_PRIVATE": "true",
            "GITHUB_ACTOR": "azumag",
            "GITHUB_ACTOR_ID": "9018513",
            "GITHUB_TRIGGERING_ACTOR": "azumag",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "a" * 40,
            "INPUT_OPERATION": "diagnostics",
            "INPUT_TARGET": "production",
            "INPUT_REF": "main",
            "INPUT_CONFIRM": "",
        }
        env.update(overrides)
        return subprocess.run(["python3", str(AUTH)], text=True, capture_output=True, env=env)

    def test_production_diagnostics_needs_no_confirm_but_stays_owner_only(self):
        proc = self.run_auth()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual((data["operation"], data["target"]), ("diagnostics", "production"))
        denied = self.run_auth(GITHUB_TRIGGERING_ACTOR="collab")
        self.assertNotEqual(denied.returncode, 0)

    def test_preview_diagnostics_is_denied(self):
        proc = self.run_auth(INPUT_TARGET="preview")
        self.assertNotEqual(proc.returncode, 0)


class DiagnosticsWorkflowTests(unittest.TestCase):
    def test_workflow_exposes_diagnostics_operation(self):
        text = WF.read_text(encoding="utf-8")
        self.assertIn("options: [status, deploy, exec, bootstrap, diagnostics]", text)
        self.assertIn("Query read-only runtime diagnostics", text)
        self.assertIn('"diagnostics docich production $SHA"', text)
        self.assertIn("steps.auth.outputs.operation == 'diagnostics'", text)

    def test_workflow_explains_stale_installed_gateway_without_relaxing_exec_boundary(self):
        text = WF.read_text(encoding="utf-8")
        self.assertIn("diagnostics-gateway.err", text)
        self.assertIn("Installed VM gateway does not accept diagnostics", text)
        self.assertIn("trusted owner-only privileged install path", text)
        self.assertNotIn("exec docich production $SHA\" 2>", text)


if __name__ == "__main__":
    unittest.main()
