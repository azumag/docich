"""Contracts for the independent VM monitor silence detector (#1881 follow-up)."""

import datetime as dt
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ops.vm_actions import monitor_silence as silence


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-monitor-silence-alert.yml"
NOW = dt.datetime(2026, 10, 17, 0, 0, 0, tzinfo=dt.timezone.utc)
THRESHOLD = 6.0


class FakeGh:
    """A bounded ``gh`` stand-in that records every mutation."""

    def __init__(self, created=None, alert=None, fail_query=None, list_shape=None, runs=None):
        self.created = created
        self.alert = alert
        self.fail_query = fail_query
        self.list_shape = list_shape
        self.runs = runs
        self.calls = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        joined = " ".join(args)
        if args and args[0] == "api":
            if self.fail_query == "runs":
                raise silence.QueryError("runs query failed")
            if self.runs is not None:
                query = parse_qs(urlsplit(args[1]).query)
                runs = self.runs
                for parameter, field in (("branch", "head_branch"),
                                         ("status", "conclusion"), ("event", "event")):
                    if parameter in query:
                        runs = [r for r in runs if r[field] in query[parameter]]
                runs = sorted(runs, key=lambda r: r["created_at"], reverse=True)
                return (runs[0]["created_at"] if runs else "") + "\n"
            return (self.created or "") + "\n"
        if args[:2] == ["issue", "list"]:
            if self.fail_query == "issues":
                raise silence.QueryError("issue list failed")
            if self.list_shape is not None:
                return self.list_shape
            return "" if self.alert is None else f"{self.alert}\n"
        if args[:2] in (["issue", "create"], ["issue", "edit"], ["issue", "close"]):
            if self.fail_query == "publish":
                raise silence.QueryError("publish failed")
            return ""
        raise AssertionError(f"unexpected gh call: {joined}")

    def mutations(self):
        return [c for c in self.calls if c[:2] in
                (["issue", "create"], ["issue", "edit"], ["issue", "close"])]


def run_detector(fake, threshold=THRESHOLD):
    lines = []
    code = silence.run(
        fake,
        repo="azumag/docich",
        server="https://github.com",
        run_id="12345",
        threshold_hours=threshold,
        now=NOW,
        log=lines.append,
    )
    return code, lines


