#!/usr/bin/env python3
"""Fail-closed authorization for fixed retro-corner operations."""
from __future__ import annotations

import json
import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = ".github/workflows/retro-corner-operator.yml"
ALLOWED_OPERATIONS = {"restart-service", "recover-failed"}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    env = os.environ
    repo = env.get("GITHUB_REPOSITORY", "")
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
        env.get("GITHUB_WORKFLOW_REF") == f"{repo}/{WORKFLOW}@refs/heads/main",
    ]
    if not all(checks):
        fail("retro corner authorization denied")

    if not SHA_RE.fullmatch(env.get("GITHUB_SHA", "")):
        fail("invalid workflow SHA")
    if env.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch":
        fail("unsupported event")
    if env.get("INPUT_CONFIRM") != "production":
        fail("production confirmation required")
    operation = env.get("INPUT_OPERATION", "")
    if operation not in ALLOWED_OPERATIONS:
        fail("unsupported retro corner operation")

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
