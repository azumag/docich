#!/usr/bin/env python3
"""Malicious-looking test candidate proving the broker cannot see game arena files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

TARGET = Path("/canary/episode/playground/xlogfile")


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return 2
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return 2

    readable = False
    writable = False
    try:
        TARGET.read_bytes()
        readable = True
    except OSError:
        pass
    try:
        with TARGET.open("ab") as stream:
            stream.write(b"FORGED\n")
        writable = True
    except OSError:
        pass

    response = {
        "schema_version": 1,
        "kind": "hold",
        "rationale": f"arena_readable={str(readable).lower()};arena_writable={str(writable).lower()}",
        "inventory_letter": None,
        "prompt_answer": None,
        "narration": "",
    }
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
