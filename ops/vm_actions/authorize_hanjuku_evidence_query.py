#!/usr/bin/env python3
"""Authorize owner-only Hanjuku evidence queries from one fixed Issue."""
from __future__ import annotations

import os
import re
import sys

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = "azumag/docich/.github/workflows/hanjuku-evidence-query.yml@refs/heads/main"
ISSUE_NUMBER = "1339"
EXPORT = re.compile(r"/hanjuku-evidence export (g[1-9][0-9]{0,17}-[0-9a-f]{8})\Z")


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
        env.get("COMMENT_AUTHOR") == OWNER,
        env.get("COMMENT_AUTHOR_ID") == OWNER_ID,
    )
    sha = env.get("GITHUB_SHA", "")
    if not all(checks) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("authorization_denied")
    body = env.get("COMMENT_BODY", "").strip()
    if body == "/hanjuku-evidence list":
        return "list", ""
    match = EXPORT.fullmatch(body)
    if match:
        return "export", match.group(1)
    raise ValueError("authorization_denied")


def main():
    try:
        mode, runtime_id = authorize(os.environ)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"mode={mode}\n")
            out.write(f"runtime_id={runtime_id}\n")
    except Exception:
        print("Hanjuku evidence query authorization denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
