#!/usr/bin/env python3
"""Fail-closed authorization for fixed corner-rotation operations.

The canonical workflow and the legacy `retro-corner-operator` workflow are
both accepted during the staged rename, but only as an exact
`<repo>/<workflow path>@refs/heads/main` match. Operations stay a fixed
allowlist; arbitrary commands, issue bodies and PR payloads are never an
authorization source.
"""
from __future__ import annotations

import json
import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOWS = (
    ".github/workflows/corner-rotation-operator.yml",
    ".github/workflows/retro-corner-operator.yml",
)
ALLOWED_OPERATIONS = {"restart-service", "recover-failed", "rollback-timer", "start-hanjuku"}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    env = os.environ
    repo = env.get("GITHUB_REPOSITORY", "")
    allowed_refs = {f"{repo}/{workflow}@refs/heads/main" for workflow in WORKFLOWS}
    checks = [
        repo == REPOSITORY,
        env.get("GITHUB_REPOSITORY_ID") == REPOSITORY_ID,
        env.get("GITHUB_REPOSITORY_OWNER") == OWNER,
        env.get("GITHUB_REPOSITORY_OWNER_ID") == OWNER_ID,
        env.get("GITHUB_ACTOR") == OWNER,
        env.get("GITHUB_ACTOR_ID") == OWNER_ID,
        env.get("GITHUB_TRIGGERING_ACTOR") == OWNER,
        env.get("GITHUB_REF") == "refs/heads/main",
        env.get("GITHUB_REF_PROTECTED", "").lower() == "true",
        env.get("GITHUB_DEFAULT_BRANCH") == "main",
        env.get("GITHUB_WORKFLOW_REF") in allowed_refs,
    ]
    if not all(checks):
        fail("corner rotation authorization denied")

    if not SHA_RE.fullmatch(env.get("GITHUB_SHA", "")):
        fail("invalid workflow SHA")
    if env.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch":
        fail("unsupported event")
    if env.get("INPUT_CONFIRM") != "production":
        fail("production confirmation required")
    operation = env.get("INPUT_OPERATION", "")
    if operation not in ALLOWED_OPERATIONS:
        fail("unsupported corner rotation operation")

    if operation == "start-hanjuku" and env.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/{WORKFLOWS[0]}@refs/heads/main":
        fail("Hanjuku start requires the canonical operator workflow")

    result = {"operation": operation, "target": "production", "ref": "main"}
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"operation={operation}\n")
            out.write("target=production\n")
            out.write("ref=main\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
