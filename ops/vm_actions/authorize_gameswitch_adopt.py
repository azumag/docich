#!/usr/bin/env python3
"""Fail-closed authorization for the fixed GameSwitch failed-candidate adoption.

This operation adopts a still-running ``failed`` GameSwitch candidate (a
rollback that could not stop the candidate) as ``active`` without stopping it.
It is a fixed, owner-only operation: no arbitrary command, unit, or argument is
accepted, and the only executable surface is the canonical CLI
``recover --adopt-failed-candidate`` with a bounded timeout.

Adoption only applies to a canonical ``failed`` state whose single ``retiring``
seat is a failed candidate, so the declared phase and cleanup-pending flag are
fixed to exactly that shape. A declaration that does not match canonical state
is rejected by the VM helper before anything runs.

The existing owner/protected main/immutable SHA contract is maintained. Shared
delivery and audio are never touched. The operation is permitted even when the
repository is public, because it is not an arbitrary exec.
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
WORKFLOW = ".github/workflows/gameswitch-adopt.yml"
OPERATION = "adopt_failed_candidate"
# Adoption only ever applies to a canonical failed state. Unknown phases are
# rejected so the declaration cannot drift into another recovery shape.
ALLOWED_PHASES = {"failed"}
# That failed shape always holds its candidate in retiring, so cleanup must be
# pending. Any other declared value is rejected.
ALLOWED_CLEANUP_PENDING = {"true"}
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
        fail("GameSwitch adoption authorization denied")
    if not SHA_RE.fullmatch(env.get("GITHUB_SHA", "")):
        fail("invalid workflow SHA")
    if env.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch":
        fail("unsupported event")
    # The existing owner-only contract requires the explicit production
    # confirmation on every operation, including this fixed one.
    if env.get("INPUT_CONFIRM") != "production":
        fail("production confirmation required")
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
            # A single-line JSON decision so every declared output exists and
            # the workflow can report the authorized declaration verbatim.
            out.write(f"decision={json.dumps(result, separators=(',', ':'))}\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
