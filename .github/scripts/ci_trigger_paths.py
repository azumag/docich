#!/usr/bin/env python3
"""Reproduce which path-filtered workflows a set of changed files triggers.

Usage:
    python3 .github/scripts/ci_trigger_paths.py FILE [FILE ...]
    python3 .github/scripts/ci_trigger_paths.py --diff BASE HEAD
    python3 .github/scripts/ci_trigger_paths.py --event push FILE ...

It reads ``on.<event>.paths`` straight from ``.github/workflows/*.yml`` (a plain
line parser, no YAML dependency) and applies the documented GitHub filter-pattern
rules:

* ``*``   matches zero or more characters except ``/``
* ``**``  matches zero or more of any character, including ``/``
* ``?``   zero or one of the preceding character, ``+`` one or more of it
* ``[...]`` character classes, ``[!...]`` negated classes
* ``!pattern`` re-excludes a path an earlier pattern selected; patterns are
  evaluated in order and the last one that matches a path decides it.

A workflow starts when at least one changed file ends up selected. For the three
workflows that carry a ``soren-gitlink-gate`` job (retro-corner-regressions,
trading-regressions, overlay-queue-interoperability) the heavy job additionally
needs the gate's ``--root-path`` list (``fnmatch`` semantics, exactly as
``soren_gitlink_gate.py`` applies it) to match; this tool reports that too.

This is a reproduction of the documented rules for review and regression tests,
not a byte-exact copy of GitHub's matcher; the authoritative answer is always the
checks list of the pull request.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _translate(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                i += 2
                if i < n and pattern[i] == "/":
                    out.append("(?:.*/)?")  # "a/**/b" also matches "a/b"
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c in "?+":
            out.append(c)
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : j]
                out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def path_selected(patterns: list[str], path: str) -> bool:
    selected = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if _translate(pattern[1:] if negated else pattern).match(path):
            selected = not negated
    return selected


def event_paths(text: str, event: str) -> list[str] | None:
    """Return ``on.<event>.paths`` or None when the event has no paths filter."""
    lines = text.splitlines()
    try:
        start = lines.index(f"  {event}:") + 1
    except ValueError:
        return None
    paths: list[str] | None = None
    for index in range(start, len(lines)):
        line = lines[index]
        if line and not line.startswith("    "):
            break  # next event / next top-level key
        if line == "    paths:":
            paths = []
            for item in lines[index + 1 :]:
                if not item.startswith("      - "):
                    break
                paths.append(item.removeprefix("      - ").strip().strip("'\""))
            break
    return paths


def gate_root_paths(text: str) -> list[str]:
    return re.findall(r"--root-path\s+'([^']+)'", text)


def gate_allows(root_paths: list[str], changed: list[str]) -> bool:
    """Direct-path half of soren_gitlink_gate.py (gitlink-only changes are not modelled)."""
    direct = [p for p in changed if p != "games/soviet_now"]
    return any(fnmatch.fnmatchcase(p, rp) for p in direct for rp in root_paths)


def triggered(changed: list[str], event: str = "pull_request") -> dict[str, dict[str, bool]]:
    """workflow file name -> {"starts": bool, "heavy": bool} for path-filtered workflows."""
    result: dict[str, dict[str, bool]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        patterns = event_paths(text, event)
        if patterns is None:
            continue
        starts = any(path_selected(patterns, p) for p in changed)
        roots = gate_root_paths(text)
        heavy = starts and (gate_allows(roots, changed) if roots else True)
        result[wf.name] = {"starts": starts, "heavy": heavy}
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--diff", nargs=2, metavar=("BASE", "HEAD"))
    ap.add_argument("--event", default="pull_request", choices=("pull_request", "push"))
    args = ap.parse_args(argv)
    files = list(args.files)
    if args.diff:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{args.diff[0]}...{args.diff[1]}"], cwd=ROOT, text=True
        )
        files += [line for line in out.splitlines() if line]
    if not files:
        ap.error("no changed files given")
    for name, state in triggered(files, args.event).items():
        if state["starts"]:
            note = "" if state["heavy"] else "  (gate job only: heavy job skipped)"
            print(f"{name}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
