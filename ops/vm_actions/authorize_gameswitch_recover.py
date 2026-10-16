#!/usr/bin/env python3
"""Fail-closed authorization for the fixed GameSwitch recover operation.

This operation recovers a corner slot whose canonical GameSwitch is in a
stable recovery state (``recovery_required`` or ``failed``). It is a fixed,
owner-only operation: no arbitrary command, unit, or argument is accepted.
The canonical CLI is executed with a bounded timeout.

The existing owner/protected main/immutable SHA contract is maintained.
Shared delivery and audio are never touched. The operation is permitted
even when the repository is public, because it is not an arbitrary exec.
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
WORKFLOW = ".github/workflows/gameswitch-recover.yml"
OPERATION = "recover_gameswitch"
# Fixed phase values that permit recovery. Unknown phases are rejected.
ALLOWED_PHASES = {"recovery_required", "failed"}
# Fixed cleanup_pending values. Unknown values are rejected.
ALLOWED_CLEANUP_PENDING = {"true", "false"}
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
        fail("GameSwitch recover authorization denied")
    if not SHA_RE.fullmatch(env.get("GITHUB_SHA", "")):
        fail("invalid workflow SHA")
    if env.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch":
        fail("unsupported event")
    if env.get("INPUT_OPERATION", "") != OPERATION:
        fail("unsupported operation")
    phase = env.get("INPUT_PHASE", "")
    if phase not in ALLOWED_PHASES:
        fail("invalid or unknown phase")
    cleanup_pending = env.get("INPUT_CLEANUP_PENDING", "")
    if cleanup_pending not in ALLOWED_CLEANUP_PENDING:
        fail("invalid or unknown cleanup_pending")
    result = {
        "operation": OPERATION,
        "target": "production",
        "ref": "main",
        "phase": phase,
        "cleanup_pending": cleanup_pending,
    }
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"operation={OPERATION}\n")
            out.write("target=production\n")
            out.write("ref=main\n")
            out.write(f"phase={phase}\n")
            out.write(f"cleanup_pending={cleanup_pending}\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
