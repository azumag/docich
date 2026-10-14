#!/usr/bin/env python3
"""Parse the durable, owner-authenticated PAPER restore request markers.

The marker comments are written by the owner-only issue workflow.  Keeping the
queue state in comments means a pending GitHub Actions run can be replaced by
another VM operation without losing the restore request.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable


NONCE_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")
REQUEST_MARKER = "docich:paper-restore-request"
COMPLETE_MARKER = "docich:paper-restore-complete"
BOT_LOGIN = "github-actions[bot]"


def _marker(kind: str, nonce: str) -> str:
    if kind not in {REQUEST_MARKER, COMPLETE_MARKER}:
        raise ValueError("invalid marker kind")
    if not isinstance(nonce, str) or NONCE_RE.fullmatch(nonce) is None:
        raise ValueError("invalid restore nonce")
    return f"<!-- {kind} nonce={nonce} -->"


def request_marker(nonce: str) -> str:
    return _marker(REQUEST_MARKER, nonce)


def complete_marker(nonce: str) -> str:
    return _marker(COMPLETE_MARKER, nonce)


def parse_command(body: str) -> str:
    """Validate the fixed Issue #569 command and return its nonce."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid restore command") from exc
    if not isinstance(data, dict) or set(data) != {"operation", "confirm", "nonce"}:
        raise ValueError("invalid restore command")
    if data.get("operation") != "restore-scheduled" or data.get("confirm") != "production":
        raise ValueError("invalid restore command")
    nonce = data.get("nonce")
    if not isinstance(nonce, str) or NONCE_RE.fullmatch(nonce) is None:
        raise ValueError("invalid restore nonce")
    return nonce


def _comment_bodies(lines: Iterable[str]) -> Iterable[str]:
    for raw in lines:
        if not raw.strip():
            continue
        try:
            comment = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid issue comment response") from exc
        user = comment.get("user") if isinstance(comment, dict) else None
        if not isinstance(user, dict):
            continue
        if user.get("login") != BOT_LOGIN or user.get("type") != "Bot":
            continue
        body = comment.get("body")
        if isinstance(body, str):
            yield body


def _marker_nonces(lines: Iterable[str], kind: str) -> list[str]:
    marker_re = re.compile(rf"^<!-- {re.escape(kind)} nonce=([A-Za-z0-9._:-]{{1,64}}) -->$")
    found: list[str] = []
    for body in _comment_bodies(lines):
        for line in body.splitlines():
            match = marker_re.fullmatch(line.strip())
            if match:
                found.append(match.group(1))
    return found


def nonce_state_from_lines(lines: list[str], nonce: str) -> str:
    _marker(REQUEST_MARKER, nonce)
    requests = set(_marker_nonces(lines, REQUEST_MARKER))
    completed = set(_marker_nonces(lines, COMPLETE_MARKER))
    if nonce in completed:
        return "complete"
    if nonce in requests:
        return "pending"
    return "new"


def oldest_pending_nonce(lines: list[str]) -> str | None:
    completed = set(_marker_nonces(lines, COMPLETE_MARKER))
    seen: set[str] = set()
    for nonce in _marker_nonces(lines, REQUEST_MARKER):
        if nonce not in seen and nonce not in completed:
            return nonce
        seen.add(nonce)
    return None


def _read_lines(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("usage: paper_restore_queue.py command|state|queue ...")
    mode = args.pop(0)
    try:
        if mode == "command" and len(args) == 1:
            print(f"nonce={parse_command(args[0])}")
        elif mode == "state" and len(args) == 2:
            state = nonce_state_from_lines(_read_lines(args[0]), args[1])
            print(f"state={state}")
        elif mode == "queue" and len(args) == 1:
            nonce = oldest_pending_nonce(_read_lines(args[0]))
            print("needed=true" if nonce else "needed=false")
            if nonce:
                print(f"nonce={nonce}")
        else:
            raise ValueError("invalid arguments")
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
