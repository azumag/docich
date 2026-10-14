#!/usr/bin/env python3
"""Summarize COMMENT winner routing without exposing model identifiers.

The owner-only VM diagnostics already contains bounded recent AI events with
provider/model fields. This helper intentionally emits only fixed backend
families for successful COMMENT winners so operators can tell whether viewer
replies are being served by the cheap/free front of the chain or by a fallback
backend without publishing model names, prompts, response text, paths, or
arbitrary labels.
"""
from collections import Counter
import json
import sys

BACKEND_FAMILIES = (
    "local",
    "codex",
    "opencode",
    "opencode_go",
    "vercel",
    "amd",
    "other",
)


def _backend_family(event):
    if not isinstance(event, dict):
        return "other"
    provider = str(event.get("provider") or "").strip().lower()
    if provider == "local":
        return "local"
    if provider == "codex":
        return "codex"
    if provider == "opencode":
        return "opencode"
    if provider == "opencode-go":
        return "opencode_go"
    if provider == "vercel":
        return "vercel"
    if provider == "amd":
        return "amd"
    return "other"


def _is_comment_winner(event):
    if not isinstance(event, dict) or event.get("event") != "winner":
        return False
    component = str(event.get("component") or "").strip().lower()
    return component.startswith("comment")


def summarize(data):
    if not isinstance(data, dict):
        raise ValueError("diagnostics must be an object")
    ai = data.get("ai")
    recent = ai.get("recent_events") if isinstance(ai, dict) else None
    counts = Counter()
    sampled = 0
    if isinstance(recent, list):
        for event in recent:
            if not _is_comment_winner(event):
                continue
            sampled += 1
            counts[_backend_family(event)] += 1

    parts = [f"comment_winner_sampled={sampled}"]
    parts.extend(
        f"comment_winner_backend_{family}={counts[family]}"
        for family in BACKEND_FAMILIES
    )
    return ",".join(parts)


def main(argv):
    if len(argv) != 2:
        print("usage: summarize_comment_winners.py <diagnostics.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
    print(f"summary={summarize(data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
