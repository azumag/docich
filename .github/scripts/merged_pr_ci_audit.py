#!/usr/bin/env python3
"""Audit merged PRs for missing CI evidence and record early ci-required state.

The repository keeps Actions/check evidence only for a short retention window.
This script therefore writes two kinds of persistent, reviewable records before
that window expires:

* post-merge monitor comments on each merged PR, recording whether the PR head
  had a successful/pending/absent ``ci-required`` check a few minutes after the
  merge; and
* daily audit evidence comments in one rolling evidence issue, including the
  workflow run id/event/head_sha/created_at/conclusion fields needed for later
  incident reconstruction.

A recent merge whose PR head has zero check runs is classified as a possible
zero-check merge.  Once the merge is more than 24 hours old, the same zero
count is not called an event drop: it may simply be evidence retention expiry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Iterable, Sequence

EVIDENCE_ISSUE_TITLE = "[CI] merged-pr-daily-audit evidence log"
INCIDENT_TITLE_PREFIX = "[CI] PR #"
INCIDENT_TITLE_SUFFIX = " merged with zero head checks"
MONITOR_COMMENT_PREFIX = "<!-- merged-pr-ci-monitor:v1 "
MONITOR_COMMENT_SUFFIX = " -->"
DEFAULT_AUDIT_HOURS = 30.0
DEFAULT_MONITOR_LOOKBACK_HOURS = 6.0
DEFAULT_MONITOR_MIN_AGE_SECONDS = 180
CLASSIFICATION_LABELS = {
    "checks-present": "checks present",
    "zero-check-merge": "0チェック merge",
    "evidence-retention-possible": "証跡期限切れの可能性",
    "evidence-retention-possible-monitor-success": "証跡期限切れの可能性（post-merge SUCCESS 記録あり）",
    "evidence-unreadable": "証跡読み取り不能",
}
MAX_COMMENT_CHARS = 60000
MAX_WEBHOOK_HOOKS = 5
MAX_WEBHOOK_DELIVERIES_PER_HOOK = 10

EXIT_OK = 0
EXIT_QUERY_FAILED = 2
EXIT_PUBLISH_FAILED = 3

Runner = Callable[[Sequence[str]], str]


class QueryError(RuntimeError):
    """A bounded GitHub query failed."""


class PublishError(RuntimeError):
    """A GitHub write needed for the audit record failed."""


def gh_runner(args: Sequence[str]) -> str:
    """Run ``gh`` with the workflow-scoped token and return stdout."""

    completed = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise QueryError("gh query failed")
    return completed.stdout


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def gh_json(runner: Runner, args: Sequence[str]) -> object:
    try:
        return json.loads(runner(args))
    except json.JSONDecodeError as exc:
        raise QueryError("unexpected gh JSON shape") from exc


def api_json(runner: Runner, path: str) -> object:
    return gh_json(runner, ["api", path])


def post_comment(runner: Runner, repo: str, number: int, body: str) -> None:
    runner(["api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"])


def update_comment(runner: Runner, repo: str, comment_id: int, body: str) -> None:
    runner(["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "-f", f"body={body}"])


def search_merged_pr_numbers(
    runner: Runner, repo: str, since: dt.datetime, *, limit: int = 100
) -> list[int]:
    """Recently merged PR numbers without relying on the Search API.

    GitHub App installation tokens used by Actions cannot always use
    ``/search/issues`` in this repository, while the pulls list endpoint is
    available with ordinary pull-request read permission.  The repository's
    merge rate is bounded enough that the newest closed-pull pages cover the
    daily audit window; pages stop once updated timestamps are older than the
    window.
    """

    numbers: list[int] = []
    per_page = min(max(limit, 1), 100)
    for page in (1, 2, 3):
        data = api_json(
            runner,
            f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page={per_page}&page={page}",
        )
        if not isinstance(data, list):
            raise QueryError("unexpected closed pull list shape")
        if not data:
            break
        oldest_update: dt.datetime | None = None
        for item in data:
            if not isinstance(item, dict):
                continue
            updated = item.get("updated_at")
            if isinstance(updated, str):
                oldest_update = parse_time(updated)
            merged_at = item.get("merged_at")
            if isinstance(merged_at, str) and parse_time(merged_at) >= since:
                number = item.get("number")
                if isinstance(number, int):
                    numbers.append(number)
        if len(data) < per_page:
            break
        if oldest_update is not None and oldest_update < since:
            break
    return sorted(set(numbers))


def fetch_pr(runner: Runner, repo: str, number: int) -> dict:
    data = api_json(runner, f"repos/{repo}/pulls/{number}")
    if not isinstance(data, dict):
        raise QueryError("unexpected pull shape")
    return data


def associated_pr_numbers(runner: Runner, repo: str, sha: str) -> list[int]:
    data = api_json(runner, f"repos/{repo}/commits/{sha}/pulls")
    if not isinstance(data, list):
        raise QueryError("unexpected associated pull shape")
    return [item["number"] for item in data if isinstance(item, dict) and isinstance(item.get("number"), int)]


def commit_metadata(runner: Runner, repo: str, sha: str) -> dict:
    """Commit timestamps used to bound how old the PR head evidence can be."""

    data = api_json(runner, f"repos/{repo}/commits/{sha}")
    if not isinstance(data, dict):
        raise QueryError("unexpected commit shape")
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    return {
        "sha": data.get("sha") or sha,
        "committed_at": committer.get("date"),
        "authored_at": author.get("date"),
        "html_url": data.get("html_url"),
    }


def check_runs(runner: Runner, repo: str, sha: str) -> dict:
    data = api_json(runner, f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    if not isinstance(data, dict):
        raise QueryError("unexpected check-runs shape")
    return data


def check_suites(runner: Runner, repo: str, sha: str) -> dict:
    data = api_json(runner, f"repos/{repo}/commits/{sha}/check-suites?per_page=100")
    if not isinstance(data, dict):
        raise QueryError("unexpected check-suites shape")
    return data


def workflow_runs(runner: Runner, repo: str, sha: str, event: str) -> dict:
    data = api_json(
        runner,
        f"repos/{repo}/actions/runs?event={event}&head_sha={sha}&per_page=100",
    )
    if not isinstance(data, dict):
        raise QueryError("unexpected workflow-runs shape")
    return data


def issue_comments(runner: Runner, repo: str, number: int) -> list[dict]:
    data = api_json(runner, f"repos/{repo}/issues/{number}/comments?per_page=100")
    if not isinstance(data, list):
        raise QueryError("unexpected issue comments shape")
    return [item for item in data if isinstance(item, dict)]


def compact_check_run(run: dict) -> dict:
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "html_url": run.get("html_url"),
    }


def compact_check_suite(suite: dict) -> dict:
    return {
        "id": suite.get("id"),
        "status": suite.get("status"),
        "conclusion": suite.get("conclusion"),
        "created_at": suite.get("created_at"),
        "updated_at": suite.get("updated_at"),
    }


def compact_workflow_run(run: dict) -> dict:
    """Keep the fields required to reconstruct the run after log expiry."""

    return {
        "id": run.get("id"),
        "event": run.get("event"),
        "head_sha": run.get("head_sha"),
        "created_at": run.get("created_at"),
        "conclusion": run.get("conclusion"),
        "status": run.get("status"),
        "name": run.get("name"),
        "workflow_id": run.get("workflow_id"),
        "html_url": run.get("html_url"),
    }


def workflow_evidence(payload: dict) -> dict:
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        runs = []
    return {
        "total_count": payload.get("total_count", len(runs)),
        "runs": [compact_workflow_run(run) for run in runs if isinstance(run, dict)],
    }


def check_run_evidence(payload: dict) -> dict:
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        runs = []
    return {
        "total_count": payload.get("total_count", len(runs)),
        "check_runs": [compact_check_run(run) for run in runs if isinstance(run, dict)],
    }


def check_suite_evidence(payload: dict) -> dict:
    suites = payload.get("check_suites")
    if not isinstance(suites, list):
        suites = []
    return {
        "total_count": payload.get("total_count", len(suites)),
        "check_suites": [compact_check_suite(suite) for suite in suites if isinstance(suite, dict)],
    }


def ci_required_state(check_payload: dict) -> str:
    """Classify the exact required check context on a PR head."""

    matches = []
    for run in check_payload.get("check_runs") or []:
        if isinstance(run, dict) and run.get("name") == "ci-required":
            matches.append(run)
    if not matches:
        return "ABSENT"
    if any(run.get("conclusion") == "success" for run in matches):
        return "SUCCESS"
    if any(run.get("status") != "completed" or not run.get("conclusion") for run in matches):
        return "PENDING"
    failure_conclusions = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
    if any(run.get("conclusion") in failure_conclusions for run in matches):
        return "FAILURE"
    return "PRESENT_OTHER"


def monitor_state_is_final(state: str) -> bool:
    return state in {"SUCCESS", "FAILURE", "ABSENT", "PRESENT_OTHER"}


def base_pr_record(pr: dict) -> dict:
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "html_url": pr.get("html_url"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "merged_at": pr.get("merged_at"),
        "merge_commit_sha": pr.get("merge_commit_sha"),
        "head_sha": head.get("sha"),
        "base_ref": base.get("ref"),
    }


def collect_pr_evidence(
    runner: Runner,
    repo: str,
    pr: dict,
    *,
    include_merge_push_runs: bool = True,
) -> dict:
    """Collect durable check/workflow metadata for one merged PR."""

    record = base_pr_record(pr)
    head_sha = str(record.get("head_sha") or "")
    merge_sha = str(record.get("merge_commit_sha") or "")
    errors: list[str] = []

    try:
        head_commit = commit_metadata(runner, repo, head_sha)
    except QueryError:
        # The commit object can be unavailable for a deleted fork branch even
        # though the merged PR metadata remains readable.  Classification then
        # falls back to merged_at rather than making the whole record unusable.
        head_commit = {"sha": head_sha, "committed_at": None, "authored_at": None}

    try:
        head_checks = check_run_evidence(check_runs(runner, repo, head_sha))
    except QueryError:
        head_checks = {"total_count": None, "check_runs": []}
        errors.append("head_check_runs_query_failed")

    try:
        pull_runs = workflow_evidence(workflow_runs(runner, repo, head_sha, "pull_request"))
    except QueryError:
        pull_runs = {"total_count": None, "runs": []}
        errors.append("pull_request_runs_query_failed")

    push_runs = {"total_count": None, "runs": []}
    if include_merge_push_runs and merge_sha:
        try:
            push_runs = workflow_evidence(workflow_runs(runner, repo, merge_sha, "push"))
        except QueryError:
            errors.append("merge_push_runs_query_failed")

    record.update(
        {
            "head_commit": head_commit,
            "head_check_runs": head_checks,
            "pull_request_workflow_runs": pull_runs,
            "merge_push_workflow_runs": push_runs,
            "ci_required": ci_required_state({"check_runs": head_checks.get("check_runs") or []}),
            "evidence_errors": errors,
        }
    )
    return record


def monitor_payload_from_comment(comment: dict) -> dict | None:
    body = comment.get("body")
    if not isinstance(body, str) or MONITOR_COMMENT_PREFIX not in body:
        return None
    match = re.search(
        re.escape(MONITOR_COMMENT_PREFIX) + r"(\{.*?\})" + re.escape(MONITOR_COMMENT_SUFFIX),
        body,
        flags=re.DOTALL,
    )
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload["_comment_id"] = comment.get("id")
    return payload


def existing_monitor_payload(comments: Iterable[dict], head_sha: str) -> dict | None:
    newest: dict | None = None
    for comment in comments:
        payload = monitor_payload_from_comment(comment)
        if payload and payload.get("head_sha") == head_sha:
            newest = payload
    return newest


def run_lines(runs: Iterable[dict]) -> list[str]:
    lines = []
    for run in runs:
        lines.append(
            "| {id} | {event} | `{head_sha}` | {created_at} | {conclusion} |".format(
                id=run.get("id"),
                event=run.get("event"),
                head_sha=str(run.get("head_sha") or "")[:12],
                created_at=run.get("created_at"),
                conclusion=run.get("conclusion") or run.get("status") or "in_progress",
            )
        )
    return lines or ["| — | — | — | — | — |"]


def render_monitor_comment(record: dict, observed_at: dt.datetime) -> str:
    payload = {
        "schema_version": 1,
        "kind": "post-merge-ci-required-record",
        "pr": record.get("number"),
        "head_sha": record.get("head_sha"),
        "merge_commit_sha": record.get("merge_commit_sha"),
        "merged_at": record.get("merged_at"),
        "observed_at": iso_z(observed_at),
        "ci_required": record.get("ci_required"),
        "pull_request_workflow_run_ids": [
            run.get("id") for run in record.get("pull_request_workflow_runs", {}).get("runs", [])
        ],
    }
    runs = record.get("pull_request_workflow_runs", {}).get("runs", [])
    lines = [
        MONITOR_COMMENT_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":")) + MONITOR_COMMENT_SUFFIX,
        "post-merge `ci-required` record",
        "",
        f"- result: **{record.get('ci_required')}**",
        f"- head: `{record.get('head_sha')}`",
        f"- merged_at: `{record.get('merged_at')}`",
        f"- observed_at: `{iso_z(observed_at)}`",
        f"- check-runs total_count: `{record.get('head_check_runs', {}).get('total_count')}`",
        "",
        "pull_request workflow evidence:",
        "",
        "| run id | event | head_sha | created_at | conclusion |",
        "|---:|---|---|---|---|",
        *run_lines(runs),
        "",
        "この記録は merged-pr-daily-audit が retention 後の 0 件と実欠落を分けるための恒久証跡です。",
    ]
    return "\n".join(lines)


def merged_main_pr(pr: dict) -> bool:
    if not pr.get("merged_at"):
        return False
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    return base.get("ref") == "main"


def age_seconds(merged_at: str, now: dt.datetime) -> float:
    return (now - parse_time(merged_at)).total_seconds()


def monitor_candidates(
    runner: Runner,
    repo: str,
    *,
    explicit_pr: int | None,
    push_sha: str | None,
    since: dt.datetime,
) -> list[int]:
    direct: list[int] = []
    if explicit_pr is not None:
        direct.append(explicit_pr)
    if push_sha:
        try:
            direct.extend(associated_pr_numbers(runner, repo, push_sha))
        except QueryError:
            # The scheduled fallback still covers the merge when the optional
            # commit association endpoint is unavailable.
            pass
    if direct:
        return sorted(set(direct))
    return sorted(set(search_merged_pr_numbers(runner, repo, since)))


def monitor(
    runner: Runner,
    *,
    repo: str,
    explicit_pr: int | None = None,
    push_sha: str | None = None,
    now: dt.datetime | None = None,
    lookback_hours: float = DEFAULT_MONITOR_LOOKBACK_HOURS,
    min_age_seconds: int = DEFAULT_MONITOR_MIN_AGE_SECONDS,
    server: str = "https://github.com",
    run_id: str = "0",
    log: Callable[[str], None] = print,
) -> int:
    """Record ci-required evidence on recently merged main PRs."""

    now = now or now_utc()
    since = now - dt.timedelta(hours=lookback_hours)
    try:
        candidates = monitor_candidates(
            runner, repo, explicit_pr=explicit_pr, push_sha=push_sha, since=since
        )
    except QueryError:
        log("post-merge monitor candidate query failed; state left unchanged")
        return EXIT_QUERY_FAILED

    written = 0
    skipped = 0
    publish_failed = False
    for number in candidates:
        try:
            pr = fetch_pr(runner, repo, number)
        except QueryError:
            skipped += 1
            continue
        if not merged_main_pr(pr):
            skipped += 1
            continue
        merged_at = str(pr.get("merged_at"))
        if age_seconds(merged_at, now) < min_age_seconds:
            skipped += 1
            continue
        head_sha = str((pr.get("head") or {}).get("sha") or "")
        if not head_sha:
            skipped += 1
            continue
        try:
            comments = issue_comments(runner, repo, number)
        except QueryError:
            skipped += 1
            continue
        existing = existing_monitor_payload(comments, head_sha)
        if existing and monitor_state_is_final(str(existing.get("ci_required"))):
            skipped += 1
            continue

        try:
            record = collect_pr_evidence(runner, repo, pr, include_merge_push_runs=True)
        except QueryError:
            skipped += 1
            continue
        body = render_monitor_comment(record, now)
        try:
            if existing and isinstance(existing.get("_comment_id"), int):
                update_comment(runner, repo, int(existing["_comment_id"]), body)
            else:
                post_comment(runner, repo, number, body)
        except (QueryError, PublishError):
            publish_failed = True
            continue
        written += 1
        if (
            record.get("ci_required") == "ABSENT"
            and record.get("head_check_runs", {}).get("total_count") == 0
        ):
            try:
                incident = collect_incident_evidence(runner, repo, record)
                incident_number = publish_incident(
                    runner,
                    repo,
                    record,
                    incident,
                    server=server,
                    audit_run_id=run_id,
                )
                log(f"zero-check incident issue: #{incident_number}")
            except (QueryError, PublishError):
                publish_failed = True

    log(f"post-merge monitor: recorded={written} skipped={skipped}")
    if publish_failed:
        return EXIT_PUBLISH_FAILED
    return EXIT_OK


def merged_age_hours(record: dict, now: dt.datetime) -> float:
    merged_at = record.get("merged_at")
    if not merged_at:
        return 0.0
    return (now - parse_time(str(merged_at))).total_seconds() / 3600.0


def monitor_state_for_record(comments: Iterable[dict], head_sha: str) -> str | None:
    payload = existing_monitor_payload(comments, head_sha)
    if not payload:
        return None
    state = payload.get("ci_required")
    return str(state) if state else None


def head_evidence_age_hours(record: dict, now: dt.datetime) -> float:
    """Age of the PR head evidence, not merely of the merge event.

    Retention starts when the head commit's checks/runs are created.  A PR head
    can be much older than its merge time, so merged_at alone would misclassify
    expired evidence as a fresh zero-check merge.
    """

    commit = record.get("head_commit") if isinstance(record.get("head_commit"), dict) else {}
    candidates = []
    for stamp in (commit.get("committed_at"), record.get("created_at")):
        if stamp:
            candidates.append(parse_time(str(stamp)))
    if candidates:
        # A commit can predate the PR by days, but the pull_request event that
        # should have created ci-required cannot predate the PR opening.
        evidence_time = max(candidates)
    elif record.get("merged_at"):
        evidence_time = parse_time(str(record["merged_at"]))
    else:
        return 0.0
    return (now - evidence_time).total_seconds() / 3600.0


def classify_record(record: dict, now: dt.datetime) -> str:
    """Classify zero-check evidence without confusing retention with event loss."""

    if record.get("evidence_errors"):
        return "evidence-unreadable"
    total = record.get("head_check_runs", {}).get("total_count")
    if isinstance(total, int) and total > 0:
        return "checks-present"
    if total != 0:
        return "evidence-unreadable"
    if head_evidence_age_hours(record, now) >= 24.0:
        if record.get("post_merge_ci_required") == "SUCCESS":
            return "evidence-retention-possible-monitor-success"
        return "evidence-retention-possible"
    return "zero-check-merge"


def add_monitor_states(runner: Runner, repo: str, records: list[dict]) -> None:
    for record in records:
        number = record.get("number")
        head_sha = str(record.get("head_sha") or "")
        if not isinstance(number, int) or not head_sha:
            continue
        try:
            comments = issue_comments(runner, repo, number)
        except QueryError:
            continue
        record["post_merge_ci_required"] = monitor_state_for_record(comments, head_sha)


def compact_for_evidence_log(records: list[dict]) -> list[dict]:
    compacted = []
    for record in records:
        item = dict(record)
        # Keep every required workflow field, but bound accidental API fan-out.
        for key in ("pull_request_workflow_runs", "merge_push_workflow_runs"):
            group = dict(item.get(key) or {})
            group["runs"] = list(group.get("runs") or [])[:30]
            item[key] = group
        checks = dict(item.get("head_check_runs") or {})
        checks["check_runs"] = list(checks.get("check_runs") or [])[:30]
        item["head_check_runs"] = checks
        compacted.append(item)
    return compacted


def evidence_document(
    records: list[dict], *, repo: str, server: str, run_id: str, now: dt.datetime
) -> dict:
    return {
        "schema_version": 1,
        "kind": "merged-pr-daily-audit-evidence",
        "repo": repo,
        "generated_at": iso_z(now),
        "audit_run_url": f"{server}/{repo}/actions/runs/{run_id}",
        "prs": compact_for_evidence_log(records),
    }


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def render_evidence_comment(document: dict) -> str:
    counts: dict[str, int] = {}
    for record in document["prs"]:
        classification = str(record.get("classification") or "unclassified")
        counts[classification] = counts.get(classification, 0) + 1
    count_text = ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "no merged PRs"
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "\n".join(
        [
            f"## merged-pr-daily-audit evidence `{document['generated_at']}`",
            "",
            f"audit run: {document['audit_run_url']}",
            f"classifications: {count_text}",
            "",
            "machine-readable evidence (workflow run id / event / head_sha / created_at / conclusion を含む):",
            "",
            "```json",
            payload,
            "```",
        ]
    )


def chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def find_open_issue(runner: Runner, repo: str, title: str) -> int | None:
    out = runner(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"{title}" in:title',
            "--limit",
            "10",
            "--json",
            "number,title",
            "--jq",
            f".[] | select(.title == {json.dumps(title)}) | .number",
        ]
    )
    for line in out.splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def create_issue(runner: Runner, repo: str, title: str, body: str) -> int:
    out = runner(["issue", "create", "--repo", repo, "--title", title, "--body", body])
    match = re.search(r"/issues/(\d+)", out)
    if not match:
        raise PublishError("issue create did not return an issue URL")
    return int(match.group(1))


def ensure_issue(runner: Runner, repo: str, title: str, body: str) -> int:
    number = find_open_issue(runner, repo, title)
    if number is not None:
        return number
    return create_issue(runner, repo, title, body)


def publish_evidence_log(
    runner: Runner,
    repo: str,
    document: dict,
) -> int:
    """Persist the daily evidence in a rolling issue beyond Actions retention."""

    issue_body = "\n".join(
        [
            "merged-pr-daily-audit の恒久 evidence log。",
            "",
            "GitHub Actions/check の retention が短くても、日次監査時点の workflow run id / event / head_sha / created_at / conclusion と post-merge monitor 状態をこの issue のコメントへ保存します。",
            "この issue は監査証跡専用で、通常運用の会話には使いません。",
        ]
    )
    number = ensure_issue(runner, repo, EVIDENCE_ISSUE_TITLE, issue_body)
    body = render_evidence_comment(document)
    parts = chunks(body, MAX_COMMENT_CHARS)
    for index, part in enumerate(parts, 1):
        if len(parts) > 1:
            part = part.replace(
                "## merged-pr-daily-audit evidence",
                f"## merged-pr-daily-audit evidence part {index}/{len(parts)}",
                1,
            )
        post_comment(runner, repo, number, part)
    return number


def webhook_delivery_evidence(
    runner: Runner,
    repo: str,
    *,
    head_sha: str,
    merge_commit_sha: str,
) -> dict:
    """Best-effort webhook delivery capture, bounded and free of payloads."""

    result = {
        "status": "ok",
        "matched": [],
        "inspected_delivery_count": 0,
        "note": "payload bodies are intentionally not copied into the issue",
    }
    try:
        hooks = api_json(runner, f"repos/{repo}/hooks?per_page=100")
    except QueryError:
        result["status"] = "unavailable"
        result["error"] = "repository hooks query failed with the workflow token"
        return result
    if not isinstance(hooks, list):
        result["status"] = "unavailable"
        result["error"] = "unexpected hooks shape"
        return result

    needles = {sha for sha in (head_sha, merge_commit_sha) if sha}
    inspected = 0
    matched = []
    try:
        for hook in hooks[:MAX_WEBHOOK_HOOKS]:
            if not isinstance(hook, dict) or not isinstance(hook.get("id"), int):
                continue
            hook_id = hook["id"]
            deliveries = api_json(runner, f"repos/{repo}/hooks/{hook_id}/deliveries?per_page=100")
            if not isinstance(deliveries, list):
                continue
            for delivery in deliveries[:MAX_WEBHOOK_DELIVERIES_PER_HOOK]:
                if not isinstance(delivery, dict) or not isinstance(delivery.get("id"), int):
                    continue
                detail = api_json(runner, f"repos/{repo}/hooks/{hook_id}/deliveries/{delivery['id']}")
                inspected += 1
                if not isinstance(detail, dict):
                    continue
                request = detail.get("request") if isinstance(detail.get("request"), dict) else {}
                payload = request.get("payload")
                encoded = json.dumps(payload, sort_keys=True) if payload is not None else ""
                if any(needle in encoded for needle in needles):
                    matched.append(
                        {
                            "hook_id": hook_id,
                            "delivery_id": delivery["id"],
                            "event": delivery.get("event"),
                            "status": delivery.get("status"),
                            "status_code": delivery.get("status_code"),
                            "delivered_at": delivery.get("delivered_at"),
                            "redelivery": delivery.get("redelivery"),
                        }
                    )
    except QueryError:
        result["status"] = "partial"
        result["error"] = "hook delivery query failed with the workflow token"
    result["matched"] = matched
    result["inspected_delivery_count"] = inspected
    return result


def collect_incident_evidence(runner: Runner, repo: str, record: dict) -> dict:
    """Immediately re-collect the evidence required for a zero-check merge."""

    head_sha = str(record.get("head_sha") or "")
    merge_sha = str(record.get("merge_commit_sha") or "")
    incident = {
        "head_sha": head_sha,
        "merge_commit_sha": merge_sha,
        "collected_at": iso_z(now_utc()),
    }
    for key, collector in (
        ("check_runs", lambda: check_run_evidence(check_runs(runner, repo, head_sha))),
        ("check_suites", lambda: check_suite_evidence(check_suites(runner, repo, head_sha))),
        (
            "pull_request_workflow_runs",
            lambda: workflow_evidence(workflow_runs(runner, repo, head_sha, "pull_request")),
        ),
    ):
        try:
            incident[key] = collector()
        except QueryError:
            incident[key] = {"total_count": None, "error": "query_failed"}
    incident["webhook_deliveries"] = webhook_delivery_evidence(
        runner, repo, head_sha=head_sha, merge_commit_sha=merge_sha
    )
    return incident


def workflow_run_ids(evidence: dict) -> list[int]:
    ids = []
    for run in evidence.get("runs") or []:
        if isinstance(run, dict) and isinstance(run.get("id"), int):
            ids.append(run["id"])
    return ids


def incident_title(number: int) -> str:
    return f"{INCIDENT_TITLE_PREFIX}{number}{INCIDENT_TITLE_SUFFIX}"


def render_incident_body(
    record: dict,
    incident: dict,
    *,
    repo: str,
    server: str,
    audit_run_id: str,
) -> str:
    pr_number = record.get("number")
    head_sha = str(record.get("head_sha") or "")
    merge_sha = str(record.get("merge_commit_sha") or "")
    run_ids = workflow_run_ids(incident.get("pull_request_workflow_runs") or {})
    run_id_text = ", ".join(str(run_id) for run_id in run_ids) if run_ids else "なし"
    webhook = incident.get("webhook_deliveries") or {}
    webhook_lines = [
        "| {hook_id} | {delivery_id} | {event} | {status} | {status_code} | {delivered_at} |".format(**item)
        for item in webhook.get("matched") or []
    ] or ["| — | — | — | — | — | — |"]
    commands = "\n".join(
        [
            f"gh api repos/{repo}/commits/{head_sha}/check-runs",
            f"gh api repos/{repo}/commits/{head_sha}/check-suites",
            f"gh api 'repos/{repo}/actions/runs?event=pull_request&head_sha={head_sha}&per_page=100'",
            f"gh api repos/{repo}/hooks --jq '.[].id'",
            f"gh api repos/{repo}/hooks/<hook_id>/deliveries",
            f"gh api repos/{repo}/hooks/<hook_id>/deliveries/<delivery_id>",
        ]
    )
    webhook_note = webhook.get("error") or "matched deliveries are listed below"
    return "\n".join(
        [
            f"PR #{pr_number} が head `{head_sha}` の check-runs total_count=0 のまま main へマージされました。",
            "",
            "## 即時採取した証拠",
            "",
            f"- merged_at: `{record.get('merged_at')}`",
            f"- merge_commit_sha: `{merge_sha}`",
            f"- commits/<sha>/check-runs total_count: `{(incident.get('check_runs') or {}).get('total_count')}`",
            f"- commits/<sha>/check-suites total_count: `{(incident.get('check_suites') or {}).get('total_count')}`",
            f"- actions/runs?event=pull_request total_count: `{(incident.get('pull_request_workflow_runs') or {}).get('total_count')}`",
            f"- workflow run id: {run_id_text}",
            f"- detector run: {server}/{repo}/actions/runs/{audit_run_id}",
            "",
            "## workflow runs（保存対象）",
            "",
            "| run id | event | head_sha | created_at | conclusion |",
            "|---:|---|---|---|---|",
            *run_lines((incident.get("pull_request_workflow_runs") or {}).get("runs") or []),
            "",
            "## webhook delivery 採取",
            "",
            f"status: `{webhook.get('status')}` / note: {webhook_note}",
            "",
            "| hook id | delivery id | event | status | status_code | delivered_at |",
            "|---:|---:|---|---|---:|---|",
            *webhook_lines,
            "",
            "workflow token で hooks/deliveries を読めない場合は、owner 権限で次を即時実行してください。payload 本文ではなく delivery id と status を残します。",
            "",
            "```sh",
            commands,
            "```",
            "",
            "## 判定メモ",
            "",
            "24時間未満の 0 件は `zero-check-merge` として扱います。24時間以上前の 0 件は event 欠落と断定せず、`証跡期限切れの可能性` に分類します。ruleset bypass の設定変更はこの監査のスコープ外です。",
            "",
            "Refs #5717 #5800",
        ]
    )


def publish_incident(
    runner: Runner,
    repo: str,
    record: dict,
    incident: dict,
    *,
    server: str,
    audit_run_id: str,
) -> int:
    title = incident_title(int(record["number"]))
    body = render_incident_body(record, incident, repo=repo, server=server, audit_run_id=audit_run_id)
    existing = find_open_issue(runner, repo, title)
    if existing is None:
        return create_issue(runner, repo, title, body)
    runner(["issue", "edit", str(existing), "--repo", repo, "--body", body])
    return existing


def audit(
    runner: Runner,
    *,
    repo: str,
    server: str,
    run_id: str,
    output: Path,
    now: dt.datetime | None = None,
    hours: float = DEFAULT_AUDIT_HOURS,
    publish: bool = True,
    log: Callable[[str], None] = print,
) -> int:
    """Run the daily merged-PR evidence audit."""

    now = now or now_utc()
    since = now - dt.timedelta(hours=hours)
    try:
        numbers = search_merged_pr_numbers(runner, repo, since)
    except QueryError:
        log("merged PR search failed; no audit state changed")
        return EXIT_QUERY_FAILED

    records: list[dict] = []
    for number in numbers:
        try:
            pr = fetch_pr(runner, repo, number)
        except QueryError:
            continue
        if not merged_main_pr(pr):
            continue
        records.append(collect_pr_evidence(runner, repo, pr, include_merge_push_runs=True))

    add_monitor_states(runner, repo, records)
    for record in records:
        record["classification"] = classify_record(record, now)
        record["classification_label"] = CLASSIFICATION_LABELS.get(
            record["classification"], record["classification"]
        )

    document = evidence_document(records, repo=repo, server=server, run_id=run_id, now=now)
    write_json(output, document)

    counts: dict[str, int] = {}
    for record in records:
        counts[record["classification"]] = counts.get(record["classification"], 0) + 1
    log("classification counts: " + json.dumps(counts, sort_keys=True))

    if not publish:
        return EXIT_OK

    try:
        evidence_issue = publish_evidence_log(runner, repo, document)
        log(f"evidence issue: #{evidence_issue}")
        incidents = []
        for record in records:
            if record.get("classification") != "zero-check-merge":
                continue
            incident = collect_incident_evidence(runner, repo, record)
            incidents.append(
                publish_incident(
                    runner,
                    repo,
                    record,
                    incident,
                    server=server,
                    audit_run_id=run_id,
                )
            )
        if incidents:
            log("incident issues: " + ",".join(f"#{number}" for number in incidents))
    except (QueryError, PublishError):
        log("audit evidence publish failed")
        return EXIT_PUBLISH_FAILED
    return EXIT_OK


def env_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    return int(value) if value.isdigit() else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    monitor_parser = subparsers.add_parser("monitor")
    monitor_parser.add_argument("--pr-number", type=int, default=env_int("PR_NUMBER"))
    monitor_parser.add_argument("--push-sha", default=os.environ.get("PUSH_SHA") or None)
    monitor_parser.add_argument("--lookback-hours", type=float, default=DEFAULT_MONITOR_LOOKBACK_HOURS)
    monitor_parser.add_argument("--min-age-seconds", type=int, default=DEFAULT_MONITOR_MIN_AGE_SECONDS)

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--hours", type=float, default=DEFAULT_AUDIT_HOURS)
    audit_parser.add_argument("--output", default=os.environ.get("AUDIT_OUTPUT", "merged-pr-audit-evidence.json"))
    audit_parser.add_argument("--no-publish", action="store_true")

    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "azumag/docich")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "0")

    if args.command == "monitor":
        return monitor(
            gh_runner,
            repo=repo,
            explicit_pr=args.pr_number,
            push_sha=args.push_sha,
            lookback_hours=args.lookback_hours,
            min_age_seconds=args.min_age_seconds,
            server=server,
            run_id=run_id,
        )
    return audit(
        gh_runner,
        repo=repo,
        server=server,
        run_id=run_id,
        output=Path(args.output),
        hours=args.hours,
        publish=not args.no_publish,
    )


if __name__ == "__main__":
    sys.exit(main())
