#!/usr/bin/env python3
"""Fail-closed authorization for fixed owner-only NetHack corner operations."""
from __future__ import annotations

import json
import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = ".github/workflows/nethack-corner-operator.yml"
OPERATIONS = {"start", "stop", "status", "recover"}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
MIN_DURATION = 1
MAX_DURATION = 60


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _validate_duration(value: object) -> int:
    if isinstance(value, bool):
        fail("invalid NetHack corner duration")
    try:
        duration = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        fail("invalid NetHack corner duration")
    if str(duration) != str(value).strip() or not MIN_DURATION <= duration <= MAX_DURATION:
        fail("invalid NetHack corner duration")
    return duration


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
        fail("NetHack corner authorization denied")

    sha = env.get("GITHUB_SHA", "")
    if not SHA_RE.fullmatch(sha):
        fail("invalid workflow SHA")

    if env.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch":
        fail("unsupported event")
    if env.get("INPUT_CONFIRM") != "production":
        fail("production confirmation required")
    operation = env.get("INPUT_OPERATION", "")
    if operation not in OPERATIONS:
        fail("unsupported NetHack corner operation")
    # stop/status ignore the duration, but validating one grammar keeps a second
    # looser issue payload grammar from becoming another control surface.
    duration = _validate_duration(env.get("INPUT_DURATION_MINUTES", ""))

    result = {
        "operation": operation,
        "target": "production",
        "ref": "main",
        "duration_minutes": duration,
    }
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"operation={operation}\n")
            out.write("target=production\n")
            out.write("ref=main\n")
            out.write(f"duration_minutes={duration}\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
