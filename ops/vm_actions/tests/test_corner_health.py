"""Contracts for the corner health Issue monitor."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from ops.vm_actions import corner_health as health


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-corner-monitor.yml"
NOW = 2_000_000.0


def base_document(*, rotation=None, timer=None, states=None):
    rotation_state = {
        "present": True,
        "readable": True,
        "status": "waiting",
        "waiting_overdue": False,
        "overdue_sec": None,
        "reason_age_sec": None,
        "last_seen_at": NOW - 60,
        "next_due_at": NOW + 3600,
        "pending": False,
        "manual_pending": False,
        "pending_corner": None,
        "pending_phase": None,
        "pending_age_sec": -1,
        "pending_owner": "absent",
        "manual_pending_corner": None,
        "manual_pending_age_sec": -1,
        "manual_pending_owner": "absent",
        "excluded": {},
        "failed_corners": [],
    }
    rotation_state.update(rotation or {})
    timer_state = {"active": True, "enabled": True}
    timer_state.update(timer or {})
    corners = {
        "state_dir_found": True,
        "corner_rotation": rotation_state,
        "corner_rotation_timer": timer_state,
    }
    corners.update(states or {})
    return {"diagnostics": {"meta": {"generated_at": NOW}, "corners": corners}}


class FakeGh:
    """Bounded ``gh`` stand-in that records every call."""

    def __init__(self, issues=None, fail_query=False, fail_publish=False):
        self.issues = [dict(item) for item in (issues or [])]
        self.fail_query = fail_query
        self.fail_publish = fail_publish
        self.calls = []
        self._next = 100

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            if self.fail_query:
                raise health.GhError("query failed")
            return json.dumps(self.issues)
        if args[:2] in (["issue", "create"], ["issue", "edit"], ["issue", "close"]):
            if self.fail_publish:
                raise health.GhError("publish failed")
            return self._mutate(args)
        raise AssertionError(f"unexpected gh call: {args}")

    def _mutate(self, args):
        if args[:2] == ["issue", "create"]:
            title = args[args.index("--title") + 1]
            self.issues.append({"number": self._next, "title": title})
            self._next += 1
        elif args[:2] == ["issue", "edit"]:
            number = int(args[2])
            body = args[args.index("--body") + 1]
            for item in self.issues:
                if item["number"] == number:
                    item["body"] = body
        elif args[:2] == ["issue", "close"]:
            number = int(args[2])
            self.issues = [item for item in self.issues if item["number"] != number]
        return ""

    def mutations(self):
        return [
            call for call in self.calls
            if call[:2] in (["issue", "create"], ["issue", "edit"], ["issue", "close"])
        ]


class AssessTests(unittest.TestCase):
    def test_fixed_enums_stay_in_sync_with_the_rotation_ledger(self):
        sys.path.insert(0, str(ROOT / "src"))
        from docich.corner_rotation import ERROR_KINDS, FAILED_CORNER_REASONS

        self.assertEqual(health.ERROR_KINDS, ERROR_KINDS)
        self.assertEqual(health.FAILED_CORNER_REASONS, FAILED_CORNER_REASONS)

    def test_healthy_snapshot_has_no_alerts(self):
        assessment = health.assess(base_document())
        self.assertTrue(assessment["available"])
        self.assertEqual(assessment["abnormal"], [])
        self.assertEqual(assessment["rotation"], [])

    def test_missing_or_unreadable_rotation_is_unavailable(self):
        for document in (
            {},
            {"diagnostics": {"corners": {}}},
            {"diagnostics": {"corners": {"corner_rotation": {
                "present": False, "readable": False}}}},
        ):
            assessment = health.assess(document)
            self.assertFalse(assessment["available"])
            self.assertEqual(assessment["abnormal"], [])
            self.assertEqual(assessment["rotation"], [])

    def test_failed_corner_state_is_reported_with_fixed_fields(self):
        assessment = health.assess(base_document(states={
            "retro_corner": {
                "readable": True,
                "status": "failed",
                "game": "nsnake",
                "started_at": NOW - 3600,
                "completed_at": NOW - 900,
                "recovery_required": True,
                "last_error": "DO-NOT-PUBLISH",
                "last_error_code": "recovery_required",
            },
        }))
        self.assertEqual(len(assessment["abnormal"]), 1)
        row = assessment["abnormal"][0]
        self.assertEqual(row["corner"], "retro_corner")
        self.assertEqual(row["game"], "nsnake")
        self.assertEqual(row["source"], "state")
        self.assertEqual(row["age_sec"], 900)
        self.assertIs(row["recovery_required"], True)
        body = health.render_abnormal_body(assessment, repo="azumag/docich")
        self.assertIn("retro_corner", body)
        self.assertIn("nsnake", body)
        self.assertIn("状態ファイルが failed", body)
        self.assertIn("要復旧: yes", body)
        self.assertNotIn("DO-NOT-PUBLISH", body)

    def test_transient_failed_state_waits_for_the_grace_window(self):
        assessment = health.assess(base_document(states={
            "weather_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 60,
                "recovery_required": False,
            },
        }))
        self.assertEqual(assessment["abnormal"], [])

    def test_old_failed_state_is_ignored_unless_it_needs_recovery(self):
        old = {
            "readable": True,
            "status": "failed",
            "completed_at": NOW - health.FAILED_STATE_MAX_AGE_SECONDS - 60,
            "recovery_required": False,
        }
        self.assertEqual(
            health.assess(base_document(states={"weather_corner": old}))["abnormal"], []
        )
        old["recovery_required"] = True
        self.assertEqual(
            len(health.assess(base_document(states={"weather_corner": old}))["abnormal"]), 1
        )

    def test_recent_released_failure_is_reported_then_ages_out(self):
        recent = health.assess(base_document(rotation={"failed_corners": [{
            "corner": "mystery",
            "at": NOW - 600,
            "resolution": "released-unverified",
            "reason_code": "failed-corner-slot-released",
            "error_kind": "execution-error",
        }]}))
        self.assertEqual(len(recent["abnormal"]), 1)
        row = recent["abnormal"][0]
        self.assertEqual(row["corner"], "mystery")
        self.assertEqual(row["source"], "rotation-audit")
        self.assertEqual(row["resolution"], "released-unverified")
        self.assertEqual(row["reason_code"], "failed-corner-slot-released")
        self.assertEqual(row["error_kind"], "execution-error")

        old = health.assess(base_document(rotation={"failed_corners": [{
            "corner": "mystery",
            "at": NOW - health.RECENT_RELEASED_FAILURE_SECONDS - 600,
            "resolution": "released-unverified",
        }]}))
        self.assertEqual(old["abnormal"], [])

    def test_recovery_required_audit_row_keeps_a_longer_window(self):
        row = {
            "corner": "weather",
            "at": NOW - 3 * 86400,
            "resolution": "recovery-required",
            "reason_code": "terminal-owner-absent",
            "error_kind": "execution-unverified",
        }
        assessment = health.assess(base_document(rotation={"failed_corners": [row]}))
        self.assertEqual(len(assessment["abnormal"]), 1)
        self.assertIs(assessment["abnormal"][0]["recovery_required"], True)

    def test_latched_rotation_reports_pending_identity(self):
        assessment = health.assess(base_document(rotation={
            "status": "recovery_required",
            "error_kind": "execution-unverified",
            "pending": True,
            "pending_corner": "hanjuku-hero",
            "pending_phase": "dispatched",
            "pending_age_sec": 1200,
            "pending_owner": "none",
        }))
        rows = {row["code"]: row for row in assessment["rotation"]}
        self.assertEqual(rows["latched"]["pending_corner"], "hanjuku-hero")
        self.assertEqual(rows["latched"]["pending_phase"], "dispatched")
        self.assertEqual(rows["latched"]["pending_age_sec"], 1200)
        self.assertEqual(rows["latched"]["pending_owner"], "none")
        body = health.render_rotation_body(assessment, repo="azumag/docich")
        self.assertIn("recovery_required", body)
        self.assertIn("hanjuku-hero", body)

    def test_invalid_rotation_state_is_reported(self):
        running_without_owner = health.assess(base_document(rotation={
            "status": "running",
            "pending": False,
            "manual_pending": False,
        }))
        self.assertEqual(
            [row["code"] for row in running_without_owner["rotation"]], ["invalid_state"]
        )

        malformed_status = health.assess(base_document(rotation={
            "status": ["not-a-string"],
        }))
        self.assertIn(
            "invalid_state", [row["code"] for row in malformed_status["rotation"]]
        )

        both_reservations = health.assess(base_document(rotation={
            "status": "waiting",
            "pending": True,
            "manual_pending": True,
            "pending_corner": "mystery",
        }))
        self.assertIn(
            "invalid_state", [row["code"] for row in both_reservations["rotation"]]
        )

    def test_waiting_overdue_is_reported_only_without_a_reservation(self):
        assessment = health.assess(base_document(rotation={
            "status": "waiting",
            "waiting_overdue": True,
            "overdue_sec": 3600,
            "reason_age_sec": 4000,
        }))
        codes = [row["code"] for row in assessment["rotation"]]
        self.assertIn("waiting_overdue", codes)

        assessment = health.assess(base_document(rotation={
            "status": "waiting",
            "waiting_overdue": True,
            "overdue_sec": 3600,
            "reason_age_sec": 4000,
            "pending": True,
            "pending_owner": "retro_corner",
            "pending_age_sec": 3600,
        }))
        codes = [row["code"] for row in assessment["rotation"]]
        self.assertNotIn("waiting_overdue", codes)

    def test_released_owner_unretired_reports_safe_corner_ids_only(self):
        assessment = health.assess(base_document(rotation={
            "excluded": {
                "mystery": "released-owner-unretired",
                "weather": "disabled-or-paused",
                "DO NOT PUBLISH": "released-owner-unretired",
            },
        }))
        rows = {row["code"]: row for row in assessment["rotation"]}
        self.assertEqual(rows["released_owner_unretired"]["corners"], ["mystery"])
        self.assertNotIn("DO NOT PUBLISH", json.dumps(assessment))

    def test_timer_inactive_needs_enabled_and_stale(self):
        assessment = health.assess(base_document(
            timer={"active": False, "enabled": True},
            rotation={"last_seen_at": NOW - health.TIMER_INACTIVE_GRACE_SECONDS - 60},
        ))
        self.assertIn("timer_inactive", [row["code"] for row in assessment["rotation"]])

        fresh = health.assess(base_document(
            timer={"active": False, "enabled": True},
            rotation={"last_seen_at": NOW - 60},
        ))
        self.assertNotIn("timer_inactive", [row["code"] for row in fresh["rotation"]])

        disabled = health.assess(base_document(
            timer={"active": False, "enabled": False},
            rotation={"last_seen_at": NOW - 86400},
        ))
        self.assertNotIn("timer_inactive", [row["code"] for row in disabled["rotation"]])

    def test_stalled_tick_is_reported_for_a_free_slot(self):
        assessment = health.assess(base_document(rotation={
            "status": "ready",
            "last_seen_at": NOW - health.STALE_TICK_SECONDS - 60,
        }))
        self.assertIn("stalled", [row["code"] for row in assessment["rotation"]])

    def test_pending_without_an_owner_is_reported_after_the_threshold(self):
        assessment = health.assess(base_document(rotation={
            "pending": True,
            "pending_corner": "mystery",
            "pending_phase": "selected",
            "pending_age_sec": health.PENDING_UNOWNED_SECONDS,
            "pending_owner": "absent",
        }))
        rows = {row["code"]: row for row in assessment["rotation"]}
        self.assertEqual(rows["pending_unowned"]["pending_corner"], "mystery")
        self.assertEqual(rows["pending_unowned"]["pending_owner"], "absent")

        owned = health.assess(base_document(rotation={
            "pending": True,
            "pending_corner": "mystery",
            "pending_phase": "dispatched",
            "pending_age_sec": 86400,
            "pending_owner": "mystery_corner",
        }))
        self.assertNotIn("pending_unowned", [row["code"] for row in owned["rotation"]])

    def test_unknown_pending_age_minus_one_never_looks_fresh_or_old(self):
        assessment = health.assess(base_document(rotation={
            "pending": True,
            "pending_corner": "mystery",
            "pending_phase": "selected",
            "pending_age_sec": -1,
            "pending_owner": "absent",
        }))
        self.assertNotIn("pending_unowned", [row["code"] for row in assessment["rotation"]])

    def test_iso_timestamp_is_accepted_for_corner_state_age(self):
        now = 1_767_227_400.0  # 2026-01-01T00:30:00Z
        assessment = health.assess(
            base_document(states={
                "weather_corner": {
                    "readable": True,
                    "status": "failed",
                    "completed_at": "2026-01-01T00:00:00Z",
                },
            }),
            now=now,
        )
        row = assessment["abnormal"][0]
        self.assertEqual(row["corner"], "weather_corner")
        self.assertIsNotNone(row["age_sec"])

    def test_manual_pending_without_an_owner_is_reported_after_the_threshold(self):
        assessment = health.assess(base_document(rotation={
            "manual_pending": True,
            "manual_pending_corner": "hanjuku-hero",
            "manual_pending_age_sec": health.MANUAL_PENDING_UNOWNED_SECONDS,
            "manual_pending_owner": "none",
        }))
        rows = {row["code"]: row for row in assessment["rotation"]}
        self.assertEqual(
            rows["manual_pending_unowned"]["manual_pending_corner"], "hanjuku-hero"
        )

    def test_body_contains_no_raw_state_text(self):
        document = base_document(
            rotation={
                "status": "recovery_required",
                "reason": "DO-NOT-PUBLISH-REASON",
                "seed": "DO-NOT-PUBLISH-SEED",
                "error_kind": "DO-NOT-PUBLISH-KIND",
                "pending": True,
                "pending_corner": "mystery",
                "pending_phase": "dispatched",
                "pending_age_sec": 1200,
                "pending_owner": "none",
                "failed_corners": [{
                    "corner": "mystery",
                    "at": NOW - 600,
                    "resolution": "released-unverified",
                    "reason_code": "DO-NOT-PUBLISH-CODE",
                    "error_kind": "DO-NOT-PUBLISH-KIND",
                }],
            },
            states={"mystery_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 900,
                "last_error": "DO-NOT-PUBLISH-ERROR",
                "prompt": "DO-NOT-PUBLISH-PROMPT",
            }},
        )
        assessment = health.assess(document)
        body = "\n".join((
            health.render_abnormal_body(assessment, repo="azumag/docich"),
            health.render_rotation_body(assessment, repo="azumag/docich"),
        ))
        self.assertNotIn("DO-NOT-PUBLISH", body)
        self.assertIn("unknown", body)


class SyncTests(unittest.TestCase):
    def test_creates_one_fixed_title_without_duplicating(self):
        assessment = health.assess(base_document(states={
            "weather_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 900,
            },
        }))
        gh = FakeGh()
        result = health.sync_issues(gh, repo="azumag/docich", assessment=assessment)
        self.assertEqual(result["actions"], [
            {"key": "abnormal", "action": "create", "title": health.ABNORMAL_TITLE},
        ])

        gh = FakeGh(issues=[{"number": 12, "title": health.ABNORMAL_TITLE}])
        health.sync_issues(gh, repo="azumag/docich", assessment=assessment)
        self.assertEqual(
            [call[:2] for call in gh.mutations()], [["issue", "edit"]]
        )
        self.assertIn("--body",
                      gh.mutations()[0])
        self.assertFalse(any(call[:2] == ["issue", "create"] for call in gh.calls))

    def test_up_to_date_issue_is_not_edited(self):
        assessment = health.assess(base_document(states={
            "weather_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 900,
            },
        }))
        body = health.render_abnormal_body(assessment, repo="azumag/docich")
        gh = FakeGh(issues=[{
            "number": 12,
            "title": health.ABNORMAL_TITLE,
            "body": body,
        }])
        result = health.sync_issues(gh, repo="azumag/docich", assessment=assessment)
        self.assertEqual(result["actions"], [])
        self.assertEqual(gh.mutations(), [])

    def test_recovery_closes_all_matching_open_issues(self):
        gh = FakeGh(issues=[
            {"number": 12, "title": health.ABNORMAL_TITLE},
            {"number": 13, "title": health.ABNORMAL_TITLE},
            {"number": 14, "title": health.ROTATION_TITLE},
        ])
        result = health.sync_issues(
            gh, repo="azumag/docich", assessment=health.assess(base_document())
        )
        closed = sorted(
            action["number"] for action in result["actions"] if action["action"] == "close"
        )
        self.assertEqual(closed, [12, 13, 14])
        self.assertFalse(any(call[:2] == ["issue", "create"] for call in gh.calls))

    def test_rotation_issue_uses_its_own_title(self):
        assessment = health.assess(base_document(rotation={
            "status": "recovery_required",
            "error_kind": "execution-unverified",
        }))
        gh = FakeGh()
        result = health.sync_issues(gh, repo="azumag/docich", assessment=assessment)
        self.assertEqual(result["actions"], [
            {"key": "rotation", "action": "create", "title": health.ROTATION_TITLE},
        ])

    def test_dry_run_does_not_mutate(self):
        assessment = health.assess(base_document(states={
            "weather_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 900,
            },
        }))
        gh = FakeGh(issues=[{"number": 12, "title": health.ABNORMAL_TITLE}])
        health.sync_issues(
            gh, repo="azumag/docich", assessment=assessment, dry_run=True
        )
        self.assertEqual(gh.mutations(), [])

    def test_unavailable_assessment_never_mutates(self):
        gh = FakeGh(issues=[{"number": 12, "title": health.ABNORMAL_TITLE}])
        with self.assertRaises(health.QueryError):
            health.sync_issues(
                gh, repo="azumag/docich", assessment=health.assess({})
            )
        self.assertEqual(gh.calls, [])


class RunTests(unittest.TestCase):
    def _write(self, document):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "diagnostics.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_healthy_run_is_ok(self):
        path = self._write(base_document())
        gh = FakeGh()
        self.assertEqual(
            health.run(gh, repo="azumag/docich", diagnostics_path=path), health.EXIT_OK
        )
        self.assertEqual(gh.mutations(), [])

    def test_malformed_diagnostics_leaves_state_untouched(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "diagnostics.json"
        path.write_text("{ not json", encoding="utf-8")
        gh = FakeGh(issues=[{"number": 12, "title": health.ABNORMAL_TITLE}])
        self.assertEqual(
            health.run(gh, repo="azumag/docich", diagnostics_path=path),
            health.EXIT_UNAVAILABLE,
        )
        self.assertEqual(gh.calls, [])

    def test_query_failure_leaves_state_untouched(self):
        path = self._write(base_document(states={
            "weather_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 900,
            },
        }))
        gh = FakeGh(fail_query=True)
        self.assertEqual(
            health.run(gh, repo="azumag/docich", diagnostics_path=path),
            health.EXIT_UNAVAILABLE,
        )
        self.assertEqual(gh.mutations(), [])

    def test_publish_failure_is_reported(self):
        path = self._write(base_document(states={
            "weather_corner": {
                "readable": True,
                "status": "failed",
                "completed_at": NOW - 900,
            },
        }))
        gh = FakeGh(fail_publish=True)
        self.assertEqual(
            health.run(gh, repo="azumag/docich", diagnostics_path=path),
            health.EXIT_PUBLISH_FAILED,
        )


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_periodically_on_protected_main(self):
        self.assertIn("cron: '*/10 * * * *'", self.text)
        self.assertIn("github.ref_protected == true", self.text)
        self.assertIn("environment: vm-operations", self.text)
        self.assertIn("issues: write", self.text)

    def test_uses_read_only_diagnostics_and_the_health_module(self):
        self.assertIn("diagnostics docich production", self.text)
        self.assertIn("ops/vm_actions/corner_health.py", self.text)
        self.assertNotIn("exec docich production", self.text)
        self.assertNotIn("restart", self.text.lower())
        self.assertNotIn("gh issue", self.text)

    def test_raw_diagnostics_and_stderr_are_not_published(self):
        self.assertIn('2>"$err"', self.text)
        self.assertNotIn('cat "$RUNNER_TEMP/corner-diagnostics.json"', self.text)
        self.assertIn("Raw diagnostics, stderr, paths, credentials", self.text)

    def test_workflow_has_its_own_concurrency_lane(self):
        self.assertIn("group: vm-corner-monitor-${{ github.repository }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)


if __name__ == "__main__":
    unittest.main()
