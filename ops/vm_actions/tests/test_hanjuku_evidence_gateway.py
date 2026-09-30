"""Synthetic local integration; no SSH, service changes or production access."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import hanjuku_evidence_gateway as g
import authorize_hanjuku_evidence_export as auth
import gateway_entry as entry
import receive_hanjuku_evidence as receiver
import validate_hanjuku_ciphertext as transport
from test_hanjuku_evidence import EvidenceFixture, RID, canonical, run_state


class GatewayTests(EvidenceFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.keys.cleanup)
        cls.key = Path(cls.keys.name) / "private.pem"
        cls.cert = Path(cls.keys.name) / "recipient.pem"
        subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
                        "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1",
                        "-subj", "/CN=synthetic-gateway-test"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)

    def setUp(self):
        super().setUp()
        self.production = self.root / "production"
        self.production.mkdir()
        new_state = self.production / "run-soren-live"
        self.state.rename(new_state)
        self.state = new_state
        self.run = self.state / "runtimes" / RID
        self.ops = self.root / "ops-state"
        self.ops.mkdir()
        (self.ops / "vm-operations.lock").touch()
        self.install = self.root / "installed"
        self.install.mkdir()
        self.cfg_path = self.root / "config.json"
        self.cfg = {"state": str(self.ops), "repos": {"docich": {
            "production": str(self.production), "mode": "git"}}}
        self.cfg_path.write_text(json.dumps(self.cfg))
        for name in g.INSTALLED_FILES:
            src = self.production / "ops/vm_actions" / name
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text("# Synthetic installed source: " + name + "\n")
            shutil.copyfile(src, self.install / name)
        self.git("init", "-q")
        self.git("add", "ops")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic source")
        self.sha = self.git("rev-parse", "HEAD").strip()
        for key, value in {"CONFIG": self.cfg_path, "PRODUCTION": self.production,
                           "OPERATIONS_STATE": self.ops, "INSTALL": self.install,
                           "TRUSTED_UID": os.getuid()}.items():
            patcher = patch.object(g, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.core = Mock()
        self.core.status_result.return_value = {"status": "configured", "sha": self.sha}
        self.command = f"hanjuku_evidence docich production {self.sha}"
        self.request = g.evidence._dump({"schema": 1, "runtime_id": RID,
                                         "recipient": self.cert.read_text()})

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.production), "-c",
                                        "core.hooksPath=/dev/null", *args],
                                       stderr=subprocess.DEVNULL, text=True, timeout=10)

    def export(self, raw=None):
        return g.export(self.core, self.cfg_path, self.command,
                        self.request if raw is None else raw)

    def test_encrypted_roundtrip_without_state_mutation(self):
        before = {p: p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        ciphertext = self.export()
        transport.validate(ciphertext)
        clear = receiver.decrypt(ciphertext, self.key, self.cert, runtime_id=RID)
        self.assertEqual(receiver.verify_archive(clear)["identity"]["runtime_id"], RID)
        self.assertNotIn(b"hanjuku_run.json", ciphertext)
        self.assertEqual(before, {p: p.read_bytes() for p in self.state.rglob("*") if p.is_file()})
        self.assertEqual(self.core.status_result.call_count, 2)
        self.assertFalse(list(self.production.rglob("*.cms")))

    def test_fixed_command_rejects_extra_targets_and_shell(self):
        for command in (self.command + " extra", self.command.replace("production", "preview"),
                        self.command.replace("docich", "soviet_now"), self.command + ";id",
                        self.command.replace(" ", "\t"), self.command + "\n",
                        "exec docich production " + self.sha, " " + self.command,
                        "hanjuku_evidence docich production main"):
            with self.subTest(command=command), self.assertRaises(g.evidence.EvidenceError):
                g.parse_command(command)

    def test_unknown_request_inputs_are_rejected_before_snapshot(self):
        original = json.loads(self.request)
        for key in ("path", "state_dir", "command", "url", "private_key", "output"):
            raw = g.evidence._dump({**original, key: "synthetic"})
            with self.subTest(key=key), patch.object(g.evidence, "snapshot") as snapshot:
                with self.assertRaises(g.evidence.EvidenceError):
                    self.export(raw)
                snapshot.assert_not_called()

    def test_bad_request_types_sizes_and_ids(self):
        original = json.loads(self.request)
        invalid = [b"", b"x" * (g.MAX_REQUEST + 1), b"[]", b'{"schema":1,"schema":1}',
                   g.evidence._dump({**original, "schema": True})]
        invalid += [g.evidence._dump({**original, "runtime_id": value})
                    for value in (None, True, "latest", "active", "../g7-1234abcd", "g07-1234abcd")]
        for raw in invalid:
            with self.subTest(raw=raw[:50]), self.assertRaises(g.evidence.EvidenceError):
                g.parse_request(raw)

    def test_private_key_cannot_be_a_recipient(self):
        request = json.loads(self.request)
        request["recipient"] = self.key.read_text()
        with patch.object(g.evidence, "snapshot") as snapshot:
            with self.assertRaises(g.evidence.EvidenceError):
                self.export(g.evidence._dump(request))
            snapshot.assert_not_called()

    def test_active_run_refused(self):
        state = canonical(phase="ready", active={"runtime_id": RID, "generation": 7})
        (self.state / "game_switch.json").write_text(json.dumps(state))
        with self.assertRaisesRegex(g.evidence.EvidenceError, "runtime_active"):
            self.export()

    def test_no_terminal_refused(self):
        (self.run / "hanjuku_run.json").write_text(json.dumps(run_state(terminal_reason=None)))
        with self.assertRaisesRegex(g.evidence.EvidenceError, "not_completed"):
            self.export()

    def test_wrong_config_path_and_fixed_roots(self):
        with self.assertRaisesRegex(g.evidence.EvidenceError, "invalid_configuration"):
            g.load_config(self.root / "other.json")
        for key in ("state", "production", "mode"):
            cfg = copy.deepcopy(self.cfg)
            if key == "state":
                cfg[key] = str(self.root)
            else:
                cfg["repos"]["docich"][key] = "other"
            self.cfg_path.write_text(json.dumps(cfg))
            with self.subTest(key=key), self.assertRaises(g.evidence.EvidenceError):
                self.export()

    def test_untrusted_config_permissions(self):
        self.cfg_path.chmod(0o666)
        with self.assertRaisesRegex(g.evidence.EvidenceError, "untrusted_installation"):
            self.export()

    def test_testing_environment_cannot_bypass_owner_check(self):
        with patch.dict(os.environ, {"VMOPS_TESTING": "1"}), patch.object(g, "TRUSTED_UID", os.getuid() + 1):
            with self.assertRaisesRegex(g.evidence.EvidenceError, "untrusted_installation"):
                self.export()

    def test_config_symlink_refused(self):
        target = self.root / "actual.json"
        self.cfg_path.rename(target)
        self.cfg_path.symlink_to(target)
        with self.assertRaises((OSError, g.evidence.EvidenceError)):
            self.export()

    def test_installed_source_must_match_pinned_commit(self):
        (self.install / "hanjuku_evidence.py").write_text("# stale source\n")
        with self.assertRaisesRegex(g.evidence.EvidenceError, "installed_source_mismatch"):
            self.export()

    def test_untrusted_installed_permissions(self):
        (self.install / "hanjuku_evidence.py").chmod(0o666)
        with self.assertRaisesRegex(g.evidence.EvidenceError, "untrusted_installation"):
            self.export()

    def test_production_status_and_sha_fail_closed(self):
        for value in ({"status": "drift", "sha": self.sha}, {"status": "configured", "sha": "a" * 40},
                      {"status": "recovery_required", "sha": self.sha}, {}):
            self.core.status_result.return_value = value
            with self.subTest(value=value), patch.object(g.evidence, "snapshot") as snapshot:
                with self.assertRaisesRegex(g.evidence.EvidenceError, "production_unverified"):
                    self.export()
                snapshot.assert_not_called()

    def test_final_status_change_discards_ciphertext(self):
        self.core.status_result.side_effect = [{"status": "configured", "sha": self.sha},
                                               {"status": "drift", "sha": self.sha}]
        with self.assertRaisesRegex(g.evidence.EvidenceError, "production_unverified"):
            self.export()

    def test_deployment_lock_contention_does_not_wait(self):
        with (self.ops / "vm-operations.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.export()

    def test_missing_deployment_lock_not_created(self):
        lock = self.ops / "vm-operations.lock"
        lock.unlink()
        with self.assertRaises(FileNotFoundError):
            self.export()
        self.assertFalse(lock.exists())

    def test_deployment_lock_replacement_rejected(self):
        with self.assertRaisesRegex(g.evidence.EvidenceError, "source_changed"):
            with g.operations_lock():
                lock = self.ops / "vm-operations.lock"
                lock.rename(self.ops / "old.lock")
                lock.touch()

    def test_ciphertext_budget(self):
        with patch.object(g, "MAX_CIPHERTEXT", 1):
            with self.assertRaisesRegex(g.evidence.EvidenceError, "ciphertext_too_large"):
                self.export()

    def test_authorization_exact_environment(self):
        env = {**auth.EXPECTED, "GITHUB_SHA": self.sha, "INPUT_RUNTIME_ID": RID,
               "INPUT_RECIPIENT": self.cert.read_text()}
        self.assertEqual(auth.authorize(env), self.request)
        for key in (*auth.EXPECTED, "GITHUB_SHA"):
            with self.subTest(key=key), self.assertRaises(g.evidence.EvidenceError):
                auth.authorize({**env, key: "unexpected"})
        for event in ("schedule", "issue_comment", "pull_request", "workflow_run"):
            with self.subTest(event=event), self.assertRaises(g.evidence.EvidenceError):
                auth.authorize({**env, "GITHUB_EVENT_NAME": event})

    def test_ciphertext_transport_rejects_plaintext_truncation_and_trailing_bytes(self):
        raw = self.export()
        for bad in (b"PK\x03\x04plain", raw[:-1], raw + b"hidden", b"0\x80" + raw,
                    b"0\x85" + raw, b"0\x81\x10" + raw, b"0\x82\0\x80" + raw):
            with self.subTest(), self.assertRaises(g.evidence.EvidenceError):
                transport.validate(bad)

    def test_receiver_rejects_unauthenticated_cbc(self):
        clear = self.pack()
        cbc = g.evidence._openssl(["cms", "-encrypt", "-binary", "-aes-256-cbc",
                                  "-outform", "DER", "-recip", str(self.cert)], clear)
        with patch.object(receiver.evidence, "_openssl") as openssl:
            with self.assertRaisesRegex(g.evidence.EvidenceError, "invalid_ciphertext"):
                receiver.decrypt(cbc, self.key, self.cert, runtime_id=RID)
            openssl.assert_not_called()

    def test_receiver_runtime_and_wrong_recipient(self):
        encrypted = self.export()
        with self.assertRaisesRegex(g.evidence.EvidenceError, "unexpected_runtime"):
            receiver.decrypt(encrypted, self.key, self.cert, runtime_id="g8-1234abcd")
        other = self.root / "other.pem"
        subprocess.run(["/usr/bin/openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(other)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=30)
        with self.assertRaisesRegex(g.evidence.EvidenceError, "crypto_failed"):
            receiver.decrypt(encrypted, other, self.cert, runtime_id=RID)

    def test_transport_cli_has_no_payload_output(self):
        path = self.root / "input.cms"
        path.write_bytes(self.export())
        self.assertEqual(transport.main([str(path)]), 0)
        path.write_bytes(b"DO_NOT_PRINT")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(transport.main([str(path)]), 1)
        self.assertEqual(stderr.getvalue(), "Hanjuku ciphertext validation rejected\n")


class EntryWorkflowTests(unittest.TestCase):
    def test_main_errors_never_emit_request_or_exception(self):
        script = """
