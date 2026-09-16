#!/usr/bin/env python3
"""Normalize successful Soren91 OpenCode JSONL without exposing raw model text.

The daily OpenCode process already writes stdout to a private 0600 temporary
file. This helper reads that file, and only when the stream itself satisfies
the reviewed no-tool event contract and contains a fenced JavaScript block with
the mandatory strategy entrypoint does it replace stdout with one canonical
text event containing that block. Otherwise it emits the original bytes
unchanged so the existing runtime parser remains fail-closed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
DECIDE = "export function decide(boardState)"
ALLOWED_EVENT_TYPES = {"step_start", "step_finish", "text"}
TOOL_REASONS = {"tool-calls", "tool_calls", "error"}
FENCE_RE = re.compile(
    r"```(?:javascript|js|mjs)?[ \t]*\r?\n([\s\S]*?)```",
    re.IGNORECASE,
)


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return b""
    return data


def _collect_text(raw: bytes) -> str | None:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    pieces: list[str] = []
    saw_event = False
    for line in decoded.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            return None
        if not isinstance(event, dict) or event.get("error"):
            return None
        event_type = event.get("type")
        if event_type not in ALLOWED_EVENT_TYPES:
            return None
        part = event.get("part") or {}
        if not isinstance(part, dict) or part.get("error"):
            return None
        if (
            part.get("reason") in TOOL_REASONS
            or "tool" in part
            or "toolCallID" in part
            or "tool_calls" in part
        ):
            return None
        saw_event = True
        if event_type != "text":
            continue
        if part.get("type") not in (None, "text"):
            return None
        text = part.get("text")
        if not isinstance(text, str):
            return None
        pieces.append(text)
    if not saw_event or not pieces:
        return None
    return "".join(pieces)


def _select_complete_block(text: str) -> str | None:
    matches = []
    for match in FENCE_RE.finditer(text):
        code = match.group(1).strip()
        if DECIDE in code:
            matches.append(code)
    if not matches:
        return None
    # A complete module is normally the largest matching block. This also
    # avoids adopting a short illustrative decide() snippet before the actual
    # complete strategy module.
    return max(matches, key=len)


def normalize(raw: bytes) -> bytes:
    text = _collect_text(raw)
    if text is None:
        return raw
    code = _select_complete_block(text)
    if code is None:
        return raw
    event = {
        "type": "text",
        "part": {
            "type": "text",
            "text": code,
        },
    }
    return (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 64
    path = Path(argv[1])
    if not path.is_file() or path.is_symlink():
        return 65
    raw = _read_bounded(path)
    if not raw:
        # Preserve fail-closed behavior for empty/oversized output. Emit no
        # model text rather than trying to synthesize a candidate.
        return 0
    sys.stdout.buffer.write(normalize(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
