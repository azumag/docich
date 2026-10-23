"""Contracts for the deploy blocker alert (docich#2487)."""

import datetime as dt
import json
import unittest
from pathlib import Path

from ops.vm_actions import reconcile_refused_alert as alert


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-deploy-blocker-alert.yml"
NOW = dt.datetime(2026, 10, 23, 0, 0, 0, tzinfo=dt.timezone.utc)


def iso(minutes_ago: int) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGh:
    """A bounded ``gh`` stand-in that records every mutation."""

    def __init__(self, runs=None, alert=None, fail=None, failed_log=""):
        self.runs = runs or []
        self.alert = alert
        self.fail = fail
        self.failed_log = failed_log
        self.calls = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        joined = " ".join(args)
        if self.fail == "runs" and args and args[0] == "api":
            raise alert.QueryError("runs query failed")
        if args and args[0] == "api":
            lines = [json.dumps(run) for run in self.runs]
            return "\n".join(lines) + ("\n" if lines else "")
        if args[:2] == ["run", "view"]:
            if self.fail == "logs":
                raise alert.QueryError("log query failed")
            return self.failed_log
        if args[:2] == ["issue", "list"]:
            if self.fail == "issues":
                raise alert.QueryError("issue list failed")
            return "" if self.alert is None else f"{self.alert}\n"
        if args[:2] in (["issue", "create"], ["issue", "edit"], ["issue", "close"]):
            if self.fail == "publish":
                raise alert.QueryError("publish failed")
            return ""
        raise AssertionError(f"unexpected gh call: {joined}")

    def mutations(self):
        return [c for c in self.calls if c[:2] in
                (["issue", "create"], ["issue", "edit"], ["issue", "close"])]


def run_detector(fake, lookback=alert.DEFAULT_LOOKBACK_MINUTES):
    lines = []
    code = alert.run(
        fake,
        repo="azumag/docich",
        server="https://github.com",
        run_id="12345",
        lookback_minutes=lookback,
        now=NOW,
        log=lines.append,
    )
    return code, lines


def failed_run(minutes_ago=5, run_id=9001, sha="a" * 40):
    return {
        "id": run_id,
        "created_at": iso(minutes_ago),
        "conclusion": "failure",
        "head_sha": sha,
        "url": f"https://github.com/azumag/docich/actions/runs/{run_id}",
    }


def success_run(minutes_ago=1, run_id=9002, sha="b" * 40):
    return {
        "id": run_id,
        "created_at": iso(minutes_ago),
        "conclusion": "success",
        "head_sha": sha,
        "url": f"https://github.com/azumag/docich/actions/runs/{run_id}",
    }


class ClassificationTests(unittest.TestCase):
    def test_reconcile_refusal_is_classified_from_the_gateway_reason_code(self):
        self.assertEqual(
            alert.classify_reason("VM operation rejected: presynced reconcile refused -> reconcile_refused"),
            "reconcile_refused",
        )

    def test_allowlisted_codes_win_over_free_text(self):
        text = "some log line\nnext: reconcile_fetch_failed\nthen more text with secrets"
        self.assertEqual(alert.classify_reason(text), "reconcile_fetch_failed")

    def test_unknown_failure_uses_the_fixed_fallback(self):
        self.assertEqual(alert.classify_reason("boom: traceback with tokens"), "deploy_failed")

    def test_only_enum_members_are_returned(self):
        for probe in ("", "x", "reconcile_refused", "operation_rejected", "unexpected"):
            self.assertIn(alert.classify_reason(probe), alert.REASON_CODES + (alert.FALLBACK_REASON,))


class SelectionTests(unittest.TestCase):
    def test_failed_run_inside_the_window_blocks(self):
        runs = [failed_run(minutes_ago=5)]
        self.assertEqual(alert.select_blocking_failure(runs, NOW, 90)["id"], 9001)

    def test_newer_success_supersedes_the_failure(self):
        runs = [success_run(minutes_ago=1), failed_run(minutes_ago=5)]
        self.assertIsNone(alert.select_blocking_failure(runs, NOW, 90))

    def test_old_failure_outside_the_window_does_not_block(self):
        runs = [failed_run(minutes_ago=300)]
        self.assertIsNone(alert.select_blocking_failure(runs, NOW, 90))

    def test_in_progress_run_does_not_block_by_itself(self):
        runs = [{"id": 7, "created_at": iso(1), "conclusion": None, "head_sha": "c" * 40, "url": "u"}]
        self.assertIsNone(alert.select_blocking_failure(runs, NOW, 90))


