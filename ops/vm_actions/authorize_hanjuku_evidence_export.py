#!/usr/bin/env python3
"""Build a bounded request only for an owner dispatch on protected main."""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hanjuku_evidence as evidence
import hanjuku_evidence_gateway as gateway

WORKFLOW = "azumag/docich/.github/workflows/hanjuku-evidence-export.yml@refs/heads/main"
EXPECTED = {
    "GITHUB_REPOSITORY": "azumag/docich", "GITHUB_REPOSITORY_ID": "1327276249",
    "GITHUB_REPOSITORY_OWNER": "azumag", "GITHUB_REPOSITORY_OWNER_ID": "9018513",
    "GITHUB_ACTOR": "azumag", "GITHUB_ACTOR_ID": "9018513",
    "GITHUB_TRIGGERING_ACTOR": "azumag", "GITHUB_REF": "refs/heads/main",
    "GITHUB_REF_PROTECTED": "true", "GITHUB_DEFAULT_BRANCH": "main",
    "GITHUB_WORKFLOW_REF": WORKFLOW, "GITHUB_EVENT_NAME": "workflow_dispatch",
    "INPUT_CONFIRM": "production",
}


def authorize(env):
    if (any(env.get(key) != value for key, value in EXPECTED.items())
            or not isinstance(env.get("GITHUB_SHA"), str)
            or not gateway.SHA_RE.fullmatch(env["GITHUB_SHA"])):
        raise evidence.EvidenceError("authorization_denied")
    raw = evidence._dump({"schema": 1, "runtime_id": env.get("INPUT_RUNTIME_ID"),
                          "recipient": env.get("INPUT_RECIPIENT")})
    gateway.parse_request(raw)
    return raw


def main():
    try:
        raw = authorize(os.environ)
    except Exception:
        print("Hanjuku evidence authorization denied", file=sys.stderr)
        return 1
    # Redirected directly to a private runner file by the trusted workflow.
    # Only the public certificate and selector; never any evidence or key.
    sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
