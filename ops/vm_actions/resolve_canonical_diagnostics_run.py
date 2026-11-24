#!/usr/bin/env python3
"""Resolve the canonical read-only diagnostics run without pinning a run id.

Public repository Actions run retention can delete a fixed run id (#1881).  The
canonical reference is therefore the newest completed ``workflow_dispatch`` run
of ``vm-operations.yml`` whose latest attempt is a read-only diagnostics run.

A candidate is accepted only when all of the following hold:

* run conclusion is ``success`` and the run was dispatched from ``main``;
* every job of the latest attempt succeeded and no step failed;
* ``Query read-only runtime diagnostics`` succeeded;
* ``Deploy staged code`` and ``Execute owner command on VM`` were skipped;
* the authorization log shows ``diagnostics / production / main`` with an empty
  confirmation; and
* the sanitized structured diagnostics output is present in the log.

Masked fragments in the structured output are emitted exactly as GitHub printed
them.  The helper never attempts to restore a mask.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


REPO = "azumag/docich"
WORKFLOW = "vm-operations.yml"
SCAN = 30
DIAGNOSTICS_STEP = "Query read-only runtime diagnostics"
DEPLOY_STEP = "Deploy staged code"
EXEC_STEP = "Execute owner command on VM"
BAD_STEP_CONCLUSIONS = {"failure", "cancelled", "timed_out"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_ID_RE = re.compile(r"^[0-9]+$")
TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\s?"
)
AUTHORIZATION_RE = (
    re.compile(r"^.*INPUT_OPERATION:\s*diagnostics\s*$", re.MULTILINE),
    re.compile(r"^.*INPUT_TARGET:\s*production\s*$", re.MULTILINE),
    re.compile(r"^.*INPUT_REF:\s*main\s*$", re.MULTILINE),
    re.compile(r"^.*INPUT_CONFIRM:\s*$", re.MULTILINE),
)
STRUCTURED_STATUS_RE = re.compile(r'"status"\s*:\s*"diagnosed"')


class ResolutionError(RuntimeError):
    """A bounded query failure that should not leak command output."""


def _gh_json(args: list[str], what: str) -> Any:
    try:
        result = subprocess.run(
            ["gh", *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if result.returncode != 0:
            raise ResolutionError(f"{what} failed")
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ResolutionError(f"{what} failed") from exc


def _gh_text(args: list[str], what: str) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if result.returncode != 0:
            raise ResolutionError(f"{what} failed")
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResolutionError(f"{what} failed") from exc


def _diagnostics_log_text(line: str) -> str | None:
    parts = line.split("\t", 2)
    if len(parts) != 3 or parts[1] != DIAGNOSTICS_STEP:
        return None
    text = parts[2].lstrip("\ufeff")
    return TIMESTAMP_RE.sub("", text, count=1).strip()


def extract_structured_output(log: str) -> str | None:
    """Return the sanitized diagnostics JSON line, without log prefixes."""
    for line in log.splitlines():
        text = _diagnostics_log_text(line)
        if text is not None and STRUCTURED_STATUS_RE.search(text):
            return text
    return None


def has_readonly_authorization(log: str) -> bool:
    """Whether the authorization log proves read-only diagnostics inputs."""
    return all(pattern.search(log) for pattern in AUTHORIZATION_RE)


def is_canonical(metadata: dict[str, Any]) -> bool:
    if metadata.get("conclusion") != "success":
        return False
    if metadata.get("event") != "workflow_dispatch":
        return False
    if metadata.get("headBranch") != "main":
        return False
    head_sha = metadata.get("headSha")
    if not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha):
        return False

    jobs = metadata.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return False

    saw_diagnostics = False
    saw_deploy_skipped = False
    saw_exec_skipped = False
    for job in jobs:
        if not isinstance(job, dict) or job.get("conclusion") != "success":
            return False
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            return False
        for step in steps:
            if not isinstance(step, dict):
                return False
            conclusion = step.get("conclusion")
            if conclusion in BAD_STEP_CONCLUSIONS:
                return False
            name = step.get("name")
            if name == DIAGNOSTICS_STEP and conclusion == "success":
                saw_diagnostics = True
            elif name == DEPLOY_STEP and conclusion == "skipped":
                saw_deploy_skipped = True
            elif name == EXEC_STEP and conclusion == "skipped":
                saw_exec_skipped = True

    return saw_diagnostics and saw_deploy_skipped and saw_exec_skipped


def resolve(repo: str, workflow: str, scan: int) -> dict[str, Any] | None:
    """Resolve the newest canonical diagnostics run, or return ``None``."""
    listed = _gh_json(
        [
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow,
            "--event",
            "workflow_dispatch",
            "--status",
            "completed",
            "--limit",
            str(scan),
            "--json",
            "databaseId",
        ],
        "workflow run list",
    )
    if not isinstance(listed, list):
        return None

    run_ids: list[int] = []
    for item in listed:
        if isinstance(item, dict) and isinstance(item.get("databaseId"), int):
            run_ids.append(item["databaseId"])

    for run_id in run_ids[:scan]:
        try:
            metadata = _gh_json(
                [
                    "run",
                    "view",
                    str(run_id),
                    "--repo",
                    repo,
                    "--json",
                    "conclusion,event,headBranch,headSha,jobs,url",
                ],
                "workflow run metadata",
            )
            log = _gh_text(
                ["run", "view", str(run_id), "--repo", repo, "--log"],
                "workflow run log",
            )
        except ResolutionError:
            # A pinned run can disappear before a newer canonical run exists.
            continue

        if not isinstance(metadata, dict) or not is_canonical(metadata):
            continue
        if not has_readonly_authorization(log):
            continue
        structured_output = extract_structured_output(log)
        if structured_output is None:
            continue

        return {
            "databaseId": run_id,
            "url": metadata.get("url") or f"https://github.com/{repo}/actions/runs/{run_id}",
            "event": metadata.get("event"),
            "headBranch": metadata.get("headBranch"),
            "headSha": metadata.get("headSha"),
            "conclusion": metadata.get("conclusion"),
            "authorization": {
                "operation": "diagnostics",
                "target": "production",
                "ref": "main",
                "confirm": "",
            },
            "steps": {
                DIAGNOSTICS_STEP: "success",
                DEPLOY_STEP: "skipped",
                EXEC_STEP: "skipped",
            },
            "structuredOutputStatus": "diagnosed",
        }

    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the newest read-only diagnostics run"
    )
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--workflow", default=WORKFLOW)
    parser.add_argument("--scan", type=int, default=SCAN)
    parser.add_argument("--dump-json", metavar="RUN_ID")
    args = parser.parse_args(argv)

    if args.scan < 1:
        return 2

    try:
        if args.dump_json is not None:
            if not RUN_ID_RE.fullmatch(args.dump_json):
                return 2
            log = _gh_text(
                ["run", "view", args.dump_json, "--repo", args.repo, "--log"],
                "workflow run log",
            )
            structured_output = extract_structured_output(log)
            if structured_output is None:
                print("sanitized diagnostics output not found", file=sys.stderr)
                return 3
            print(structured_output)
            return 0

        resolved = resolve(args.repo, args.workflow, args.scan)
        if resolved is None:
            print("no canonical read-only diagnostics run found", file=sys.stderr)
            return 3
        print(
            json.dumps(
                {"repo": args.repo, "workflow": args.workflow, "run": resolved},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ResolutionError:
        print("canonical diagnostics run query failed", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
