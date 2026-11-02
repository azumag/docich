#!/usr/bin/env python3
"""Operator parity gate for the production VM (docich#2681).

Replaces the previous "production is exactly current protected main" check.
The 2026-10-23 incident showed that requiring ``production == main`` blocks
every operator workflow (including the recovery operators) whenever a deploy
fails and production stays one commit behind main.

The gate now accepts production at its deployed reviewed baseline:
``status == "configured"`` (tracked-clean checkout whose HEAD equals the
recorded baseline) plus, when that baseline is not exactly current main, a
locally verified proof that the deployed SHA is an ancestor of the current
main SHA. Anything else — drift, recovery_required, or a baseline that is not
reachable from main — still refuses. Operator scripts keep coming from the
current main checkout, so the ancestor proof bounds how far the operator code
may run ahead of the deployed code.

The ancestor proof deepens the shallow ``control`` checkout by a fixed
``PARITY_DEPTH`` commits and asks git locally; no tokens or API calls are
used, and a production baseline deeper than that window fails closed (deploy
required). All refusals print fixed messages only; raw git output is never
echoed.

Usage: check_operator_parity.py <status-json> <expected-main-sha> <control-dir>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

PARITY_DEPTH = 50
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
GIT_TIMEOUT = 120


def _refuse(reason: str) -> None:
    sys.stdout.write(f"parity gate refused: {reason}\n")
    raise SystemExit(1)


def _git(control: str, *argv: str) -> int:
    try:
        return subprocess.run(
            ["git", "-C", control, *argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT,
        ).returncode
    except (subprocess.SubprocessError, OSError):
        return 1


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: check_operator_parity.py <status-json> <expected-sha> <control-dir>"
        )
    status_raw, expected, control = sys.argv[1], sys.argv[2], sys.argv[3]
    if not SHA_RE.fullmatch(expected):
        raise SystemExit("invalid expected sha")
    try:
        data = json.loads(status_raw)
    except ValueError:
        _refuse("status payload is not valid JSON")
    if not isinstance(data, dict):
        _refuse("status payload is not an object")
    if data.get("status") != "configured":
        # drift / recovery_required / bootstrap_required all keep blocking.
        _refuse("production is not at the recorded reviewed baseline")
    deployed = data.get("sha")
    if not isinstance(deployed, str) or not SHA_RE.fullmatch(deployed):
        _refuse("status did not report a valid deployed sha")
    if deployed == expected:
        return
    # The deployed baseline is behind (or diverged from) current main. Allow it
    # only when it is a reviewed ancestor of the current main SHA.
    if _git(control, "fetch", "--quiet", "--no-tags", f"--deepen={PARITY_DEPTH}", "origin") != 0:
        _refuse("main history fetch failed")
    if _git(control, "merge-base", "--is-ancestor", deployed, expected) != 0:
        _refuse(
            f"production baseline is not an ancestor of current main within {PARITY_DEPTH} commits; deploy required"
        )


if __name__ == "__main__":
    main()
