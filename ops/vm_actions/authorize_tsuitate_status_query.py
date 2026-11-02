#!/usr/bin/env python3
"""Authorize one owner-only Tsuitate historical status request from control Issue #2938."""
from __future__ import annotations

import json
import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = "azumag/docich/.github/workflows/tsuitate-status-query.yml@refs/heads/main"
ISSUE_NUMBER = "2938"
COMMAND_RE = re.compile(r"/tsuitate-status production (hourly-[0-9]{10})\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def authorize(env: dict[str, str]) -> dict[str, str]:
    body = env.get("COMMENT_BODY", "")
    command = COMMAND_RE.fullmatch(body)
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
        env.get("GITHUB_EVENT_NAME") == "issue_comment",
        env.get("GITHUB_EVENT_ACTION") == "created",
        env.get("ISSUE_NUMBER") == ISSUE_NUMBER,
        env.get("COMMENT_AUTHOR") == OWNER,
        env.get("COMMENT_AUTHOR_ID") == OWNER_ID,
        command is not None,
    )
    if not all(checks) or not SHA_RE.fullmatch(env.get("GITHUB_SHA", "")):
        raise ValueError("authorization_denied")
    assert command is not None
    return {
        "operation": "tsuitate-status-query",
        "target": "production",
        "ref": "main",
        "run_id": command.group(1),
    }


def main() -> int:
    try:
        result = authorize(dict(os.environ))
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as out:
                for key in ("operation", "target", "ref", "run_id"):
                    out.write(f"{key}={result[key]}\n")
        print(json.dumps(result, separators=(",", ":")))
    except Exception:
        print("Tsuitate status query authorization denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
