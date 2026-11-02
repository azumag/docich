"""Contracts for the owner-only live Hanjuku boss status query."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "ops" / "vm_actions"
AUTH_PATH = OPS / "authorize_hanjuku_live_status.py"
STATUS_PATH = OPS / "hanjuku_live_status.py"
WORKFLOW = ROOT / ".github" / "workflows" / "hanjuku-live-status.yml"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = load(AUTH_PATH, "hanjuku_live_status_auth_test")

    def env(self, body="/hanjuku-status production"):
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

    def test_only_exact_owner_command_is_authorized(self):
        self.assertEqual(self.auth.authorize(self.env()), "a" * 40)
        for body in (
            "/hanjuku-status",
            "/hanjuku-status preview",
            "/hanjuku-status production ",
            "/hanjuku-status production\n",
            "/hanjuku-status production; id",
            " /hanjuku-status production",
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.auth.authorize(self.env(body))

    def test_identity_issue_event_and_ref_are_pinned(self):
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


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.status = load(STATUS_PATH, "hanjuku_live_status_projection_test")

    def payload(self):
        return {
            "diagnostics": {
                "hanjuku_tactical": {
                    "status": "unsupported_chapter",
                    "chapter": 4,
                    "age_sec": 2,
                    "policy": {
                        "active": "4-B2",
                        "orders": {
                            "4-B1": "pending",
                            "4-B2": "launched_unconfirmed",
                            "4-B3": "launched",
                            "4-B4": "failed",
                            "PRIVATE_STEP": "PRIVATE_STATE",
                        },
                        "recent_decisions": [
                            {"decision": "boss_roster_reprobe", "reason": "PRIVATE_REASON"},
                            {"decision": "order_start", "reason": "PRIVATE_REASON"},
                            {"decision": "PRIVATE_DECISION", "reason": "PRIVATE_REASON"},
                        ],
                        "PRIVATE": "DO NOT EMIT",
                    },
                },
                "PRIVATE": "DO NOT EMIT",
            }
        }

    def test_projection_exposes_only_fixed_boss_tokens(self):
        out = self.status.project(self.payload())
        self.assertEqual(out["chapter"], 4)
        self.assertEqual(out["age_sec"], 2)
        self.assertEqual(out["active"], "4-B2")
        self.assertEqual(out["orders"], {
            "4-B1": "pending",
            "4-B2": "launched_unconfirmed",
            "4-B3": "launched",
            "4-B4": "failed",
        })
        self.assertTrue(out["recent"]["boss_roster_reprobe"])
        self.assertTrue(out["recent"]["order_start"])
        self.assertFalse(out["recent"]["order_launched"])
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_unknown_values_fail_closed_to_fixed_tokens(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical["status"] = "PRIVATE_STATUS"
        tactical["policy"]["active"] = "PRIVATE_ACTIVE"
        tactical["policy"]["orders"]["4-B1"] = "PRIVATE_STATE"
        out = self.status.project(payload)
        self.assertEqual(out["status"], "unknown")
        self.assertIsNone(out["active"])
        self.assertEqual(out["orders"]["4-B1"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_reply_contains_only_projection_and_fixed_metadata(self):
        out = self.status.project(self.payload())
        env = {
            "RUN_SHA": "b" * 40,
            "RUN_ID": "123",
            "RUN_ATTEMPT": "1",
            "SOURCE_COMMENT_ID": "456",
        }
        reply = self.status.reply(out, env)
        text = reply["body"]
        self.assertIn("`4-B2=launched_unconfirmed`", text)
        self.assertIn("`boss_roster_reprobe=yes`", text)
        self.assertNotIn("PRIVATE", text)


class WorkflowTests(unittest.TestCase):
    def test_workflow_is_read_only_fixed_diagnostics_query(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "issue_comment:",
            "github.event.issue.number == 1339",
            "github.actor_id == 9018513",
            "github.event.comment.user.id == 9018513",
            "github.event.comment.body == '/hanjuku-status production'",
            "environment: vm-operations",
            "persist-credentials: false",
            '"status docich production $SHA"',
            '"diagnostics docich production $SHA"',
            "StrictHostKeyChecking=yes",
            "authorize_hanjuku_live_status.py",
            "hanjuku_live_status.py",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "pull_request_target:",
            "workflow_dispatch:",
            "schedule:",
            "contents: write",
            "exec docich",
            "hanjuku_evidence_query",
            "actions/upload-artifact",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
