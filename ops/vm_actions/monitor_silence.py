#!/usr/bin/env python3
"""Alert when the VM storage monitor stops producing successful runs.

``vm-storage-monitor.yml`` is the only producer of the VM storage / runtime
health alerts, so when its scheduled runs stop succeeding nothing reports that
outage. The monitor cannot alert about its own silence, and the external
Cloudflare watchdog (``ops/vm_monitor_watchdog``) fails closed and silently when
GitHub is unreachable or its own token is no longer valid.

This detector therefore runs as its own fixed workflow with a different failure
mode from both:

* its own concurrency group, so a jammed monitor queue cannot block it;
* the run-scoped ``GITHUB_TOKEN`` instead of the watchdog's personal token, so
  an expired watchdog credential cannot blind it.

It is quiet by design. While the monitor has had no successful run for longer
than the threshold it keeps exactly one deduplicated Issue open (created, then
refreshed with the current age), and it closes that Issue as soon as a
successful run is within the threshold again. A query failure fails closed: the
existing alert state is left untouched instead of being closed on unknown data.

Only fixed fields are published: run timestamps, ages, the monitor workflow URL
and the detector run URL. No paths, credentials, logs or raw API bodies.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from typing import Callable, Sequence

ALERT_TITLE = "[VM monitor] silence (no successful run)"
DEFAULT_WORKFLOW = "vm-storage-monitor.yml"
DEFAULT_THRESHOLD_HOURS = 6.0

# Exit codes: 0 acted or nothing to do, 2 could not query (state untouched),
# 3 could not publish the alert.
EXIT_OK = 0
EXIT_QUERY_FAILED = 2
EXIT_PUBLISH_FAILED = 3

Runner = Callable[[Sequence[str]], str]


class QueryError(RuntimeError):
    """A bounded ``gh`` query failed; no alert state may change."""


class PublishError(RuntimeError):
    """A bounded ``gh`` mutation failed."""


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


def latest_success_created_at(runner: Runner, repo: str, workflow: str) -> str | None:
    """The creation time of the newest successful run, or ``None``."""

    out = runner(
        [
            "api",
            f"repos/{repo}/actions/workflows/{workflow}/runs?status=success&per_page=1",
            "--jq",
            ".workflow_runs[0].created_at // empty",
        ]
    )
    value = out.strip()
    return value or None


def open_alert_number(runner: Runner, repo: str) -> int | None:
    """The number of the open deduplicated silence Issue, or ``None``.

    The lookup is a bounded title search, not a scan of every open issue, so a
    busy repository cannot push the alert outside the window.
    """

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

    text = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def hours_since(created: dt.datetime, now: dt.datetime) -> float:
    return (now - created).total_seconds() / 3600.0


def decide(age_hours: float | None, threshold_hours: float, alert_open: bool) -> str:
    """``ok`` (nothing to do), ``recover`` (close the alert) or ``alert``."""

    if age_hours is None or age_hours >= threshold_hours:
        return "alert"
    return "recover" if alert_open else "ok"


def render_body(
    *,
    repo: str,
    server: str,
    run_id: str,
    created: str | None,
    age_hours: float | None,
    threshold_hours: float,
    workflow: str = DEFAULT_WORKFLOW,
) -> str:
    if created is None or age_hours is None:
        observed = "(no successful run observed)"
        age = "unknown"
    else:
        observed = created
        age = f"{age_hours:.1f}"
    return "\n".join(
        (
            f"`{workflow}` の成功 run が閾値内にありません。",
            "",
            f"- Last successful run (UTC): {observed}",
            f"- Age (hours): {age} (threshold {threshold_hours:g})",
            f"- Monitor workflow: {server}/{repo}/actions/workflows/{workflow}",
            f"- Detector run: {server}/{repo}/actions/runs/{run_id}",
            "",
            "監視が止まると通常のアラートも止まるため、この検知は monitor とは別の",
            "workflow・別の concurrency group・run 単位の `GITHUB_TOKEN` で独立に動作します。",
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
    """Resolve the monitor's freshness and keep the alert state consistent."""

    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        created = latest_success_created_at(runner, repo, workflow)
        alert = open_alert_number(runner, repo)
    except QueryError:
        # Unknown state must never close an existing alert.
        log("monitor silence check unavailable; alert state left unchanged")
        return EXIT_QUERY_FAILED

    age_hours = None
    if created is not None:
        try:
            age_hours = hours_since(parse_created_at(created), now)
        except ValueError:
            log("monitor silence check unavailable; alert state left unchanged")
            return EXIT_QUERY_FAILED

    action = decide(age_hours, threshold_hours, alert is not None)
    if action == "ok":
        log("monitor is fresh; no silence alert open")
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
                    "VM storage monitor recovered: a successful run is within the threshold.",
                ]
            )
            log("monitor recovered; closed the silence alert")
            return EXIT_OK
        body = render_body(
            repo=repo,
            server=server,
            run_id=run_id,
            created=created,
            age_hours=age_hours,
            threshold_hours=threshold_hours,
            workflow=workflow,
        )
        if alert is not None:
            runner(["issue", "edit", str(alert), "--repo", repo, "--body", body])
            log("monitor silence alert refreshed")
        else:
            runner(
                ["issue", "create", "--repo", repo, "--title", ALERT_TITLE, "--body", body]
            )
            log("monitor silence alert opened")
        return EXIT_OK
    except (QueryError, subprocess.SubprocessError):
        log("monitor silence alert could not be published")
        return EXIT_PUBLISH_FAILED


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not repo or "/" not in repo:
        print("monitor silence check requires GITHUB_REPOSITORY", file=sys.stderr)
        return EXIT_QUERY_FAILED
    return run(
        gh_runner,
        repo=repo,
        server=server,
        run_id=run_id or "unknown",
        workflow=DEFAULT_WORKFLOW,
        threshold_hours=DEFAULT_THRESHOLD_HOURS,
    )


if __name__ == "__main__":
    raise SystemExit(main())
