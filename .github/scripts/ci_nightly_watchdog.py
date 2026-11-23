#!/usr/bin/env python3
"""Keep the nightly full-suite coverage on main when scheduled events are dropped.

ci.yml declares a daily schedule cron, but GitHub can delay or drop scheduled
workflow runs; in this repository every scheduled workflow stopped receiving
events between 2026-10-30 and 2026-11-22. That nightly run is the only full
suite for Hanjuku-only pull requests, so its silence hides whole-repository
regressions.

This detector also runs on pushes to main and on manual dispatch, so a dropped
schedule cannot silence it. A successful ci.yml run with the schedule or
workflow_dispatch event on main within the threshold counts as a healthy
nightly. When none exists the detector opens (or refreshes) one deduplicated
Issue and dispatches one full-suite run on main, unless a dispatch was already
attempted in the window. A fresh successful run closes the Issue again.

A query failure fails closed: no Issue state changes and no dispatch happens.
Only fixed fields are published: run timestamps, ages, and workflow/run URLs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from typing import Callable, Sequence

ALERT_TITLE = "[CI] nightly full-suite missing"
DEFAULT_WORKFLOW = "ci.yml"
DEFAULT_THRESHOLD_HOURS = 30.0
IN_FLIGHT_STATUSES = {"queued", "pending", "in_progress", "waiting"}

EXIT_OK = 0
EXIT_QUERY_FAILED = 2
EXIT_PUBLISH_FAILED = 3

Runner = Callable[[Sequence[str]], str]


class QueryError(RuntimeError):
    """A bounded ``gh`` query failed; no alert or dispatch state may change."""


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


def recent_runs(runner: Runner, repo: str, workflow: str) -> list[dict[str, str]]:
    """Main-branch schedule/dispatch runs of the workflow, newest first."""

    out = runner(
        [
            "api",
            f"repos/{repo}/actions/workflows/{workflow}/runs?branch=main&per_page=100",
            "--jq",
            '.workflow_runs[] | select(.event == "schedule" or .event == "workflow_dispatch")'
            ' | [.event, .created_at, .status, .conclusion] | @tsv',
        ]
    )
    runs = []
    for line in out.splitlines():
        fields = line.strip().split("\t")
        if len(fields) != 4:
            raise QueryError("unexpected workflow run list shape")
        event, created_at, status, conclusion = fields
        runs.append(
            {
                "event": event,
                "created_at": created_at,
                "status": status,
                "conclusion": conclusion,
            }
        )
    return runs


def open_alert_number(runner: Runner, repo: str) -> int | None:
    """The number of the open deduplicated nightly alert, or ``None``."""

    out = runner(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"{ALERT_TITLE}" in:title',
            "--limit",
            "10",
            "--json",
            "number,title",
            "--jq",
            f".[] | select(.title == {json.dumps(ALERT_TITLE)}) | .number",
        ]
    )
    for line in out.splitlines():
        line = line.strip()
        if line:
            if not line.isdigit():
                raise QueryError("unexpected alert issue list shape")
            return int(line)
    return None


def parse_created_at(value: str) -> dt.datetime:
    """Parse GitHub's ``YYYY-MM-DDTHH:MM:SSZ`` timestamp."""

    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def hours_since(created: dt.datetime, now: dt.datetime) -> float:
    return (now - created).total_seconds() / 3600.0


def classify(
    runs: list[dict[str, str]], now: dt.datetime, threshold_hours: float
) -> tuple[str, str | None, float | None]:
    """Return (fresh | in-flight | missing, newest success timestamp, age)."""

    successes: list[tuple[dt.datetime, float]] = []
    in_flight = False
    for run in runs:
        created = parse_created_at(run["created_at"])
        age = hours_since(created, now)
        # A run that has remained queued/running beyond the freshness window
        # must not suppress the missing-nightly alert forever.
        if run["status"] in IN_FLIGHT_STATUSES and age < threshold_hours:
            in_flight = True
        elif run["status"] == "completed" and run["conclusion"] == "success":
            successes.append((created, age))
    if successes:
        created, age = max(successes, key=lambda item: item[0])
        if age < threshold_hours:
            return "fresh", created.strftime("%Y-%m-%dT%H:%M:%SZ"), age
    if in_flight:
        return "in-flight", None, None
    return "missing", None, None


