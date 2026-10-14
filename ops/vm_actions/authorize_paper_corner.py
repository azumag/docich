#!/usr/bin/env python3
"""Fail-closed authorization for fixed owner-only PAPER operations."""
from __future__ import annotations

import json
import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = ".github/workflows/paper-corner-operator.yml"
COMMAND_ISSUE = "293"
OPERATIONS = {"start", "reload-worker"}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
NONCE_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _issue_payload(raw: str) -> tuple[str, str]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        fail("invalid PAPER operation issue command")
    if not isinstance(data, dict) or set(data) != {"operation", "confirm", "nonce"}:
        fail("invalid PAPER operation issue command")
    operation = data.get("operation")
    if operation not in OPERATIONS or data.get("confirm") != "production":
        fail("invalid PAPER operation issue command")
    nonce = data.get("nonce")
    if not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce):
        fail("invalid PAPER operation issue nonce")
    return str(operation), nonce


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
        fail("PAPER operation authorization denied")

    sha = env.get("GITHUB_SHA", "")
    if not SHA_RE.fullmatch(sha):
        fail("invalid workflow SHA")

    event = env.get("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch":
        if env.get("INPUT_CONFIRM") != "production":
            fail("production confirmation required")
        operation = env.get("INPUT_OPERATION", "")
        if operation not in OPERATIONS:
            fail("unsupported PAPER operation")
        nonce = "workflow-dispatch"
    elif event == "issues":
        if env.get("GITHUB_EVENT_ACTION") != "edited":
            fail("unsupported issue event")
        if env.get("GITHUB_ISSUE_NUMBER") != COMMAND_ISSUE:
            fail("unexpected PAPER command issue")
        if env.get("GITHUB_ISSUE_AUTHOR") != OWNER or env.get("GITHUB_ISSUE_AUTHOR_ID") != OWNER_ID:
            fail("unexpected PAPER command issue owner")
        operation, nonce = _issue_payload(env.get("GITHUB_ISSUE_BODY", ""))
    else:
        fail("unsupported event")

    result = {
        "operation": operation,
        "target": "production",
        "ref": "main",
        "nonce": nonce,
    }
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"operation={operation}\n")
            out.write("target=production\n")
            out.write("ref=main\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
