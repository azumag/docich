#!/usr/bin/env python3
"""Known-safe local strategist used only to smoke-test the canary pipeline."""
from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return 2
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        return 2
    intent = request.get("intent")
    if not isinstance(intent, str) or not intent:
        return 2
    response = {
        "schema_version": 1,
        "kind": "hold",
        "rationale": f"canary smoke candidate holds on {intent}",
        "inventory_letter": None,
        "prompt_answer": None,
        "narration": "",
    }
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
