#!/usr/bin/env python3
"""Authorize one fixed owner-only live Hanjuku status query."""
from __future__ import annotations

import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = "azumag/docich/.github/workflows/hanjuku-live-status.yml@refs/heads/main"
ISSUE_NUMBER = "1339"
COMMAND = "/hanjuku-status production"


def authorize(env):
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
        env.get("COMMENT_BODY") == COMMAND,
        env.get("COMMENT_AUTHOR") == OWNER,
        env.get("COMMENT_AUTHOR_ID") == OWNER_ID,
    )
    sha = env.get("GITHUB_SHA", "")
    if not all(checks) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("authorization_denied")
    return sha


def main():
    try:
        authorize(os.environ)
    except Exception:
        print("Hanjuku live status authorization denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