def iso(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FreshnessDecisionTests(unittest.TestCase):
    def test_decide_is_alert_at_and_above_the_threshold(self):
        self.assertEqual(silence.decide(THRESHOLD, THRESHOLD, False), "alert")
        self.assertEqual(silence.decide(THRESHOLD + 0.1, THRESHOLD, False), "alert")

    def test_decide_recovers_only_when_an_alert_is_open(self):
        self.assertEqual(silence.decide(1.0, THRESHOLD, True), "recover")
        self.assertEqual(silence.decide(1.0, THRESHOLD, False), "ok")

    def test_decide_alerts_when_no_successful_run_is_known(self):
        self.assertEqual(silence.decide(None, THRESHOLD, True), "alert")


class DetectorStateTests(unittest.TestCase):
    def test_success_query_is_main_only_without_an_event_filter(self):
        fake = FakeGh(created=iso(1))
        silence.latest_success_created_at(fake, "azumag/docich", silence.DEFAULT_WORKFLOW)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(parse_qs(urlsplit(fake.calls[0][1]).query), {
            "branch": ["main"], "status": ["success"], "per_page": ["1"],
        })

    def test_fresh_other_branch_success_does_not_mask_stale_main(self):
        runs = [
            {"head_branch": "main", "conclusion": "success", "event": "schedule",
             "created_at": iso(8)},
            {"head_branch": "monitor-test", "conclusion": "success",
             "event": "workflow_dispatch", "created_at": iso(1)},
        ]
        for alert, expected in ((42, ["issue", "edit", "42"]),
                                (None, ["issue", "create", "--repo"])):
            with self.subTest(alert=alert):
                fake = FakeGh(runs=runs, alert=alert)
                code, _ = run_detector(fake)
                self.assertEqual(code, silence.EXIT_OK)
                self.assertEqual([c[:3] for c in fake.mutations()], [expected])
                body = fake.mutations()[0][-1]
                self.assertIn(iso(8), body)
                self.assertNotIn(iso(1), body)

    def test_a_fresh_main_dispatch_success_can_close_the_alert(self):
        fake = FakeGh(alert=42, runs=[
            {"head_branch": "main", "conclusion": "success", "event": "schedule",
             "created_at": iso(8)},
            {"head_branch": "main", "conclusion": "success",
             "event": "workflow_dispatch", "created_at": iso(1)},
        ])
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        self.assertEqual([c[:3] for c in fake.mutations()], [["issue", "close", "42"]])

    def test_a_fresh_monitor_without_an_alert_does_nothing(self):
        fake = FakeGh(created=iso(1))
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        self.assertEqual(fake.mutations(), [])

    def test_a_fresh_monitor_closes_the_open_alert(self):
        fake = FakeGh(created=iso(1), alert=42)
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        self.assertEqual([c[:3] for c in fake.mutations()], [["issue", "close", "42"]])

    def test_a_silent_monitor_opens_one_alert(self):
        fake = FakeGh(created=iso(8))
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        create = fake.mutations()
        self.assertEqual([c[:2] for c in create], [["issue", "create"]])
        self.assertIn(silence.ALERT_TITLE, create[0])

    def test_a_silent_monitor_refreshes_instead_of_duplicating(self):
        fake = FakeGh(created=iso(8), alert=42)
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        self.assertEqual([c[:3] for c in fake.mutations()], [["issue", "edit", "42"]])

    def test_never_succeeding_monitor_counts_as_silence(self):
        fake = FakeGh(created=None)
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        self.assertEqual([c[:2] for c in fake.mutations()], [["issue", "create"]])

    def test_query_failure_fails_closed_and_never_closes_an_alert(self):
        fake = FakeGh(created=iso(1), alert=42, fail_query="runs")
        code, lines = run_detector(fake)
        self.assertEqual(code, silence.EXIT_QUERY_FAILED)
        self.assertEqual(fake.mutations(), [])
        self.assertTrue(any("state left unchanged" in line for line in lines))

    def test_issue_list_failure_fails_closed(self):
        fake = FakeGh(created=iso(1), fail_query="issues")
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_QUERY_FAILED)
        self.assertEqual(fake.mutations(), [])

    def test_an_unexpected_issue_list_shape_fails_closed(self):
        fake = FakeGh(created=iso(1), list_shape="not-a-number\n")
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_QUERY_FAILED)
        self.assertEqual(fake.mutations(), [])

    def test_the_alert_lookup_is_a_bounded_title_search(self):
        # A scan of every open issue would silently miss the alert once the
        # repository has more open issues than the page size.
        fake = FakeGh(created=iso(1))
        run_detector(fake)
        lookup = [c for c in fake.calls if c[:2] == ["issue", "list"]]
        self.assertEqual(len(lookup), 1)
        self.assertIn("--search", lookup[0])
        self.assertIn(f'"{silence.ALERT_TITLE}" in:title', lookup[0])
        self.assertIn("--limit", lookup[0])
        limit = lookup[0][lookup[0].index("--limit") + 1]
        self.assertLessEqual(int(limit), 20)

    def test_a_failed_publication_is_reported_without_a_second_attempt(self):
        fake = FakeGh(created=iso(8), fail_query="publish")
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_PUBLISH_FAILED)
        self.assertEqual(len(fake.mutations()), 1)

    def test_threshold_boundary_alerts(self):
        fake = FakeGh(created=iso(THRESHOLD))
        code, _ = run_detector(fake)
        self.assertEqual(code, silence.EXIT_OK)
        self.assertEqual([c[:2] for c in fake.mutations()], [["issue", "create"]])


class AlertBodyTests(unittest.TestCase):
    def body(self, created, age):
        return silence.render_body(
            repo="azumag/docich",
            server="https://github.com",
            run_id="12345",
            created=created,
            age_hours=age,
            threshold_hours=THRESHOLD,
        )

    def test_body_carries_only_fixed_fields(self):
        body = self.body("2026-10-01T19:32:57Z", 15.2)
        self.assertIn("2026-10-01T19:32:57Z", body)
        self.assertIn("15.2", body)
        self.assertIn("https://github.com/azumag/docich/actions/workflows/vm-storage-monitor.yml", body)
        self.assertIn("https://github.com/azumag/docich/actions/runs/12345", body)
        lowered = body.lower()
        # Only fixed fields: no credentials, paths or key material.
        for forbidden in ("ghp_", "gho_", "secret", "/home/", "private key", "ssh-"):
            self.assertNotIn(forbidden, lowered)

    def test_body_reports_an_unknown_last_success_without_guessing(self):
        body = self.body(None, None)
        self.assertIn("no successful run observed", body)


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.content = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_runs_on_a_schedule_and_by_owner_dispatch(self):
        self.assertIn("schedule:", self.content)
        self.assertIn("workflow_dispatch:", self.content)
        self.assertIn("cron: '29 * * * *'", self.content)
        self.assertIn("9018513", self.content)
        self.assertIn("github.ref_protected == true", self.content)

    def test_workflow_uses_its_own_concurrency_group(self):
        self.assertIn("group: vm-monitor-silence-${{ github.repository }}", self.content)
        self.assertNotIn("group: vm-storage-monitor-", self.content)

    def test_workflow_needs_only_issue_write(self):
        self.assertIn("contents: read", self.content)
        self.assertIn("issues: write", self.content)
        self.assertNotIn("secrets.", self.content)

    def test_workflow_is_pinned_bounded_and_argument_free(self):
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.content)
        self.assertIn("timeout-minutes: 5", self.content)
        self.assertIn("python3 ops/vm_actions/monitor_silence.py", self.content)
        self.assertNotIn("inputs.", self.content)


if __name__ == "__main__":
    unittest.main()
