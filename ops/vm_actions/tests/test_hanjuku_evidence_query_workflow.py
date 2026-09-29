"""Hanjuku Actions evidence query contracts; synthetic, no production access."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sys
import textwrap
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import hanjuku_evidence_gateway as gateway
import gateway_entry as entry
from test_hanjuku_evidence import EvidenceFixture, RID, canonical, run_state

AUTH_PATH = HERE / "authorize_hanjuku_evidence_query.py"
WORKFLOW = ROOT / ".github" / "workflows" / "hanjuku-evidence-query.yml"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = load(AUTH_PATH, "hanjuku_evidence_query_auth_test")

    def env(self, body="/hanjuku-evidence list"):
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
            "GITHUB_WORKFLOW_REF": self.auth.WORKFLOW,
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "1339",
            "COMMENT_BODY": body,
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
        }

    def test_exact_list_and_export_commands(self):
        self.assertEqual(self.auth.authorize(self.env()), ("list", ""))
        self.assertEqual(
            self.auth.authorize(self.env("/hanjuku-evidence export g12-12345678")),
            ("export", "g12-12345678"),
        )

    def test_wrong_owner_issue_event_or_ref_is_rejected(self):
        cases = {
            "GITHUB_ACTOR_ID": "1",
            "GITHUB_TRIGGERING_ACTOR": "someone",
            "GITHUB_REF": "refs/heads/other",
            "GITHUB_REF_PROTECTED": "false",
            "GITHUB_WORKFLOW_REF": "other",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_EVENT_ACTION": "edited",
            "ISSUE_NUMBER": "1338",
            "COMMENT_AUTHOR": "someone",
            "COMMENT_AUTHOR_ID": "1",
        }
        for key, value in cases.items():
            env = self.env()
            env[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.auth.authorize(env)

    def test_selector_is_explicit_and_has_no_shell_surface(self):
        for body in (
            "/hanjuku-evidence export latest",
            "/hanjuku-evidence export active",
            "/hanjuku-evidence export g01-12345678",
            "/hanjuku-evidence export ../g12-12345678",
            "/hanjuku-evidence export g12-12345678 extra",
            "/hanjuku-evidence export g12-12345678;id",
            "/hanjuku-evidence",
            " /hanjuku-evidence list",
            "/hanjuku-evidence list\n",
            "/hanjuku-evidence export g12-12345678\n",
            "",
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.auth.authorize(self.env(body))


class QueryGatewayTests(EvidenceFixture, unittest.TestCase):
    def test_query_parser_accepts_only_list_or_explicit_runtime(self):
        sha = "a" * 40
        self.assertEqual(
            gateway.parse_query_command(
                f"hanjuku_evidence_query docich production {sha} list"
            ),
            (sha, "list", None),
        )
        self.assertEqual(
            gateway.parse_query_command(
                f"hanjuku_evidence_query docich production {sha} export {RID}"
            ),
            (sha, "export", RID),
        )
        for command in (
            f"hanjuku_evidence_query docich production {sha} export latest",
            f"hanjuku_evidence_query docich production {sha} export {RID};id",
            f"hanjuku_evidence_query docich preview {sha} list",
            f"hanjuku_evidence_query docich production main list",
            f"hanjuku_evidence_query docich production {sha} list extra",
            f" hanjuku_evidence_query docich production {sha} list",
        ):
            with self.subTest(command=command), self.assertRaises(Exception):
                gateway.parse_query_command(command)

    def test_completed_candidate_list_is_ids_and_fixed_counters_only(self):
        payload = json.loads(gateway._completed_candidates(self.state))
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["truncated"])
        self.assertEqual(len(payload["runtimes"]), 1)
        item = payload["runtimes"][0]
        self.assertEqual(item["runtime_id"], RID)
        self.assertEqual(
            set(item),
            {"runtime_id", "generation", "terminal_reason", "observations", "actions_sent"},
        )

    def test_active_runtime_is_not_offered(self):
        (self.state / "game_switch.json").write_text(json.dumps(
            canonical(phase="ready", active={"runtime_id": RID, "generation": 7})
        ))
        payload = json.loads(gateway._completed_candidates(self.state))
        self.assertEqual(payload["runtimes"], [])

    def test_retiring_runtime_is_not_offered(self):
        (self.state / "game_switch.json").write_text(json.dumps(
            canonical(retiring=[{"runtime_id": RID, "generation": 7}])
        ))
        self.assertEqual(json.loads(gateway._completed_candidates(self.state))["runtimes"], [])

    def test_candidate_mutation_is_not_offered(self):
        original = gateway.evidence._read
        reads = 0

        def changing(parent, name, limit, **kw):
            nonlocal reads
            value = original(parent, name, limit, **kw)
            if name == "hanjuku_run.json":
                reads += 1
                if reads == 2:
                    return value + b" "
            return value

        with patch.object(gateway.evidence, "_read", side_effect=changing):
            self.assertEqual(json.loads(gateway._completed_candidates(self.state))["runtimes"], [])

    def test_candidate_query_time_budget(self):
        with patch.object(gateway.time, "monotonic", side_effect=[0, 4]):
            with self.assertRaisesRegex(gateway.evidence.EvidenceError, "query_budget_exceeded"):
                gateway._completed_candidates(self.state)

    def test_nonterminal_runtime_is_not_offered(self):
        (self.run / "hanjuku_run.json").write_text(
            json.dumps(run_state(terminal_reason=None))
        )
        payload = json.loads(gateway._completed_candidates(self.state))
        self.assertEqual(payload["runtimes"], [])

    def test_runtime_scan_has_independent_bound(self):
        with patch.object(gateway, "MAX_RUNTIME_ENTRIES", 0), self.assertRaisesRegex(
            Exception, "runtime_scan_limit"
        ):
            gateway._completed_candidates(self.state)

    def test_query_export_returns_verified_plain_zip_without_encryption(self):
        sha = "a" * 40
        command = f"hanjuku_evidence_query docich production {sha} export {RID}"
        archive = b"PK\x03\x04synthetic-private-zip"
        with patch.object(gateway, "load_config", return_value={}), \
                patch.object(gateway, "operations_lock") as lock, \
                patch.object(gateway, "_ready"), \
                patch.object(gateway, "verify_installed_source"), \
                patch.object(gateway.evidence, "snapshot", return_value=({"runtime_id": RID}, {}, [])), \
                patch.object(gateway.evidence, "build_archive", return_value=archive):
            lock.return_value.__enter__ = Mock(return_value=None)
            lock.return_value.__exit__ = Mock(return_value=False)
            self.assertEqual(gateway.query(None, "/ignored", command), archive)

    def test_dispatcher_routes_query_to_fixed_gateway(self):
        core, fixed = Mock(), Mock()
        fixed.main.return_value = 0
        command = "hanjuku_evidence_query docich production " + "a" * 40 + " list"
        with patch.dict(sys.modules, {"gateway": core, "hanjuku_evidence_gateway": fixed}), \
                patch.object(sys, "argv", ["entry", "/etc/azumag-vm-ops.json"]), \
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}):
            self.assertEqual(entry.main(), 0)
            fixed.main.assert_called_once_with(core, "/etc/azumag-vm-ops.json")
            core.main.assert_not_called()


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text()

    def test_fixed_owner_issue_and_plaintext_artifact(self):
        for required in (
            "issue_comment:",
            "github.event.issue.number == 1339",
            "github.actor_id == 9018513",
            "github.event.comment.user.id == 9018513",
            "startsWith(github.event.comment.body, '/hanjuku-evidence ')",
            "environment: vm-operations",
            "contents: read",
            "persist-credentials: false",
            "hanjuku_evidence_query docich production $SHA list",
            "hanjuku_evidence_query docich production $SHA export $RUNTIME_ID",
            "output/candidates.json",
            "output/evidence.zip",
            "retention-days: 1",
            "StrictHostKeyChecking=yes",
        ):
            self.assertIn(required, self.workflow)
        for forbidden in (
            "pull_request_target:",
            "schedule:",
            "contents: write",
            "exec docich",
            "diagnostics docich",
            "evidence.cms",
            "openssl req",
            "private.pem",
        ):
            self.assertNotIn(forbidden, self.workflow)

    def test_artifact_upload_is_only_the_query_output_directory(self):
        upload = self.workflow.split("- name: Upload short-lived plaintext query result", 1)[1]
        self.assertIn("path: $" + "{{ runner.temp }}/hanjuku-query/output", upload)
        self.assertNotIn("/ssh", upload)

    def test_shell_blocks_parse(self):
        blocks = re.findall(r"        run: \|\n((?:          [^\n]*\n|\n)+)", self.workflow)
        self.assertGreaterEqual(len(blocks), 5)
        for block in blocks:
            subprocess.run(["bash", "-n"], input=textwrap.dedent(block), text=True, check=True)

    def test_current_main_checks_fail_closed_before_transport_and_upload(self):
        # Execute the actual workflow check with a fake git remote; a queued
        # comment must not publish an old deployed SHA after main advances.
        check = 'current="$(git -C control ls-remote --exit-code origin refs/heads/main | cut -f1)"'
        self.assertEqual(self.workflow.count(check), 3)
        auth = self.workflow.index(check)
        transport = self.workflow.index(check, auth + 1)
        publish = self.workflow.index(check, transport + 1)
        self.assertLess(auth, self.workflow.index('- name: Configure pinned SSH transport'))
        self.assertLess(transport, self.workflow.index('status_json='))
        self.assertGreater(publish, self.workflow.index('from receive_hanjuku_evidence import verify_archive'))
        self.assertLess(publish, self.workflow.index('uses: actions/upload-artifact@'))
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'git'
            fake.write_text('#!/bin/bash\nprintf "%s\\trefs/heads/main\\n" "$REMOTE_SHA"\nexit "${REMOTE_RC:-0}"\n')
            fake.chmod(0o700)
            for remote, rc, expected in [('a' * 40, '0', 0), ('b' * 40, '0', 1), ('a' * 40, '1', 1)]:
                env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                           SHA='a' * 40, REMOTE_SHA=remote, REMOTE_RC=rc)
                result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + check + '\n[[ "$current" == "$SHA" ]]'], env=env)
                self.assertEqual(result.returncode == 0, expected == 0)

    def test_actions_are_commit_pinned(self):
        pins = re.findall(r"uses: [^@\n]+@([^\n]+)", self.workflow)
        self.assertTrue(pins)
        for pin in pins:
            self.assertRegex(pin, r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
