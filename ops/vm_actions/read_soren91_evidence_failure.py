#!/usr/bin/env python3
"""Read only the fixed sanitized failure reason from a diagnostic evidence tar."""
from __future__ import annotations

import json
import stat
import sys
import tarfile
from pathlib import Path

ALLOWED_REASONS = frozenset({
    "no_recent_completed_evidence",
    "evidence_path_contract",
    "evidence_file_contract",
    "screenshot_transcode_failed",
    "screenshot_contract",
    "bundle_contract",
    "invalid_request",
    "io_error",
    "unexpected_error",
})
MAX_DIAGNOSTIC_BYTES = 4096


def read_reason(path: Path) -> str:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name != "diagnostic.json":
                return "unclassified_prepare_failure"
            member = members[0]
            if not member.isfile() or member.size < 1 or member.size > MAX_DIAGNOSTIC_BYTES:
                return "unclassified_prepare_failure"
            handle = archive.extractfile(member)
            if handle is None:
                return "unclassified_prepare_failure"
            raw = handle.read(MAX_DIAGNOSTIC_BYTES + 1)
            if len(raw) > MAX_DIAGNOSTIC_BYTES:
                return "unclassified_prepare_failure"
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("status") != "prepare_failed":
            return "unclassified_prepare_failure"
        reason = data.get("reason")
        return reason if reason in ALLOWED_REASONS else "unclassified_prepare_failure"
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError):
        return "unclassified_prepare_failure"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("unclassified_prepare_failure")
        return 0
    print(read_reason(Path(argv[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