def recent_dispatch(
    runs: list[dict[str, str]], now: dt.datetime, threshold_hours: float
) -> bool:
    """Whether a full-suite dispatch was already attempted in the window."""

    for run in runs:
        if run["event"] == "workflow_dispatch":
            if hours_since(parse_created_at(run["created_at"]), now) < threshold_hours:
                return True
    return False


def decide(state: str, alert_open: bool) -> str:
    """Resolve the next action: ok, recover or alert."""

    if state == "fresh":
        return "recover" if alert_open else "ok"
    if state == "in-flight":
        return "ok"
    return "alert"


def dispatch_full_suite(runner: Runner, repo: str, workflow: str) -> None:
    runner(
        [
            "api",
            "-X",
            "POST",
            f"repos/{repo}/actions/workflows/{workflow}/dispatches",
            "-f",
            "ref=main",
        ]
    )


def render_body(
    *,
    repo: str,
    server: str,
    run_id: str,
    created: str | None,
    age_hours: float | None,
    threshold_hours: float,
) -> str:
    observed = created or "(no successful nightly observed)"
    age = f"{age_hours:.1f}" if age_hours is not None else "unknown"
    return "\n".join(
        (
            "ci.yml の夜間フルスイート（schedule cron 18:23 UTC）が閾値内に成功 run を持ちません。",
            "",
            f"- Last successful nightly (UTC): {observed}",
            f"- Age (hours): {age} (threshold {threshold_hours:g})",
            f"- Workflow: {server}/{repo}/actions/workflows/ci.yml",
            f"- Detector run: {server}/{repo}/actions/runs/{run_id}",
            "",
            "GitHub は schedule イベントを遅延・ドロップすることがあるため、この検出は main への push と手動実行でも動きます。",
            "欠損を検出すると main にフルスイートを 1 回 dispatch します。同じ窗口で既に dispatch 済みなら再送しません。",
            "成功 run が閾値内に戻ると、この Issue は自動で close されます。",
        )
    )


def run(
    runner: Runner,
    *,
    repo: str,
    server: str,
    run_id: str,
    workflow: str = DEFAULT_WORKFLOW,
    threshold_hours: float = DEFAULT_THRESHOLD_HOURS,
    now: dt.datetime | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Reconcile nightly coverage with the deduplicated alert Issue."""

    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        runs = recent_runs(runner, repo, workflow)
        alert = open_alert_number(runner, repo)
        state, created, age = classify(runs, now, threshold_hours)
        dispatched = recent_dispatch(runs, now, threshold_hours)
    except (QueryError, ValueError):
        log("nightly check unavailable; alert state left unchanged")
        return EXIT_QUERY_FAILED

    action = decide(state, alert is not None)
    if action == "ok":
        log(f"nightly status {state}; no alert change")
        return EXIT_OK

    try:
        if action == "recover":
            runner(
                [
                    "issue",
                    "close",
                    str(alert),
                    "--repo",
                    repo,
                    "--comment",
                    "Nightly full suite recovered: a successful run is within the threshold.",
                ]
            )
            log("nightly recovered; closed the alert")
            return EXIT_OK

        body = render_body(
            repo=repo,
            server=server,
            run_id=run_id,
            created=created,
            age_hours=age,
            threshold_hours=threshold_hours,
        )
        if alert is not None:
            runner(["issue", "edit", str(alert), "--repo", repo, "--body", body])
            log("nightly alert refreshed")
        else:
            runner(
                ["issue", "create", "--repo", repo, "--title", ALERT_TITLE, "--body", body]
            )
            log("nightly alert opened")

        if dispatched:
            log("a dispatch was already attempted in the window; not sending another")
        else:
            dispatch_full_suite(runner, repo, workflow)
            log("full-suite dispatch requested on main")
        return EXIT_OK
    except QueryError:
        log("nightly alert could not be published")
        return EXIT_PUBLISH_FAILED


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not repo or "/" not in repo:
        print("nightly watchdog requires GITHUB_REPOSITORY", file=sys.stderr)
        return EXIT_QUERY_FAILED
    return run(
        gh_runner,
        repo=repo,
        server=server,
        run_id=run_id or "unknown",
    )


if __name__ == "__main__":
    raise SystemExit(main())
