#!/usr/bin/env python3
"""Alert when a production deploy stays blocked by a reconcile failure.

``vm-operations.yml`` is the only path that advances the production VM, so when
its push deploy fails and the follow-up reconcile cannot converge the checkout,
``main`` stops reaching production. The 2026-10-17 incident (docich#2482) sat in
that state for five consecutive deploys (~5 days) because nothing reported the
failure as an incident: the storage monitor's baseline / tracked-drift alerts
describe the *VM* state, not a *blocked deploy*, and the failure only lived in
the workflow run's red X.

This detector therefore reads the recent ``vm-operations.yml`` push runs on
``main``, classifies the failure through the owner-only gateway's fixed reason
enum (``reconcile_refused`` and friends), and keeps exactly one deduplicated
Issue open while production is blocked. It closes that Issue as soon as a later
main push deploy succeeds. A query failure fails closed: the existing alert
state is left untouched instead of being closed on unknown data.

Only fixed fields are published: the reason code from the gateway's enum, the
run id / URL / head SHA / created_at, the detector run URL and the lookback
window. Raw log lines, VM paths, credentials and request bodies never reach the
Issue.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from typing import Callable, Sequence

ALERT_TITLE = "[VM deploy] production deploy blocked"
DEFAULT_WORKFLOW = "vm-operations.yml"
DEFAULT_LOOKBACK_MINUTES = 90

# Fixed reason enum: the gateway's REASON_CODES for the deploy / reconcile
# operations. The detector may only publish one of these strings (or the
# generic fallback), never free-form log text.
REASON_CODES = (
    "reconcile_refused",
    "reconcile_fetch_failed",
    "reconcile_helper_missing",
    "reconcile_object_missing",
    "reconcile_invalid_request",
    "reconcile_production_only",
    "projected_submodule_not_at_new_gitlink",
    "projected_submodule_missing_in_parent",
    "operation_rejected",
)
FALLBACK_REASON = "deploy_failed"

# Fixed aliases: the gateway logs the human message for some refusals while the
# workflow surface prints the reason code. Both are fixed strings, never
# free-form text, so they can be mapped without risk of publishing log content.
REASON_ALIASES = (
    ("presynced reconcile fetch failed", "reconcile_fetch_failed"),
    ("presynced reconcile refused", "reconcile_refused"),
)

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
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise QueryError("gh query failed")
    return completed.stdout


def parse_created_at(value: str) -> dt.datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def recent_runs(runner: Runner, repo: str, workflow: str, limit: int = 20) -> list[dict]:
    """Recent main push runs of the deploy workflow, newest first."""

    out = runner(
        [
            "api",
            f"repos/{repo}/actions/workflows/{workflow}/runs"
            f"?branch=main&event=push&per_page={limit}",
            "--jq",
            ".workflow_runs[] | "
            "{id: .id, created_at: .created_at, conclusion: .conclusion, "
            "head_sha: .head_sha, url: .html_url}",
        ]
    )
    runs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            raise QueryError("unexpected run list shape") from exc
        if not isinstance(item, dict) or "id" not in item or "created_at" not in item:
            raise QueryError("unexpected run list shape")
        runs.append(item)
    runs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return runs


def select_blocking_failure(
    runs: Sequence[dict], now: dt.datetime, lookback_minutes: int
) -> dict | None:
    """The newest failed run inside the window, or ``None``.

    A newer successful run supersedes the failure: production recovered, so the
    failure is history rather than a current blocker.
    """

    cutoff = now - dt.timedelta(minutes=lookback_minutes)
    for run in runs:
        created_raw = str(run.get("created_at") or "")
        try:
            created = parse_created_at(created_raw)
        except ValueError:
            continue
        if created < cutoff:
            return None
        if str(run.get("conclusion") or "") == "success":
            return None
        if str(run.get("conclusion") or "") in {"failure", "timed_out", "startup_failure"}:
            return run
    return None


def classify_reason(failed_log: str) -> str:
    """Map failed-step log text onto the gateway's fixed reason enum.

    The gateway prints ``VM operation rejected: <reason_code>`` on refusal; the
    workflow also greps the raw message. Match the allowlisted codes only, so no
    free-form log content can be published.
    """

    for message, code in REASON_ALIASES:
        if message in failed_log:
            return code
    for code in REASON_CODES:
        if code in failed_log:
            return code
    return FALLBACK_REASON


def failure_reason(runner: Runner, repo: str, run_id: object) -> str:
    """Classify one failed run from its failed-step log (bounded, allowlisted)."""

    try:
        text = runner(["run", "view", str(run_id), "--repo", repo, "--log-failed"])
    except (QueryError, subprocess.SubprocessError):
        # Log access can be unavailable (retention / permissions). Still alert,
        # but without claiming a specific reason code.
        return FALLBACK_REASON
    return classify_reason(text)


def open_alert_number(runner: Runner, repo: str) -> int | None:
    """The number of the open deduplicated deploy blocker Issue, or ``None``."""

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


def render_body(
    *,
    repo: str,
    server: str,
    run: dict,
    reason: str,
    lookback_minutes: int,
    run_id: str,
    workflow: str = DEFAULT_WORKFLOW,
) -> str:
    return "\n".join(
        (
            "`main` の production deploy が失敗したままです。",
            "",
            f"- reason: `{reason}`",
            f"- deploy run: {run.get('url') or '(unknown)'}",
            f"- head SHA: {run.get('head_sha') or '(unknown)'}",
            f"- created_at (UTC): {run.get('created_at') or '(unknown)'}",
            f"- workflow: {server}/{repo}/actions/workflows/{workflow}",
            f"- lookback window (minutes): {lookback_minutes}",
            f"- detector run: {server}/{repo}/actions/runs/{run_id}",
            "",
            "deploy が失敗している間、それ以降の main は本番へ届きません。",
            "reconcile 系の reason は「VM checkout の live bytes が reviewed lineage と",
            "一致しない」ことを示すため、docich#2482 と同じ手順（差分特定 → PR で",
            "byte-exact upstream または reviewed bytes へ収束 → gitlink advance）で解消します。",
            "詳細は soviet_now の `docs/projection_guard_runbook.md` を参照してください。",
            "その後の main push deploy が成功すると、この Issue は自動で close されます。",
        )
    )


def run(
    runner: Runner,
    *,
    repo: str,
    server: str,
    run_id: str,
    workflow: str = DEFAULT_WORKFLOW,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    now: dt.datetime | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Keep the deduplicated deploy blocker alert consistent."""

    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        runs = recent_runs(runner, repo, workflow)
        alert = open_alert_number(runner, repo)
    except QueryError:
        log("deploy blocker check unavailable; alert state left unchanged")
        return EXIT_QUERY_FAILED

    failure = select_blocking_failure(runs, now, lookback_minutes)
    if failure is None:
        if alert is None:
            log("no blocked deploy; no alert open")
            return EXIT_OK
        try:
            runner(
                [
                    "issue",
                    "close",
                    str(alert),
                    "--repo",
                    repo,
                    "--comment",
                    "Production deploy recovered: a newer main push deploy succeeded.",
                ]
            )
        except (QueryError, subprocess.SubprocessError):
            log("deploy blocker alert could not be closed")
            return EXIT_PUBLISH_FAILED
        log("deploy recovered; closed the blocker alert")
        return EXIT_OK

    reason = failure_reason(runner, repo, failure.get("id"))
    body = render_body(
        repo=repo,
        server=server,
        run=failure,
        reason=reason,
        lookback_minutes=lookback_minutes,
        run_id=run_id,
    )
    try:
        if alert is not None:
            runner(["issue", "edit", str(alert), "--repo", repo, "--body", body])
            log(f"deploy blocker alert refreshed (reason={reason})")
        else:
            runner(
                ["issue", "create", "--repo", repo, "--title", ALERT_TITLE, "--body", body]
            )
            log(f"deploy blocker alert opened (reason={reason})")
        return EXIT_OK
    except (QueryError, subprocess.SubprocessError):
        log("deploy blocker alert could not be published")
        return EXIT_PUBLISH_FAILED


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    workflow = os.environ.get("RECONCILE_ALERT_WORKFLOW") or DEFAULT_WORKFLOW
    if not repo or "/" not in repo:
        print("deploy blocker check requires GITHUB_REPOSITORY", file=sys.stderr)
        return EXIT_QUERY_FAILED
    return run(
        gh_runner,
        repo=repo,
        server=server,
        run_id=run_id or "unknown",
        workflow=workflow,
        lookback_minutes=_int_env(
            "RECONCILE_ALERT_LOOKBACK_MINUTES", DEFAULT_LOOKBACK_MINUTES
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
