#!/usr/bin/env python3
"""Fail-closed authorization for the owner-only pull-request merge report.

The workflow runs on a push to protected ``main`` after a pull request is
merged.  There is no free-form workflow input: the only data spoken later is the
merged pull request's title, fetched from the GitHub API after this check.  This
module re-validates the push payload so a workflow copy from another ref, a
force push, a branch deletion, or a non-main event cannot reach the VM gateway.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = "azumag/docich/.github/workflows/pr-merge-audio-report.yml@refs/heads/main"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ZERO_SHA = "0" * 40


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and SHA_RE.fullmatch(value) else None


def authorize(env: dict[str, str], event: object) -> dict[str, str]:
    checks = (
        env.get("GITHUB_REPOSITORY") == REPOSITORY,
        env.get("GITHUB_REPOSITORY_ID") == REPOSITORY_ID,
        env.get("GITHUB_REPOSITORY_OWNER") == OWNER,
        env.get("GITHUB_REPOSITORY_OWNER_ID") == OWNER_ID,
        env.get("GITHUB_ACTOR") == OWNER,
        env.get("GITHUB_ACTOR_ID") == OWNER_ID,
        env.get("GITHUB_TRIGGERING_ACTOR") == OWNER,
        env.get("GITHUB_REF") == "refs/heads/main",
        env.get("GITHUB_REF_PROTECTED", "").lower() == "true",
        env.get("GITHUB_DEFAULT_BRANCH") == "main",
        env.get("GITHUB_WORKFLOW_REF") == WORKFLOW,
        env.get("GITHUB_EVENT_NAME") == "push",
    )
    if not all(checks):
        raise ValueError("authorization_denied")
    sha = _sha(env.get("GITHUB_SHA"))
    if sha is None or sha == ZERO_SHA:
        raise ValueError("authorization_denied")
    if not isinstance(event, dict):
        raise ValueError("authorization_denied")
    repository = event.get("repository")
    if (
        not isinstance(repository, dict)
        or repository.get("full_name") != REPOSITORY
        or str(repository.get("id")) != REPOSITORY_ID
    ):
        raise ValueError("authorization_denied")
    if event.get("ref") != "refs/heads/main":
        raise ValueError("authorization_denied")
    if event.get("deleted") is not False or event.get("forced") is not False:
        raise ValueError("authorization_denied")
    before = _sha(event.get("before"))
    after = _sha(event.get("after"))
    if before is None or after is None or after != sha or before == after:
        raise ValueError("authorization_denied")
    commits = event.get("commits")
    if not isinstance(commits, list) or not commits:
        raise ValueError("authorization_denied")
    head_commit = event.get("head_commit")
    if not isinstance(head_commit, dict) or head_commit.get("id") != sha:
        raise ValueError("authorization_denied")
    return {"operation": "pr-merge-audio-report", "target": "production", "ref": "main"}


def main() -> int:
    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_path:
            raise ValueError("authorization_denied")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        result = authorize(dict(os.environ), event)
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as out:
                for key in ("operation", "target", "ref"):
                    out.write(f"{key}={result[key]}\n")
        print(json.dumps(result, separators=(",", ":")))
    except Exception:
        print("PR merge audio report authorization denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
