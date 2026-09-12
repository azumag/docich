#!/usr/bin/env python3
"""Fail-closed authorization for the bounded PAPER corner operator workflow."""
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
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
NONCE_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")
MIN_DURATION = 1
MAX_DURATION = 60


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _validate_duration(value: object) -> int:
    if isinstance(value, bool):
        fail("invalid PAPER corner duration")
    try:
        duration = int(value)
    except (TypeError, ValueError):
        fail("invalid PAPER corner duration")
    if str(duration) != str(value).strip() or not MIN_DURATION <= duration <= MAX_DURATION:
        fail("invalid PAPER corner duration")
    return duration


def _validate_nonce(value: object) -> str:
    if not isinstance(value, str) or not NONCE_RE.fullmatch(value):
        fail("invalid PAPER corner issue nonce")
    return value


def _issue_payload(raw: str) -> tuple[str, int | None, str]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        fail("invalid PAPER corner issue command")
    if not isinstance(data, dict) or data.get("confirm") != "production":
        fail("invalid PAPER corner issue command")
    operation = data.get("operation")
    if operation == "start":
        if set(data) != {"operation", "duration_minutes", "confirm", "nonce"}:
            fail("invalid PAPER corner issue command")
        return "start", _validate_duration(data.get("duration_minutes")), _validate_nonce(data.get("nonce"))
    if operation == "status":
        if set(data) != {"operation", "confirm", "nonce"}:
            fail("invalid PAPER corner issue command")
        return "status", None, _validate_nonce(data.get("nonce"))
    fail("invalid PAPER corner issue command")


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
        fail("PAPER corner authorization denied")

    sha = env.get("GITHUB_SHA", "")
    if not SHA_RE.fullmatch(sha):
        fail("invalid workflow SHA")

    event = env.get("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch":
        if env.get("INPUT_CONFIRM") != "production":
            fail("production confirmation required")
        operation = env.get("INPUT_OPERATION", "")
        if operation == "start":
            duration = _validate_duration(env.get("INPUT_DURATION_MINUTES", ""))
        elif operation == "status":
            duration = None
        else:
            fail("unsupported PAPER corner operation")
        nonce = "workflow-dispatch"
    elif event == "issues":
        if env.get("GITHUB_EVENT_ACTION") != "edited":
            fail("unsupported issue event")
        if env.get("GITHUB_ISSUE_NUMBER") != COMMAND_ISSUE:
            fail("unexpected PAPER corner command issue")
        if env.get("GITHUB_ISSUE_AUTHOR") != OWNER or env.get("GITHUB_ISSUE_AUTHOR_ID") != OWNER_ID:
            fail("unexpected PAPER corner command issue owner")
        operation, duration, nonce = _issue_payload(env.get("GITHUB_ISSUE_BODY", ""))
    else:
        fail("unsupported event")

    result: dict[str, object] = {
        "operation": operation,
        "target": "production",
        "ref": "main",
        "nonce": nonce,
    }
    if duration is not None:
        result["duration_minutes"] = duration

    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"operation={operation}\n")
            out.write("target=production\n")
            out.write("ref=main\n")
            if duration is not None:
                out.write(f"duration_minutes={duration}\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()