import sys
sys.path.insert(0, sys.argv[1])
import hanjuku_evidence_gateway as g
def fail(*args):
    raise ValueError('DO_NOT_PRINT_REQUEST_OR_EVIDENCE')
g.export = fail
raise SystemExit(g.main(None, '/not-production'))
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(HERE)],
                                input=b"DO_NOT_PRINT_REQUEST", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"Hanjuku evidence export rejected\n")

    def test_main_success_emits_only_returned_ciphertext(self):
        script = """
import sys
sys.path.insert(0, sys.argv[1])
import hanjuku_evidence_gateway as g
g.export = lambda *args: b'synthetic-ciphertext'
raise SystemExit(g.main(None, '/not-production'))
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(HERE)],
                                input=b"{}", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"synthetic-ciphertext")
        self.assertEqual(result.stderr, b"")

    def test_legacy_dispatch_preserves_parser_argv_and_output(self):
        core, fixed = Mock(), Mock()
        argv = ["gateway_entry.py", "/config-for-test.json"]
        for command in ("diagnostics docich production " + "a" * 40, "exec docich preview " + "a" * 40,
                        "unknown", "hanjuku_evidence;evil", ""):
            with self.subTest(command=command), patch.dict(sys.modules, {"gateway": core, "hanjuku_evidence_gateway": fixed}), \
                    patch.object(sys, "argv", argv), patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}):
                core.reset_mock()
                self.assertEqual(entry.main(), 0)
                core.main.assert_called_once_with()
                fixed.main.assert_not_called()
                self.assertEqual(sys.argv, argv)

    def test_fixed_dispatch_does_not_fall_back_to_exec(self):
        core, fixed = Mock(), Mock()
        fixed.main.return_value = 1
        with patch.dict(sys.modules, {"gateway": core, "hanjuku_evidence_gateway": fixed}), \
                patch.object(sys, "argv", ["entry", "/etc/azumag-vm-ops.json"]), \
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "hanjuku_evidence docich preview invalid"}):
            self.assertEqual(entry.main(), 1)
            fixed.main.assert_called_once_with(core, "/etc/azumag-vm-ops.json")
            core.main.assert_not_called()

    def test_installer_uses_root_owned_isolated_entry(self):
        script = (HERE / "install_vm_gateway.sh").read_text()
        self.assertIn('/usr/bin/python3 -I /usr/local/libexec/azumag-vm-ops/gateway_entry.py', script)
        self.assertIn('gateway_entry.py hanjuku_evidence_gateway.py hanjuku_evidence.py', script)
        self.assertIn('install -o root -g root -m 0644 "$source_dir/$helper"', script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    def test_workflow_permissions_pins_and_ciphertext_only(self):
        path = HERE.parents[1] / ".github/workflows/hanjuku-evidence-export.yml"
        workflow = path.read_text()
        for required in ("workflow_dispatch:", "environment: vm-operations", "contents: read",
                         "github.actor_id == 9018513", "github.triggering_actor == 'azumag'",
                         "persist-credentials: false", "cancel-in-progress: false", "retention-days: 1",
                         "hanjuku_evidence docich production $SHA", "StrictHostKeyChecking=yes",
                         "validate_hanjuku_ciphertext.py", "if: ${{ always() }}"):
            self.assertIn(required, workflow)
        for forbidden in ("pull_request_target:", "schedule:", "issue_comment:", "contents: write",
                          "exec docich", "diagnostics docich", "cms -decrypt", "continue-on-error: true"):
            self.assertNotIn(forbidden, workflow)
        self.assertEqual(workflow.count("ls-remote --exit-code origin refs/heads/main"), 3)
        self.assertIn("path: ${{ runner.temp }}/hanjuku-export/evidence.cms", workflow)
        self.assertNotIn("inputs.", "\n".join(line for line in workflow.splitlines() if "run:" in line))
        for pin in re.findall(r"uses: [^@\n]+@([^\n]+)", workflow):
            self.assertRegex(pin, r"^[0-9a-f]{40}$")

    def test_workflow_shell_blocks_parse(self):
        workflow = (HERE.parents[1] / ".github/workflows/hanjuku-evidence-export.yml").read_text()
        blocks = re.findall(r"        run: \|\n((?:          [^\n]*\n|\n)+)", workflow)
        self.assertEqual(len(blocks), 3)
        for block in blocks:
            self.assertNotIn("${{ inputs.", block)
            subprocess.run(["bash", "-n"], input=textwrap.dedent(block), text=True, check=True)


if __name__ == "__main__":
    unittest.main()
