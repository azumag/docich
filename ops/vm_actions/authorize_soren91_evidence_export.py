#!/usr/bin/env python3
"""Fail-closed authorization for explicit Soren91 evidence exports."""
from __future__ import annotations

import json
import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = ".github/workflows/soren91-evidence-export.yml"
ISSUE_NUMBER = "414"
COMMENT_COMMAND = "/soren91-evidence-export"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _games(value: object) -> int:
    raw = str(value).strip()
    if not re.fullmatch(r"[1-3]", raw):
        fail("invalid Soren91 evidence game count")
    return int(raw)


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
        fail("Soren91 evidence export authorization denied")

    sha = env.get("GITHUB_SHA", "")
    if not SHA_RE.fullmatch(sha):
        fail("invalid workflow SHA")

    event = env.get("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch":
        if env.get("INPUT_CONFIRM") != "production":
            fail("production confirmation required")
        games = _games(env.get("INPUT_GAMES", ""))
        source = "dispatch"
    elif event == "issue_comment":
        if (
            env.get("GITHUB_EVENT_ACTION") != "created"
            or env.get("ISSUE_NUMBER") != ISSUE_NUMBER
            or env.get("COMMENT_BODY", "").strip() != COMMENT_COMMAND
            or env.get("COMMENT_AUTHOR") != OWNER
            or env.get("COMMENT_AUTHOR_ID") != OWNER_ID
        ):
            fail("unsupported Soren91 evidence issue command")
        games = 2
        source = "issue_comment"
    else:
        fail("unsupported event")

    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"games={games}\n")
            out.write("target=production\n")
            out.write("ref=main\n")
            out.write(f"source={source}\n")
    print(json.dumps({"games": games, "target": "production", "ref": "main", "source": source}, separators=(",", ":")))


if __name__ == "__main__":
    main()