class DetectorTests(unittest.TestCase):
    def test_blocked_deploy_opens_a_deduplicated_issue_with_the_reason(self):
        fake = FakeGh(runs=[failed_run()], failed_log="presynced reconcile refused")
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_OK, lines)
        creates = [c for c in fake.calls if c[:2] == ["issue", "create"]]
        self.assertEqual(len(creates), 1)
        body = creates[0][creates[0].index("--body") + 1]
        self.assertIn("`reconcile_refused`", body)
        self.assertIn("actions/runs/9001", body)
        self.assertIn("detector run", body)
        self.assertNotIn("presynced reconcile refused", body)

    def test_existing_alert_is_refreshed_not_duplicated(self):
        fake = FakeGh(runs=[failed_run()], alert=42, failed_log="reconcile_refused")
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_OK, lines)
        self.assertFalse([c for c in fake.calls if c[:2] == ["issue", "create"]])
        self.assertTrue([c for c in fake.calls if c[:2] == ["issue", "edit"] and "42" in c])

    def test_recovery_closes_the_open_alert(self):
        fake = FakeGh(runs=[success_run(minutes_ago=1), failed_run(minutes_ago=30)], alert=42)
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_OK, lines)
        closes = [c for c in fake.calls if c[:2] == ["issue", "close"]]
        self.assertEqual(len(closes), 1)
        self.assertIn("42", closes[0])

    def test_no_alert_state_change_when_nothing_is_blocked(self):
        fake = FakeGh(runs=[success_run(minutes_ago=1)])
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_OK, lines)
        self.assertEqual(fake.mutations(), [])

    def test_query_failure_leaves_the_state_untouched(self):
        fake = FakeGh(runs=[failed_run()], alert=42, fail="runs")
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_QUERY_FAILED)
        self.assertEqual(fake.mutations(), [])

    def test_log_unavailable_still_alerts_with_the_fallback_reason(self):
        fake = FakeGh(runs=[failed_run()], fail="logs")
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_OK, lines)
        create = [c for c in fake.calls if c[:2] == ["issue", "create"]][0]
        body = create[create.index("--body") + 1]
        self.assertIn("`deploy_failed`", body)

    def test_publish_failure_is_reported(self):
        fake = FakeGh(runs=[failed_run()], fail="publish")
        code, lines = run_detector(fake)
        self.assertEqual(code, alert.EXIT_PUBLISH_FAILED)


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_schedule_and_isolation_contract(self):
        self.assertIn("cron: '9,39 * * * *'", self.text)
        self.assertIn("group: vm-deploy-blocker-${{ github.repository }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("github.ref_protected == true", self.text)

    def test_permissions_are_least_privilege(self):
        self.assertIn("contents: read", self.text)
        self.assertIn("actions: read", self.text)
        self.assertIn("issues: write", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("id-token: write", self.text)

    def test_runs_the_fixed_detector_with_the_workflow_token(self):
        self.assertIn("ops/vm_actions/reconcile_refused_alert.py", self.text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.text)
        self.assertIn("RECONCILE_ALERT_LOOKBACK_MINUTES: '90'", self.text)
        # never executes anything on the VM
        self.assertNotIn("exec docich production", self.text)
        self.assertNotIn("ssh ", self.text)

    def test_body_publishes_only_fixed_fields(self):
        body = alert.render_body(
            repo="azumag/docich",
            server="https://github.com",
            run=failed_run(),
            reason="reconcile_refused",
            lookback_minutes=90,
            run_id="12345",
        )
        self.assertIn("`reconcile_refused`", body)
        self.assertIn("actions/runs/9001", body)
        self.assertIn("head SHA", body)
        for forbidden in ("log", "token", "password", "secret", "Authorization"):
            self.assertNotIn(forbidden, body.lower())


if __name__ == "__main__":
    unittest.main()
