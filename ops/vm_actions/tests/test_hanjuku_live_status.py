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
                "corners": {
                    "corner_rotation": {
                        "status": "waiting",
                        "pending": False,
                        "queued_manual": True,
                        "manual_pending": True,
                        "manual_pending_corner": "hanjuku-hero",
                        "request_id": "DO-NOT-EMIT",
                        "reason": "DO-NOT-EMIT",
                    },
                    "game_switch": {
                        "phase": "ready",
                        "active_game": "sorengame",
                        "PRIVATE": "DO-NOT-EMIT",
                    },
                },
                "PRIVATE": "DO NOT EMIT",
            }
        }

    def chapter9_payload(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical["chapter"] = 9
        tactical["policy"]["active"] = "9-B2"
        tactical["policy"]["orders"] = {
            "9-B1": "pending",
            "9-B2": "launched_unconfirmed",
            "9-B3": "done",
            "9-B4": "unknown_state",
        }
        return payload

    def test_boss_steps_follow_the_chapter_the_run_is_on(self):
        """A chapter 9 game reported chapter 4's steps (production 2026-11-17)."""
        out = self.status.project(self.chapter9_payload())
        self.assertEqual(out["chapter"], 9)
        self.assertEqual(out["active"], "9-B2")
        self.assertEqual(set(out["orders"]), {"9-B1", "9-B2", "9-B3", "9-B4"})
        self.assertEqual(out["orders"]["9-B1"], "pending")
        self.assertEqual(out["orders"]["9-B4"], "unknown")
        self.assertEqual(self.status.boss_steps(9), ("9-B1", "9-B2", "9-B3", "9-B4"))
        # An unknown chapter keeps the legacy tuple so old records still render.
        self.assertEqual(self.status.boss_steps(None), self.status.STEPS)
        self.assertEqual(self.status.boss_steps(True), self.status.STEPS)

    def test_rendered_reply_names_the_current_chapter_steps(self):
        out = self.status.project(self.chapter9_payload())
        body = self.status.reply(out, {
            "RUN_SHA": "a" * 40, "RUN_ID": "1", "RUN_ATTEMPT": "1",
            "SOURCE_COMMENT_ID": "1",
        })["body"]
        self.assertIn("`9-B1=pending`", body)
        self.assertNotIn("`4-B1=", body)

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
        self.assertEqual(out["rotation"], {
            "status": "waiting",
            "pending": False,
            "queued_manual": True,
            "manual_pending": True,
            "manual_pending_hanjuku": True,
            "pending_hanjuku": False,
            "error_kind": "unknown",
            "active_hanjuku": False,
        })
        self.assertNotIn("PRIVATE", json.dumps(out))
        self.assertNotIn("DO-NOT-EMIT", json.dumps(out))

    def test_boss_cell_projection_is_bounded_and_rendered(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical["chapter"] = 9
        tactical["policy"]["boss_cell"] = {
            "chapter": 9, "cell": [874, 637], "readings": 2,
            "age_ticks": 12, "PRIVATE": "DO-NOT-EMIT",
        }
        out = self.status.project(payload)
        self.assertEqual(out["boss_cell"], {
            "chapter": 9, "cell": [874, 637], "readings": 2, "age_ticks": 12,
        })
        body = self.status.reply(out, {
            "RUN_SHA": "c" * 40, "RUN_ID": "1", "RUN_ATTEMPT": "1",
            "SOURCE_COMMENT_ID": "1",
        })["body"]
        self.assertIn("Moving boss cell: `measured=yes`", body)
        self.assertIn("`cell=874,637`", body)
        self.assertIn("`readings=2`", body)
        self.assertIn("`age_ticks=12`", body)
        self.assertNotIn("PRIVATE", json.dumps(out))
        self.assertNotIn("DO-NOT-EMIT", body)

    def test_boss_cell_from_world_region_and_invalid_values_fail_closed(self):
        payload = self.payload()
        policy = payload["diagnostics"]["hanjuku_tactical"]["policy"]
        policy["world"] = {"boss_cell": {"chapter": 9, "cell": [874, 637],
                                          "readings": 2, "age_ticks": 3}}
        self.assertEqual(self.status.project(payload)["boss_cell"]["cell"], [874, 637])
        policy["world"]["boss_cell"] = {
            "chapter": "PRIVATE", "cell": ["X", 637], "readings": -1,
            "age_ticks": "PRIVATE",
        }
        out = self.status.project(payload)
        self.assertEqual(out["boss_cell"], {
            "chapter": None, "cell": None, "readings": None, "age_ticks": None,
        })
        body = self.status.reply(out, {
            "RUN_SHA": "c" * 40, "RUN_ID": "1", "RUN_ATTEMPT": "1",
            "SOURCE_COMMENT_ID": "1",
        })["body"]
        self.assertIn("Moving boss cell: `measured=no`", body)
        self.assertNotIn("PRIVATE", json.dumps(out))
        self.assertNotIn("PRIVATE", body)

    def test_recent_decisions_include_boss_cell_and_pursuit_tokens(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical["chapter"] = 9
        tactical["policy"]["recent_decisions"] = [
            {"decision": "boss_cell_measured", "reason": "PRIVATE_REASON"},
            {"decision": "boss_pursuit_queued", "reason": "PRIVATE_REASON"},
            {"decision": "boss_pursuit_redirected", "reason": "PRIVATE_REASON"},
            {"decision": "boss_pursuit_unverified", "reason": "PRIVATE_REASON"},
            {"decision": "PRIVATE_DECISION", "reason": "PRIVATE_REASON"},
        ]
        out = self.status.project(payload)
        for name in ("boss_cell_measured", "boss_pursuit_queued",
                     "boss_pursuit_redirected", "boss_pursuit_unverified"):
            self.assertTrue(out["recent"][name], name)
        self.assertFalse(out["recent"]["boss_roster_reprobe"])
        body = self.status.reply(out, {
            "RUN_SHA": "c" * 40, "RUN_ID": "1", "RUN_ATTEMPT": "1",
            "SOURCE_COMMENT_ID": "1",
        })["body"]
        for name in ("boss_cell_measured", "boss_pursuit_queued",
                     "boss_pursuit_redirected", "boss_pursuit_unverified"):
            self.assertIn(f"`{name}=yes`", body)
        self.assertIn("`boss_roster_reprobe=no`", body)
        self.assertNotIn("PRIVATE", body)

    def test_compact_field_projects_boss_progress_without_names(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical["status"] = "output_omitted"
        tactical["chapter"] = 6
        tactical["policy"] = {
            "active": "I:abc123:1",
            "orders": {},
            "captured_count": 7,
            "castle_loss_count": 1,
            "month": "57-2",
            "phase": "field",
            "unknown_generals": 3,
            "garrison": {"A": 2, "B": 0, "C": None},
            "uncertain": True,
            "march": {"state": "overdue", "overdue": ["I:abc123:1"],
                      "exhausted": [], "oldest_age_ticks": 123},
            "boss_victories": {"count": 0, "sites": ["PRIVATE_SITE"]},
            "sorties": {
                "I:abc123:1": {"status": "overdue", "target": "ボス",
                               "age_ticks": 123, "relaunch": 2},
                "I:def456:2": {"status": "launched", "target": "PRIVATE_CASTLE",
                               "age_ticks": 1, "relaunch": 0},
            },
            "recent_decisions": [],
        }
        out = self.status.project(payload)
        self.assertIsNone(out["active"])
        self.assertEqual(out["field"]["captured"], 7)
        self.assertEqual(out["field"]["lost"], 1)
        self.assertEqual(out["field"]["month"], "57-2")
        self.assertEqual(out["field"]["phase"], "field")
        self.assertEqual(out["field"]["unknown_generals"], 3)
        self.assertEqual(out["field"]["generals_home"], 2)
        self.assertEqual(out["field"]["castles_unread"], 1)
        self.assertEqual(out["field"]["march"], "overdue")
        self.assertEqual(out["field"]["overdue"], 1)
        self.assertEqual(out["field"]["boss_wins"], 0)
        self.assertEqual(out["field"]["boss_sorties"], 1)
        self.assertEqual(out["field"]["boss_sorties_overdue"], 1)
        self.assertTrue(out["field"]["active_boss"])
        self.assertTrue(out["field"]["uncertain"])
        rendered = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn("I:abc123", rendered)
        self.assertNotIn("ボス", rendered)

    def test_active_hanjuku_is_derived_without_exposing_game_name(self):
        payload = self.payload()
        payload["diagnostics"]["corners"]["game_switch"]["active_game"] = "hanjuku-hero"
        out = self.status.project(payload)
        self.assertTrue(out["rotation"]["active_hanjuku"])
        self.assertNotIn("active_game", json.dumps(out))

    def test_unknown_values_fail_closed_to_fixed_tokens(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical["status"] = "PRIVATE_STATUS"
        tactical["policy"]["active"] = "PRIVATE_ACTIVE"
        tactical["policy"]["month"] = "PRIVATE_MONTH"
        tactical["policy"]["phase"] = "PRIVATE_PHASE"
        tactical["policy"]["unknown_generals"] = "PRIVATE_UNKNOWN"
        tactical["policy"]["orders"]["4-B1"] = "PRIVATE_STATE"
        rotation = payload["diagnostics"]["corners"]["corner_rotation"]
        rotation["status"] = "PRIVATE_ROTATION"
        rotation["pending"] = "yes"
        rotation["queued_manual"] = 1
        rotation["manual_pending"] = "yes"
        rotation["manual_pending_corner"] = {"PRIVATE": True}
        payload["diagnostics"]["corners"]["game_switch"]["active_game"] = None
        out = self.status.project(payload)
        self.assertEqual(out["status"], "unknown")
        self.assertIsNone(out["active"])
        self.assertEqual(out["orders"]["4-B1"], "unknown")
        self.assertEqual(out["rotation"], {
            "status": "unknown",
            "pending": None,
            "queued_manual": None,
            "manual_pending": None,
            "manual_pending_hanjuku": None,
            "pending_hanjuku": None,
            "error_kind": "unknown",
            "active_hanjuku": None,
        })
        self.assertIsNone(out["field"]["month"])
        self.assertIsNone(out["field"]["phase"])
        self.assertIsNone(out["field"]["unknown_generals"])
        self.assertIsNone(out["field"]["generals_home"])
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_missing_rotation_projection_stays_unknown_without_failing_status(self):
        payload = self.payload()
        payload["diagnostics"].pop("corners")
        out = self.status.project(payload)
        self.assertEqual(out["rotation"], {
            "status": "unknown",
            "pending": None,
            "queued_manual": None,
            "manual_pending": None,
            "manual_pending_hanjuku": None,
            "pending_hanjuku": None,
            "error_kind": "unknown",
            "active_hanjuku": None,
        })

    def test_reduced_diagnostics_keep_the_fixed_cause_tokens(self):
        payload = self.payload()
        tactical = payload["diagnostics"]["hanjuku_tactical"]
        tactical.clear()
        tactical.update({"status": "output_omitted", "basis": "bot_record",
                         "detail_status": "stale", "detail_reason": "record_stale",
                         "chapter": 9, "age_sec": 100,
                         "policy": {"chapter": 9, "month": "47-9",
                                    "phase": "month_menu"}})
        out = self.status.project(payload)
        self.assertEqual(out["status"], "output_omitted")
        self.assertEqual(out["detail_status"], "stale")
        self.assertEqual(out["detail_reason"], "record_stale")
        self.assertEqual(out["age_sec"], 100)
        body = self.status.reply(out, {
            "RUN_SHA": "b" * 40, "RUN_ID": "123",
            "RUN_ATTEMPT": "1", "SOURCE_COMMENT_ID": "456"})["body"]
        self.assertIn("`detail=stale`", body)
        self.assertIn("`reason=record_stale`", body)
        self.assertIn("age `100s`", body)
        # The last known position is shown, but never as the current one.
        self.assertIn("Chapter: `9` (last record; stale)", body)
        self.assertIn("`month=47-9`", body)

    def test_an_unknown_detail_token_is_not_projected(self):
        payload = self.payload()
        payload["diagnostics"]["hanjuku_tactical"].update({
            "detail_status": "PRIVATE", "detail_reason": "DO-NOT-EMIT"})
        out = self.status.project(payload)
        self.assertIsNone(out["detail_status"])
        self.assertIsNone(out["detail_reason"])

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
        self.assertIn("`status=waiting`", text)
        self.assertIn("`pending=no`", text)
        self.assertIn("`queued_manual=yes`", text)
        self.assertIn("`manual_pending=yes`", text)
        self.assertIn("`manual_pending_hanjuku=yes`", text)
        self.assertIn("`active_hanjuku=no`", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("DO-NOT-EMIT", text)
        self.assertNotIn("active_game", text)


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


def test_region_fields_are_read_from_the_world_region_too():
    """The declared keys moved into ``world`` (docich#3193).

    g1163 2026-11-09 15:50: every sortie row vanished from the live status
    because only the flat key was read.
    """
    module = load(STATUS_PATH, "hanjuku_live_status_region")
    _compact_field = module._compact_field
    flat = {'sorties': {'A:x:M1': {'target': 'ボス', 'status': 'en_route'}},
            'boss_victories': {'count': 2},
            'garrison': {'アルマムーン': 3, 'セレーネ': 2}}
    moved = {'world': flat}
    assert _compact_field(flat, 6, 'A:x:M1') == _compact_field(moved, 6, 'A:x:M1')
    field = _compact_field(moved, 6, 'A:x:M1')
    assert field['boss_sorties'] == 1
    assert field['generals_home'] == 5
    assert field['boss_wins'] == 2
