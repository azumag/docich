#!/usr/bin/env python3
"""Classify OpenCode JSON stdout after a non-zero CLI exit.

This helper runs only inside the production VM. It emits exactly one fixed
category and never emits provider text, model output, prompts, or evidence.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

CATEGORIES = {
    "error_event",
    "error_part",
    "tool_event",
    "unexpected_event",
    "invalid_json",
    "structured_nonzero",
    "no_json",
}
MAX_BYTES = 2 * 1024 * 1024


def classify(path: Path) -> str:
    try:
        st = path.lstat()
    except OSError:
        return "no_json"
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
        return "no_json"
    try:
        raw = path.read_bytes()
    except OSError:
        return "no_json"
    if len(raw) > MAX_BYTES:
        return "no_json"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "invalid_json"

    saw = False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return "invalid_json"
        if not isinstance(event, dict) or event.get("error"):
            return "error_event"
        saw = True
        if event.get("type") not in {"step_start", "step_finish", "text"}:
            return "unexpected_event"
        part = event.get("part") or {}
        if not isinstance(part, dict) or part.get("error"):
            return "error_part"
        if part.get("reason") in {"tool-calls", "tool_calls", "error"} or any(
            key in part for key in ("tool", "toolCallID", "tool_calls")
        ):
            return "tool_event"
    return "structured_nonzero" if saw else "no_json"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("no_json")
        return 0
    result = classify(Path(argv[1]))
    print(result if result in CATEGORIES else "structured_nonzero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
