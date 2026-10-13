#!/usr/bin/env python3
"""Fail-closed authorization for the owner-only audio-worker speak operation.

The only free-form input is the sentence to be spoken.  It is never an
authorization source and is never interpolated into shell: the workflow hands
it to the VM as base64 (see ``encode_for_vm``) and the VM script re-validates
it.  Everything else (repository, owner, protected main, workflow path) is an
exact match, like the other fixed operators.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import unicodedata

OWNER = "azumag"
OWNER_ID = "9018513"
REPOSITORY = "azumag/docich"
REPOSITORY_ID = "1327276249"
WORKFLOW = ".github/workflows/audio-worker-speak.yml"
MAX_CHARS = 240  # same bound as the work-indicator audio (AGENTS.md section 8)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def normalize_text(raw: str) -> str:
    """Return the sentence to speak or raise ValueError (message has no text)."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"text exceeds {MAX_CHARS} characters")
    for char in text:
        # Control, format (bidi/zero-width), surrogate, private-use, unassigned.
        if unicodedata.category(char)[0] == "C":
            raise ValueError("text contains control or invisible characters")
    return text


def encode_for_vm(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def main() -> None:
    env = os.environ
    checks = [
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
        env.get("GITHUB_WORKFLOW_REF") == f"{REPOSITORY}/{WORKFLOW}@refs/heads/main",
    ]
    if not all(checks):
        fail("audio speak authorization denied")
    if not SHA_RE.fullmatch(env.get("GITHUB_SHA", "")):
        fail("invalid workflow SHA")
    if env.get("GITHUB_EVENT_NAME", "") != "workflow_dispatch":
        fail("unsupported event")
    if env.get("INPUT_CONFIRM") != "production":
        fail("production confirmation required")
    try:
        text = normalize_text(env.get("INPUT_TEXT", ""))
    except ValueError as exc:
        fail(f"invalid speak text: {exc}")

    # The sentence is deliberately not echoed to stdout or step outputs.
    output = env.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"text_b64={encode_for_vm(text)}\n")
    print(json.dumps({"operation": "audio-speak", "target": "production",
                      "ref": "main", "chars": len(text)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
