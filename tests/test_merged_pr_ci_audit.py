"""Merged-PR CI evidence audit and post-merge monitor contracts."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github/scripts"))
_spec = importlib.util.spec_from_file_location(
    "merged_pr_ci_audit", ROOT / ".github/scripts/merged_pr_ci_audit.py"
)
auditmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auditmod)

NOW = dt.datetime(2026, 11, 28, 5, 16, tzinfo=dt.timezone.utc)
REPO = "azumag/docich"
HEAD = "a" * 40
MERGE = "b" * 40


def z(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_pr(number=44, hours_ago=1.0, head=HEAD):
    return {
        "number": number,
        "title": "fix: example",
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "merged_at": z(hours_ago),
        "merge_commit_sha": MERGE,
        "head": {"sha": head},
        "base": {"ref": "main"},
    }


def workflow_run(run_id=123456, sha=HEAD, conclusion="success"):
    return {
        "id": run_id,
        "event": "pull_request",
        "head_sha": sha,
        "created_at": "2026-11-28T03:50:00Z",
        "conclusion": conclusion,
        "status": "completed",
        "name": "CI",
        "workflow_id": 77,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


class FakeRunner:
    def __init__(self, *, checks=None, runs=None, comments=None, prs=None):
        self.calls = []
        self.posts = []
        self.patches = []
        self.created_issues = []
        self.edited_issues = []
        self.checks = checks if checks is not None else {"total_count": 0, "check_runs": []}
        self.runs = runs if runs is not None else {"total_count": 0, "workflow_runs": []}
        self.comments = comments if comments is not None else []
        self.prs = prs if prs is not None else [make_pr()]
        self.open_issue_number = None

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[0] == "api" and len(args) > 1 and args[1].startswith(f"repos/{REPO}/pulls?state=closed"):
            if "page=1" not in args[1]:
                return "[]"
            return json.dumps([
                {"number": pr["number"], "merged_at": pr["merged_at"], "updated_at": pr["merged_at"]}
                for pr in self.prs
            ])
        if args[0] == "api":
            index = 1
            method = "GET"
            if len(args) > 2 and args[1] == "-X":
                method = args[2]
                index = 3
            endpoint = args[index]
            body = ""
            for i, value in enumerate(args):
                if value.startswith("body="):
                    body = value[5:]
            if method == "POST" and endpoint.endswith("/comments"):
                self.posts.append((endpoint, body))
                return "{}"
            if method == "PATCH":
                self.patches.append((endpoint, body))
                return "{}"
            if endpoint.startswith(f"repos/{REPO}/pulls/"):
                number = int(endpoint.rsplit("/", 1)[1])
                pr = next(item for item in self.prs if item["number"] == number)
                return json.dumps(pr)
            if endpoint.endswith("/check-runs?per_page=100"):
                return json.dumps(self.checks)
            if endpoint == f"repos/{REPO}/commits/{HEAD}":
                return json.dumps({
                    "sha": HEAD,
                    "commit": {
                        "committer": {"date": z(2)},
                        "author": {"date": z(2)},
                    },
                    "html_url": f"https://github.com/{REPO}/commit/{HEAD}",
                })
            if endpoint.endswith("/check-suites?per_page=100"):
                return json.dumps({"total_count": 0, "check_suites": []})
            if "actions/runs?event=pull_request" in endpoint:
                return json.dumps(self.runs)
            if "actions/runs?event=push" in endpoint:
                return json.dumps({"total_count": 0, "workflow_runs": []})
            if endpoint.endswith("/comments?per_page=100"):
                return json.dumps(self.comments)
            if endpoint.endswith("/hooks?per_page=100"):
                return json.dumps([])
            raise AssertionError(f"unexpected api endpoint {endpoint}")
        if args[:2] == ["issue", "list"]:
            return f"{self.open_issue_number}\n" if self.open_issue_number else ""
        if args[:2] == ["issue", "create"]:
            title = args[args.index("--title") + 1]
            body = args[args.index("--body") + 1]
            self.created_issues.append((title, body))
            number = 900 + len(self.created_issues)
            return f"https://github.com/{REPO}/issues/{number}\n"
        if args[:2] == ["issue", "edit"]:
            self.edited_issues.append(args)
            return ""
        raise AssertionError(f"unexpected gh args {args}")


def test_workflow_evidence_preserves_required_run_fields():
    evidence = auditmod.workflow_evidence(
        {"total_count": 1, "workflow_runs": [workflow_run()]}
    )
    row = evidence["runs"][0]
    assert evidence["total_count"] == 1
    assert row["id"] == 123456
    assert row["event"] == "pull_request"
    assert row["head_sha"] == HEAD
    assert row["created_at"] == "2026-11-28T03:50:00Z"
    assert row["conclusion"] == "success"


def test_ci_required_state_success_pending_absent():
    assert auditmod.ci_required_state({"check_runs": []}) == "ABSENT"
    assert auditmod.ci_required_state(
        {"check_runs": [{"name": "ci-required", "status": "completed", "conclusion": "success"}]}
    ) == "SUCCESS"
    assert auditmod.ci_required_state(
        {"check_runs": [{"name": "ci-required", "status": "in_progress", "conclusion": None}]}
    ) == "PENDING"


def test_zero_checks_before_24_hours_are_zero_check_merge():
    record = {
        "merged_at": z(2),
        "head_check_runs": {"total_count": 0, "check_runs": []},
        "evidence_errors": [],
    }
    assert auditmod.classify_record(record, NOW) == "zero-check-merge"


def test_zero_checks_after_24_hours_are_retention_expiry_not_event_loss():
    record = {
        "merged_at": z(25),
        "head_check_runs": {"total_count": 0, "check_runs": []},
        "evidence_errors": [],
    }
    assert auditmod.classify_record(record, NOW) == "evidence-retention-possible"
    record["post_merge_ci_required"] = "SUCCESS"
    assert auditmod.classify_record(record, NOW) == "evidence-retention-possible-monitor-success"


def test_old_head_at_recent_merge_is_retention_expiry():
    record = {
        "merged_at": z(1),
        "head_commit": {"sha": HEAD, "committed_at": z(25), "authored_at": z(25)},
        "head_check_runs": {"total_count": 0, "check_runs": []},
        "evidence_errors": [],
    }
    assert auditmod.classify_record(record, NOW) == "evidence-retention-possible"


def test_pr_opened_over_24h_before_audit_is_retention_expiry():
    record = {
        "created_at": z(25),
        "merged_at": z(23.5),
        "head_commit": {"sha": HEAD, "committed_at": z(24.7), "authored_at": z(24.7)},
        "head_check_runs": {"total_count": 0, "check_runs": []},
        "evidence_errors": [],
    }
    assert auditmod.classify_record(record, NOW) == "evidence-retention-possible"


def test_monitor_records_success_as_persistent_pr_comment():
    checks = {
        "total_count": 1,
        "check_runs": [{"name": "ci-required", "status": "completed", "conclusion": "success"}],
    }
    runs = {"total_count": 1, "workflow_runs": [workflow_run(222)]}
    runner = FakeRunner(checks=checks, runs=runs)
    assert auditmod.monitor(runner, repo=REPO, explicit_pr=44, now=NOW, min_age_seconds=180) == 0
    assert len(runner.posts) == 1
    body = runner.posts[0][1]
    assert "post-merge `ci-required` record" in body
    assert "**SUCCESS**" in body
    assert "| 222 | pull_request |" in body
    payload = json.loads(body.split(auditmod.MONITOR_COMMENT_PREFIX, 1)[1].split(auditmod.MONITOR_COMMENT_SUFFIX, 1)[0])
    assert payload["ci_required"] == "SUCCESS"
    assert payload["pull_request_workflow_run_ids"] == [222]


def test_monitor_zero_check_absent_opens_immediate_incident_issue():
    runner = FakeRunner()
    rc = auditmod.monitor(
        runner,
        repo=REPO,
        explicit_pr=44,
        now=NOW,
        min_age_seconds=180,
        server="https://github.com",
        run_id="777",
        log=lambda _: None,
    )
    assert rc == 0
    assert len(runner.posts) == 1
    assert "**ABSENT**" in runner.posts[0][1]
    titles = [title for title, _ in runner.created_issues]
    assert auditmod.incident_title(44) in titles
    body = runner.created_issues[0][1]
    assert "workflow run id: なし" in body
    assert "detector run: https://github.com/azumag/docich/actions/runs/777" in body
    assert "hooks/<hook_id>/deliveries" in body


def test_monitor_does_not_rewrite_final_existing_record():
    checks = {
        "total_count": 1,
        "check_runs": [{"name": "ci-required", "status": "completed", "conclusion": "success"}],
    }
    record = auditmod.collect_pr_evidence(FakeRunner(checks=checks), REPO, make_pr())
    body = auditmod.render_monitor_comment(record, NOW)
    runner = FakeRunner(checks=checks, comments=[{"id": 5, "body": body}])
    assert auditmod.monitor(runner, repo=REPO, explicit_pr=44, now=NOW, min_age_seconds=180) == 0
    assert runner.posts == []
    assert runner.patches == []


def test_monitor_skips_merges_inside_grace_window():
    runner = FakeRunner(prs=[make_pr(hours_ago=0.01)])
    assert auditmod.monitor(runner, repo=REPO, explicit_pr=44, now=NOW, min_age_seconds=180) == 0
    assert runner.posts == []


def test_daily_audit_publishes_evidence_and_zero_check_incident(tmp_path):
    runner = FakeRunner()
    output = tmp_path / "evidence.json"
    rc = auditmod.audit(
        runner,
        repo=REPO,
        server="https://github.com",
        run_id="999",
        output=output,
        now=NOW,
        publish=True,
        log=lambda _: None,
    )
    assert rc == 0
    document = json.loads(output.read_text())
    record = document["prs"][0]
    assert record["classification"] == "zero-check-merge"
    assert record["classification_label"] == "0チェック merge"
    assert "pull_request_workflow_runs" in record

    titles = [title for title, _ in runner.created_issues]
    assert auditmod.EVIDENCE_ISSUE_TITLE in titles
    assert auditmod.incident_title(44) in titles
    incident_body = next(body for title, body in runner.created_issues if title == auditmod.incident_title(44))
    assert "workflow run id: なし" in incident_body
    assert "commits/<sha>/check-runs" in incident_body
    assert "check-suites" in incident_body
    assert "actions/runs?event=pull_request" in incident_body
    assert "hooks/<hook_id>/deliveries" in incident_body
    assert "証跡期限切れの可能性" in incident_body

    evidence_comments = [body for _, body in runner.posts]
    assert evidence_comments
    assert "merged-pr-daily-audit evidence" in evidence_comments[0]
    assert '"head_sha"' in evidence_comments[0]


def test_incident_body_includes_run_ids_when_present():
    incident = {
        "check_runs": {"total_count": 0},
        "check_suites": {"total_count": 0},
        "pull_request_workflow_runs": {
            "total_count": 1,
            "runs": [auditmod.compact_workflow_run(workflow_run(321))],
        },
        "webhook_deliveries": {"status": "unavailable", "error": "repository hooks query failed with the workflow token", "matched": []},
    }
    body = auditmod.render_incident_body(
        {**make_pr(), **{"head_sha": HEAD, "merge_commit_sha": MERGE}},
        incident,
        repo=REPO,
        server="https://github.com",
        audit_run_id="999",
    )
    assert "workflow run id: 321" in body
    assert "| 321 | pull_request |" in body


def test_workflows_wire_audit_and_post_merge_monitor():
    daily = (ROOT / ".github/workflows/merged-pr-daily-audit.yml").read_text()
    monitor = (ROOT / ".github/workflows/post-merge-ci-monitor.yml").read_text()
    assert "merged_pr_ci_audit.py audit" in daily
    assert "actions: read" in daily
    assert "issues: write" in daily
    assert "merged_pr_ci_audit.py monitor" in monitor
    assert "types: [closed]" in monitor
    assert "branches: [main]" in monitor
    assert "sleep 180" in monitor
    assert "pull-requests: write" in monitor
