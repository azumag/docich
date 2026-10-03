#!/usr/bin/env python3
"""Fixed Issue result metadata only; never includes evidence or transport output."""
from __future__ import annotations

import json
import os
import re
import sys

BASE = "https://github.com/azumag/docich"


def reply(env):
    def checked(key, pattern):
        value = env.get(key, "")
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("invalid_reply_metadata")
        return value

    comment = checked("SOURCE_COMMENT_ID", r"[1-9][0-9]{0,19}")
    run = checked("RUN_ID", r"[1-9][0-9]{0,19}")
    attempt = checked("RUN_ATTEMPT", r"[1-9][0-9]{0,3}")
    sha = checked("RUN_SHA", r"[0-9a-f]{40}")
    mode = checked("QUERY_MODE", r"list|export")
    runtime = env.get("RUNTIME_ID", "")
    if mode == "export":
        runtime = checked("RUNTIME_ID", r"g[1-9][0-9]{0,17}-[0-9a-f]{8}")
    elif runtime != "":
        raise ValueError("invalid_reply_metadata")
    outcome = checked("UPLOAD_OUTCOME", r"success|failure|cancelled|skipped")
    lines = [f"Hanjuku evidence {mode}: {'success' if outcome == 'success' else 'not available'}",
             f"Request: {BASE}/issues/1339#issuecomment-{comment}",
             f"Run: {BASE}/actions/runs/{run}/attempts/{attempt}",
             f"SHA: `{sha}`"]
    if runtime:
        lines.append(f"Runtime: `{runtime}`")
    if outcome == "success":
        artifact = checked("ARTIFACT_ID", r"[1-9][0-9]{0,19}")
        digest = checked("ARTIFACT_DIGEST", r"[0-9a-f]{64}")
        lines += [f"Artifact ID: `{artifact}` (retention: 1 day)",
                  f"Artifact SHA-256: `{digest}`"]
    else:
        # Never turn stderr/source data into a public explanation.
        lines.append("No result artifact was published. See the run status.")
    return {"body": "\n\n".join(lines)}


def main():
    try:
        print(json.dumps(reply(os.environ), ensure_ascii=False))
    except ValueError:
        print("Hanjuku result metadata rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
