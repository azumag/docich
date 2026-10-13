#!/usr/bin/env python3
"""Read-only extraction of live Jev classification pairs from Soren logs.

Soren's live comment worker writes every reply-generation prompt to
``tmp/debug/ai_dispatch/*_COMMENT_*_prompt.txt``. Each file embeds two blocks
that matter here:

  【Comments to Reply To (this round)】  the exact batch lines (``user: comment``)
  【Comment Classifications】            ``[N] user: comment -> category -> lang``

The classification block records the category the production classifier
actually selected (keyless heuristic, or Jev once it replaced a row), so it is
the "current system output" reference the #1263 benchmark compares against.

This tool only reads files and prints JSONL on stdout; it never writes to the
VM or touches production state. Pair it with ``build_jev_suite.py`` to turn the
output into the committed, projected suite.

    # on a host that can read the Soren log tree
    python3 bench/tools/extract_live_logs.py /home/ubuntu/soren > /tmp/pairs.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

CRITERIA = (
    "card_gacha", "raid", "subscription", "stream_goal", "bits",
    "sing_request", "game_question", "game_status", "general_question",
    "strategy_advice", "comment_advice", "stream_bug_report", "chitchat", "other",
)
INDEX_RE = re.compile(r"^\[(\d+)\]\s?(.*)$", re.S)


def sections(text):
    """Map every ``【...】`` heading to the lines that follow it."""
    out, current = {}, None
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.startswith("【") and stripped.endswith("】"):
            current = stripped
            out.setdefault(current, [])
            continue
        if current is not None:
            out[current].append(line)
    return out


def classification_entries(lines):
    entries = []
    for line in lines:
        match = INDEX_RE.match(line.strip())
        if not match:
            continue
        parts = match.group(2).rsplit(" -> ", 2)
        if len(parts) != 3:
            continue
        text, category, lang = parts
        if category not in CRITERIA:
            continue
        entries.append({"index": int(match.group(1)), "text": text,
                        "category": category, "lang": lang})
    return entries


def split_line(raw):
    if ": " in raw:
        user, comment = raw.split(": ", 1)
        return user, comment
    return "", raw


def extract(roots):
    files = []
    for root in roots:
        files.extend(sorted(glob.glob(os.path.join(
            root, "tmp/debug/ai_dispatch/*_COMMENT_*_prompt.txt"))))
    rows = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as stream:
                text = stream.read()
        except OSError:
            continue
        classification = []
        for title, lines in sections(text).items():
            if "Comment Classifications" in title:
                classification = classification_entries(lines)
        for entry in classification:
            # ``entry["text"]`` is the exact ``user: comment`` line the batch
            # file held (the formatter copies it from there), so it is both the
            # authoritative prompt and the handle source. It is not re-read from
            # the 【Comments to Reply To】 block, which can merge adjacent lines.
            line = entry["text"]
            user, comment = split_line(line)
            rows.append({"source": os.path.basename(path), "index": entry["index"],
                         "batch_line": line, "user": user, "comment": comment,
                         "category": entry["category"], "lang": entry["lang"]})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="Soren checkout roots to scan")
    args = parser.parse_args(argv)
    for row in extract(args.roots):
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
